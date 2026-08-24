"""出題の速さの守り（次の問題が出るまでの待ち時間）。

守っている約束:

  - 画像を用意できるかの点検（imageSupportAllowsQuestionObject_）を、
    出題候補の全部にかけない。点検は1問につきキャッシュ参照2回と
    SHA-256計算1回を伴うため、148問ぶん先にかけると
    1問出すたびに数百回の往復が発生する。
  - 点検を後回しにしても、**選ばれる問題は今までと1問も変わらない**。
    ここは静的検査では守れないので、以前の実装（先に全部点検してから選ぶ）を
    このファイル内に再現し、乱数で作った多数の場面で結果を突き合わせる。
  - 画像を用意できない問題は、遅延評価にしても出題されない。
  - 結果画面で行う次の問題の先読みが、採点や学習ログに触れない。

いずれもNodeで実際に関数を動かして確かめる。スプレッドシートへは接続しない。
"""

import random
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from gas_source import function_body, html_script, read, strip_comments  # noqa: E402
from js_runtime import collect, gas_bundle, js_value, require_node, run  # noqa: E402

CODE = read('Code.gs')
CLIENT = html_script('Client.html')

# 出題に必要な部分だけをNode上で組み立てるための差し替え。
# シート読み取りと画像点検だけを偽物にし、選び方の本体は本物を動かす。
STUBS = """
const CALLS = { gate: [] };
const BLOCKED = new Set(BLOCKED_IDS);

readColumns_ = function (sheetName, headers) {
  return sheetName === APP_CONFIG.SHEETS.QUESTIONS ? QUESTIONS : LOGS;
};
readMindmapLeafNodes_ = function () { return NODES; };
imageSupportAllowsQuestionObject_ = function (q) {
  CALLS.gate.push(String(q.question_id));
  return !BLOCKED.has(String(q.question_id));
};
"""

# 以前の実装（先に全部点検してから選ぶ）。結果の突き合わせ用。
LEGACY = """
function legacyNextQuestion(mode, excludeIds) {
  const excludes = new Set(excludeIds);
  const formal = QUESTIONS.filter(isFormalQuestion_);
  const logs = LOGS;

  if (mode === 'unanswered') {
    const answered = answeredQuestionIdSet_(logs);
    const unanswered = formal.filter(q => !answered.has(String(q.question_id)));
    if (!unanswered.length) return { ok: false, reason: 'ALL_QUESTIONS_ANSWERED' };
    const candidates = unanswered
      .filter(q => !excludes.has(String(q.question_id)))
      .filter(q => !BLOCKED.has(String(q.question_id)));
    if (!candidates.length) return { ok: false, reason: 'NO_ELIGIBLE_UNANSWERED_QUESTION' };
    const nodeById = {};
    NODES.forEach(node => { nodeById[node.node_id] = node; });
    const picked = candidates.slice()
      .sort((a, b) => compareUnansweredCandidates_(a, b, nodeById))[0];
    return { ok: true, question_id: String(picked.question_id) };
  }

  const questions = formal
    .filter(q => !excludes.has(String(q.question_id)))
    .filter(q => !BLOCKED.has(String(q.question_id)));
  if (!questions.length) return { ok: false, reason: 'NO_ELIGIBLE_QUESTION' };

  const latest = latestFormalLogByQuestion_(logs);
  const nodes = NODES;
  const today = startOfDay_(new Date(TODAY));

  const rankedNodes = nodes.slice().sort((a, b) => {
    const adue = isDue_(a.next_review_at, today) ? 0 : 1;
    const bdue = isDue_(b.next_review_at, today) ? 0 : 1;
    if (mode === 'review' && adue !== bdue) return adue - bdue;
    if (a.mastery_pct !== b.mastery_pct) return a.mastery_pct - b.mastery_pct;
    if (a.weighted_accuracy !== b.weighted_accuracy) return a.weighted_accuracy - b.weighted_accuracy;
    const acov = a.required_unique_questions ? a.primary_unique_answered_count / a.required_unique_questions : 1;
    const bcov = b.required_unique_questions ? b.primary_unique_answered_count / b.required_unique_questions : 1;
    if (acov !== bcov) return acov - bcov;
    return String(a.node_id).localeCompare(String(b.node_id));
  });

  for (const node of rankedNodes) {
    const primary = questions.filter(q => String(q.primary_node_id || '') === node.node_id);
    if (!primary.length) continue;
    const due = isDue_(node.next_review_at, today);
    const scored = primary.map(q => {
      const l = latest[String(q.question_id)];
      let bucket = 50;
      if (mode === 'review') {
        if (due && l && !truthy_(l.is_correct)) bucket = 0;
        else if (due && l && Number(l.confidence || 0) <= 1) bucket = 1;
        else if (!l) bucket = 5;
        else if (!truthy_(l.is_correct)) bucket = 10;
        else if (Number(l.confidence || 0) <= 1) bucket = 15;
      } else {
        if (due && l && !truthy_(l.is_correct)) bucket = 0;
        else if (!l) bucket = 2;
        else if (l && !truthy_(l.is_correct)) bucket = 8;
        else if (l && Number(l.confidence || 0) <= 1) bucket = 12;
        else bucket = 30;
      }
      return { q, bucket, last: l ? dateFromCell_(l.answered_at) : null };
    });
    scored.sort((a, b) => {
      if (a.bucket !== b.bucket) return a.bucket - b.bucket;
      const at = a.last ? a.last.getTime() : 0;
      const bt = b.last ? b.last.getTime() : 0;
      if (at !== bt) return at - bt;
      return String(a.q.question_id).localeCompare(String(b.q.question_id));
    });
    if (scored.length) return { ok: true, question_id: String(scored[0].q.question_id) };
  }

  const fallback = questions.slice()
    .sort((a, b) => String(a.question_id).localeCompare(String(b.question_id)))[0];
  return { ok: true, question_id: String(fallback.question_id) };
}
"""


