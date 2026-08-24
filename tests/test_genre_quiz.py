"""ジャンル指定ランダム出題の守り。

守っている約束:

  - ジャンルの判定は 03_問題台帳 の primary_node_id だけ。
    secondary_node_ids にだけ一致する問題は出さない。
  - 出題できるのは verified かつ active の正式問題だけ
    （既存の isFormalQuestion_ をそのまま通す）。
  - 画像を用意できない問題は、ジャンル指定でも出さない。
  - ジャンル内はランダムだが「ランダムな順で一巡する」。
    一巡し終わる前に同じ問題を出さず、一巡したら候補を作り直す。
  - ジャンル未指定・"ALL" のときは、これまでとまったく同じ経路を通る
    （乱数を1回も引かない）。
  - 存在しない node_id はサーバー側で弾く。
  - 採点・理解度・学習ログ・AI補助解説には一切触れない。
    04_学習ログ G列 mode へ新しい値を足さない。
  - 回答前のブラウザへ正解や解説を送らない。

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
STYLES = read('Styles.html')

# シート読み取り・画像点検・キャッシュ・乱数だけを差し替える。
# 「どの問題を選ぶか」の本体は本物の Code.gs をそのまま動かす。
STUBS = """
const CALLS = { random: 0, gate: [], columns: [] };
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
  formatDate: (d, tz, fmt) => new Date(d).toISOString().slice(0, 19).replace('T', ' ')
};

readColumns_ = function (sheetName, headers) {
  CALLS.columns.push(sheetName);
  return sheetName === APP_CONFIG.SHEETS.QUESTIONS ? QUESTIONS : LOGS;
};
readMindmapLeafNodes_ = function () { return NODES; };
imageSupportAllowsQuestionObject_ = function (q) {
  CALLS.gate.push(String(q.question_id));
  return !BLOCKED.has(String(q.question_id));
};

