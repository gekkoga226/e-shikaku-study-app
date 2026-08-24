"""間違えた問題・自信なし優先の守り。

守っている約束:

  - 復習したい問題 =
      03_問題台帳で verification_status=verified / active=TRUE の正式問題のうち、
      04_学習ログで counts_for_mastery=TRUE の行に
        is_correct=FALSE が1つでもある、または confidence=1 が1つでもある もの。
    （provisional / system_test / write失敗 / tracking開始前 は
      U列の数式が既に FALSE にしているので、それをそのまま信じる）
  - 一度も間違えず、自信なしでも答えていない問題は出さない。
  - 並べる順は 最新が誤答 → 最新が自信なし → 最新は正解、同点なら古い順。
  - 対象が0件なら完了を返し、対象外の問題へフォールバックしない。
  - ジャンル指定と組み合わせられる。
  - 既存の通常出題・未回答優先・採点・学習ログ・理解度を壊さない。
    04_学習ログ G列 mode へ新しい値を足さない。
  - 回答前のブラウザへ「この問題は前回間違えた」を送らない。

判定ロジックは静的検査だけでなく、実際にNodeで動かして確かめる。
スプレッドシートへは接続しない。
"""

import re
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from gas_source import (  # noqa: E402
    function_body,
    html_script,
    read,
    strip_comments,
    strip_literals,
)
from js_runtime import gas_bundle, js_value, require_node, run  # noqa: E402

CODE = read('Code.gs')
CLIENT = html_script('Client.html')
INDEX = read('Index.html')

STUBS = """
const CALLS = { gate: [], columns: [] };
const BLOCKED = new Set(BLOCKED_IDS);
const CACHE_STORE = {};

const CacheService = {
  getUserCache: () => FAKE_CACHE,
  getScriptCache: () => FAKE_CACHE
};
const FAKE_CACHE = {
  get: key => (Object.prototype.hasOwnProperty.call(CACHE_STORE, key) ? CACHE_STORE[key] : null),
  put: (key, value) => { CACHE_STORE[key] = String(value); },
  remove: key => { delete CACHE_STORE[key]; }
};
const Utilities = {
  formatDate: d => new Date(d).toISOString().slice(0, 19).replace('T', ' ')
};

readColumns_ = function (sheetName) {
  CALLS.columns.push(sheetName);
  return sheetName === APP_CONFIG.SHEETS.QUESTIONS ? QUESTIONS : LOGS;
};
readMindmapLeafNodes_ = function () { return NODES; };
imageSupportAllowsQuestionObject_ = function (q) {
  CALLS.gate.push(String(q.question_id));
  return !BLOCKED.has(String(q.question_id));
};
"""


def question(qid, node_id='DL-RNN', image=False, status='verified', active=True):
    return {
        'question_id': qid,
        'question_text': '本文 ' + qid,
        'correct_option': 'A',
        'answer_type': 'single',
        'primary_node_id': node_id,
        'verification_status': status,
        'active': active,
        'difficulty': 'standard',
        'question_image_refs': ('images/%s.png' % qid) if image else '',
        'option_a': 'あ', 'option_b': 'い', 'option_c': 'う', 'option_d': 'え',
    }


def log(qid, correct=True, confidence=3, counts=True, at='2026-08-01 10:00:00'):
    return {
        'question_id': qid,
        'counts_for_mastery': counts,
        'is_correct': correct,
        'confidence': confidence,
        'answered_at': at,
    }


def node(node_id, topic=None, area='深層学習の基礎'):
    return {
        'node_id': node_id,
        'topic': topic or (node_id + ' の論点'),
        'major_area': area,
        'mastery_pct': 0,
        'mastery_level': '',
        'weighted_accuracy': 0,
        'required_unique_questions': 5,
        'primary_unique_answered_count': 0,
        'latest_unique_correct_count': 0,
        'primary_verified_question_count': 10,
        'next_review_at': '',
        'last_answered_at': '',
    }


def ask(questions, logs, nodes=None, requests=None, blocked=()):
    nodes = nodes if nodes is not None else [node('DL-RNN')]
    requests = requests if requests is not None else [
        {'mode': 'learning', 'strategy': 'mistakes', 'excludeQuestionIds': []}
    ]
    script = '\n'.join([
        'const QUESTIONS = %s;' % js_value(questions),
        'const LOGS = %s;' % js_value(logs),
        'const NODES = %s;' % js_value(nodes),
        'const BLOCKED_IDS = %s;' % js_value(list(blocked)),
        gas_bundle(),
        STUBS,
        'const results = %s.map(req => getNextQuestion(req));' % js_value(requests),
        'console.log(JSON.stringify({ results: results, calls: CALLS }));',
    ])
    return run(script)


