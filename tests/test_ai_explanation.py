"""AI補助解説（Gemini）の守り。

守っている約束:

  - AIは「回答後の補助解説」だけを担当する。
    出題・採点・理解度・学習ログは、これまでどおり既存の確定ロジックだけが決める。
  - AI解説は 04_学習ログ に write_status=success の回答が実在するときだけ動く。
    attempt が無い／成功記録が無い場合は、正解も解説も1文字も返さない。
  - Gemini APIキーはスクリプトプロパティからのみ読み、
    クライアント（ブラウザ）へは渡さない。
  - AI処理は 02_マインドマップ / 04_学習ログ へ書き込まない。
  - 画像問題では既存の画像取得を再利用し、inline_data として渡す。
  - AIはボタンを押したときだけ呼ぶ（毎問自動では呼ばない）。
"""

import json
import re
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from gas_source import (  # noqa: E402
    SRC,
    function_body,
    html_script,
    read,
    strip_comments,
    strip_literals,
)
from js_runtime import collect, js_value, require_node, run  # noqa: E402

CODE = read('Code.gs')

# AI補助解説を構成するサーバー側の関数。
AI_SERVER_FUNCTIONS = (
    'getAiExplanation',
    'readAnsweredAttempt_',
    'buildAiPromptParts_',
    'appendAiImageParts_',
    'aiInlineDataFromDataUrl_',
    'callGeminiGenerateContent_',
    'aiCacheGet_',
    'aiCachePut_',
    'aiPick_',
    'aiFailure_',
    'aiRepairMathDelimiters_',
)

# シートを書き換えるApps Script API。AI側では1つも使わない。
SHEET_WRITE_CALLS = (
    'setValue(',
    'setValues(',
    'setFormula(',
    'setFormulas(',
    'appendRow(',
    'insertRowsAfter(',
    'insertRowBefore(',
    'clearContent(',
    'deleteRow(',
    'setNumberFormat(',
)


class TestSubmitAnswerExposesAttemptId(unittest.TestCase):
    """AI解説を紐づけるための attempt_id が回答結果に含まれること。"""

    def test_submit_answer_returns_attempt_id(self):
        body = strip_comments(function_body(read('Code.gs'), 'submitAnswer'))
        self.assertRegex(body, r'attempt_id\s*:\s*attemptId',
                         'submitAnswer が attempt_id を返していません')

    def test_client_remembers_attempt_id_only_from_result(self):
        body = strip_comments(function_body(html_script('Client.html'), 'renderResult'))
        self.assertRegex(body, r'state\.attemptId\s*=\s*String\(\s*r\.attempt_id',
                         '採点結果から attempt_id を受け取っていません')

        # 出題時には attempt_id を持たない（＝AIを呼べない）。
        loader = strip_comments(function_body(html_script('Client.html'), 'loadNextQuestion'))
        self.assertRegex(loader, r"state\.attemptId\s*=\s*''",
                         '次の問題へ進むときに attempt_id を消していません')


