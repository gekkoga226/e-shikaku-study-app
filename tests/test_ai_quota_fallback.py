"""AIの1日ぶんの枠が尽きたときの動き（主モデル→予備モデル→AIだけ停止）の守り。

守っている約束:

  - 残してある解説があれば、Geminiを呼ばない（枠の節約と速さ）。
  - 主モデル（既定 gemini-3.5-flash）が「今日ぶんを使い切った」と答えた日だけ、
    予備モデル（既定 gemini-3.5-flash-lite）へ回す。
  - 1分あたりの混雑（すぐ直る429）では、モデルを切り替えない。
  - 一度「使い切った」と分かったモデルは、その日はもう呼ばない
    （＝次の問題では毎回429を出させない）。
  - どちらのモデルも使えないときは、AI補助解説だけを止める。
    採点・理解度・学習ログ・出題には一切影響しない。
  - APIキーの誤りを「利用上限」と誤って伝えない。
  - Googleの英語のエラー全文を画面へ返さない。APIキーも返さない。
  - 改修前に作られた6列の 07_AI解説キャッシュ を、無効にしない。

Nodeで実際に関数を動かし、Geminiを何回・どのモデルへ呼んだかを記録して確かめる。
スプレッドシートにもGeminiにも接続しない。
"""

import json
import re
import sys
import unittest
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

sys.path.insert(0, str(Path(__file__).resolve().parent))

from gas_source import function_body, html_script, read, strip_comments  # noqa: E402
from js_runtime import gas_bundle, js_value, require_node, run  # noqa: E402
from test_ai_reuse import (  # noqa: E402
    CACHE_HEADERS,
    FAKE_ENV,
    LOG_HEADERS,
    QUESTION_HEADERS,
    log_row,
    question_row,
)

CODE = read('Code.gs')
PACIFIC = ZoneInfo('America/Los_Angeles')

# 実物のGeminiが返す本文の形。ここを推測で単純化すると、分類の検査にならない。
SUCCESS_BODY = json.dumps({
    'candidates': [{
        'content': {'parts': [{'text': '【まず一言】ここが要点です。'}]},
        'finishReason': 'STOP',
    }]
}, ensure_ascii=False)


def quota_body(quota_id, message, retry_delay=None):
    details = [{
        '@type': 'type.googleapis.com/google.rpc.QuotaFailure',
        'violations': [{
            'quotaMetric': 'generativelanguage.googleapis.com/generate_content_free_tier_requests',
            'quotaId': quota_id,
            'quotaDimensions': {'model': 'gemini-3.5-flash', 'location': 'global'},
            'quotaValue': '250',
        }],
    }]
    if retry_delay:
        details.append({
            '@type': 'type.googleapis.com/google.rpc.RetryInfo',
            'retryDelay': retry_delay,
        })
    return json.dumps({
        'error': {
            'code': 429,
            'message': message,
            'status': 'RESOURCE_EXHAUSTED',
            'details': details,
        }
    })


ENGLISH_QUOTA_MESSAGE = (
    'You exceeded your current quota, please check your plan and billing details. '
    'Quota exceeded for metric: generativelanguage.googleapis.com/'
    'generate_content_free_tier_requests, limit: 250'
)

DAILY_429 = quota_body(
    'GenerateRequestsPerDayPerProjectPerModel-FreeTier', ENGLISH_QUOTA_MESSAGE)
DAILY_429_WITH_RETRY = quota_body(
    'GenerateRequestsPerDayPerProjectPerModel-FreeTier', ENGLISH_QUOTA_MESSAGE, '31728s')
MINUTE_429 = quota_body(
    'GenerateRequestsPerMinutePerProjectPerModel-FreeTier',
    'Quota exceeded for quota metric per minute, limit: 10', '23s')
VAGUE_429 = json.dumps({
    'error': {'code': 429, 'message': 'Resource has been exhausted (e.g. check quota).',
              'status': 'RESOURCE_EXHAUSTED'}
})
BAD_KEY_400 = json.dumps({
    'error': {'code': 400, 'message': 'API key not valid. Please pass a valid API key.',
              'status': 'INVALID_ARGUMENT'}
})