def summary(questions, logs):
    script = '\n'.join([
        'const QUESTIONS = %s;' % js_value(questions),
        'const LOGS = %s;' % js_value(logs),
        'const NODES = [];',
        'const BLOCKED_IDS = [];',
        gas_bundle(),
        STUBS,
        'console.log(JSON.stringify(getMistakeSummary()));',
    ])
    return run(script)


class TestCandidateSet(unittest.TestCase):
    """どの問題が復習対象になるか。"""

    def setUp(self):
        require_node(self)

    def test_wrong_answers_are_offered(self):
        out = ask([question('Q1'), question('Q2')],
                  [log('Q1', correct=True), log('Q2', correct=False)])
        self.assertEqual(out['results'][0]['question_id'], 'Q2')

    def test_low_confidence_answers_are_offered(self):
        """正解でも「1 自信なし」なら復習対象。"""
        out = ask([question('Q1'), question('Q2')],
                  [log('Q1', correct=True, confidence=3),
                   log('Q2', correct=True, confidence=1)])
        self.assertEqual(out['results'][0]['question_id'], 'Q2')

    def test_confidence_two_is_not_a_target(self):
        """「2 少し自信」は対象にしない（自信なしは1だけ）。"""
        out = ask([question('Q1')], [log('Q1', correct=True, confidence=2)])
        self.assertFalse(out['results'][0]['ok'])
        self.assertEqual(out['results'][0]['reason'], 'NO_MISTAKE_QUESTION')

    def test_past_mistake_still_counts_after_a_correct_retry(self):
        """一度間違えた問題は、次に正解しても対象に残る（定着の確認）。"""
        out = ask([question('Q1')],
                  [log('Q1', correct=False, at='2026-08-01 10:00:00'),
                   log('Q1', correct=True, confidence=3, at='2026-08-05 10:00:00')])
        self.assertTrue(out['results'][0]['ok'])
        self.assertEqual(out['results'][0]['question_id'], 'Q1')

    def test_never_wrong_never_unsure_is_not_offered(self):
        out = ask([question('Q1'), question('Q2')],
                  [log('Q1'), log('Q2')])
        result = out['results'][0]
        self.assertFalse(result['ok'])
        self.assertEqual(result['reason'], 'NO_MISTAKE_QUESTION')
        self.assertIn('自信なし', result['message'])
        self.assertEqual(result['remaining_mistakes'], 0)

    def test_unanswered_questions_are_not_offered(self):
        """一度も解いていない問題は、このモードの対象ではない（未回答モードの担当）。"""
        out = ask([question('Q1'), question('Q2')], [log('Q2', correct=False)])
        self.assertEqual(out['results'][0]['question_id'], 'Q2')

    def test_only_counts_for_mastery_rows_are_believed(self):
        """U列がFALSEの行（system_test / write失敗など）では対象にしない。"""
        out = ask([question('Q1')], [log('Q1', correct=False, counts=False)])
        self.assertFalse(out['results'][0]['ok'])
        self.assertEqual(out['results'][0]['reason'], 'NO_MISTAKE_QUESTION')

    def test_unverified_and_inactive_questions_are_not_offered(self):
        out = ask(
            [question('NG1', status='provisional'), question('NG2', active=False),
             question('OK1')],
            [log('NG1', correct=False), log('NG2', correct=False), log('OK1', correct=False)],
        )
        self.assertEqual(out['results'][0]['question_id'], 'OK1')

    def test_image_unavailable_questions_are_skipped(self):
        out = ask([question('IMG', image=True), question('TEXT')],
                  [log('IMG', correct=False, at='2026-08-01 10:00:00'),
                   log('TEXT', correct=False, at='2026-08-02 10:00:00')],
                  blocked=['IMG'])
        self.assertEqual(out['results'][0]['question_id'], 'TEXT')

    def test_no_fallback_when_all_targets_are_blocked(self):
        """表示できないだけのときは、対象外の問題へ戻さない。"""
        out = ask([question('IMG', image=True), question('CLEAN')],
                  [log('IMG', correct=False), log('CLEAN', correct=True, confidence=3)],
                  blocked=['IMG'])
        result = out['results'][0]
        self.assertFalse(result['ok'])
        self.assertEqual(result['reason'], 'NO_ELIGIBLE_MISTAKE_QUESTION')
        self.assertEqual(result['remaining_mistakes'], 1)