class TestAiRequiresAnsweredAttempt(unittest.TestCase):
    """回答済みでなければAIを呼ばないこと（回答前の正解漏洩の禁止）。"""

    def test_missing_attempt_id_is_rejected(self):
        body = strip_comments(function_body(read('Code.gs'), 'getAiExplanation'))
        self.assertRegex(
            body,
            r"if\s*\(\s*!attemptId\s*\)[\s\S]{0,200}?return\s+aiFailure_\(\s*'MISSING_ATTEMPT_ID'",
            'attemptId が無いときにAIを呼ばずに拒否していません',
        )

    def test_attempt_is_verified_before_calling_gemini(self):
        body = strip_literals(function_body(read('Code.gs'), 'getAiExplanation'))
        check = body.find('readAnsweredAttempt_(')
        call = body.find('callGeminiGenerateContent_(')
        key = body.find('getProperty(')
        self.assertNotEqual(check, -1, '回答記録の確認を行っていません')
        self.assertNotEqual(call, -1, 'Gemini呼び出しが見つかりません')
        self.assertLess(check, call, '回答記録を確認する前にAIを呼んでいます')
        self.assertLess(check, key, '回答記録を確認する前にAPIキーを読み出しています')
        self.assertRegex(
            body,
            r'if\s*\(\s*!\s*found\.ok\s*\)\s*return\s+found',
            '回答記録が無い場合に処理を止めていません',
        )

    def test_attempt_lookup_requires_success_write_status(self):
        body = strip_comments(function_body(read('Code.gs'), 'readAnsweredAttempt_'))

        self.assertRegex(
            body,
            r"if\s*\(\s*!attempt\s*\)\s*\{[\s\S]{0,200}?'ATTEMPT_NOT_FOUND'",
            '学習ログに存在しない attempt を拒否していません',
        )
        self.assertRegex(
            body,
            r"attempt\.write_status[\s\S]{0,120}?!==\s*'success'[\s\S]{0,200}?'ATTEMPT_NOT_SUCCESS'",
            "write_status が success でない記録を拒否していません",
        )
        self.assertRegex(
            body,
            r"\/\^\[A-H\]\$\/\.test\(userAnswer\)[\s\S]{0,200}?'ATTEMPT_NOT_ANSWERED'",
            '選択肢が記録されていない attempt を拒否していません',
        )
        self.assertIn('APP_CONFIG.SHEETS.LOG', body,
                      '04_学習ログを確認していません')

    def test_failure_payload_never_contains_answer_or_explanation(self):
        """拒否時の返り値に、正解や解説が混ざらないこと。"""
        body = strip_comments(function_body(read('Code.gs'), 'aiFailure_'))
        for field in ('correct_option', 'correct_answer', 'explanation_plain',
                      'explanation_formal', 'explanation_calculation', 'explanation_options',
                      'text:'):
            self.assertNotIn(field, body, f'失敗時の返り値に {field} が含まれています')


class TestApiKeyHandling(unittest.TestCase):
    """APIキーの扱い。"""

    def test_key_is_read_only_from_script_properties(self):
        code = strip_comments(read('Code.gs'))
        self.assertIn("API_KEY_PROPERTY: 'GEMINI_API_KEY'", code,
                      'GEMINI_API_KEY をスクリプトプロパティ名として定義していません')
        self.assertRegex(
            code,
            r'PropertiesService\.getScriptProperties\(\)',
            'スクリプトプロパティからキーを読み出していません',
        )
        self.assertRegex(
            strip_comments(function_body(read('Code.gs'), 'getAiExplanation')),
            r'properties\.getProperty\(\s*AI_CONFIG\.API_KEY_PROPERTY\s*\)',
            'APIキーをスクリプトプロパティから読み出していません',
        )

    def test_no_api_key_literal_in_sources(self):
        """キーそのものをソースへ埋め込んでいないこと。"""
        for path in sorted(SRC.iterdir()):
            if not path.is_file():
                continue
            text = path.read_text(encoding='utf-8')
            self.assertNotRegex(text, r'AIza[0-9A-Za-z_\-]{20,}',
                                f'{path.name} にAPIキーらしき文字列があります')

    def test_client_never_sees_the_key(self):
        """ブラウザへ送るファイルがキーにも設定ストアにも触れないこと。"""
        for name in ('Client.html', 'Index.html', 'Styles.html'):
            text = read(name)
            self.assertNotIn('GEMINI_API_KEY', text,
                             f'{name} が GEMINI_API_KEY に触れています')
            self.assertNotIn('PropertiesService', text,
                             f'{name} がスクリプトプロパティへ触れています')
            self.assertNotIn('x-goog-api-key', text,
                             f'{name} がAPIキーヘッダーを扱っています')
            self.assertNotIn('generativelanguage.googleapis.com', text,
                             f'{name} が直接Gemini APIを呼ぼうとしています')

    def test_ai_response_to_client_has_no_key(self):
        body = strip_literals(function_body(read('Code.gs'), 'getAiExplanation'))
        for literal in re.findall(r'return\s*\{[\s\S]*?\};', body):
            self.assertNotIn('apiKey', literal,
                             'クライアントへの返り値にAPIキーが含まれています')

    def test_key_is_not_placed_in_url_or_logs(self):
        body = strip_comments(function_body(read('Code.gs'), 'callGeminiGenerateContent_'))
        self.assertNotRegex(body, r'url\s*\+.*apiKey',
                            'APIキーをURLへ付けています')
        self.assertNotRegex(body, r'console\.[a-z]+\([^)]*apiKey',
                            'APIキーをログへ出力しています')


