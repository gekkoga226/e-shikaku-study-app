"""出題ジャンルに出てこない論点を見つける点検（runGenreOptionDiagnostics）の守り。

守っている約束:

  - 読み取りだけ。02_マインドマップ / 03_問題台帳 / 04_学習ログ へ書き込まない。
  - 数え方は出題側とまったく同じ（isFormalQuestion_ ＋ primary_node_id）。
    点検のためだけの別の数え方を作らない。
  - 「選択肢に出ない理由」を論点ごとに1つ返す。
      progress_eligible が FALSE           → NOT_PROGRESS_ELIGIBLE
      IDの表記だけが台帳とずれている        → NODE_ID_MISMATCH
      問題はあるが正式問題が0問             → NO_FORMAL_QUESTION
      primaryで紐づく問題が1問もない        → NO_QUESTION
  - 02_マインドマップに行が無い primary_node_id は orphans として別に出す
    （その問題はどのジャンルからも選べない）。
  - 問題文・正解・選択肢を結果へ載せない。

スプレッドシートへは接続せず、実際にNodeで動かして確かめる。
"""

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from gas_source import function_body, read, strip_literals  # noqa: E402
from js_runtime import gas_bundle, js_value, require_node, run  # noqa: E402

CODE = read('Code.gs')

# シート読み取りとキャッシュだけを差し替える。点検の本体は本物をそのまま動かす。
STUBS = """
const CacheService = {
  getUserCache: () => FAKE_CACHE,
  getScriptCache: () => FAKE_CACHE
};
const FAKE_CACHE = { get: () => null, put: () => {}, remove: () => {} };

const WRITES = [];
readColumns_ = function (sheetName) {
  if (sheetName === APP_CONFIG.SHEETS.MINDMAP) return NODES;
  if (sheetName === APP_CONFIG.SHEETS.QUESTIONS) return QUESTIONS;
  return [];
};
"""


def question(qid, node_id, status='verified', active=True, correct='A', text=None):
    return {
        'question_id': qid,
        'question_text': ('本文 ' + qid) if text is None else text,
        'correct_option': correct,
        'primary_node_id': node_id,
        'verification_status': status,
        'active': active,
    }


def node(node_id, topic=None, area='深層学習の基礎', eligible=True, verified_count=0):
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
        'primary_verified_question_count': verified_count,
        'next_review_at': '',
        'last_answered_at': '',
        'progress_eligible': 'TRUE' if eligible else 'FALSE',
    }


def diagnose(questions, nodes):
    script = '\n'.join([
        'const QUESTIONS = %s;' % js_value(questions),
        'const NODES = %s;' % js_value(nodes),
        gas_bundle(),
        STUBS,
        'const out = runGenreOptionDiagnostics();',
        'console.log(JSON.stringify(out));',
    ])
    return run(script)


def reasons_of(result):
    return {row['node_id']: row['reason'] for row in result['nodes']}


class TestReasonsAreReported(unittest.TestCase):
    """なぜ選択肢に出ないのかを、論点ごとに1つだけ返す。"""

    def setUp(self):
        require_node(self)

    def test_eligible_node_with_formal_questions_is_shown(self):
        out = diagnose([question('Q1', 'DL-OPT')], [node('DL-OPT', topic='深層モデルのための最適化')])
        self.assertEqual(reasons_of(out)['DL-OPT'], 'OK')
        self.assertTrue(out['nodes'][0]['shown_in_picker'])
        self.assertEqual(out['summary']['shown_genres'], 1)
        self.assertEqual(out['summary']['hidden_formal_questions'], 0)

    def test_not_progress_eligible_node_is_reported_with_its_question_count(self):
        """問題を持っているのに progress_eligible が FALSE の論点（今回の本命）。"""
        out = diagnose(
            [question('Q%d' % i, 'DL-OPT') for i in range(18)],
            [node('DL-OPT', topic='深層モデルのための最適化', eligible=False)],
        )
        self.assertEqual(reasons_of(out)['DL-OPT'], 'NOT_PROGRESS_ELIGIBLE')
        self.assertFalse(out['nodes'][0]['shown_in_picker'])
        self.assertEqual(out['summary']['hidden_nodes'], 1)
        self.assertEqual(out['summary']['hidden_formal_questions'], 18,
                         '選択肢から落ちている問題数が数えられていません')

    def test_node_id_mismatch_is_separated_from_a_missing_question(self):
        """台帳側のIDに空白や大文字小文字のずれがあるだけの場合。"""
        out = diagnose([question('Q1', 'DL-OPT ')], [node('DL-OPT')])
        row = out['nodes'][0]
        self.assertEqual(row['reason'], 'NODE_ID_MISMATCH')
        self.assertEqual(row['similar_ledger_node_ids'], 'DL-OPT ')

    def test_questions_that_are_not_formal_are_reported_separately(self):
        out = diagnose(
            [question('Q1', 'DL-OPT', status='provisional'),
             question('Q2', 'DL-OPT', active=False),
             question('Q3', 'DL-OPT', correct='')],
            [node('DL-OPT')],
        )
        row = out['nodes'][0]
        self.assertEqual(row['reason'], 'NO_FORMAL_QUESTION')
        self.assertEqual(row['ledger_questions'], 3)
        self.assertEqual(row['ledger_formal_questions'], 0)

    def test_node_without_any_primary_question(self):
        out = diagnose([question('Q1', 'DL-CNN')], [node('DL-OPT'), node('DL-CNN')])
        self.assertEqual(reasons_of(out)['DL-OPT'], 'NO_QUESTION')

    def test_counting_matches_the_offering_rule(self):
        """数えるのは primary_node_id だけ。02_マインドマップの数式の値は別に出す。"""
        out = diagnose(
            [question('Q1', 'DL-OPT'), question('Q2', 'DL-OPT'), question('Q3', 'DL-CNN')],
            [node('DL-OPT', verified_count=99)],
        )
        row = out['nodes'][0]
        self.assertEqual(row['ledger_formal_questions'], 2)
        self.assertEqual(row['sheet_verified_questions'], 99,
                         '02_マインドマップ側の数字も並べて出していません')


