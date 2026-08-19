"""未回答問題優先モードの守り。

守っている約束:

  - 未回答 =
      03_問題台帳で verification_status=verified / active=TRUE の正式問題のうち、
      04_学習ログで counts_for_mastery=TRUE の行を question_id 単位で1件も持たないもの。
    （provisional / inactive / system_test / write失敗 / tracking開始前 は
      U列の数式が既に FALSE にしているので、それをそのまま信じる）
  - 未回答が残っている間、回答済み問題を1問も混ぜない。
  - 未回答が0件になったら完了を返し、回答済み問題へフォールバックしない。
  - coverageが不足している論点の未回答問題を先に出す。
  - 既存の「弱点から1問解く」「期限到来の復習をする」を壊さない。
  - 未回答モードの回答も、正式条件を満たせば理解度へ反映される
    （U列が除外するのは mode="system_test" だけ）。

判定ロジックは静的検査だけでなく、実際にNodeで動かして確かめる。
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
from js_runtime import collect, js_value, require_node, run  # noqa: E402

CODE = read('Code.gs')

# 出題ロジックの実行に必要な関数（Google APIへ触れないものだけ）。
SELECTION_FUNCTIONS = (
    'nextUnansweredQuestion_',
    'compareUnansweredCandidates_',
    'coverageShortage_',
    'answeredQuestionIdSet_',
    'normalizeMode_',
    'isFormalQuestion_',
    'availableOptionLetters_',
    'publicQuestion_',
    'truthy_',
    'numberOrZero_',
)

APP_CONFIG_LITERAL = re.search(
    r'const APP_CONFIG = Object\.freeze\(\{[\s\S]*?\n\}\);', CODE
).group(0)


def question(qid, node='N1', **overrides):
    row = {
        'question_id': qid,
        'question_text': qid + ' の本文',
        'correct_option': 'A',
        'option_a': 'あ', 'option_b': 'い', 'option_c': 'う', 'option_d': 'え',
        'primary_node_id': node,
        'verification_status': 'verified',
        'active': True,
    }
    row.update(overrides)
    return row


def log(qid, counts=True, **overrides):
    row = {
        'question_id': qid,
        'counts_for_mastery': counts,
        'write_status': 'success',
        'verification_status': 'verified',
        'mode': 'learning',
    }
    row.update(overrides)
    return row


def node(node_id, required=5, answered=0, mastery=0):
    return {
        'node_id': node_id,
        'topic': node_id + ' の論点',
        'major_area': 'テスト',
        'mastery_pct': mastery,
        'mastery_level': '',
        'weighted_accuracy': 0,
        'required_unique_questions': required,
        'primary_unique_answered_count': answered,
        'latest_unique_correct_count': 0,
        'next_review_at': '',
        'last_answered_at': '',
    }


def select(questions, logs, nodes, excludes=(), blocked=()):
    """nextUnansweredQuestion_ を実際に動かして結果を返す。"""
    script = '\n'.join([
        APP_CONFIG_LITERAL,
        'const IMAGE_ROLE_MAP_BY_QUESTION_ID = {};',
        collect(CODE, SELECTION_FUNCTIONS),
        'const BLOCKED = new Set(%s);' % js_value(list(blocked)),
        'function imageSupportAllowsQuestionObject_(q) {',
        '  return !BLOCKED.has(String(q.question_id));',
        '}',
        'function readMindmapLeafNodes_() { return %s; }' % js_value(nodes),
        'const formal = %s.filter(isFormalQuestion_);' % js_value(questions),
        'const out = nextUnansweredQuestion_(formal, %s, new Set(%s));'
        % (js_value(logs), js_value(list(excludes))),
        'console.log(JSON.stringify(out));',
    ])
    return run(script)


class TestUnansweredSelection(unittest.TestCase):
    """実際に動かして、選ばれる問題を確かめる。"""

    def setUp(self):
        require_node(self)

    def test_answered_questions_are_never_offered(self):
        result = select(
            questions=[question('Q1'), question('Q2'), question('Q3')],
            logs=[log('Q1'), log('Q2')],
            nodes=[node('N1')],
        )
        self.assertTrue(result['ok'])
        self.assertEqual(result['question_id'], 'Q3',
                         '回答済みの問題が未回答モードで出題されています')
        self.assertEqual(result['remaining_unanswered'], 1)
        self.assertEqual(result['mode'], 'unanswered')

    def test_only_counts_for_mastery_marks_a_question_answered(self):
        """provisional / system_test / write失敗などは U列が FALSE なので未回答のまま。"""
        result = select(
            questions=[question('Q1')],
            logs=[
                log('Q1', counts=False, verification_status='provisional'),
                log('Q1', counts=False, mode='system_test'),
                log('Q1', counts=False, write_status='failed'),
                log('Q1', counts='FALSE'),
            ],
            nodes=[node('N1')],
        )
        self.assertTrue(result['ok'])
        self.assertEqual(result['question_id'], 'Q1',
                         '正式に数えられない履歴を「回答済み」と誤判定しています')

    def test_true_as_text_is_treated_as_answered(self):
        """シートから文字列 "TRUE" で返ってきても回答済みとして扱う。"""
        result = select(
            questions=[question('Q1')],
            logs=[log('Q1', counts='TRUE')],
            nodes=[node('N1')],
        )
        self.assertFalse(result['ok'])
        self.assertEqual(result['reason'], 'ALL_QUESTIONS_ANSWERED')

    def test_all_answered_returns_completion_without_fallback(self):
        result = select(
            questions=[question('Q1'), question('Q2')],
            logs=[log('Q1'), log('Q2')],
            nodes=[node('N1')],
        )
        self.assertFalse(result['ok'])
        self.assertEqual(result['reason'], 'ALL_QUESTIONS_ANSWERED')
        self.assertEqual(result['remaining_unanswered'], 0)
        self.assertNotIn('question_id', result,
                         '未回答が0件なのに問題を返しています')
        self.assertIn('未回答', result['message'])

    def test_provisional_and_inactive_questions_are_not_offered(self):
        result = select(
            questions=[
                question('Q1', verification_status='provisional'),
                question('Q2', active=False),
                question('Q3', verification_status='draft'),
                question('Q4'),
            ],
            logs=[],
            nodes=[node('N1')],
        )
        self.assertTrue(result['ok'])
        self.assertEqual(result['question_id'], 'Q4')
        self.assertEqual(result['remaining_unanswered'], 1,
                         'provisional / inactive を未回答の残数に数えています')

    def test_coverage_shortage_nodes_come_first(self):
        """required_unique_questions に届いていない論点を先に出す。"""
        result = select(
            questions=[question('Q1', node='FULL'), question('Q2', node='SHORT')],
            logs=[],
            nodes=[
                node('FULL', required=5, answered=5, mastery=10),
                node('SHORT', required=5, answered=1, mastery=90),
            ],
        )
        self.assertEqual(result['question_id'], 'Q2',
                         'coverageが足りている論点を先に出しています')

    def test_larger_shortage_comes_first(self):
        result = select(
            questions=[question('Q1', node='A1'), question('Q2', node='A2')],
            logs=[],
            nodes=[
                node('A1', required=5, answered=4),
                node('A2', required=10, answered=1),
            ],
        )
        self.assertEqual(result['question_id'], 'Q2',
                         '不足量が大きい論点を先に出していません')

    def test_low_mastery_breaks_the_tie(self):
        result = select(
            questions=[question('Q1', node='A1'), question('Q2', node='A2')],
            logs=[],
            nodes=[
                node('A1', required=5, answered=5, mastery=80),
                node('A2', required=5, answered=5, mastery=20),
            ],
        )
        self.assertEqual(result['question_id'], 'Q2',
                         'coverage同点のとき理解度が低い論点を優先していません')

    def test_excluded_questions_are_skipped_but_still_counted(self):
        result = select(
            questions=[question('Q1'), question('Q2')],
            logs=[],
            nodes=[node('N1')],
            excludes=['Q1'],
        )
        self.assertEqual(result['question_id'], 'Q2')
        self.assertEqual(result['remaining_unanswered'], 2,
                         '直前に出しただけの問題を「回答済み」に数えています')

    def test_image_unavailable_questions_are_not_offered(self):
        result = select(
            questions=[question('Q1'), question('Q2')],
            logs=[],
            nodes=[node('N1')],
            blocked=['Q1'],
        )
        self.assertEqual(result['question_id'], 'Q2',
                         '画像を用意できない問題を出題しています')

    def test_no_fallback_to_answered_when_all_candidates_are_blocked(self):
        result = select(
            questions=[question('Q1'), question('Q2')],
            logs=[log('Q2')],
            nodes=[node('N1')],
            blocked=['Q1'],
        )
        self.assertFalse(result['ok'])
        self.assertEqual(result['reason'], 'NO_ELIGIBLE_UNANSWERED_QUESTION')
        self.assertNotIn('question_id', result,
                         '回答済み問題へフォールバックしています')

    def test_payload_never_contains_the_answer(self):
        result = select(
            questions=[question('Q1')],
            logs=[],
            nodes=[node('N1')],
        )
        for field in ('correct_option', 'correct_answer', 'answer_evidence',
                      'explanation_plain', 'explanation_formal'):
            self.assertNotIn(field, result,
                             f'未回答モードの出題が {field} を返しています')
        self.assertEqual(sorted(result['options'].keys()), ['A', 'B', 'C', 'D'])


class TestModeNormalisation(unittest.TestCase):
    """mode の値の扱い。"""

    def setUp(self):
        require_node(self)

    def test_known_modes_pass_through_and_unknown_falls_back(self):
        script = '\n'.join([
            APP_CONFIG_LITERAL,
            collect(CODE, ('normalizeMode_',)),
            "const inputs = ['learning', 'review', 'unanswered', 'system_test', '', null];",
            'console.log(JSON.stringify(inputs.map(normalizeMode_)));',
        ])
        self.assertEqual(
            run(script),
            ['learning', 'review', 'unanswered', 'learning', 'learning', 'learning'],
            'mode の正規化が想定と違います',
        )

    def test_submit_answer_uses_the_shared_normaliser(self):
        body = strip_comments(function_body(CODE, 'submitAnswer'))
        self.assertRegex(body, r'const mode = normalizeMode_\(payload\.mode\)',
                         '回答記録が未回答モードを受け取れません')

    def test_unanswered_answers_still_count_for_mastery(self):
        """U列の数式が除外するのは system_test だけであること。"""
        body = function_body(CODE, 'submitAnswer')
        formula = re.search(r"uCell\.setFormula\(([\s\S]*?)\);", body).group(1)
        self.assertIn('<>"system_test"', formula,
                      'U列の除外条件が変わっています')
        self.assertNotIn('unanswered', formula,
                         '未回答モードの回答を理解度から除外しています')


class TestExistingModesArePreserved(unittest.TestCase):
    """既存モードを壊していないこと。"""

    def test_learning_and_review_paths_remain(self):
        body = strip_comments(function_body(CODE, 'getNextQuestion'))
        self.assertIn("mode === 'review'", body, '復習モードの分岐が消えています')
        self.assertIn('latestFormalLogByQuestion_', body,
                      '既存の優先ロジック（Phase8）が消えています')
        self.assertIn('rankedNodes', body, '既存の論点ランキングが消えています')

    def test_unanswered_mode_is_a_separate_branch(self):
        body = strip_comments(function_body(CODE, 'getNextQuestion'))
        self.assertRegex(
            body,
            r"if\s*\(\s*mode === 'unanswered'\s*\)\s*\{[\s\S]{0,160}?return nextUnansweredQuestion_\(",
            '未回答モードが独立した経路になっていません',
        )
        branch = body.index("mode === 'unanswered'")
        ranked = body.index('rankedNodes')
        self.assertLess(branch, ranked,
                        '未回答モードが既存の優先ロジックへ入り込んでいます')

    def test_image_gate_is_applied_in_both_paths(self):
        for name in ('getNextQuestion', 'nextUnansweredQuestion_'):
            body = strip_literals(function_body(CODE, name))
            self.assertIn('imageSupportAllowsQuestionObject_', body,
                          f'{name} が画像の可否で絞り込んでいません')

    def test_home_still_offers_the_two_original_buttons(self):
        index = read('Index.html')
        self.assertIn('弱点から1問解く', index)
        self.assertIn('期限到来の復習をする', index)
        self.assertIn('未回答問題を優先して解く', index)


class TestUnansweredSummary(unittest.TestCase):
    """ホーム画面の残数。"""

    def test_summary_uses_the_same_definition(self):
        body = strip_comments(function_body(CODE, 'readUnansweredSummary_'))
        self.assertIn('isFormalQuestion_', body,
                      'verified / active の判定を通していません')
        self.assertIn('answeredQuestionIdSet_', body,
                      '出題側と同じ「回答済み」の定義を使っていません')

    def test_answered_set_is_built_only_from_counts_for_mastery(self):
        body = strip_comments(function_body(CODE, 'answeredQuestionIdSet_'))
        self.assertRegex(body, r'if\s*\(\s*!truthy_\(row\.counts_for_mastery\)\s*\)\s*return',
                         'counts_for_mastery 以外を根拠にしています')
        for field in ('write_status', 'verification_status', 'answered_at'):
            self.assertNotIn(field, body,
                             f'U列の数式と別に {field} を判定し直しています')

    def test_remaining_count_has_its_own_server_call(self):
        """残数の集計は重いので、ホーム画面の表示をブロックしない別呼び出しにする。"""
        body = strip_comments(function_body(CODE, 'getUnansweredSummary'))
        self.assertIn('readUnansweredSummary_()', body,
                      '未回答の残数を返す入口がありません')

        initial = strip_comments(function_body(CODE, 'getInitialData'))
        self.assertNotIn('readUnansweredSummary_', initial,
                         'ホーム画面の初回表示が、重い残数集計の完了を待っています')

    def test_summary_reads_only_the_columns_it_needs(self):
        """解説など長い列まで読むと、ホーム画面が返ってこなくなる。"""
        body = strip_comments(function_body(CODE, 'readUnansweredSummary_'))
        self.assertIn('readColumns_(', body, '列を絞らずシート全体を読んでいます')
        self.assertNotIn('readObjects_(', body, '全列読み取りが残っています')

        for name in ('UNANSWERED_QUESTION_COLUMNS', 'UNANSWERED_LOG_COLUMNS'):
            columns = re.search(
                r'const ' + name + r' = Object\.freeze\(\[([\s\S]*?)\]\)', CODE
            )
            self.assertIsNotNone(columns, f'{name} が見つかりません')
            for forbidden in ('explanation', 'answer_evidence'):
                self.assertNotIn(forbidden, columns.group(1),
                                 f'{name} が重い列 {forbidden} を読もうとしています')

    def test_count_is_refreshed_after_answering(self):
        """回答したぶん残数が減るよう、書き込み後に使い回しをやめる。"""
        body = strip_comments(function_body(CODE, 'submitAnswer'))
        self.assertIn('clearUnansweredSummaryCache_()', body,
                      '回答後も古い残数が表示され続けます')


class TestClientUnansweredMode(unittest.TestCase):
    """ブラウザ側の配線。"""

    @classmethod
    def setUpClass(cls):
        cls.script = html_script('Client.html')

    def test_button_starts_the_unanswered_mode(self):
        stripped = strip_comments(self.script)
        self.assertIn(
            "$('unansweredBtn').addEventListener('click', () => startLearning('unanswered'))",
            stripped,
            '未回答モードのボタンが処理へつながっていません',
        )

    def test_mode_is_kept_as_unanswered(self):
        body = strip_comments(function_body(self.script, 'startLearning'))
        self.assertNotRegex(
            body, r"mode === 'review' \? 'review' : 'learning'",
            '未回答モードが learning へ丸められています',
        )
        self.assertIn('MODES.indexOf(mode)', body,
                      '未回答モードを含む判定になっていません')

    def test_remaining_count_is_shown_on_the_button(self):
        body = strip_comments(function_body(self.script, 'renderUnansweredButton'))
        self.assertIn('未回答問題を優先して解く', body, 'ボタンの表示名がありません')
        self.assertIn('summary.remaining', body, '残数を使っていません')

    def test_home_does_not_wait_for_the_count(self):
        """残数の集計が遅くても、理解度と弱点は先に表示されること。"""
        body = strip_comments(function_body(self.script, 'loadHomeData'))
        self.assertIn('.getInitialData()', body, 'ホームの読み込みがありません')
        self.assertIn('loadUnansweredSummary()', body,
                      '残数を別呼び出しにしていません')

        summary = strip_comments(function_body(self.script, 'loadUnansweredSummary'))
        self.assertIn('.getUnansweredSummary()', summary,
                      '残数専用のサーバー呼び出しを使っていません')
        for forbidden in ('renderWeaknesses(', 'renderDashboard('):
            self.assertNotIn(forbidden, summary,
                             f'残数の取得が {forbidden} を巻き込んでいます')

    def test_home_failure_is_visible(self):
        """ホームで失敗したとき、問題画面用の表示では気づけない。"""
        body = strip_comments(function_body(self.script, 'loadHomeData'))
        self.assertIn('showHomeError', body, 'ホーム専用の失敗表示がありません')

        handler = strip_comments(function_body(self.script, 'showHomeError'))
        self.assertIn("$('ruleLabel')", handler,
                      '失敗しても「読み込み中」の表示のままになります')

    def test_completion_does_not_fall_back_to_answered_questions(self):
        body = strip_comments(function_body(self.script, 'loadNextQuestion'))
        marker = body.index("ALL_QUESTIONS_ANSWERED")
        after = body[marker:marker + 400]
        self.assertIn('showQuestionNotice(', after,
                      '未回答が0件のときの案内表示がありません')
        self.assertIn('return;', after,
                      '未回答が0件でも処理を続けています')
        self.assertNotIn('startLearning(', after,
                         '未回答が0件のとき、別モードへ勝手に切り替えています')
        self.assertNotIn('presentQuestion(', after,
                         '未回答が0件なのに問題を表示しようとしています')


if __name__ == '__main__':
    unittest.main(verbosity=2)