class TestMathRendering(unittest.TestCase):
    """数式（LaTeX）の表示。"""

    def test_instruction_asks_for_latex_delimiters(self):
        code = read('Code.gs')
        match = re.search(r'const AI_SYSTEM_INSTRUCTION = \[([\s\S]*?)\]\.join', code)
        instruction = match.group(1)
        self.assertIn('LaTeX', instruction, 'LaTeXで書く指示がありません')
        self.assertIn(r'\\(', instruction, r'\( を区切りに使う指示がありません')
        self.assertIn(r'\\[', instruction, r'\[ を区切りに使う指示がありません')
        self.assertIn('記号が何を指すのか', instruction,
                      '記号の意味を日本語で説明させる指示がありません')

    def test_math_is_protected_from_markdown_stripping(self):
        """\\(a^*+b^*\\) のような式が、記法の除去で壊されないこと。"""
        body = function_body(read('Code.gs'), 'aiPlainText_')
        self.assertRegex(
            body,
            r'math\.push\(',
            '数式を退避してから記法を除去していません',
        )
        self.assertRegex(
            body,
            r'return cleaned\.replace\(',
            '退避した数式を戻していません',
        )

    def test_client_renders_math_after_setting_text(self):
        client = read('Client.html')
        self.assertIn('renderMathIn(', client, '数式を組版する呼び出しがありません')
        self.assertRegex(
            client,
            r'box\.textContent = res\.text[^\n]*\n\s*renderMathIn\(box\)',
            'AI解説を入れたあとに数式を組版していません',
        )
        self.assertRegex(
            client,
            r"typeof renderMathInElement !== 'function'",
            'KaTeXを読み込めなかった場合の分岐がありません',
        )

    def test_math_delimiters_avoid_dollar_sign(self):
        """$ は金額と紛らわしいので数式の区切りに使わないこと。"""
        client = read('Client.html')
        match = re.search(r'delimiters:\s*\[([\s\S]*?)\]', client)
        self.assertIsNotNone(match, 'KaTeXの区切り設定が見つかりません')
        self.assertNotIn('$', match.group(1), '$ を数式の区切りに使っています')

    def test_katex_is_pinned_and_optional(self):
        index = read('Index.html')
        self.assertRegex(index, r'katex@\d+\.\d+\.\d+',
                         'KaTeXのバージョンを固定していません')
        self.assertIn('defer', index, 'KaTeXの読み込みで表示を止めています')