class TestPriorityOrder(unittest.TestCase):
    """並べる順（弱いものから先に）。"""

    def setUp(self):
        require_node(self)

    def test_latest_wrong_comes_first(self):
        questions = [question('LOW'), question('WRONG'), question('FIXED')]
        logs = [
            log('LOW', correct=True, confidence=1, at='2026-08-01 10:00:00'),
            log('WRONG', correct=False, confidence=3, at='2026-08-02 10:00:00'),
            log('FIXED', correct=False, at='2026-08-01 09:00:00'),
            log('FIXED', correct=True, confidence=3, at='2026-08-03 10:00:00'),
        ]
        out = ask(questions, logs, requests=[
            {'mode': 'learning', 'strategy': 'mistakes', 'excludeQuestionIds': []},
            {'mode': 'learning', 'strategy': 'mistakes', 'excludeQuestionIds': ['WRONG']},
            {'mode': 'learning', 'strategy': 'mistakes',
             'excludeQuestionIds': ['WRONG', 'LOW']},
        ])
        offered = [r['question_id'] for r in out['results']]
        self.assertEqual(offered, ['WRONG', 'LOW', 'FIXED'],
                         '最新が誤答 → 最新が自信なし → 最新は正解 の順になっていません')

    def test_older_answers_come_first_within_the_same_rank(self):
        questions = [question('OLD'), question('NEW')]
        logs = [
            log('NEW', correct=False, at='2026-08-20 10:00:00'),
            log('OLD', correct=False, at='2026-08-01 10:00:00'),
        ]
        out = ask(questions, logs)
        self.assertEqual(out['results'][0]['question_id'], 'OLD',
                         '同じ優先度なら、解いてから時間が経ったものを先に出す')

    def test_order_is_stable(self):
        """同点でも毎回同じ順番になる（乱数を使わない）。"""
        questions = [question('Q%d' % i) for i in range(5)]
        logs = [log('Q%d' % i, correct=False) for i in range(5)]
        first = ask(questions, logs)['results'][0]['question_id']
        second = ask(questions, logs)['results'][0]['question_id']
        self.assertEqual(first, second)


class TestGenreCombination(unittest.TestCase):
    """ジャンル × 間違えた問題・自信なし優先。"""

    def setUp(self):
        require_node(self)

    def test_only_the_selected_genre_is_offered(self):
        questions = [question('RNN1', 'DL-RNN'), question('NLP1', 'APP-NLP')]
        logs = [log('RNN1', correct=False, at='2026-08-05 10:00:00'),
                log('NLP1', correct=False, at='2026-08-01 10:00:00')]
        nodes = [node('DL-RNN'), node('APP-NLP')]

        out = ask(questions, logs, nodes, requests=[
            {'mode': 'learning', 'strategy': 'mistakes', 'nodeId': 'DL-RNN',
             'excludeQuestionIds': []}
        ])
        result = out['results'][0]
        self.assertTrue(result['ok'])
        self.assertEqual(result['question_id'], 'RNN1')
        self.assertEqual(result['primary_node_id'], 'DL-RNN')
        self.assertEqual(result['genre_node_id'], 'DL-RNN')

    def test_empty_genre_target_is_reported(self):
        questions = [question('RNN1', 'DL-RNN'), question('NLP1', 'APP-NLP')]
        logs = [log('RNN1', correct=True, confidence=3), log('NLP1', correct=False)]
        nodes = [node('DL-RNN'), node('APP-NLP')]

        out = ask(questions, logs, nodes, requests=[
            {'mode': 'learning', 'strategy': 'mistakes', 'nodeId': 'DL-RNN',
             'excludeQuestionIds': []}
        ])
        result = out['results'][0]
        self.assertFalse(result['ok'])
        self.assertEqual(result['reason'], 'NO_MISTAKE_QUESTION')
        self.assertEqual(result['genre_node_id'], 'DL-RNN')
        self.assertIn('このジャンル', result['message'])