# test_ai_reuse の偽環境に、この検査で必要なものを足す。
# const の宣言は上書きできないので、中身（メソッド）だけを差し替える。
QUOTA_ENV = """
const FETCHES = [];
const PROPS = new Map(Object.entries(START_PROPERTIES));

PropertiesService.getScriptProperties = () => ({
  getProperty: k => (PROPS.has(k) ? PROPS.get(k) : null),
  setProperty: (k, v) => { PROPS.set(k, String(v)); },
  deleteProperty: k => { PROPS.delete(k); }
});

// 実物と同じく、タイムゾーン名から夏時間の有無まで判断する。
Utilities.formatDate = (date, timezone, format) => {
  const parts = new Intl.DateTimeFormat('en-US', {
    timeZone: timezone, hour12: false,
    year: 'numeric', month: '2-digit', day: '2-digit',
    hour: '2-digit', minute: '2-digit', second: '2-digit'
  }).formatToParts(new Date(date)).reduce((acc, p) => {
    acc[p.type] = p.value;
    return acc;
  }, {});
  const hour = parts.hour === '24' ? '00' : parts.hour;
  return format
    .replace('yyyy', parts.year).replace('MM', parts.month).replace('dd', parts.day)
    .replace('HH', hour).replace('mm', parts.minute).replace('ss', parts.second);
};

function fakeResponse(status, body) {
  return { getResponseCode: () => status, getContentText: () => body };
}

const UrlFetchApp = {
  fetch: (url, options) => {
    const headers = (options && options.headers) || {};
    const model = decodeURIComponent(
      String(url)
        .replace('https://generativelanguage.googleapis.com/v1beta/models/', '')
        .replace(':generateContent', '')
    );
    FETCHES.push({
      model: model,
      url: String(url),
      key_in_header: String(headers['x-goog-api-key'] || ''),
      payload: String(options.payload || '')
    });

    const queue = RESPONSES[model];
    const spec = !queue ? null : (queue.length > 1 ? queue.shift() : queue[0]);
    if (!spec) return fakeResponse(200, SUCCESS_BODY);
    return fakeResponse(spec.status, spec.body);
  }
};
"""


def run_quota(driver, responses=None, properties=None, log_rows=None,
              question_rows=None, cache_rows=None):
    """AI解説を実際に動かし、どのモデルへ何回投げたかを記録して返す。"""
    props = {'GEMINI_API_KEY': 'test-key'}
    props.update(properties or {})

    script = '\n'.join([
        'const LOG_HEADERS = %s;' % js_value(LOG_HEADERS),
        'const QUESTION_HEADERS = %s;' % js_value(QUESTION_HEADERS),
        'const CACHE_HEADERS = %s;' % js_value(CACHE_HEADERS),
        'const LOG_ROWS = %s;' % js_value(
            log_rows if log_rows is not None else [log_row('ATT-1'), log_row('ATT-2', question_id='Q2')]),
        'const QUESTION_ROWS = %s;' % js_value(
            question_rows if question_rows is not None
            else [question_row(), question_row('Q2', text='2問目の本文です。')]),
        'const CACHE_ROWS = %s;' % js_value(cache_rows if cache_rows is not None else []),
        'const START_PROPERTIES = %s;' % js_value(props),
        'const RESPONSES = %s;' % js_value(responses or {}),
        'const SUCCESS_BODY = %s;' % js_value(SUCCESS_BODY),
        FAKE_ENV,
        QUOTA_ENV,
        gas_bundle(),
        driver,
    ])
    return run(script)


def models_called(out):
    return [f['model'] for f in out['fetches']]


REPORT = """
console.log(JSON.stringify({
  first: FIRST,
  second: (typeof SECOND === 'undefined' ? null : SECOND),
  fetches: FETCHES,
  props: Object.fromEntries(PROPS),
  now: NOW,
  cacheRows: cacheSheetRows(),
  writes: WRITES,
  inserted: INSERTED
}));
"""