def pick(questions, logs, nodes, mode='learning', excludes=(), blocked=(), today='2026-08-19'):
    """本物の getNextQuestion と、以前の実装の両方を動かして結果を返す。"""
    script = '\n'.join([
        'const QUESTIONS = %s;' % js_value(questions),
        'const LOGS = %s;' % js_value(logs),
        'const NODES = %s;' % js_value(nodes),
        'const BLOCKED_IDS = %s;' % js_value(list(blocked)),
        "const TODAY = '%s';" % today,
        gas_bundle(),
        STUBS,
        LEGACY,
        'const request = { mode: %s, excludeQuestionIds: %s };'
        % (js_value(mode), js_value(list(excludes))),
        'const actual = getNextQuestion(request);',
        'const legacy = legacyNextQuestion(%s, %s);' % (js_value(mode), js_value(list(excludes))),
        'console.log(JSON.stringify({',
        '  actual: actual,',
        '  legacy: legacy,',
        '  gateCalls: CALLS.gate,',
        '  imageQuestionCount: QUESTIONS.filter(q => String(q.question_image_refs || "")).length',
        '}));',
    ])
    return run(script)


def question(qid, node_id='N1', image=False):
    return {
        'question_id': qid,
        'question_text': '本文 ' + qid,
        'correct_option': 'A',
        'answer_type': 'single',
        'primary_node_id': node_id,
        'verification_status': 'verified',
        'active': True,
        'difficulty': 'standard',
        'question_image_refs': 'images/%s.png' % qid if image else '',
        'option_a': 'あ', 'option_b': 'い', 'option_c': 'う', 'option_d': 'え',
    }


def node(node_id, mastery=0, required=5, answered=0, accuracy=0, next_review=''):
    return {
        'node_id': node_id,
        'topic': '論点' + node_id,
        'major_area': '深層学習',
        'mastery_pct': mastery,
        'mastery_level': '',
        'weighted_accuracy': accuracy,
        'required_unique_questions': required,
        'primary_unique_answered_count': answered,
        'latest_unique_correct_count': 0,
        'next_review_at': next_review,
        'last_answered_at': '',
    }


def log(qid, correct=True, confidence=2, answered_at='2026-08-01 10:00:00'):
    return {
        'question_id': qid,
        'counts_for_mastery': True,
        'is_correct': correct,
        'confidence': confidence,
        'answered_at': answered_at,
    }