class TestSummary(unittest.TestCase):
    """ホーム画面に出す残数。"""

    def setUp(self):
        require_node(self)

    def test_counts_match_the_selection_rule(self):
        questions = [question('W'), question('L'), question('F'), question('CLEAN'),
                     question('NG', status='provisional')]
        logs = [
            log('W', correct=False),
            log('L', correct=True, confidence=1),
            log('F', correct=False), log('F', correct=True, confidence=3),
            log('CLEAN', correct=True, confidence=3),
            log('NG', correct=False),
        ]
        data = summary(questions, logs)
        self.assertEqual(data['total'], 3)
        self.assertEqual(data['latest_wrong'], 1)
        self.assertEqual(data['low_confidence'], 1)
        self.assertEqual(data['solved_but_marked'], 1)

    def test_zero_when_nothing_needs_review(self):
        data = summary([question('Q1')], [log('Q1', correct=True, confidence=3)])
        self.assertEqual(data['total'], 0)

    def test_summary_reads_only_the_columns_it_needs(self):
        """解説など長い列まで読むと、ホーム画面が返ってこなくなる。"""
        columns = strip_comments(
            re.search(r'const MISTAKE_LOG_COLUMNS = Object\.freeze\(\[[\s\S]*?\]\);', CODE).group(0)
        )
        for banned in ('notes', 'explanation', 'answer_evidence'):
            self.assertNotIn(banned, columns)
        body = strip_comments(function_body(CODE, 'readMistakeSummary_'))
        self.assertIn('UNANSWERED_QUESTION_COLUMNS', body)
        self.assertIn('MISTAKE_LOG_COLUMNS', body)

    def test_count_is_refreshed_after_answering(self):
        body = strip_comments(function_body(CODE, 'submitAnswer'))
        self.assertIn('clearMistakeSummaryCache_()', body,
                      '回答したあとも古い残数が残ります')

    def test_remaining_count_has_its_own_server_call(self):
        body = strip_comments(function_body(CLIENT, 'loadHomeData'))
        self.assertIn('loadMistakeSummary()', body)
        self.assertIn('.getMistakeSummary()',
                      strip_comments(function_body(CLIENT, 'loadMistakeSummary')))


class TestExistingBehaviourIsUnchanged(unittest.TestCase):
    """既存の出題・記録の作りを変えない。"""

    def setUp(self):
        require_node(self)

    def test_without_strategy_the_existing_path_is_used(self):
        questions = [question('Q1'), question('Q2')]
        logs = [log('Q1', correct=False)]
        out = ask(questions, logs, requests=[
            {'mode': 'learning', 'excludeQuestionIds': []},
            {'mode': 'learning', 'strategy': '', 'excludeQuestionIds': []},
            {'mode': 'learning', 'strategy': 'unknown-value', 'excludeQuestionIds': []},
        ])
        ids = [r['question_id'] for r in out['results']]
        self.assertEqual(len(set(ids)), 1,
                         '出題方法の指定がないのに結果が変わっています: %s' % ids)

    def test_unanswered_mode_wins_over_the_strategy(self):
        """未回答モードは独立した経路のまま（既存の約束を壊さない）。"""
        questions = [question('ANSWERED'), question('FRESH')]
        logs = [log('ANSWERED', correct=False)]
        out = ask(questions, logs, requests=[
            {'mode': 'unanswered', 'strategy': 'mistakes', 'excludeQuestionIds': []}
        ])
        result = out['results'][0]
        self.assertEqual(result['question_id'], 'FRESH')
        self.assertEqual(result['mode'], 'unanswered')

    def test_modes_list_is_unchanged(self):
        modes = re.search(r"MODES:\s*\[([^\]]*)\]", CODE).group(1)
        self.assertEqual(
            [m.strip().strip("'\"") for m in modes.split(',')],
            ['learning', 'review', 'unanswered'],
            '出題方法のために新しい mode を足しています'
        )
        for banned in ('mistake_mode', 'wrong_only', 'low_confidence_mode'):
            self.assertNotIn(banned, CODE)

    def test_logged_mode_stays_learning(self):
        """このモードで解いた回答も、これまでと同じ正式回答として記録される。"""
        body = strip_comments(function_body(CLIENT, 'selectedStrategy'))
        self.assertIn("mode: 'learning', strategy: 'mistakes'", body)
        submit = strip_comments(function_body(CLIENT, 'submitCurrentAnswer'))
        self.assertNotIn('strategy', submit,
                         '回答の記録に出題方法を混ぜています')

    def test_selection_never_writes_anything(self):
        for name in ('nextMistakeQuestion_', 'mistakeStateByQuestion_',
                     'readMistakeSummary_', 'normalizeStrategy_',
                     'compareMistakeCandidates_'):
            body = strip_literals(function_body(CODE, name))
            for banned in ('setValue', 'setValues', 'setFormula', 'appendRow', 'flush'):
                self.assertNotIn(banned, body,
                                 '%s がスプレッドシートへ書き込んでいます' % name)

    def test_selection_never_calls_ai(self):
        for name in ('nextMistakeQuestion_', 'readMistakeSummary_'):
            body = strip_comments(function_body(CODE, name))
            for banned in ('Gemini', 'gemini', 'AI_CONFIG', 'getAiExplanation'):
                self.assertNotIn(banned, body, '%s がAIに依存しています' % name)

    def test_no_extra_sheet_reads(self):
        questions = [question('Q%d' % i) for i in range(5)]
        logs = [log('Q%d' % i, correct=False) for i in range(5)]
        plain = ask(questions, logs, requests=[{'mode': 'learning', 'excludeQuestionIds': []}])
        mistakes = ask(questions, logs)
        self.assertEqual(mistakes['calls']['columns'], plain['calls']['columns'],
                         '出題方法の指定で読み取り回数が増えています')

    def test_image_check_is_not_run_on_every_candidate(self):
        questions = [question('Q%02d' % i) for i in range(30)]
        logs = [log('Q%02d' % i, correct=False) for i in range(30)]
        out = ask(questions, logs)
        self.assertEqual(len(out['calls']['gate']), 1,
                         '候補全部に画像点検をかけています')