class TestPrimaryPath(unittest.TestCase):
    """TEST1・TEST2: ふつうに使えるときの動き。"""

    def setUp(self):
        require_node(self)

    def test_primary_model_generates_and_is_saved(self):
        """未キャッシュなら主モデルで作り、07_AI解説キャッシュへ残すこと。"""
        out = run_quota("""
            const NOW = Date.now();
            const FIRST = getAiExplanation({ attemptId: 'ATT-1' });
        """ + REPORT)

        self.assertTrue(out['first']['ok'])
        self.assertEqual(models_called(out), ['gemini-3.5-flash'])
        self.assertEqual(out['first']['model'], 'gemini-3.5-flash')
        self.assertEqual(out['first']['source'], 'generated')
        self.assertFalse(out['first']['fallback_used'])
        self.assertEqual(out['first']['notice'], '', '通常時によけいな案内を出しています')
        self.assertEqual(len(out['cacheRows']), 1, '解説を残していません')
        self.assertEqual(out['cacheRows'][0][3], 'gemini-3.5-flash',
                         'どのモデルで作ったかを残していません')
        self.assertEqual(out['cacheRows'][0][6], 'ai_explanation_v2',
                         '解説の書き方の版を残していません')

    def test_second_time_does_not_call_the_api(self):
        """TEST2: 同じ条件の2回目はGeminiを呼ばないこと。"""
        out = run_quota("""
            const NOW = Date.now();
            const FIRST = getAiExplanation({ attemptId: 'ATT-1' });
            CACHE.clear();
            const SECOND = getAiExplanation({ attemptId: 'ATT-1' });
        """ + REPORT)

        self.assertEqual(len(out['fetches']), 1,
                         '2回目もGeminiを呼んでいます（残した解説を使っていません）')
        self.assertEqual(out['second']['source'], 'sheet')
        self.assertEqual(out['second']['text'], out['first']['text'])