class TestGeminiCall(unittest.TestCase):
    """呼び出し方（サーバー側 / ヘッダー / モデル）。"""

    def test_called_server_side_with_header(self):
        body = strip_comments(function_body(read('Code.gs'), 'callGeminiGenerateContent_'))
        self.assertIn('UrlFetchApp.fetch(', body,
                      'Apps Scriptサーバー側から呼び出していません')
        self.assertRegex(body, r"headers\s*:\s*\{\s*'x-goog-api-key'\s*:\s*apiKey\s*\}",
                         'x-goog-api-key ヘッダーでキーを送っていません')
        self.assertIn('muteHttpExceptions: true', body,
                      'HTTPエラーを握って安全に扱う設定になっていません')

    def test_model_is_configurable_with_default(self):
        code = strip_comments(read('Code.gs'))
        self.assertIn("MODEL_PROPERTY: 'GEMINI_MODEL'", code,
                      'モデル名のスクリプトプロパティ名が違います')
        self.assertIn("DEFAULT_MODEL: 'gemini-3.5-flash'", code,
                      '既定モデルが gemini-3.5-flash ではありません')

        body = strip_comments(function_body(read('Code.gs'), 'getAiExplanation'))
        self.assertRegex(
            body,
            r'getProperty\(\s*AI_CONFIG\.MODEL_PROPERTY\s*\)[\s\S]{0,120}?AI_CONFIG\.DEFAULT_MODEL',
            'GEMINI_MODEL 未設定時に既定モデルへ落ちていません',
        )

    def test_endpoint_is_the_official_gemini_api(self):
        code = strip_comments(read('Code.gs'))
        self.assertIn(
            "ENDPOINT_BASE: 'https://generativelanguage.googleapis.com/v1beta/models/'",
            code,
            'Gemini APIのエンドポイントが想定と違います',
        )

    def test_system_instruction_rules(self):
        code = read('Code.gs')
        match = re.search(r'const AI_SYSTEM_INSTRUCTION = \[([\s\S]*?)\]\.join', code)
        self.assertIsNotNone(match, 'AI_SYSTEM_INSTRUCTION が見つかりません')
        instruction = match.group(1)

        for heading in ('【まず一言】', '【なぜそうなる？】', '【他の選択肢との違い】', '【覚え方】'):
            self.assertIn(heading, instruction, f'構成 {heading} の指示がありません')

        self.assertIn('絶対基準', instruction, '登録正解を絶対基準とする指示がありません')
        self.assertIn('変更・否定・訂正してはいけません', instruction,
                      '勝手に正解を変えない指示がありません')
        self.assertIn('非エンジニア', instruction, '非エンジニア向けの指示がありません')
        self.assertIn('日常のことば', instruction, '日常語から説明する指示がありません')
        self.assertIn('正式な用語', instruction, '正式用語へつなぐ指示がありません')
        self.assertIn('採点をしません', instruction, '採点しない旨の指示がありません')

    def test_system_instruction_is_sent_to_gemini(self):
        body = strip_literals(function_body(read('Code.gs'), 'callGeminiGenerateContent_'))
        self.assertIn('AI_SYSTEM_INSTRUCTION', body,
                      'system instruction を送っていません')
        self.assertIn('system_instruction', function_body(read('Code.gs'), 'callGeminiGenerateContent_'),
                      'system_instruction フィールドを使っていません')


class TestAiDoesNotTouchLearningState(unittest.TestCase):
    """AI処理が理解度・学習ログを書き換えないこと。"""

    def test_no_sheet_writes_in_ai_functions(self):
        code = read('Code.gs')
        for name in AI_SERVER_FUNCTIONS:
            body = strip_comments(function_body(code, name))
            for call in SHEET_WRITE_CALLS:
                self.assertNotIn(call, body,
                                 f'{name} がシートを書き換えています: {call}')

    def test_ai_does_not_touch_mindmap_sheet(self):
        code = read('Code.gs')
        for name in AI_SERVER_FUNCTIONS:
            body = strip_comments(function_body(code, name))
            self.assertNotIn('SHEETS.MINDMAP', body,
                             f'{name} が02_マインドマップへ触れています')
            self.assertNotIn('mastery_pct', body,
                             f'{name} が理解度を扱っています')

    def test_ai_does_not_lock_or_flush(self):
        """採点処理と同じ書き込み経路を使っていないこと。"""
        code = read('Code.gs')
        for name in AI_SERVER_FUNCTIONS:
            body = strip_comments(function_body(code, name))
            self.assertNotIn('SpreadsheetApp.flush', body,
                             f'{name} がシートの書き込み確定を行っています')
            self.assertNotIn('LockService', body,
                             f'{name} が回答用のロックを使っています')

    def test_client_shows_that_ai_is_not_the_grader(self):
        index = read('Index.html')
        self.assertIn('AI補助解説', index, 'AI解説の別枠がありません')
        self.assertIn('採点・理解度・学習ログ', index,
                      '採点・理解度がAIではない旨の明記がありません')

    def test_existing_explanation_is_shown_before_ai(self):
        index = read('Index.html')
        registered = index.index('id="explanationBox"')
        ai = index.index('id="aiExplanationBox"')
        self.assertLess(registered, ai,
                        '既存の登録解説よりAI解説を先に表示しています')