class TestImageGateIsLazy(unittest.TestCase):
    """画像の点検を候補全部にかけないこと（ここが出題の待ち時間の主因だった）。"""

    def setUp(self):
        require_node(self)

    def test_gate_runs_far_fewer_times_than_there_are_image_questions(self):
        questions = [question('Q%03d' % i, node_id='N1', image=True) for i in range(60)]
        result = pick(questions, logs=[], nodes=[node('N1')])

        self.assertTrue(result['actual']['ok'])
        self.assertEqual(result['imageQuestionCount'], 60)
        self.assertEqual(
            len(result['gateCalls']), 1,
            '出題する1問だけを点検すれば足りるのに、'
            f'{len(result["gateCalls"])}回点検しています',
        )

    def test_gate_is_not_called_at_all_for_text_only_candidates(self):
        """文字だけの問題でも、点検は選ばれた1問ぶんしか走らないこと。"""
        questions = [question('Q%03d' % i) for i in range(40)]
        result = pick(questions, logs=[], nodes=[node('N1')])
        self.assertLessEqual(len(result['gateCalls']), 1)

    def test_gate_continues_down_the_list_when_a_candidate_is_blocked(self):
        """上位が表示できないときだけ、その次を点検する。"""
        questions = [question('Q1', image=True), question('Q2', image=True),
                     question('Q3', image=True)]
        result = pick(questions, logs=[], nodes=[node('N1')], blocked=['Q1', 'Q2'])

        self.assertEqual(result['actual']['question_id'], 'Q3')
        self.assertEqual(result['gateCalls'], ['Q1', 'Q2', 'Q3'],
                         '上から順に必要なぶんだけ点検していません')

    def test_blocked_questions_are_still_never_offered(self):
        questions = [question('Q1', image=True), question('Q2')]
        result = pick(questions, logs=[], nodes=[node('N1')], blocked=['Q1'])
        self.assertEqual(result['actual']['question_id'], 'Q2',
                         '画像を用意できない問題を出題しています')

    def test_no_question_is_offered_when_every_candidate_is_blocked(self):
        questions = [question('Q1', image=True), question('Q2', image=True)]
        result = pick(questions, logs=[], nodes=[node('N1')], blocked=['Q1', 'Q2'])
        self.assertFalse(result['actual']['ok'])
        self.assertEqual(result['actual']['reason'], 'NO_ELIGIBLE_QUESTION')
        self.assertNotIn('question_id', result['actual'])


class TestSelectionIsUnchanged(unittest.TestCase):
    """点検を後回しにしても、選ばれる問題が変わらないこと。"""

    def setUp(self):
        require_node(self)

    def _random_case(self, rng, mode):
        node_ids = ['N%d' % i for i in range(rng.randint(1, 4))]
        nodes = [
            node(
                nid,
                mastery=rng.choice([0, 20, 40, 60, 80]),
                required=rng.choice([0, 3, 5]),
                answered=rng.randint(0, 5),
                accuracy=rng.choice([0, 50, 100]),
                next_review=rng.choice(['', '2026-08-01', '2026-12-31']),
            )
            for nid in node_ids
        ]
        questions = []
        logs = []
        for i in range(rng.randint(3, 14)):
            qid = 'Q%02d' % i
            has_image = rng.random() < 0.5
            questions.append(question(qid, node_id=rng.choice(node_ids), image=has_image))
            if rng.random() < 0.5:
                logs.append(log(
                    qid,
                    correct=rng.random() < 0.5,
                    confidence=rng.choice([1, 2, 3]),
                    answered_at='2026-08-%02d 09:00:00' % rng.randint(1, 18),
                ))
        blocked = [q['question_id'] for q in questions
                   if q['question_image_refs'] and rng.random() < 0.4]
        excludes = [q['question_id'] for q in questions if rng.random() < 0.2]
        return questions, logs, nodes, blocked, excludes

    def _compare(self, mode):
        rng = random.Random(20260819)
        for case in range(25):
            questions, logs, nodes, blocked, excludes = self._random_case(rng, mode)
            result = pick(questions, logs, nodes, mode=mode,
                          excludes=excludes, blocked=blocked)
            actual = result['actual']
            legacy = result['legacy']
            self.assertEqual(
                bool(actual.get('ok')), bool(legacy.get('ok')),
                f'{mode} の{case}件目で出題可否が変わりました: {actual} / {legacy}',
            )
            if legacy.get('ok'):
                self.assertEqual(
                    actual.get('question_id'), legacy.get('question_id'),
                    f'{mode} の{case}件目で選ばれる問題が変わりました: '
                    f'{actual.get("question_id")} ≠ {legacy.get("question_id")}',
                )
            else:
                self.assertEqual(actual.get('reason'), legacy.get('reason'),
                                 f'{mode} の{case}件目で理由が変わりました')

    def test_learning_mode_picks_the_same_question(self):
        self._compare('learning')

    def test_review_mode_picks_the_same_question(self):
        self._compare('review')

    def test_unanswered_mode_picks_the_same_question(self):
        self._compare('unanswered')