class TestDailyQuotaFallback(unittest.TestCase):
    """TEST3・TEST4: 主モデルが1日ぶんを使い切った日の動き。"""

    def setUp(self):
        require_node(self)

    def test_daily_quota_switches_to_the_fallback_model(self):
        out = run_quota("""
            const NOW = Date.now();
            const FIRST = getAiExplanation({ attemptId: 'ATT-1' });
        """ + REPORT, responses={'gemini-3.5-flash': [{'status': 429, 'body': DAILY_429}]})

        self.assertTrue(out['first']['ok'], '予備モデルへ切り替えられていません')
        self.assertEqual(models_called(out), ['gemini-3.5-flash', 'gemini-3.5-flash-lite'])
        self.assertEqual(out['first']['model'], 'gemini-3.5-flash-lite')
        self.assertTrue(out['first']['fallback_used'])
        self.assertEqual(out['first']['notice_code'], 'AI_FALLBACK_MODEL')
        self.assertIn('軽量', out['first']['notice'], '切り替えたことを知らせていません')
        self.assertEqual(out['cacheRows'][0][3], 'gemini-3.5-flash-lite',
                         '予備モデルで作ったことを残していません')

    def test_exhausted_model_is_remembered_until_the_pacific_reset(self):
        """使い切った記録が、米国太平洋時間の0時まで残ること。"""
        out = run_quota("""
            const NOW = Date.now();
            const FIRST = getAiExplanation({ attemptId: 'ATT-1' });
        """ + REPORT, responses={'gemini-3.5-flash': [{'status': 429, 'body': DAILY_429}]})

        state = json.loads(out['props']['AI_MODEL_QUOTA_BLOCK'])
        self.assertIn('gemini-3.5-flash', state, '使い切ったモデルを覚えていません')
        self.assertNotIn('gemini-3.5-flash-lite', state,
                         '使えているモデルまで止めています')

        now = datetime.fromtimestamp(out['now'] / 1000, tz=ZoneInfo('UTC'))
        local = now.astimezone(PACIFIC)
        expected = (local + timedelta(days=1)).replace(
            hour=0, minute=0, second=0, microsecond=0)
        actual = datetime.fromtimestamp(state['gemini-3.5-flash'] / 1000, tz=ZoneInfo('UTC'))
        self.assertLess(abs((actual - expected).total_seconds()), 90,
                        f'解除時刻が米国太平洋時間の0時になっていません: {actual} != {expected}')

    def test_retry_information_from_google_is_preferred_when_shorter(self):
        """Googleが「これだけ待て」と返し、それが0時より早ければそちらを使うこと。"""
        out = run_quota("""
            const NOW = Date.now();
            const FIRST = getAiExplanation({ attemptId: 'ATT-1' });
        """ + REPORT,
            responses={'gemini-3.5-flash': [{'status': 429, 'body': DAILY_429_WITH_RETRY}]})

        state = json.loads(out['props']['AI_MODEL_QUOTA_BLOCK'])
        now = datetime.fromtimestamp(out['now'] / 1000, tz=ZoneInfo('UTC'))
        from_api = now + timedelta(seconds=31728)
        midnight = (now.astimezone(PACIFIC) + timedelta(days=1)).replace(
            hour=0, minute=0, second=0, microsecond=0)
        expected = min(from_api, midnight)
        actual = datetime.fromtimestamp(state['gemini-3.5-flash'] / 1000, tz=ZoneInfo('UTC'))
        self.assertLess(abs((actual - expected).total_seconds()), 90,
                        '早いほうの解除時刻を選んでいません')

    def test_next_question_goes_straight_to_the_fallback(self):
        """TEST4: 次の問題では、主モデルへ投げ直さないこと。"""
        out = run_quota("""
            const NOW = Date.now();
            const FIRST = getAiExplanation({ attemptId: 'ATT-1' });
            CACHE.clear();
            FETCHES.length = 0;
            const SECOND = getAiExplanation({ attemptId: 'ATT-2' });
        """ + REPORT, responses={'gemini-3.5-flash': [{'status': 429, 'body': DAILY_429}]})

        self.assertTrue(out['second']['ok'])
        self.assertEqual(models_called(out), ['gemini-3.5-flash-lite'],
                         '毎回、使い切った主モデルへ投げ直しています')
        self.assertTrue(out['second']['fallback_used'])


class TestBothModelsUnavailable(unittest.TestCase):
    """TEST5: どちらも使えないときは、AI解説だけを止めること。"""

    def setUp(self):
        require_node(self)

    def test_ai_stops_without_touching_learning_data(self):
        out = run_quota("""
            const NOW = Date.now();
            const FIRST = getAiExplanation({ attemptId: 'ATT-1' });
            FETCHES.length = 0;
            const SECOND = getAiExplanation({ attemptId: 'ATT-2' });
        """ + REPORT, responses={
            'gemini-3.5-flash': [{'status': 429, 'body': DAILY_429}],
            'gemini-3.5-flash-lite': [{'status': 429, 'body': DAILY_429}],
        })

        self.assertFalse(out['first']['ok'])
        self.assertEqual(out['first']['error_code'], 'AI_DAILY_QUOTA_EXHAUSTED')
        self.assertIn('採点', out['first']['message'],
                      '学習は続けられることを伝えていません')

        self.assertEqual(out['fetches'], [],
                         '使い切ったあとも、むだにGeminiを呼び続けています')
        self.assertFalse(out['second']['ok'])

        for write in out['writes']:
            self.assertEqual(write['sheet'], '07_AI解説キャッシュ',
                             f"AIの失敗が別のシートへ書き込んでいます: {write}")

    def test_the_raw_google_message_is_not_shown(self):
        """Googleの英語のエラー全文を画面へ返さないこと。"""
        out = run_quota("""
            const NOW = Date.now();
            const FIRST = getAiExplanation({ attemptId: 'ATT-1' });
        """ + REPORT, responses={
            'gemini-3.5-flash': [{'status': 429, 'body': DAILY_429}],
            'gemini-3.5-flash-lite': [{'status': 429, 'body': DAILY_429}],
        })

        payload = json.dumps(out['first'], ensure_ascii=False)
        self.assertNotIn('You exceeded your current quota', payload)
        self.assertNotIn('generativelanguage.googleapis.com', payload)
        self.assertNotIn('test-key', payload, 'APIキーが戻り値に混ざっています')