class TestImageInlineData(unittest.TestCase):
    """画像問題では既存の取得処理を再利用し inline_data として渡すこと。"""

    def test_reuses_existing_image_bundle(self):
        body = strip_comments(function_body(read('Code.gs'), 'appendAiImageParts_'))
        self.assertIn('getQuestionImageBundle(qid)', body,
                      '既存の getQuestionImageBundle を再利用していません')
        self.assertIn('bundle.question_images', body, '問題画像を渡していません')
        self.assertIn('bundle.option_images', body, '選択肢画像を渡していません')

    def test_converts_data_url_to_inline_data(self):
        body = strip_comments(function_body(read('Code.gs'), 'aiInlineDataFromDataUrl_'))
        self.assertIn('inline_data', body, 'inline_data 形式へ変換していません')
        self.assertIn('mime_type', body, 'mime_type を設定していません')
        self.assertIn('base64', body, 'base64データを取り出していません')

    def test_image_failure_still_allows_text_only_explanation(self):
        body = strip_comments(function_body(read('Code.gs'), 'appendAiImageParts_'))
        self.assertRegex(body, r'if\s*\(\s*!bundle\s*\|\|\s*!bundle\.ok\s*\)\s*return\s+parts',
                         '画像取得に失敗したときテキストだけで続行できません')
        self.assertIn('catch (e)', body, '画像取得の例外で全体が落ちます')

        # 画像取得に失敗しても getAiExplanation は続行する（throwしない）。
        outer = strip_comments(function_body(read('Code.gs'), 'buildAiPromptParts_'))
        self.assertIn('appendAiImageParts_(parts, question)', outer,
                      'プロンプトへ画像を添付する経路がありません')

    def test_images_are_attached_only_for_image_questions(self):
        body = strip_comments(function_body(read('Code.gs'), 'appendAiImageParts_'))
        self.assertRegex(
            body,
            r'if\s*\(\s*!String\(question\.question_image_refs[\s\S]{0,60}?\)\s*return\s+parts',
            '画像のない問題でも画像取得を試みています',
        )


class TestAiCaching(unittest.TestCase):
    """連打でAPI使用量が増えにくいこと。"""

    def test_user_cache_for_one_hour(self):
        code = strip_comments(read('Code.gs'))
        self.assertIn('CACHE_SECONDS: 3600', code,
                      'AI解説のキャッシュが1時間になっていません')

        put_body = strip_comments(function_body(read('Code.gs'), 'aiCachePut_'))
        self.assertIn('CacheService.getUserCache()', put_body,
                      'UserCache を使っていません')
        self.assertIn('AI_CONFIG.CACHE_SECONDS', put_body,
                      'キャッシュ期間を設定していません')

    def test_cache_is_checked_before_calling_gemini(self):
        body = strip_literals(function_body(read('Code.gs'), 'getAiExplanation'))
        cache = body.find('aiCacheGet_(')
        call = body.find('callGeminiGenerateContent_(')
        self.assertNotEqual(cache, -1, 'キャッシュを読んでいません')
        self.assertLess(cache, call, 'キャッシュより先にAIを呼んでいます')

    def test_cache_key_is_per_attempt(self):
        body = strip_comments(function_body(read('Code.gs'), 'getAiExplanation'))
        self.assertRegex(body, r'AI_CONFIG\.CACHE_PREFIX\s*\+\s*attemptId',
                         'キャッシュキーが attempt 単位になっていません')


