"""正解漏洩防止の守り。

守っている約束:

  回答前のクライアントへ
    correct_option / correct_answer / answer_evidence / 解説
  など、正解を推測できる情報を送らない。
  正誤判定はサーバー側で行う。
"""

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from gas_source import function_body, html_script, read, strip_comments, strip_literals  # noqa: E402

# 回答前に返してはいけないフィールド名
FORBIDDEN_BEFORE_ANSWER = (
    'correct_option',
    'correct_answer',
    'answer_evidence',
    'explanation_plain',
    'explanation_formal',
    'explanation_calculation',
    'explanation_options',
)


def _returned_object_literals(body: str):
    """`return { ... }` の中身をすべて取り出す。"""
    literals = []
    index = 0
    while True:
        found = body.find('return {', index)
        if found == -1:
            return literals
        start = body.index('{', found)
        depth = 0
        for pos in range(start, len(body)):
            if body[pos] == '{':
                depth += 1
            elif body[pos] == '}':
                depth -= 1
                if depth == 0:
                    literals.append(body[start:pos + 1])
                    index = pos + 1
                    break
        else:
            return literals


class TestQuestionPayload(unittest.TestCase):
    """出題時にクライアントへ渡すオブジェクトに正解が含まれないこと。"""

    def test_public_question_has_no_answer_fields(self):
        body = strip_comments(function_body(read('Code.gs'), 'publicQuestion_'))
        for field in FORBIDDEN_BEFORE_ANSWER:
            self.assertNotIn(field, body,
                             f'publicQuestion_ が {field} を返しています')

    def test_next_question_only_returns_public_question(self):
        """出題APIは publicQuestion_ を通し、正解関連の項目を一切扱わない。"""
        body = strip_literals(function_body(read('Code.gs'), 'getNextQuestion'))

        self.assertIn('publicQuestion_(', body,
                      'getNextQuestion が publicQuestion_ を通していません')

        for field in FORBIDDEN_BEFORE_ANSWER:
            self.assertNotIn(field, body,
                             f'getNextQuestion が {field} を扱っています')

        # 出題側で組み立てるオブジェクトにも正解が混ざらないこと。
        for literal in _returned_object_literals(body):
            for field in FORBIDDEN_BEFORE_ANSWER:
                self.assertNotIn(field, literal,
                                 f'getNextQuestion の戻り値に {field} が含まれています')

    def test_image_bundle_has_no_answer_fields(self):
        body = strip_comments(function_body(read('ImageSupport.gs'), 'getQuestionImageBundle'))
        for field in FORBIDDEN_BEFORE_ANSWER:
            self.assertNotIn(field, body,
                             f'getQuestionImageBundle が {field} を扱っています')

    def test_initial_data_has_no_answer_fields(self):
        for func in ('getInitialData', 'getWeaknessData'):
            body = strip_comments(function_body(read('Code.gs'), func))
            for field in FORBIDDEN_BEFORE_ANSWER:
                self.assertNotIn(field, body, f'{func} が {field} を返しています')


class TestServerSideGrading(unittest.TestCase):
    """正誤判定がサーバー側で行われていること。"""

    def test_grading_happens_in_submit_answer(self):
        body = strip_literals(function_body(read('Code.gs'), 'submitAnswer'))
        self.assertRegex(body, r'isCorrect\s*=\s*userAnswer\s*===\s*correct',
                         '正誤判定がサーバー側で行われていません')
        self.assertRegex(body, r"correct\s*=\s*String\(\s*q\.correct_option",
                         '正解をシートから読み出していません')

    def test_client_never_computes_correctness(self):
        script = strip_comments(html_script('Client.html'))
        self.assertNotIn('correct_option', script,
                         'クライアントが correct_option を扱っています')
        # 正解の表示は「採点結果を描く」関数の中だけ
        for func in ('presentQuestion', 'renderQuestion', 'renderOptions',
                     'preloadBundle', 'selectAnswer', 'updateSubmitState'):
            body = strip_comments(function_body(html_script('Client.html'), func))
            self.assertNotIn('correct_answer', body,
                             f'{func} が回答前に正解へ触れています')

    def test_correct_answer_is_only_shown_in_result(self):
        body = strip_comments(function_body(html_script('Client.html'), 'renderResult'))
        self.assertIn('r.correct_answer', body,
                      '採点結果で正解が表示されていません')


if __name__ == '__main__':
    unittest.main(verbosity=2)