class TestShortRateLimitDoesNotSwitchModels(unittest.TestCase):
    """TEST7の裏返し: 1分あたりの混雑では、モデルを切り替えないこと。"""

    def setUp(self):
        require_node(self)

    def test_per_minute_limit_keeps_the_primary_model(self):
        out = run_quota("""
            const NOW = Date.now();
            const FIRST = getAiExplanation({ attemptId: 'ATT-1' });
        """ + REPORT, responses={'gemini-3.5-flash': [{'status': 429, 'body': MINUTE_429}]})

        self.assertEqual(models_called(out), ['gemini-3.5-flash'],
                         '一時的な混雑でモデルを切り替えています')
        self.assertFalse(out['first']['ok'])
        self.assertEqual(out['first']['error_code'], 'AI_RATE_LIMITED')
        self.assertIn('一時的', out['first']['message'])
        self.assertNotIn('AI_MODEL_QUOTA_BLOCK', out['props'],
                         '一時的な混雑を「今日ぶんを使い切った」と覚えています')

    def test_unclear_429_tries_the_fallback_once_without_remembering(self):
        """種類が読めない429は、その1回だけ予備モデルを試し、記録は残さないこと。"""
        out = run_quota("""
            const NOW = Date.now();
            const FIRST = getAiExplanation({ attemptId: 'ATT-1' });
        """ + REPORT, responses={'gemini-3.5-flash': [{'status': 429, 'body': VAGUE_429}]})

        self.assertEqual(models_called(out), ['gemini-3.5-flash', 'gemini-3.5-flash-lite'])
        self.assertTrue(out['first']['ok'])
        self.assertNotIn('AI_MODEL_QUOTA_BLOCK', out['props'],
                         '判断できない429で主モデルを1日止めています')


class TestConfigurationErrorsAreNotQuotaErrors(unittest.TestCase):
    """TEST6: 設定の誤りを「利用上限」と誤って伝えないこと。"""

    def setUp(self):
        require_node(self)

    def test_bad_api_key_is_reported_as_a_setting_problem(self):
        out = run_quota("""
            const NOW = Date.now();
            const FIRST = getAiExplanation({ attemptId: 'ATT-1' });
        """ + REPORT, responses={'gemini-3.5-flash': [{'status': 400, 'body': BAD_KEY_400}]})

        self.assertFalse(out['first']['ok'])
        self.assertEqual(out['first']['error_code'], 'AI_BAD_REQUEST')
        self.assertNotIn('利用上限', out['first']['message'])
        self.assertNotIn('使い切り', out['first']['message'])
        self.assertEqual(models_called(out), ['gemini-3.5-flash'],
                         '設定の誤りで予備モデルへ切り替えています（同じ誤りが繰り返されます）')
        self.assertNotIn('AI_MODEL_QUOTA_BLOCK', out['props'])

    def test_missing_api_key_never_reaches_the_network(self):
        out = run_quota("""
            const NOW = Date.now();
            PROPS.delete('GEMINI_API_KEY');
            const FIRST = getAiExplanation({ attemptId: 'ATT-1' });
        """ + REPORT)

        self.assertFalse(out['first']['ok'])
        self.assertEqual(out['first']['error_code'], 'AI_KEY_NOT_CONFIGURED')
        self.assertEqual(out['fetches'], [])
        self.assertEqual(out['writes'], [], 'AIが使えない状態で何かを書き込んでいます')