class TestUnansweredCountIsUnaffected(unittest.TestCase):
    """未回答の残数は、点検の順番を変えても影響を受けないこと。"""

    def setUp(self):
        require_node(self)

    def test_remaining_counts_questions_before_the_image_gate(self):
        questions = [question('Q1', image=True), question('Q2'), question('Q3')]
        result = pick(questions, logs=[log('Q3')], nodes=[node('N1')],
                      mode='unanswered', blocked=['Q1'])
        self.assertEqual(result['actual']['question_id'], 'Q2')
        self.assertEqual(
            result['actual']['remaining_unanswered'], 2,
            '画像を用意できない未回答問題が、残数から抜け落ちています',
        )

    def test_all_blocked_keeps_the_remaining_count(self):
        questions = [question('Q1', image=True), question('Q2', image=True)]
        result = pick(questions, logs=[], nodes=[node('N1')],
                      mode='unanswered', blocked=['Q1', 'Q2'])
        self.assertEqual(result['actual']['reason'], 'NO_ELIGIBLE_UNANSWERED_QUESTION')
        self.assertEqual(result['actual']['remaining_unanswered'], 2)
        self.assertNotIn('question_id', result['actual'],
                         '回答済み問題へフォールバックしています')


class TestPrefetchStaysOutOfTheWay(unittest.TestCase):
    """次の問題の先読みが、採点や学習ログに触れないこと。"""

    @classmethod
    def setUpClass(cls):
        cls.script = CLIENT

    def test_prefetch_starts_after_the_result_is_shown(self):
        body = strip_comments(function_body(self.script, 'renderResult'))
        self.assertIn('prefetchNextQuestion()', body,
                      '結果を表示したあとに次の問題を用意していません')

    def test_prefetch_only_reads(self):
        body = strip_comments(function_body(self.script, 'prefetchNextQuestion'))
        for forbidden in ('submitAnswer(', 'getAiExplanation(',
                          'reportImageLoadFailure(', 'renderQuestion(',
                          'renderResult('):
            self.assertNotIn(forbidden, body,
                             f'先読みが {forbidden} を呼び出しています')
        self.assertIn('.getNextQuestion(', body, '次の問題を取りに行っていません')

    def test_prefetched_images_are_preloaded_before_being_kept(self):
        body = strip_comments(function_body(self.script, 'prefetchNextQuestion'))
        preload = body.find('preloadBundle(bundle)')
        self.assertNotEqual(preload, -1,
                            '先読みした画像の読み込み確認を省いています')
        self.assertRegex(
            body, r'preloadBundle\(bundle\)\s*\.then\(\(\)\s*=>\s*keep\(',
            '画像の読み込み成功を待たずに先読み結果を採用しています',
        )

    def test_failed_prefetch_is_discarded(self):
        body = strip_comments(function_body(self.script, 'prefetchNextQuestion'))
        self.assertIn('.catch(giveUp)', body, '画像の読み込み失敗を捨てていません')
        self.assertIn('withFailureHandler(giveUp)', body,
                      '通信失敗した先読みを捨てていません')

    def test_next_question_prefers_the_prefetched_one_but_can_fall_back(self):
        body = strip_comments(function_body(self.script, 'loadNextQuestion'))
        self.assertIn('takePrefetchedQuestion()', body,
                      '用意しておいた問題を使っていません')
        self.assertIn('.getNextQuestion(', body,
                      '先読みが無いときに取りに行く経路が消えています')

    def test_mode_change_drops_the_prefetched_question(self):
        for name in ('startLearning', 'showHome'):
            body = strip_comments(function_body(self.script, name))
            self.assertIn('clearPrefetch()', body,
                          f'{name} で古い先読みを捨てていません')