class TestClientCallsAiOnlyOnDemand(unittest.TestCase):
    """AIはボタンを押したときだけ呼ぶこと。"""

    @classmethod
    def setUpClass(cls):
        cls.script = html_script('Client.html')

    def test_ai_is_called_only_from_the_button_handler(self):
        stripped = strip_comments(self.script)
        self.assertEqual(
            stripped.count('.getAiExplanation('), 1,
            'AI呼び出しが複数箇所にあります（自動呼び出しの疑い）',
        )
        body = strip_comments(function_body(self.script, 'requestAiExplanation'))
        self.assertIn('.getAiExplanation({ attemptId: state.attemptId })', body,
                      'ボタン処理からAIを呼んでいません')

    def test_button_is_wired(self):
        stripped = strip_comments(self.script)
        self.assertIn("$('aiExplainBtn').addEventListener('click', requestAiExplanation)", stripped,
                      'AIボタンが処理へつながっていません')
        self.assertIn('AIでさらに噛み砕く', read('Index.html'),
                      'AIボタンの表示名がありません')

    def test_ai_is_not_called_while_presenting_a_question(self):
        for func in ('presentQuestion', 'renderQuestion', 'renderOptions',
                     'loadNextQuestion', 'submitCurrentAnswer', 'renderResult'):
            body = strip_comments(function_body(self.script, func))
            self.assertNotIn('getAiExplanation', body,
                             f'{func} がAIを自動で呼んでいます')

    def test_ai_request_sends_only_the_attempt_id(self):
        body = strip_literals(function_body(self.script, 'requestAiExplanation'))
        payload = re.search(r'\.getAiExplanation\(([\s\S]*?)\);', body)
        self.assertIsNotNone(payload, 'AI呼び出しの引数が読めません')
        for field in ('correct', 'answer:', 'explanation'):
            self.assertNotIn(field, payload.group(1),
                             f'AI呼び出しへ {field} を送っています')

    def test_ai_failure_does_not_disturb_the_recorded_result(self):
        body = strip_comments(function_body(self.script, 'requestAiExplanation'))
        for forbidden in ('submitAnswer(', 'renderResult(', 'loadNextQuestion('):
            self.assertNotIn(forbidden, body,
                             f'AI処理が {forbidden} を呼び出しています')


class TestExplanationIsNotCutOff(unittest.TestCase):
    """解説が途中で切れたまま黙って表示されないこと。"""

    def test_token_budget_covers_the_requested_length(self):
        """本文800〜1200文字＋思考ぶんに足りる上限であること。"""
        limit = re.search(r'MAX_OUTPUT_TOKENS:\s*(\d+)', CODE)
        self.assertIsNotNone(limit, 'MAX_OUTPUT_TOKENS が見つかりません')
        self.assertGreaterEqual(
            int(limit.group(1)), 4096,
            '日本語1200文字とモデルの思考ぶんを合わせると足りず、解説が途中で切れます',
        )

    def test_truncation_is_detected_and_reported(self):
        body = strip_comments(function_body(CODE, 'callGeminiGenerateContent_'))
        self.assertIn('finishReason', body,
                      '長さ上限で切れたかどうかを確認していません')
        self.assertIn('MAX_TOKENS', body, 'MAX_TOKENS を判定していません')
        self.assertIn('truncated', body, '切れたことを呼び出し元へ伝えていません')

    def test_thought_parts_are_not_shown_as_the_explanation(self):
        body = strip_comments(function_body(CODE, 'callGeminiGenerateContent_'))
        self.assertRegex(body, r'thought\s*!==\s*true',
                         'モデルの内部の思考を解説本文として表示しています')

    def test_truncated_text_is_not_cached(self):
        body = strip_comments(function_body(CODE, 'getAiExplanation'))
        self.assertRegex(
            body, r'truncated\s*!==\s*true[\s\S]{0,120}aiCachePut_',
            '途中で切れた解説をキャッシュすると、1時間そのまま再表示されます',
        )

    def test_client_tells_the_reader_when_it_was_cut(self):
        script = html_script('Client.html')
        self.assertIn('res.truncated', script,
                      '切れたことを画面に出していません')

    def test_explanation_box_has_no_fixed_height(self):
        styles = read('Styles.html')
        rules = re.findall(
            r'\.(?:ai-)?explanation-box[^{]*\{([^}]*)\}', styles
        )
        self.assertTrue(rules, '解説ボックスのCSSが見つかりません')
        joined = ' '.join(rules)
        self.assertNotRegex(
            joined, r'(?<!max-)height:\s*(?!auto)\S',
            '解説ボックスの高さを固定すると、長い解説が見切れます',
        )
        self.assertNotIn('overflow: hidden', joined,
                         '解説ボックスがはみ出した文字を切り落としています')