class TestFallbackCanBeTurnedOff(unittest.TestCase):
    """予備モデルを使わない設定にできること。"""

    def setUp(self):
        require_node(self)

    def test_disabled_fallback_stops_at_the_primary_model(self):
        out = run_quota("""
            const NOW = Date.now();
            const FIRST = getAiExplanation({ attemptId: 'ATT-1' });
        """ + REPORT,
            properties={'GEMINI_FALLBACK_ENABLED': 'false'},
            responses={'gemini-3.5-flash': [{'status': 429, 'body': DAILY_429}]})

        self.assertEqual(models_called(out), ['gemini-3.5-flash'])
        self.assertFalse(out['first']['ok'])
        self.assertEqual(out['first']['error_code'], 'AI_DAILY_QUOTA_EXHAUSTED')

    def test_model_names_can_be_replaced_from_script_properties(self):
        out = run_quota("""
            const NOW = Date.now();
            const FIRST = getAiExplanation({ attemptId: 'ATT-1' });
        """ + REPORT,
            properties={'GEMINI_MODEL': 'model-x', 'GEMINI_FALLBACK_MODEL': 'model-y'},
            responses={'model-x': [{'status': 429, 'body': DAILY_429}]})

        self.assertEqual(models_called(out), ['model-x', 'model-y'],
                         'スクリプトプロパティのモデル名が使われていません')


class TestOldCacheStillWorks(unittest.TestCase):
    """TEST9: 改修前に作られた6列のキャッシュを無効にしないこと。"""

    def setUp(self):
        require_node(self)

    def test_six_column_cache_is_reused_without_calling_gemini(self):
        # 改修前の行（prompt_version の列がない）を、そのまま置いた状態にする。
        out = run_quota("""
            const NOW = Date.now();
            // まず改修前と同じ6列の表を作り、そこへ解説を残す。
            const FIRST = getAiExplanation({ attemptId: 'ATT-1' });
            const saved = cacheSheetRows()[0];
            // prompt_version を消し、列も6列だけの状態へ戻す（改修前の表そのもの）。
            SHEETS['07_AI解説キャッシュ']._grid.forEach(line => { line.length = 6; });
            CACHE.clear();
            FETCHES.length = 0;
            const SECOND = getAiExplanation({ attemptId: 'ATT-1' });
        """ + REPORT)

        self.assertTrue(out['second']['ok'])
        self.assertEqual(out['second']['source'], 'sheet',
                         '改修前のキャッシュが使われず、作り直しています')
        self.assertEqual(out['fetches'], [],
                         '改修前のキャッシュがあるのにGeminiを呼んでいます')

    def test_prompt_version_is_a_record_not_a_filter(self):
        """版の印は記録用で、使い回しの判定には使わないこと。"""
        code = strip_comments(CODE)
        self.assertIn('RETIRED_PROMPT_VERSIONS: []', code,
                      '既存の解説をまとめて無効にする設定が入っています')
        body = strip_comments(function_body(CODE, 'aiSheetLookup_'))
        self.assertNotIn('AI_CONFIG.PROMPT_VERSION', body,
                         '今の版と一致する解説しか使わない作りになっています')


class TestQuotaClassification(unittest.TestCase):
    """429の読み分けそのものを、いろいろな本文で確かめる。"""

    def setUp(self):
        require_node(self)

    def classify(self, body):
        script = '\n'.join([
            'const AI_CONFIG = { LONG_RETRY_SECONDS: 300 };',
            function_body_source('aiParseRetrySeconds_'),
            function_body_source('aiClassifyQuotaError_'),
            'console.log(JSON.stringify(aiClassifyQuotaError_(%s)));' % js_value(body),
        ])
        return run(script)

    def test_daily_quota_is_detected(self):
        self.assertEqual(self.classify(DAILY_429)['scope'], 'daily')

    def test_per_minute_quota_is_detected(self):
        result = self.classify(MINUTE_429)
        self.assertEqual(result['scope'], 'short')
        self.assertEqual(result['retry_seconds'], 23)

    def test_long_retry_delay_counts_as_daily(self):
        body = json.dumps({'error': {'code': 429, 'message': 'quota', 'details': [
            {'@type': 'type.googleapis.com/google.rpc.RetryInfo', 'retryDelay': '3600s'}]}})
        self.assertEqual(self.classify(body)['scope'], 'daily')

    def test_unreadable_body_is_not_guessed(self):
        self.assertEqual(self.classify('<html>error</html>')['scope'], 'unknown')

    def test_message_wording_is_a_backup_only(self):
        body = json.dumps({'error': {'code': 429, 'message': 'Quota exceeded per day'}})
        self.assertEqual(self.classify(body)['scope'], 'daily')