class TestPrefetchFreshness(unittest.TestCase):
    """条件が変わった先読みを使い回さないこと（実際に動かして確認）。"""

    def setUp(self):
        require_node(self)

    def _take(self, prefetch_state, current_mode, excludes, failed=(), genre='', strategy=''):
        script = '\n'.join([
            'const state = {',
            '  mode: %s,' % js_value(current_mode),
            # ジャンル指定と出題方法は、どちらも先読みの条件のひとつ。
            '  genreNodeId: %s,' % js_value(genre),
            '  strategy: %s,' % js_value(strategy),
            '  excludeQuestionIds: %s,' % js_value(list(excludes)),
            '  failedQuestions: new Set(%s),' % js_value(list(failed)),
            '  prefetch: %s,' % js_value(prefetch_state),
            "  prefetchKey: %s" % js_value(prefetch_state['key'] if prefetch_state else ''),
            '};',
            collect(CLIENT, ('prefetchKey', 'clearPrefetch', 'takePrefetchedQuestion')),
            'const taken = takePrefetchedQuestion();',
            'console.log(JSON.stringify({',
            '  taken: taken ? taken.question.question_id : null,',
            '  leftOver: state.prefetch ? true : false,',
            '  key: state.prefetchKey',
            '}));',
        ])
        return run(script)

    def test_prefetched_question_is_used_when_conditions_match(self):
        ready = {'key': 'learning|||Q1', 'question': {'question_id': 'Q2'}, 'bundle': {}}
        result = self._take(ready, 'learning', ['Q1'])
        self.assertEqual(result['taken'], 'Q2')
        self.assertFalse(result['leftOver'], '使ったあとも残っています')

    def test_prefetch_from_another_mode_is_discarded(self):
        ready = {'key': 'learning|||Q1', 'question': {'question_id': 'Q2'}, 'bundle': {}}
        result = self._take(ready, 'review', ['Q1'])
        self.assertIsNone(result['taken'], '別モードの先読みを使っています')
        self.assertFalse(result['leftOver'])

    def test_prefetch_with_a_stale_exclude_list_is_discarded(self):
        ready = {'key': 'learning|||Q1', 'question': {'question_id': 'Q2'}, 'bundle': {}}
        result = self._take(ready, 'learning', ['Q1', 'Q9'])
        self.assertIsNone(result['taken'], '古い除外リストの先読みを使っています')

    def test_prefetch_from_another_genre_is_discarded(self):
        """ジャンルを変えたら、前のジャンルで用意した問題は使わない。"""
        ready = {'key': 'learning|DL-RNN||Q1', 'question': {'question_id': 'Q2'}, 'bundle': {}}
        result = self._take(ready, 'learning', ['Q1'], genre='APP-NLP')
        self.assertIsNone(result['taken'], '別ジャンルの先読みを使っています')
        self.assertFalse(result['leftOver'])

    def test_prefetch_within_the_same_genre_is_used(self):
        ready = {'key': 'learning|DL-RNN||Q1', 'question': {'question_id': 'Q2'}, 'bundle': {}}
        result = self._take(ready, 'learning', ['Q1'], genre='DL-RNN')
        self.assertEqual(result['taken'], 'Q2')

    def test_prefetch_from_another_strategy_is_discarded(self):
        """出題方法を変えたら、前の方法で用意した問題は使わない。"""
        ready = {'key': 'learning|||Q1', 'question': {'question_id': 'Q2'}, 'bundle': {}}
        result = self._take(ready, 'learning', ['Q1'], strategy='mistakes')
        self.assertIsNone(result['taken'], '別の出題方法の先読みを使っています')
        self.assertFalse(result['leftOver'])

    def test_prefetch_of_a_failed_image_question_is_discarded(self):
        ready = {'key': 'learning|||Q1', 'question': {'question_id': 'Q2'}, 'bundle': {}}
        result = self._take(ready, 'learning', ['Q1'], failed=['Q2'])
        self.assertIsNone(result['taken'],
                          '画像を表示できなかった問題を先読みから出しています')

    def test_nothing_prefetched_means_the_normal_path(self):
        result = self._take(None, 'learning', ['Q1'])
        self.assertIsNone(result['taken'])


if __name__ == '__main__':
    unittest.main(verbosity=2)