class TestMathDelimitersAreRepaired(unittest.TestCase):
    """壊れた数式記号が、まわりの説明文を巻き込んで消さないこと。

    実際に aiPlainText_ をNodeで動かして確かめる。
    """

    def setUp(self):
        require_node(self)

    def plain(self, text):
        script = '\n'.join([
            collect(CODE, ('aiRepairMathDelimiters_', 'aiPlainText_')),
            'console.log(JSON.stringify({ out: aiPlainText_(%s) }));' % js_value(text),
        ])
        return run(script)['out']

    def test_complete_math_is_left_alone(self):
        text = 'ベクトル \\(\\mathbf{u}\\) のとき \\[ \\cos\\theta = \\frac{a}{b} \\] です。'
        self.assertEqual(self.plain(text), text)

    def test_unclosed_delimiter_at_the_end_keeps_its_text(self):
        """長さ上限で切れた末尾の \\( が、読めない断片として残らないこと。"""
        out = self.plain('ここで、分母 \\(\\|\\mathbf{u}\\|')
        self.assertIn('ここで、分母', out, '手前の文章まで消えています')
        self.assertNotIn('\\(', out, '開いたままの区切り記号が残っています')

    def test_unclosed_delimiter_does_not_swallow_later_paragraphs(self):
        """これが今回の不具合。閉じ忘れの \\( が段落をまとめて飲み込んでいた。"""
        out = self.plain(
            '最初の段落 \\( a = 1\n\n'
            '二段落目の説明文はここにあります。\n\n'
            '三段落目 \\(b\\) です。'
        )
        self.assertIn('二段落目の説明文はここにあります。', out,
                      '数式の閉じ忘れが、あいだの説明文を消しています')
        self.assertIn('\\(b\\)', out, '正しく閉じた数式まで壊しています')
        # 開きと閉じの数が合っていないと、画面のKaTeXが手前の \\( から
        # ここの \\) までを1つの数式とみなし、あいだの段落を飲み込む。
        self.assertEqual(
            out.count('\\('), out.count('\\)'),
            '対になっていない区切り記号が残っており、KaTeXが説明文を飲み込みます',
        )

    def test_stray_closing_delimiter_is_removed(self):
        """画面に「{v}\\)」のような断片が出ないこと。"""
        out = self.plain('としたとき、コサイン類似度\\) を考えます。')
        self.assertIn('コサイン類似度', out)
        self.assertNotIn('\\)', out, '対応する開始記号の無い区切りが残っています')

    def test_markdown_inside_math_survives(self):
        out = self.plain('式は \\(a^*+b^*\\) です。**強調**は消える。')
        self.assertIn('\\(a^*+b^*\\)', out, '数式の中の記号を消しています')
        self.assertIn('強調は消える', out)


class TestNoNewAppsScriptFiles(unittest.TestCase):
    """Apps Scriptへ送るファイル構成を増やしていないこと。"""

    def test_ai_functions_live_in_code_gs(self):
        code = read('Code.gs')
        for name in AI_SERVER_FUNCTIONS:
            self.assertIn(f'function {name}(', code,
                          f'{name} が Code.gs にありません')

    def test_required_file_list_is_unchanged(self):
        script = (Path(__file__).resolve().parents[1] / 'scripts/check-required-files.sh')
        text = script.read_text(encoding='utf-8')
        listed = re.findall(r'^\s*"([^"]+)"\s*$', text, re.M)
        self.assertEqual(
            listed,
            [
                'appsscript.json', 'Code.gs', 'ImageManifest.gs', 'ImageSupport.gs',
                'ImageSelfTest.gs', 'Index.html', 'Styles.html', 'Client.html',
            ],
            'Apps Scriptへ送るファイルの一覧が変わっています',
        )

    def test_manifest_json_is_still_valid(self):
        manifest = json.loads((SRC / 'appsscript.json').read_text(encoding='utf-8'))
        self.assertEqual(manifest['webapp']['access'], 'MYSELF')
        self.assertEqual(manifest['webapp']['executeAs'], 'USER_DEPLOYING')


if __name__ == '__main__':
    unittest.main(verbosity=2)
