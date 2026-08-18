"""画像必須ルールの守り。

守っている約束:

  「E資格の問題を解くのに画像が必要なら、必ず画像を表示してから回答させる。
    画像を取得できない問題は出題しない。」

具体的には次の順序と失敗時の扱いを静的に確認する:

  Drive ZIP取得 → 必要画像抽出 → ブラウザ側preload成功 → 問題表示 → 回答可能

  途中で失敗したら
    ・回答させない
    ・学習ログへ記録しない
    ・その問題をスキップする
"""

import json
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


class TestServerSideImageGate(unittest.TestCase):
    """サーバー側で「画像を用意できない問題」を出題候補から外していること。"""

    def test_next_question_filters_by_image_support(self):
        body = strip_literals(function_body(read('Code.gs'), 'getNextQuestion'))
        self.assertIn(
            'imageSupportAllowsQuestionObject_', body,
            'getNextQuestion が画像の可否で問題を絞り込んでいません',
        )

    def test_image_gate_requires_manifest_and_signature(self):
        body = strip_comments(
            function_body(read('ImageSupport.gs'), 'imageSupportAllowsQuestionObject_')
        )
        self.assertIn('IMAGE_ROLE_MAP_BY_QUESTION_ID[qid]', body,
                      'manifest を参照していません')
        self.assertRegex(body, r'if\s*\(\s*!\s*manifest\s*\)\s*return\s+false',
                         'manifest が無い問題を許可しています')
        self.assertRegex(body, r'manifest\.s\s*!==\s*imageRefsSignature_\(refs\)[\s\S]{0,40}return\s+false',
                         '画像リストの署名不一致を許可しています')

    def test_bundle_failure_returns_no_images_and_marks_skip(self):
        body = strip_comments(function_body(read('ImageSupport.gs'), 'getQuestionImageBundle'))
        catch_index = body.index('catch (e)')
        catch_block = body[catch_index:]

        self.assertIn('markImageQuestionSkipped_(qid)', catch_block,
                      '画像取得に失敗した問題を一時スキップ対象にしていません')
        self.assertIn("ok: false", catch_block,
                      '画像取得の失敗が ok:false として返っていません')
        self.assertNotIn('data_url', catch_block,
                         '失敗時に画像データを返そうとしています')

    def test_missing_image_ref_is_an_error_not_a_skip(self):
        body = strip_comments(function_body(read('ImageSupport.gs'), 'getQuestionImageBundle'))
        self.assertRegex(
            body, r"if\s*\(\s*!blob\s*\)\s*throw new Error\(\s*'IMAGE_REF_NOT_FOUND",
            'ZIP内に画像が無い場合に、その問題を止めていません',
        )

    def test_manifest_entries_match_audit(self):
        """ImageManifest.gs の件数が監査値(148)と一致すること。"""
        text = read('ImageManifest.gs')
        match = re.search(r'Object\.freeze\((\{.*\})\);', text, re.S)
        self.assertIsNotNone(match, 'ImageManifest.gs の定義が読めません')
        manifest = json.loads(match.group(1))
        self.assertEqual(len(manifest), 148)
        self.assertEqual(manifest['EXAM-A4-Q039']['q'], [0, 1],
                         'EXAM-A4-Q039 の context + question の2画像対応が壊れています')


class TestClientSideImageGate(unittest.TestCase):
    """ブラウザ側で「画像の読み込み成功後にだけ問題を表示」していること。"""

    @classmethod
    def setUpClass(cls):
        cls.script = strip_literals(html_script('Client.html'))

    def test_present_question_waits_for_preload(self):
        body = strip_comments(function_body(html_script('Client.html'), 'presentQuestion'))

        preload = body.find('preloadBundle(bundle)')
        render = body.find('renderQuestion(q, bundle, seq)')
        self.assertNotEqual(preload, -1, '画像のpreloadが行われていません')
        self.assertNotEqual(render, -1, '画像問題の表示処理が見つかりません')
        self.assertLess(preload, render,
                        '画像のpreloadより前に問題を表示しています')
        self.assertRegex(
            body, r'preloadBundle\(bundle\)\s*\.then\([\s\S]{0,200}renderQuestion\(q, bundle, seq\)',
            '画像の読み込み成功を待たずに問題を表示しています',
        )

        # bundle取得失敗・preload失敗・通信失敗の3経路すべてが reject に向かうこと
        self.assertIn("rejectImageQuestion(q, 'SERVER_IMAGE_BUNDLE_FAILED')", body)
        self.assertIn("rejectImageQuestion(q, 'BROWSER_IMAGE_PRELOAD_FAILED')", body)
        self.assertIn("rejectImageQuestion(q, 'SERVER_CALL_FAILED')", body)

    def test_preload_rejects_zero_size_images(self):
        body = strip_literals(function_body(html_script('Client.html'), 'preloadBundle'))
        self.assertIn('img.onerror = reject', body, '画像読み込み失敗を検知していません')
        self.assertRegex(body, r'naturalWidth\s*>\s*0',
                         '実際に描画できたかを確認していません')

    def test_reject_skips_question_without_answering(self):
        body = strip_comments(function_body(html_script('Client.html'), 'rejectImageQuestion'))
        self.assertNotIn('renderQuestion(', body, 'スキップ対象の問題を表示しています')
        self.assertNotIn('submitAnswer(', body, 'スキップ対象の問題を回答しています')
        self.assertIn('reportImageLoadFailure(', body, 'サーバーへ失敗を通知していません')
        self.assertIn('loadNextQuestion', body, '別の問題へ進んでいません')

    def test_render_error_after_dom_insert_also_blocks_answer(self):
        body = strip_comments(function_body(html_script('Client.html'), 'rejectRenderedImage'))
        self.assertRegex(body, r"\$\('submitBtn'\)\.disabled\s*=\s*true",
                         'DOM挿入後の画像失敗で回答ボタンを止めていません')
        self.assertIn('rejectImageQuestion(q, reason)', body)

        for func in ('renderQuestionImages', 'renderOptions'):
            fbody = strip_comments(function_body(html_script('Client.html'), func))
            self.assertIn("addEventListener('error'", fbody,
                          f'{func} が画像の読み込み失敗を監視していません')

    def test_submit_requires_a_rendered_question(self):
        body = strip_literals(function_body(html_script('Client.html'), 'submitCurrentAnswer'))
        self.assertRegex(body, r'if\s*\(\s*!state\.current[^\n]*\)\s*return',
                         '表示されていない問題でも回答できてしまいます')


if __name__ == '__main__':
    unittest.main(verbosity=2)