def function_body_source(name):
    """関数の宣言をそのまま取り出す（分類だけを単体で動かすため）。"""
    from gas_source import function_source
    return function_source(CODE, name)


class TestQuotaResetTime(unittest.TestCase):
    """解除時刻が、日本時間の0時ではなく米国太平洋時間の0時になること。"""

    def setUp(self):
        require_node(self)

    def reset_at(self, iso, timezone='America/Los_Angeles'):
        script = '\n'.join([
            'const AI_CONFIG = { MAX_QUOTA_BLOCK_SECONDS: 93600 };',
            QUOTA_TIME_ENV,
            function_body_source('aiNextQuotaResetAt_'),
            'const now = new Date(%s);' % js_value(iso),
            'console.log(JSON.stringify({'
            '  until: aiNextQuotaResetAt_(now, %s).toISOString() }));' % js_value(timezone),
        ])
        return run(script)['until']

    def test_winter_uses_standard_time(self):
        # 2026-01-15 09:00 UTC = 前日 01:00（太平洋標準時 UTC-8）
        self.assertEqual(self.reset_at('2026-01-15T09:00:00Z'), '2026-01-16T08:00:00.000Z')

    def test_summer_uses_daylight_saving_time(self):
        # 2026-07-15 09:00 UTC = 前日 02:00（太平洋夏時間 UTC-7）
        self.assertEqual(self.reset_at('2026-07-15T09:00:00Z'), '2026-07-16T07:00:00.000Z')

    def test_japan_midnight_is_not_used(self):
        """日本時間の0時ちょうどに解除しない（そこはまだ枠が戻っていない）。"""
        until = self.reset_at('2026-07-15T09:00:00Z')
        self.assertNotEqual(until, '2026-07-15T15:00:00.000Z')

    def test_unknown_timezone_falls_back_to_one_hour(self):
        until = self.reset_at('2026-07-15T09:00:00Z', timezone='Mars/Olympus')
        self.assertEqual(until, '2026-07-15T10:00:00.000Z')


QUOTA_TIME_ENV = """
const Utilities = {
  formatDate: (date, timezone, format) => {
    const parts = new Intl.DateTimeFormat('en-US', {
      timeZone: timezone, hour12: false,
      hour: '2-digit', minute: '2-digit', second: '2-digit'
    }).formatToParts(new Date(date)).reduce((acc, p) => {
      acc[p.type] = p.value;
      return acc;
    }, {});
    const hour = parts.hour === '24' ? '00' : parts.hour;
    return format.replace('HH', hour).replace('mm', parts.minute).replace('ss', parts.second);
  }
};
"""


class TestServerResponseShape(unittest.TestCase):
    """画面へ返す形が、どの経路でも同じであること。"""

    def setUp(self):
        require_node(self)

    def test_every_success_has_the_same_fields(self):
        out = run_quota("""
            const NOW = Date.now();
            const FIRST = getAiExplanation({ attemptId: 'ATT-1' });
            CACHE.clear();
            const SECOND = getAiExplanation({ attemptId: 'ATT-1' });
        """ + REPORT)

        for label, payload in (('生成', out['first']), ('再表示', out['second'])):
            for field in ('ok', 'model', 'text', 'source', 'cached',
                          'fallback_used', 'notice_code', 'notice', 'disclaimer'):
                self.assertIn(field, payload, f'{label}の戻り値に {field} がありません')

    def test_api_key_is_sent_in_the_header_only(self):
        out = run_quota("""
            const NOW = Date.now();
            const FIRST = getAiExplanation({ attemptId: 'ATT-1' });
        """ + REPORT)

        fetch = out['fetches'][0]
        self.assertEqual(fetch['key_in_header'], 'test-key')
        self.assertNotIn('test-key', fetch['url'], 'APIキーがURLに載っています')
        self.assertNotIn('test-key', fetch['payload'], 'APIキーが本文に載っています')