class TestOrphanNodeIds(unittest.TestCase):
    """02_マインドマップに行が無い primary_node_id を見逃さない。"""

    def setUp(self):
        require_node(self)

    def test_orphan_ids_are_listed(self):
        out = diagnose(
            [question('Q1', 'DL-OPT'), question('Q2', 'DL-GONE'), question('Q3', 'DL-GONE')],
            [node('DL-OPT')],
        )
        self.assertEqual([o['primary_node_id'] for o in out['orphans']], ['DL-GONE'])
        self.assertEqual(out['orphans'][0]['formal_questions'], 2)
        self.assertEqual(out['summary']['orphan_formal_questions'], 2)

    def test_orphan_without_formal_questions_is_not_listed(self):
        out = diagnose(
            [question('Q1', 'DL-OPT'), question('Q2', 'DL-GONE', status='draft')],
            [node('DL-OPT')],
        )
        self.assertEqual(out['orphans'], [])


class TestMajorAreaRollup(unittest.TestCase):
    """大分類ごと（丸ごと1つ消えている大分類が分かること）。"""

    def setUp(self):
        require_node(self)

    def test_area_with_no_shown_topic_is_visible_in_the_rollup(self):
        out = diagnose(
            [question('Q1', 'DL-OPT'), question('Q2', 'DL-FFN'), question('Q3', 'APP-DET')],
            [
                node('DL-OPT', area='深層学習の基礎', eligible=False),
                node('DL-FFN', area='深層学習の基礎', eligible=False),
                node('APP-DET', area='深層学習の応用'),
            ],
        )
        byArea = {a['major_area']: a for a in out['areas']}
        self.assertEqual(byArea['深層学習の基礎']['shown'], 0,
                         '大分類が丸ごと消えていることが分かりません')
        self.assertEqual(byArea['深層学習の基礎']['hidden'], 2)
        self.assertEqual(byArea['深層学習の基礎']['hidden_formal'], 2)
        self.assertEqual(byArea['深層学習の応用']['shown'], 1)


class TestDiagnosticsStayReadOnly(unittest.TestCase):
    """点検が出題・採点・学習ログ・正解に触れないこと。"""

    def test_it_never_writes_to_any_sheet(self):
        for name in ('runGenreOptionDiagnostics', 'tallyQuestionsByPrimaryNode_',
                     'normalizeNodeKey_'):
            body = strip_literals(function_body(CODE, name))
            for banned in ('setValue', 'setValues', 'setFormula', 'appendRow', 'flush'):
                self.assertNotIn(banned, body,
                                 '%s がスプレッドシートへ書き込んでいます' % name)

    def test_it_does_not_read_the_learning_log(self):
        body = strip_literals(function_body(CODE, 'runGenreOptionDiagnostics'))
        self.assertNotIn('SHEETS.LOG', body, '点検が04_学習ログを読んでいます')

    def test_result_carries_no_answer_information(self):
        require_node(self)
        out = diagnose([question('Q1', 'DL-OPT')], [node('DL-OPT')])
        text = js_value(out)
        for field in ('correct_option', 'correct_answer', 'question_text', 'option_a', 'Q1'):
            self.assertNotIn(field, text, '点検の結果に %s が含まれています' % field)

    def test_it_does_not_change_how_genres_are_offered(self):
        """点検は既存の判定を呼ぶだけで、判定そのものを書き換えない。"""
        body = strip_literals(function_body(CODE, 'runGenreOptionDiagnostics'))
        self.assertNotIn('getGenreOptions =', body)
        self.assertIn('MINDMAP_PICK_COLUMNS', body,
                      '点検が独自の列読みを作っています')
        tally = strip_literals(function_body(CODE, 'tallyQuestionsByPrimaryNode_'))
        self.assertIn('GENRE_OPTION_QUESTION_COLUMNS', tally)
        self.assertIn('isFormalQuestion_', tally)


if __name__ == '__main__':
    unittest.main()