// 乱数は数えられるように差し替える（種を固定して毎回同じ結果にする）。
let SEED = 20260824;
Math.random = function () {
  CALLS.random++;
  SEED = (SEED * 1664525 + 1013904223) % 4294967296;
  return SEED / 4294967296;
};
"""


def question(qid, node_id='DL-RNN', secondary='', image=False,
             status='verified', active=True, text=None, correct='A'):
    return {
        'question_id': qid,
        'question_text': ('本文 ' + qid) if text is None else text,
        'correct_option': correct,
        'answer_type': 'single',
        'primary_node_id': node_id,
        'secondary_node_ids': secondary,
        'verification_status': status,
        'active': active,
        'difficulty': 'standard',
        'question_image_refs': ('images/%s.png' % qid) if image else '',
        'option_a': 'あ', 'option_b': 'い', 'option_c': 'う', 'option_d': 'え',
    }


def node(node_id, topic=None, area='深層学習の応用', mastery=0,
         required=5, answered=0, verified_count=10):
    return {
        'node_id': node_id,
        'topic': topic or (node_id + ' の論点'),
        'major_area': area,
        'mastery_pct': mastery,
        'mastery_level': '',
        'weighted_accuracy': 0,
        'required_unique_questions': required,
        'primary_unique_answered_count': answered,
        'latest_unique_correct_count': 0,
        'primary_verified_question_count': verified_count,
        'next_review_at': '',
        'last_answered_at': '',
    }


def log(qid, counts=True, correct=True, confidence=3):
    return {
        'question_id': qid,
        'counts_for_mastery': counts,
        'is_correct': correct,
        'confidence': confidence,
        'answered_at': '2026-08-01 10:00:00',
    }


def draw(questions, nodes, requests, logs=(), blocked=()):
    """getNextQuestion を続けて呼び、返ってきた結果を並べて返す。

    キャッシュ（＝出題済みの記録）は呼び出しをまたいで引き継ぐ。
    実際のセッションと同じように「続けて何問も解く」状況を再現する。
    """
    script = '\n'.join([
        'const QUESTIONS = %s;' % js_value(questions),
        'const LOGS = %s;' % js_value(list(logs)),
        'const NODES = %s;' % js_value(nodes),
        'const BLOCKED_IDS = %s;' % js_value(list(blocked)),
        gas_bundle(),
        STUBS,
        'const results = %s.map(req => getNextQuestion(req));' % js_value(list(requests)),
        'console.log(JSON.stringify({ results: results, calls: CALLS, cache: CACHE_STORE }));',
    ])
    return run(script)


def genre_requests(node_id, times, session='WEB-TEST-1', mode='learning'):
    return [
        {'mode': mode, 'nodeId': node_id, 'sessionId': session, 'excludeQuestionIds': []}
        for _ in range(times)
    ]


class TestGenreFilter(unittest.TestCase):
    """指定したジャンルの問題だけが出ること（Case 2〜7）。"""

    def setUp(self):
        require_node(self)

    def test_only_primary_node_of_the_selected_genre_is_offered(self):
        """Case 2 / 3 / 4: 出題された問題の primary_node_id が指定ジャンルと一致する。"""
        questions = (
            [question('RNN%d' % i, 'DL-RNN') for i in range(4)]
            + [question('NLP%d' % i, 'APP-NLP') for i in range(4)]
            + [question('DET%d' % i, 'APP-DET') for i in range(4)]
        )
        nodes = [node('DL-RNN'), node('APP-NLP'), node('APP-DET')]

        for target in ('DL-RNN', 'APP-NLP', 'APP-DET'):
            out = draw(questions, nodes, genre_requests(target, 8, session='S-' + target))
            offered = [r['question_id'] for r in out['results']]
            self.assertEqual(len(offered), 8)
            for r in out['results']:
                self.assertTrue(r['ok'])
                self.assertEqual(
                    r['primary_node_id'], target,
                    '%s を指定したのに %s の問題が出題されました' % (target, r['primary_node_id'])
                )

    def test_secondary_only_match_is_never_offered(self):
        """Case 7: secondary_node_ids にだけ一致する問題は混ぜない。"""
        questions = [
            question('NLP1', 'APP-NLP'),
            question('TRF1', 'DL-TRANSFORMER', secondary='APP-NLP'),
            question('TRF2', 'DL-TRANSFORMER', secondary='APP-NLP;DL-RNN'),
        ]
        nodes = [node('APP-NLP'), node('DL-TRANSFORMER')]

        out = draw(questions, nodes, genre_requests('APP-NLP', 6))
        for r in out['results']:
            self.assertTrue(r['ok'])
            self.assertEqual(r['question_id'], 'NLP1',
                             'secondary_node_ids だけ一致した問題が出題されています')

    def test_unverified_and_inactive_questions_are_not_offered(self):
        """Case 5 / 6: verified 以外・active でないものは候補にしない。"""
        questions = [
            question('OK1', 'DL-RNN'),
            question('NG_PROV', 'DL-RNN', status='provisional'),
            question('NG_DRAFT', 'DL-RNN', status='draft'),
            question('NG_INACTIVE', 'DL-RNN', active=False),
            question('NG_NOTEXT', 'DL-RNN', text=''),
            question('NG_NOANSWER', 'DL-RNN', correct=''),
        ]
        out = draw(questions, [node('DL-RNN')], genre_requests('DL-RNN', 10))
        for r in out['results']:
            self.assertTrue(r['ok'])
            self.assertEqual(r['question_id'], 'OK1',
                             '正式出題の条件を満たさない問題が出題されています')

    def test_genre_with_no_formal_question_reports_it(self):
        """Case: 選択ジャンルに0件。落とさず、別ジャンルへも広げない。"""
        questions = [question('NLP1', 'APP-NLP')]
        nodes = [node('DL-RNN'), node('APP-NLP')]

        out = draw(questions, nodes, genre_requests('DL-RNN', 1))
        result = out['results'][0]
        self.assertFalse(result['ok'])
        self.assertEqual(result['reason'], 'NO_QUESTION_IN_GENRE')
        self.assertIn('出題できる問題がありません', result['message'])
        self.assertEqual(result['genre_node_id'], 'DL-RNN')

    def test_unknown_node_id_is_rejected_by_the_server(self):
        """クライアントの値をそのまま信用しない。"""
        out = draw([question('RNN1', 'DL-RNN')], [node('DL-RNN')],
                   genre_requests('DL-NOT-EXIST', 1))
        result = out['results'][0]
        self.assertFalse(result['ok'])
        self.assertEqual(result['reason'], 'UNKNOWN_GENRE')

    def test_image_unavailable_questions_are_skipped_in_genre_mode(self):
        """Case 12: 画像を用意できない問題は、ジャンル指定でも出さない。"""
        questions = [
            question('IMG_NG', 'DL-RNN', image=True),
            question('TEXT_OK', 'DL-RNN'),
        ]
        out = draw(questions, [node('DL-RNN')], genre_requests('DL-RNN', 6),
                   blocked=['IMG_NG'])
        for r in out['results']:
            self.assertTrue(r['ok'])
            self.assertEqual(r['question_id'], 'TEXT_OK',
                             '画像を用意できない問題が出題されています')

    def test_all_images_blocked_does_not_fall_back_to_other_genres(self):
        """画像が全滅しても、別ジャンルの問題へ勝手に乗り換えない。"""
        questions = [
            question('RNN_IMG', 'DL-RNN', image=True),
            question('NLP1', 'APP-NLP'),
        ]
        nodes = [node('DL-RNN'), node('APP-NLP')]
        out = draw(questions, nodes, genre_requests('DL-RNN', 1), blocked=['RNN_IMG'])
        result = out['results'][0]
        self.assertFalse(result['ok'])
        self.assertEqual(result['reason'], 'NO_ELIGIBLE_QUESTION_IN_GENRE')


class TestGenreRotation(unittest.TestCase):
    """ランダムだが一巡すること（Case 8）。"""

    def setUp(self):
        require_node(self)

    def test_no_repeat_before_the_cycle_ends(self):
        questions = [question('RNN%02d' % i, 'DL-RNN') for i in range(8)]
        out = draw(questions, [node('DL-RNN')], genre_requests('DL-RNN', 8))
        offered = [r['question_id'] for r in out['results']]

        self.assertEqual(len(set(offered)), 8,
                         '一巡し終わる前に同じ問題が重複しました: %s' % offered)
        self.assertEqual(set(offered), set('RNN%02d' % i for i in range(8)))

    def test_candidates_are_rebuilt_after_a_full_cycle(self):
        """Case: 候補をすべて出題した → 作り直して再び出題できる。"""
        questions = [question('RNN%02d' % i, 'DL-RNN') for i in range(5)]
        out = draw(questions, [node('DL-RNN')], genre_requests('DL-RNN', 12))
        offered = [r['question_id'] for r in out['results']]

        self.assertTrue(all(r['ok'] for r in out['results']),
                        '一巡後に出題が止まりました')
        # 1周目・2周目はそれぞれ重複なし。3周目（残り2問）も既出と重ならない。
        self.assertEqual(len(set(offered[0:5])), 5, offered)
        self.assertEqual(len(set(offered[5:10])), 5, offered)
        self.assertEqual(len(set(offered[10:12])), 2, offered)

    def test_order_is_not_always_the_same(self):
        """完全な固定順ではない（＝ランダムに選んでいる）。"""
        questions = [question('RNN%02d' % i, 'DL-RNN') for i in range(8)]
        out = draw(questions, [node('DL-RNN')], genre_requests('DL-RNN', 8))
        offered = [r['question_id'] for r in out['results']]
        self.assertNotEqual(offered, sorted(offered),
                            '毎回同じ順（question_id順）で出題されています')

    def test_each_session_rotates_independently(self):
        """別セッションの一巡を巻き込まない。"""
        questions = [question('RNN%02d' % i, 'DL-RNN') for i in range(4)]
        requests = (genre_requests('DL-RNN', 4, session='S-A')
                    + genre_requests('DL-RNN', 4, session='S-B'))
        out = draw(questions, [node('DL-RNN')], requests)
        first = [r['question_id'] for r in out['results'][0:4]]
        second = [r['question_id'] for r in out['results'][4:8]]
        self.assertEqual(len(set(first)), 4)
        self.assertEqual(len(set(second)), 4)

    def test_only_question_ids_are_remembered(self):
        """出題済みの記録に、正解や本文を持ち込まない。"""
        questions = [question('RNN%02d' % i, 'DL-RNN') for i in range(4)]
        out = draw(questions, [node('DL-RNN')], genre_requests('DL-RNN', 3))
        served_keys = [k for k in out['cache'] if k.startswith('genre_served_v1:')]
        self.assertTrue(served_keys, '出題済みの記録が作られていません')
        for key in served_keys:
            stored = out['cache'][key]
            self.assertNotIn('correct', stored)
            self.assertNotIn('本文', stored)


class TestExistingBehaviourIsUnchanged(unittest.TestCase):
    """ジャンル未指定なら、これまでとまったく同じ（Case 1）。"""

    def setUp(self):
        require_node(self)

    def _sample(self):
        questions = [
            question('RNN1', 'DL-RNN'), question('RNN2', 'DL-RNN'),
            question('NLP1', 'APP-NLP'), question('NLP2', 'APP-NLP'),
        ]
        nodes = [node('DL-RNN', mastery=20), node('APP-NLP', mastery=70)]
        return questions, nodes

    def test_no_genre_and_all_take_the_existing_path(self):
        questions, nodes = self._sample()
        out = draw(questions, nodes, [
            {'mode': 'learning', 'excludeQuestionIds': []},
            {'mode': 'learning', 'nodeId': 'ALL', 'excludeQuestionIds': []},
            {'mode': 'learning', 'nodeId': '', 'excludeQuestionIds': []},
            {'mode': 'learning', 'nodeId': '  ', 'excludeQuestionIds': []},
        ])
        ids = [r['question_id'] for r in out['results']]
        self.assertEqual(len(set(ids)), 1,
                         'ALL / 未指定で出題結果が変わっています: %s' % ids)
        # 理解度の低い論点が先、その中では未回答→question_id順（既存の並び）。
        self.assertEqual(ids[0], 'RNN1')

    def test_existing_path_never_draws_a_random_number(self):
        """既存経路に乱数を持ち込んでいないこと（同じ入力なら同じ結果のまま）。"""
        questions, nodes = self._sample()
        out = draw(questions, nodes, [{'mode': 'learning', 'excludeQuestionIds': []}])
        self.assertEqual(out['calls']['random'], 0,
                         '通常出題で乱数が使われています')

    def test_unanswered_mode_is_untouched(self):
        questions, nodes = self._sample()
        out = draw(questions, nodes,
                   [{'mode': 'unanswered', 'excludeQuestionIds': []}],
                   logs=[log('RNN1')])
        result = out['results'][0]
        self.assertTrue(result['ok'])
        self.assertEqual(result['mode'], 'unanswered')
        self.assertNotEqual(result['question_id'], 'RNN1')
        self.assertEqual(out['calls']['random'], 0)

    def test_genre_and_unanswered_can_be_combined(self):
        """将来の組み合わせ（ジャンル×未回答優先）が、いまの実装で既に成立する。"""
        questions = [
            question('RNN1', 'DL-RNN'), question('RNN2', 'DL-RNN'),
            question('NLP1', 'APP-NLP'),
        ]
        nodes = [node('DL-RNN'), node('APP-NLP')]
        out = draw(questions, nodes,
                   genre_requests('DL-RNN', 1, mode='unanswered'),
                   logs=[log('RNN1')])
        result = out['results'][0]
        self.assertTrue(result['ok'])
        self.assertEqual(result['question_id'], 'RNN2')
        self.assertEqual(result['mode'], 'unanswered')
        self.assertEqual(result['genre_node_id'], 'DL-RNN')


class TestGenreOptions(unittest.TestCase):
    """ジャンル一覧は02_マインドマップから作る（ハードコードしない）。"""

    def setUp(self):
        require_node(self)

    def _options(self, questions, nodes):
        script = '\n'.join([
            'const QUESTIONS = %s;' % js_value(questions),
            'const LOGS = [];',
            'const NODES = %s;' % js_value(nodes),
            'const BLOCKED_IDS = [];',
            gas_bundle(),
            STUBS,
            'console.log(JSON.stringify(getGenreOptions()));',
        ])
        return run(script)

    def test_topic_is_shown_and_node_id_is_the_internal_value(self):
        questions = [question('RNN1', 'DL-RNN'), question('NLP1', 'APP-NLP')]
        nodes = [
            node('DL-RNN', topic='リカレントニューラルネットワーク'),
            node('APP-NLP', topic='自然言語処理'),
        ]
        data = self._options(questions, nodes)
        topics = [g['topic'] for g in data['genres']]
        ids = [g['node_id'] for g in data['genres']]
        self.assertIn('リカレントニューラルネットワーク', topics)
        self.assertIn('自然言語処理', topics)
        self.assertIn('DL-RNN', ids)

    def test_genres_without_offerable_questions_are_hidden(self):
        questions = [question('RNN1', 'DL-RNN'),
                     question('NG', 'APP-DET', status='provisional')]
        nodes = [node('DL-RNN'), node('APP-NLP'), node('APP-DET')]
        data = self._options(questions, nodes)
        ids = [g['node_id'] for g in data['genres']]
        self.assertEqual(ids, ['DL-RNN'],
                         '出題できる問題が無いジャンルを選ばせています')

    def test_counts_match_the_selection_rule(self):
        questions = ([question('RNN%d' % i, 'DL-RNN') for i in range(3)]
                     + [question('RNN_NG', 'DL-RNN', active=False)])
        data = self._options(questions, [node('DL-RNN')])
        self.assertEqual(data['genres'][0]['question_count'], 3)
        self.assertEqual(data['total_question_count'], 3)

    def test_options_carry_no_answer_information(self):
        """Case 13: 選択肢にも正解や本文を載せない。"""
        questions = [question('RNN1', 'DL-RNN')]
        data = self._options(questions, [node('DL-RNN')])
        text = js_value(data)
        for field in ('correct_option', 'correct_answer', 'question_text', 'option_a'):
            self.assertNotIn(field, text, 'ジャンル一覧が %s を返しています' % field)


class TestAnswerSecrecyInGenreMode(unittest.TestCase):
    """Case 13: 回答前に正解を送らない。"""

    def setUp(self):
        require_node(self)

    def test_genre_payload_has_no_answer_fields(self):
        questions = [question('RNN1', 'DL-RNN'), question('RNN2', 'DL-RNN')]
        out = draw(questions, [node('DL-RNN')], genre_requests('DL-RNN', 2))
        for result in out['results']:
            for field in ('correct_option', 'correct_answer', 'answer_evidence',
                          'explanation_plain', 'explanation_formal',
                          'explanation_calculation', 'explanation_options'):
                self.assertNotIn(field, result,
                                 'ジャンル出題の応答に %s が含まれています' % field)

    def test_selection_functions_never_touch_the_answer(self):
        for name in ('nextGenreQuestion_', 'nextGenreRandomQuestion_',
                     'getGenreOptions', 'countFormalQuestionsByNode_'):
            body = strip_comments(function_body(CODE, name))
            for field in ('correct_option', 'correct_answer', 'explanation_'):
                self.assertNotIn(field, body, '%s が %s を扱っています' % (name, field))

    def test_client_never_receives_the_candidate_list(self):
        """候補を丸ごとブラウザへ送ってJS側で選ぶ、という作りにしない。"""
        for name in ('loadNextQuestion', 'prefetchNextQuestion', 'startGenreQuiz'):
            body = strip_comments(function_body(CLIENT, name))
            for banned in ('Math.random', 'shuffle', 'primary_node_id ==='):
                self.assertNotIn(banned, body,
                                 '%s がクライアント側で問題を選んでいます: %s' % (name, banned))
        # 候補一覧を返すサーバー関数をそもそも持たない。
        self.assertNotIn('getQuestionCandidates', strip_comments(CLIENT))
        self.assertNotIn('function getQuestionCandidates', CODE)


class TestLoggingAndMasteryAreUntouched(unittest.TestCase):
    """Case 9〜11: 採点・学習ログ・理解度の作りを変えない。"""

    def test_modes_list_is_unchanged(self):
        modes = re.search(r"MODES:\s*\[([^\]]*)\]", CODE).group(1)
        self.assertEqual(
            [m.strip().strip("'\"") for m in modes.split(',')],
            ['learning', 'review', 'unanswered'],
            'ジャンル指定のために新しい mode を足しています'
        )
        for banned in ('genre_random', 'random_by_topic', 'category_mode'):
            self.assertNotIn(banned, CODE, '新しい mode 値 %s が入っています' % banned)

    def test_submit_answer_does_not_know_about_genres(self):
        body = strip_comments(function_body(CODE, 'submitAnswer'))
        for banned in ('payload.nodeId', 'nextGenreQuestion_', 'getGenreOptions',
                       'genre_node_id', 'normalizeGenreNodeId_'):
            self.assertNotIn(banned, body,
                             'submitAnswer がジャンル指定に依存しています: %s' % banned)
        # 記録する mode は、これまでどおり normalizeMode_ が決めた値。
        self.assertIn('normalizeMode_(payload.mode)', body)

    def test_client_submit_payload_is_unchanged(self):
        body = strip_comments(function_body(CLIENT, 'submitCurrentAnswer'))
        self.assertNotIn('nodeId', body,
                         '回答の記録にジャンル指定を混ぜています')
        self.assertIn('mode: state.mode', body)

    def test_genre_selection_does_not_write_anything(self):
        for name in ('nextGenreQuestion_', 'nextGenreRandomQuestion_',
                     'getGenreOptions', 'countFormalQuestionsByNode_',
                     'normalizeGenreNodeId_', 'shuffleForGenre_'):
            body = strip_literals(function_body(CODE, name))
            for banned in ('setValue', 'setValues', 'setFormula', 'appendRow', 'flush'):
                self.assertNotIn(banned, body,
                                 '%s がスプレッドシートへ書き込んでいます' % name)


class TestAiIsNotInvolved(unittest.TestCase):
    """Case 14: ジャンル判定・出題にAIを使わない。"""

    def test_genre_selection_never_calls_ai(self):
        for name in ('getNextQuestion', 'nextGenreQuestion_', 'nextGenreRandomQuestion_',
                     'getGenreOptions'):
            body = strip_comments(function_body(CODE, name))
            for banned in ('Gemini', 'gemini', 'callGemini', 'AI_CONFIG', 'getAiExplanation'):
                self.assertNotIn(banned, body,
                                 '%s がAIに依存しています: %s' % (name, banned))

    def test_ai_functions_do_not_drive_selection(self):
        body = strip_comments(function_body(CODE, 'getAiExplanation'))
        for banned in ('nextGenreQuestion_', 'getGenreOptions', 'nodeId'):
            self.assertNotIn(banned, body, 'AI解説が出題選択へ関与しています')


class TestPerformance(unittest.TestCase):
    """Case 16相当: 1問出すたびに余計なシート読み込みを増やさない。"""

    def setUp(self):
        require_node(self)

    def test_genre_mode_reads_the_same_sheets_as_before(self):
        questions = [question('RNN%d' % i, 'DL-RNN') for i in range(5)]
        nodes = [node('DL-RNN')]

        plain = draw(questions, nodes, [{'mode': 'learning', 'excludeQuestionIds': []}])
        genre = draw(questions, nodes, genre_requests('DL-RNN', 1))
        self.assertEqual(genre['calls']['columns'], plain['calls']['columns'],
                         'ジャンル指定で読み取り回数が増えています')

    def test_image_check_is_not_run_on_every_candidate(self):
        """画像点検は上から順に、必要なぶんだけ（既存の出題経路と同じ考え方）。"""
        questions = [question('RNN%02d' % i, 'DL-RNN') for i in range(30)]
        out = draw(questions, [node('DL-RNN')], genre_requests('DL-RNN', 1))
        self.assertEqual(len(out['calls']['gate']), 1,
                         '候補全部に画像点検をかけています')


class TestMobileFirstUi(unittest.TestCase):
    """Case 15: 狭い画面で見切れない・横スクロールにしない。"""

    def test_picker_is_on_the_home_screen(self):
        self.assertIn('id="genreSelect"', INDEX)
        self.assertIn('id="genreQuizBtn"', INDEX)
        self.assertIn('出題ジャンル', INDEX)
        # ホーム（hero-card）の中にあること。
        hero = INDEX[INDEX.index('class="hero-card"'):INDEX.index('id="dashboardGrid"')]
        self.assertIn('id="genreSelect"', hero)

    def test_select_fills_the_width_and_is_tappable(self):
        block = STYLES[STYLES.index('.genre-select {'):]
        block = block[:block.index('}')]
        self.assertIn('width: 100%', block)
        self.assertIn('min-height: 48px', block)

    def test_no_fixed_pixel_width_is_used(self):
        for selector in ('.genre-picker {', '.genre-select {'):
            block = STYLES[STYLES.index(selector):]
            block = block[:block.index('}')]
            self.assertNotRegex(block, r'\bwidth:\s*\d+px',
                                '固定幅は狭い画面で見切れる: ' + selector)

    def test_all_genres_option_exists(self):
        self.assertIn('すべてのジャンル', INDEX)
        self.assertIn("value=\"ALL\"", INDEX)


class TestClientWiring(unittest.TestCase):
    """画面とサーバーのつなぎ方。"""

    def test_selected_genre_is_sent_to_the_server(self):
        body = strip_comments(function_body(CLIENT, 'loadNextQuestion'))
        self.assertIn('nodeId: state.genreNodeId', body)
        self.assertIn('sessionId: state.sessionId', body)

    def test_prefetch_uses_the_same_genre(self):
        key = strip_comments(function_body(CLIENT, 'prefetchKey'))
        self.assertIn('state.genreNodeId', key,
                      '先読みがジャンルを跨いで使い回されます')
        body = strip_comments(function_body(CLIENT, 'prefetchNextQuestion'))
        self.assertIn('nodeId: state.genreNodeId', body)

    def test_all_genres_sends_no_filter(self):
        body = strip_comments(function_body(CLIENT, 'selectedGenreNodeId'))
        self.assertIn("GENRE_ALL_VALUE ? '' :", body.replace('\n', ' ').replace('  ', ' '))

    def test_empty_genre_is_reported_to_the_user(self):
        body = strip_comments(function_body(CLIENT, 'loadNextQuestion'))
        self.assertIn('GENRE_EMPTY_REASONS', body)
        self.assertIn('NO_QUESTION_IN_GENRE', strip_comments(CLIENT))

    def test_genre_list_is_not_hardcoded_in_the_client(self):
        script = strip_comments(CLIENT)
        self.assertIn('.getGenreOptions()', script)
        for banned in ('DL-RNN', 'APP-NLP', 'APP-DET', 'DL-CNN'):
            self.assertNotIn(banned, script,
                             'ジャンルを画面へ直書きしています: %s' % banned)


if __name__ == '__main__':
    unittest.main(verbosity=2)