class TestClientHandlesTheNewFields(unittest.TestCase):
    """画面側が、案内と二重押し防止を正しく扱うこと。"""

    @classmethod
    def setUpClass(cls):
        cls.script = html_script('Client.html')

    def test_button_is_disabled_and_relabelled_while_generating(self):
        body = strip_comments(function_body(self.script, 'setAiWaiting'))
        self.assertRegex(body, r"\$\('aiExplainBtn'\)|btn", 'ボタンを操作していません')
        self.assertIn('disabled = true', body, '生成中にボタンを無効化していません')
        self.assertIn('生成中', body, '生成中であることをボタンに出していません')

    def test_button_is_restored_after_a_failure(self):
        body = strip_comments(function_body(self.script, 'showAiExplanation'))
        failure = body.split('if (!res || !res.ok)')[1]
        self.assertIn('disabled = false', body, '失敗のあとボタンが押せなくなります')
        self.assertIn('AIでさらに噛み砕く', failure,
                      '失敗のあとボタンが「生成中…」のままになります')

    def test_notice_is_shown_from_the_server(self):
        body = strip_comments(function_body(self.script, 'showAiExplanation'))
        self.assertIn('setAiNotice(res.notice', body,
                      'サーバーからの案内を画面に出していません')
        self.assertIn('aiNotice', read('Index.html'), '案内を出す場所がありません')

    def test_notice_is_cleared_between_questions(self):
        body = strip_comments(function_body(self.script, 'resetAiExplanation'))
        self.assertIn("setAiNotice('')", body,
                      '前の問題の案内が次の問題まで残ります')

    def test_client_does_not_read_raw_api_details(self):
        stripped = strip_comments(self.script)
        for forbidden in ('quota_scope', 'retry_seconds', 'error.details'):
            self.assertNotIn(forbidden, stripped,
                             f'画面側がAPIの内部情報（{forbidden}）を見ています')


class TestLearningCoreIsUntouched(unittest.TestCase):
    """AI改修が、採点・理解度・学習ログへ広がっていないこと。"""

    def test_new_functions_do_not_touch_learning_sheets(self):
        for name in ('callGeminiWithFallback_', 'aiRememberQuotaBlock_',
                     'aiQuotaBlockState_', 'aiNextQuotaResetAt_',
                     'aiClassifyQuotaError_', 'aiModelIsBlocked_',
                     'aiEnsureSheetColumns_'):
            body = strip_comments(function_body(CODE, name))
            for forbidden in ('SHEETS.LOG', 'SHEETS.MINDMAP', 'SHEETS.QUESTIONS',
                              '02_マインドマップ', '04_学習ログ', '03_問題台帳',
                              'submitAnswer', 'mastery'):
                self.assertNotIn(forbidden, body,
                                 f'{name} が学習側（{forbidden}）に触れています')

    def test_quota_state_is_kept_outside_the_spreadsheet(self):
        body = strip_comments(function_body(CODE, 'aiRememberQuotaBlock_'))
        self.assertIn('setProperty(', body,
                      '利用枠の状態をスクリプトプロパティへ残していません')
        self.assertNotIn('getRange', body, '利用枠の状態をシートへ書いています')

    def test_answer_path_does_not_know_about_the_fallback(self):
        for name in ('submitAnswer', 'getNextQuestion'):
            body = strip_comments(function_body(CODE, name))
            for forbidden in ('callGemini', 'AI_CONFIG', 'aiQuota'):
                self.assertNotIn(forbidden, body,
                                 f'{name} がAI側の都合を見ています')


if __name__ == '__main__':
    unittest.main()