class TestAnswerSecrecy(unittest.TestCase):
    """回答前に、答えを推測できる情報を送らない。"""

    def setUp(self):
        require_node(self)

    def test_payload_has_no_answer_fields(self):
        out = ask([question('Q1')], [log('Q1', correct=False)])
        for field in ('correct_option', 'correct_answer', 'answer_evidence',
                      'explanation_plain', 'explanation_formal'):
            self.assertNotIn(field, out['results'][0])

    def test_payload_does_not_reveal_the_previous_attempt(self):
        """1問ごとの「前回は誤答／自信なし」は返さない（残り件数だけ）。"""
        out = ask([question('Q1')], [log('Q1', correct=False)])
        result = out['results'][0]
        self.assertIn('remaining_mistakes', result)
        for field in ('mistake_reason', 'latest_wrong', 'ever_wrong',
                      'latest_low_confidence', 'is_correct', 'last_user_answer'):
            self.assertNotIn(field, result,
                             '前回の回答内容が回答前に送られています: %s' % field)

    def test_client_does_not_decide_the_targets(self):
        for name in ('loadNextQuestion', 'startGenreQuiz', 'selectedStrategy'):
            body = strip_comments(function_body(CLIENT, name))
            for banned in ('is_correct', 'confidence <', 'counts_for_mastery'):
                self.assertNotIn(banned, body,
                                 '%s がクライアント側で対象を決めています' % name)


class TestUi(unittest.TestCase):
    """ホーム画面の操作。"""

    def test_button_exists_on_home(self):
        self.assertIn('id="mistakeBtn"', INDEX)
        self.assertIn('間違えた問題・自信なし', INDEX)
        hero = INDEX[INDEX.index('class="hero-card"'):INDEX.index('id="dashboardGrid"')]
        self.assertIn('id="mistakeBtn"', hero)

    def test_strategy_can_be_combined_with_a_genre(self):
        self.assertIn('id="strategySelect"', INDEX)
        self.assertIn('value="mistakes"', INDEX)
        self.assertIn('出題方法', INDEX)

    def test_strategy_is_sent_to_the_server(self):
        body = strip_comments(function_body(CLIENT, 'loadNextQuestion'))
        self.assertIn('strategy: state.strategy', body)
        prefetch = strip_comments(function_body(CLIENT, 'prefetchNextQuestion'))
        self.assertIn('strategy: state.strategy', prefetch)
        key = strip_comments(function_body(CLIENT, 'prefetchKey'))
        self.assertIn('state.strategy', key,
                      '先読みが出題方法を跨いで使い回されます')

    def test_empty_result_is_reported_to_the_user(self):
        body = strip_comments(function_body(CLIENT, 'loadNextQuestion'))
        self.assertIn('MISTAKE_EMPTY_REASONS', body)
        self.assertIn('NO_MISTAKE_QUESTION', strip_comments(CLIENT))


if __name__ == '__main__':
    unittest.main(verbosity=2)
