"""生成したAI解説を使い回す仕組みの守り。

守っている約束:

  - 同じ問題を同じ選択肢で答えたら、作り直さずに残してあるものを出す。
    （CacheService のキーは attempt_id で回答のたびに変わるため、
      これまでは解き直すたびに必ず作り直しになっていた）
  - 残す単位は question_id × user_answer。自信度は含めない
    （システム指示が自信度への言及を禁じているので本文に出ない）。
  - 問題文・選択肢・登録解説が書き換わったら、古い解説を出し続けない。
  - 途中で切れた解説は残さない。
  - 「もう一度作る」は残してあるものを無視して作り直し、行を増やさず上書きする。
  - 書き込むのは 07_AI解説キャッシュ だけ。02/03/04 には触れない。
  - 本文の列を全行まとめて読まない（1セルが数千文字になるため）。

Nodeで実際に関数を動かし、Geminiを何回呼んだか・どのシートのどこを読み書きしたかを
記録して確かめる。スプレッドシートにもGeminiにも接続しない。
"""

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from gas_source import html_script, read, strip_comments  # noqa: E402
from js_runtime import gas_bundle, js_value, require_node, run  # noqa: E402

CODE = read('Code.gs')
CLIENT = html_script('Client.html')

LOG_HEADERS = [
    'attempt_id', 'answered_at', 'session_id', 'question_id', 'primary_node_id',
    'secondary_node_ids', 'mode', 'user_answer', 'correct_answer', 'is_correct',
    'confidence', 'response_seconds', 'error_type', 'difficulty',
    'verification_status', 'mastery_before', 'mastery_after', 'next_review_at',
    'write_status', 'notes', 'counts_for_mastery',
]

QUESTION_HEADERS = [
    'question_id', 'question_text', 'question_image_refs',
    'option_a', 'option_b', 'option_c', 'option_d',
    'correct_option', 'answer_type',
    'explanation_plain', 'explanation_formal',
    'explanation_calculation', 'explanation_options',
    'primary_node_id', 'verification_status', 'active',
]

CACHE_HEADERS = ['question_id', 'user_answer', 'source_signature',
                 'model', 'generated_at', 'explanation_text']

# Google側のAPIを、呼び出しを記録する偽物へ置き換える。
FAKE_ENV = """
const READS = [];
const WRITES = [];
const GEMINI_CALLS = [];
let GEMINI_REPLY = { text: '生成された解説です。', truncated: false };

function makeSheet(name, headers, rows) {
  const grid = [headers.slice()].concat(rows.map(r => r.slice()));
  return {
    _grid: grid,
    getName: () => name,
    getLastRow: () => {
      for (let r = grid.length - 1; r >= 0; r--) {
        if (grid[r] && String(grid[r][0] || '') !== '') return r + 1;
      }
      return 0;
    },
    getLastColumn: () => headers.length,
    getMaxRows: () => grid.length,
    setFrozenRows: () => {},
    getRange(row, col, numRows, numCols) {
      numRows = numRows == null ? 1 : numRows;
      numCols = numCols == null ? 1 : numCols;
      return {
        getValues() {
          READS.push({ sheet: name, row, col, numRows, numCols });
          const out = [];
          for (let r = 0; r < numRows; r++) {
            const line = [];
            for (let c = 0; c < numCols; c++) {
              const src = grid[row - 1 + r];
              const v = src ? src[col - 1 + c] : '';
              line.push(v === undefined ? '' : v);
            }
            out.push(line);
          }
          return out;
        },
        getValue() { return this.getValues()[0][0]; },
        getDisplayValues() { return this.getValues().map(r => r.map(String)); },
        getFormula: () => '',
        setValues(values) {
          WRITES.push({ sheet: name, row, col, numRows, numCols });
          for (let r = 0; r < values.length; r++) {
            while (grid.length <= row - 1 + r) grid.push(new Array(headers.length).fill(''));
            for (let c = 0; c < values[r].length; c++) {
              grid[row - 1 + r][col - 1 + c] = values[r][c];
            }
          }
          return this;
        },
        setValue(v) { return this.setValues([[v]]); },
        setNumberFormat() { return this; }
      };
    }
  };
}

const SHEETS = {
  '03_問題台帳': makeSheet('03_問題台帳', QUESTION_HEADERS, QUESTION_ROWS),
  '04_学習ログ': makeSheet('04_学習ログ', LOG_HEADERS, LOG_ROWS)
};
if (typeof CACHE_ROWS !== 'undefined' && CACHE_ROWS !== null) {
  SHEETS['07_AI解説キャッシュ'] = makeSheet('07_AI解説キャッシュ', CACHE_HEADERS, CACHE_ROWS);
}

const INSERTED = [];
const SpreadsheetApp = {
  openById: () => ({
    getSheetByName: n => SHEETS[n] || null,
    getNumSheets: () => Object.keys(SHEETS).length,
    insertSheet: (n, pos) => {
      INSERTED.push(n);
      SHEETS[n] = makeSheet(n, CACHE_HEADERS, []);
      // 見出し行は本体側が書く。ここでは空の表だけ用意する。
      SHEETS[n]._grid.length = 0;
      SHEETS[n]._grid.push(new Array(CACHE_HEADERS.length).fill(''));
      return SHEETS[n];
    }
  }),
  flush: () => {}
};

const CACHE = new Map();
const makeCache = () => ({
  get: k => (CACHE.has(k) ? CACHE.get(k) : null),
  put: (k, v) => { CACHE.set(k, v); },
  remove: k => { CACHE.delete(k); }
});
const CacheService = { getUserCache: makeCache, getScriptCache: makeCache };

const PropertiesService = {
  getScriptProperties: () => ({ getProperty: k => (k === 'GEMINI_API_KEY' ? 'test-key' : '') })
};

const Utilities = {
  formatDate: (d) => new Date(d).toISOString().slice(0, 19).replace('T', ' '),
  getUuid: () => 'uuid',
  DigestAlgorithm: { SHA_256: 'SHA_256' },
  Charset: { UTF_8: 'UTF_8' },
  // 実物と同じ「入力が変われば結果も変わる」性質だけを持つ簡易ダイジェスト。
  computeDigest: (algo, text) => {
    const out = [];
    for (let i = 0; i < 32; i++) {
      let h = i * 31 + 7;
      for (let j = 0; j < text.length; j++) h = (h * 33 + text.charCodeAt(j) + i) % 251;
      out.push(h);
    }
    return out;
  }
};

const console = {
  log: (...a) => process.stdout.write(a.join(' ') + String.fromCharCode(10)),
  warn: () => {},
  error: () => {}
};

function cacheSheetRows() {
  const sh = SHEETS['07_AI解説キャッシュ'];
  if (!sh) return [];
  return sh._grid.slice(1).filter(r => String(r[0] || '') !== '');
}
"""


# 本体を読み込んだあとで差し替える。宣言し直すと二重定義になるため代入で上書きする。
GEMINI_STUB = """
callGeminiGenerateContent_ = function (model, apiKey, parts) {
  GEMINI_CALLS.push({ model: model, parts: parts });
  if (GEMINI_REPLY.fail) return aiFailure_('AI_TEST_FAILURE', 'テスト用の失敗');
  return { ok: true, text: GEMINI_REPLY.text, truncated: GEMINI_REPLY.truncated === true };
};
"""


def log_row(attempt_id, question_id='Q1', user_answer='A', confidence=3):
    values = {
        'attempt_id': attempt_id,
        'answered_at': '2026-08-19 10:00:00',
        'session_id': 'S1',
        'question_id': question_id,
        'primary_node_id': 'N1',
        'mode': 'learning',
        'user_answer': user_answer,
        'correct_answer': 'A',
        'is_correct': user_answer == 'A',
        'confidence': confidence,
        'difficulty': 'standard',
        'verification_status': 'verified',
        'write_status': 'success',
        'counts_for_mastery': True,
    }
    return [values.get(h, '') for h in LOG_HEADERS]


def question_row(question_id='Q1', text='問題の本文です。', explanation=''):
    values = {
        'question_id': question_id,
        'question_text': text,
        'option_a': '選択肢A', 'option_b': '選択肢B',
        'option_c': '選択肢C', 'option_d': '選択肢D',
        'correct_option': 'A',
        'answer_type': 'single',
        'explanation_plain': explanation,
        'primary_node_id': 'N1',
        'verification_status': 'verified',
        'active': True,
    }
    return [values.get(h, '') for h in QUESTION_HEADERS]


def run_ai(driver, log_rows=None, question_rows=None, cache_rows=None):
    """AI解説まわりを実際に動かし、呼び出しと読み書きの記録を返す。"""
    script = '\n'.join([
        'const LOG_HEADERS = %s;' % js_value(LOG_HEADERS),
        'const QUESTION_HEADERS = %s;' % js_value(QUESTION_HEADERS),
        'const CACHE_HEADERS = %s;' % js_value(CACHE_HEADERS),
        'const LOG_ROWS = %s;' % js_value(log_rows if log_rows is not None else [log_row('ATT-1')]),
        'const QUESTION_ROWS = %s;' % js_value(
            question_rows if question_rows is not None else [question_row()]),
        'const CACHE_ROWS = %s;' % js_value(cache_rows),
        FAKE_ENV,
        gas_bundle(),
        GEMINI_STUB,
        driver,
    ])
    return run(script)


class TestSameAnswerIsNotRegenerated(unittest.TestCase):
    """同じ問題を同じ選択肢で答えたら作り直さないこと。"""

    def setUp(self):
        require_node(self)

    def test_second_attempt_reuses_the_saved_explanation(self):
        """回答が別でも（attempt_idが変わっても）作り直さない。"""
        out = run_ai("""
            const first = getAiExplanation({ attemptId: 'ATT-1' });
            // 別の回で同じ問題を同じ選択肢で答えた場面を作る。
            CACHE.clear();
            const second = getAiExplanation({ attemptId: 'ATT-2' });
            console.log(JSON.stringify({
              calls: GEMINI_CALLS.length,
              first: { ok: first.ok, source: first.source, text: first.text },
              second: { ok: second.ok, source: second.source, text: second.text }
            }));
        """, log_rows=[log_row('ATT-1'), log_row('ATT-2')], cache_rows=[])

        self.assertTrue(out['first']['ok'])
        self.assertTrue(out['second']['ok'])
        self.assertEqual(out['first']['source'], 'generated')
        self.assertEqual(out['second']['source'], 'sheet',
                         '2回目が残してある解説を使っていません')
        self.assertEqual(out['second']['text'], out['first']['text'])
        self.assertEqual(out['calls'], 1,
                         f"Geminiを{out['calls']}回呼んでいます（1回で足りるはず）")

    def test_confidence_does_not_prevent_reuse(self):
        """自信度が違っても使い回すこと（本文には出ない値のため）。"""
        out = run_ai("""
            getAiExplanation({ attemptId: 'ATT-1' });
            CACHE.clear();
            const second = getAiExplanation({ attemptId: 'ATT-2' });
            console.log(JSON.stringify({ calls: GEMINI_CALLS.length, source: second.source }));
        """, log_rows=[log_row('ATT-1', confidence=3), log_row('ATT-2', confidence=1)],
             cache_rows=[])

        self.assertEqual(out['source'], 'sheet', '自信度の違いで作り直しています')
        self.assertEqual(out['calls'], 1)

    def test_different_answer_is_generated_separately(self):
        """選んだ選択肢が違えば別の解説を作ること。"""
        out = run_ai("""
            getAiExplanation({ attemptId: 'ATT-1' });
            CACHE.clear();
            const other = getAiExplanation({ attemptId: 'ATT-B' });
            console.log(JSON.stringify({
              calls: GEMINI_CALLS.length,
              source: other.source,
              rows: cacheSheetRows().map(r => [r[0], r[1]])
            }));
        """, log_rows=[log_row('ATT-1', user_answer='A'), log_row('ATT-B', user_answer='B')],
             cache_rows=[])

        self.assertEqual(out['source'], 'generated',
                         '違う選択肢なのに他の選択肢の解説を出しています')
        self.assertEqual(out['calls'], 2)
        self.assertEqual(sorted(out['rows']), [['Q1', 'A'], ['Q1', 'B']])

    def test_memory_cache_still_answers_first(self):
        """同じ回答の連打では、シートまで読みに行かないこと。"""
        out = run_ai("""
            getAiExplanation({ attemptId: 'ATT-1' });
            READS.length = 0;
            const again = getAiExplanation({ attemptId: 'ATT-1' });
            console.log(JSON.stringify({ source: again.source, reads: READS.length }));
        """, cache_rows=[])

        self.assertEqual(out['source'], 'memory')
        self.assertEqual(out['reads'], 0, '連打のたびにシートを読んでいます')


class TestSavedExplanationGoesStale(unittest.TestCase):
    """問題が書き換わったら、古い解説を出し続けないこと。"""

    def setUp(self):
        require_node(self)

    def test_changed_question_text_forces_regeneration(self):
        out = run_ai("""
            getAiExplanation({ attemptId: 'ATT-1' });
            CACHE.clear();
            // 問題文が書き換わった場面を作る。
            SHEETS['03_問題台帳']._grid[1][1] = '書き換えられた本文です。';
            const after = getAiExplanation({ attemptId: 'ATT-2' });
            console.log(JSON.stringify({ calls: GEMINI_CALLS.length, source: after.source }));
        """, log_rows=[log_row('ATT-1'), log_row('ATT-2')], cache_rows=[])

        self.assertEqual(out['source'], 'generated',
                         '問題文が変わったのに古い解説を出しています')
        self.assertEqual(out['calls'], 2)

    def test_added_registered_explanation_forces_regeneration(self):
        out = run_ai("""
            getAiExplanation({ attemptId: 'ATT-1' });
            CACHE.clear();
            // 登録解説があとから入った場面を作る（プロンプトの材料が変わる）。
            const col = QUESTION_HEADERS.indexOf('explanation_plain');
            SHEETS['03_問題台帳']._grid[1][col] = 'あとから登録された解説。';
            const after = getAiExplanation({ attemptId: 'ATT-2' });
            console.log(JSON.stringify({ calls: GEMINI_CALLS.length, source: after.source }));
        """, log_rows=[log_row('ATT-1'), log_row('ATT-2')], cache_rows=[])

        self.assertEqual(out['source'], 'generated',
                         '登録解説が増えたのに古い解説を出しています')
        self.assertEqual(out['calls'], 2)


class TestRegenerateIgnoresWhatIsSaved(unittest.TestCase):
    """「もう一度作る」の挙動。"""

    def setUp(self):
        require_node(self)

    def test_force_calls_gemini_again(self):
        out = run_ai("""
            getAiExplanation({ attemptId: 'ATT-1' });
            GEMINI_REPLY = { text: '作り直した解説です。', truncated: false };
            const again = getAiExplanation({ attemptId: 'ATT-1', force: true });
            console.log(JSON.stringify({
              calls: GEMINI_CALLS.length, text: again.text, source: again.source
            }));
        """, cache_rows=[])

        self.assertEqual(out['calls'], 2, '「もう一度作る」で作り直していません')
        self.assertEqual(out['text'], '作り直した解説です。')
        self.assertEqual(out['source'], 'generated')

    def test_force_overwrites_instead_of_adding_a_row(self):
        out = run_ai("""
            getAiExplanation({ attemptId: 'ATT-1' });
            GEMINI_REPLY = { text: '作り直した解説です。', truncated: false };
            getAiExplanation({ attemptId: 'ATT-1', force: true });
            console.log(JSON.stringify({
              rows: cacheSheetRows().length,
              text: cacheSheetRows()[0][5]
            }));
        """, cache_rows=[])

        self.assertEqual(out['rows'], 1, '作り直すたびに行が増えています')
        self.assertEqual(out['text'], '作り直した解説です。', '古い解説が残っています')


class TestTruncatedIsNotSaved(unittest.TestCase):
    """途中で切れた解説を残さないこと。"""

    def setUp(self):
        require_node(self)

    def test_truncated_result_is_not_written(self):
        out = run_ai("""
            GEMINI_REPLY = { text: '途中で切れた解', truncated: true };
            const res = getAiExplanation({ attemptId: 'ATT-1' });
            console.log(JSON.stringify({
              truncated: res.truncated, rows: cacheSheetRows().length
            }));
        """, cache_rows=[])

        self.assertTrue(out['truncated'])
        self.assertEqual(out['rows'], 0, '切れた解説を残しています')

    def test_failed_generation_is_not_written(self):
        out = run_ai("""
            GEMINI_REPLY = { fail: true };
            const res = getAiExplanation({ attemptId: 'ATT-1' });
            console.log(JSON.stringify({ ok: res.ok, rows: cacheSheetRows().length }));
        """, cache_rows=[])

        self.assertFalse(out['ok'])
        self.assertEqual(out['rows'], 0, '失敗した結果を残しています')


class TestCacheSheetIsSafe(unittest.TestCase):
    """保存先が学習データを壊さないこと。"""

    def setUp(self):
        require_node(self)

    def test_only_the_cache_sheet_is_written(self):
        out = run_ai("""
            getAiExplanation({ attemptId: 'ATT-1' });
            console.log(JSON.stringify({
              written: Array.from(new Set(WRITES.map(w => w.sheet)))
            }));
        """, cache_rows=[])

        self.assertEqual(out['written'], ['07_AI解説キャッシュ'],
                         '学習データのシートへ書き込んでいます')

    def test_sheet_is_created_when_missing(self):
        out = run_ai("""
            const res = getAiExplanation({ attemptId: 'ATT-1' });
            console.log(JSON.stringify({
              ok: res.ok, inserted: INSERTED, rows: cacheSheetRows().length
            }));
        """, cache_rows=None)

        self.assertTrue(out['ok'], 'シートが無いとAI解説が出せなくなっています')
        self.assertEqual(out['inserted'], ['07_AI解説キャッシュ'])
        self.assertEqual(out['rows'], 1)

    def test_works_when_the_sheet_cannot_be_created(self):
        """シートを作れない環境でも、これまでどおり動くこと。"""
        out = run_ai("""
            const ss = SpreadsheetApp.openById();
            SpreadsheetApp.openById = () => ({
              getSheetByName: n => SHEETS[n] || null,
              insertSheet: () => { throw new Error('作れません'); }
            });
            SPREADSHEET_HANDLE_ = null;
            const res = getAiExplanation({ attemptId: 'ATT-1' });
            console.log(JSON.stringify({ ok: res.ok, text: res.text }));
        """, cache_rows=None)

        self.assertTrue(out['ok'], 'シートを作れないとAI解説が止まっています')
        self.assertEqual(out['text'], '生成された解説です。')

    def test_key_scan_reads_only_the_short_columns(self):
        """本文の列を全行まとめて読まないこと。"""
        out = run_ai("""
            getAiExplanation({ attemptId: 'ATT-1' });
            CACHE.clear();
            READS.length = 0;
            getAiExplanation({ attemptId: 'ATT-2' });
            const bulk = READS.filter(r => r.sheet === '07_AI解説キャッシュ' && r.numRows > 1);
            console.log(JSON.stringify({
              widest: bulk.reduce((m, r) => Math.max(m, r.col + r.numCols - 1), 0)
            }));
        """, log_rows=[log_row('ATT-1'), log_row('ATT-2')], cache_rows=[])

        self.assertLessEqual(out['widest'], 3,
                             '複数行の読み取りが本文の列（6列目）まで届いています')


class TestPreviouslyAnsweredFlag(unittest.TestCase):
    """「2回目以降の問題か」を、読み取りを増やさずに伝えていること。"""

    def test_flag_comes_from_the_existing_log_map(self):
        body = strip_comments(CODE)
        start = body.index('function getNextQuestion(')
        end = body.index('\nfunction ', start + 1)
        section = body[start:end]
        self.assertIn('publicQuestion_(item.q, node, mode, !!latest[', section,
                      '出題側が既に作っている正式回答の表を使っていません')

    def test_unanswered_mode_reports_first_time(self):
        body = strip_comments(CODE)
        start = body.index('function nextUnansweredQuestion_(')
        end = body.index('\nfunction ', start + 1)
        self.assertIn("publicQuestion_(picked, node, 'unanswered', false)", body[start:end],
                      '未回答モードの問題が「解き直し」扱いになっています')

    def test_flag_is_emitted_for_both_cases(self):
        """実際に動かして、旗が正しく出ることを確かめる。"""
        out = run_ai("""
            const q = { question_id: 'Q1', question_text: '本文', correct_option: 'A',
                        option_a: 'A', option_b: 'B', primary_node_id: 'N1' };
            const node = { topic: '論点', major_area: 'ジャンル', mastery_pct: 10, mastery_level: 'B' };
            console.log(JSON.stringify({
              repeat: publicQuestion_(q, node, 'learning', true).previously_answered,
              first: publicQuestion_(q, node, 'learning', false).previously_answered,
              missing: publicQuestion_(q, node, 'learning').previously_answered,
              leaks: Object.keys(publicQuestion_(q, node, 'learning', true))
                .filter(k => k.indexOf('correct') >= 0)
            }));
        """, cache_rows=[])

        self.assertIs(out['repeat'], True, '解き直しの問題が初回扱いになっています')
        self.assertIs(out['first'], False, '初回の問題が解き直し扱いになっています')
        # 渡し忘れたときは「解き直し」＝先読みしない側へ倒れること。
        # 逆に倒すと、押されないAI解説を黙って作り続けてしまう。
        self.assertIs(out['missing'], True)
        self.assertEqual(out['leaks'], [], '回答前の応答へ正解を載せています')

    def test_flag_does_not_leak_the_answer(self):
        body = strip_comments(CODE)
        start = body.index('function publicQuestion_(')
        end = body.index('\nfunction ', start + 1)
        section = body[start:end]
        self.assertIn('previously_answered', section)
        self.assertNotIn('correct_option', section,
                         '回答前の応答へ正解を載せています')


class TestClientPrefetchRules(unittest.TestCase):
    """先読みの条件と、二重生成の防止。"""

    @classmethod
    def setUpClass(cls):
        cls.script = strip_comments(CLIENT)

    def _body(self, name):
        start = self.script.index('function %s(' % name)
        end = self.script.index('\n  function ', start + 1)
        return self.script[start:end]

    def test_prefetch_skips_repeated_questions(self):
        body = self._body('shouldPrefetchAi')
        self.assertIn('previouslyAnswered !== false', body,
                      '2回目以降の問題でも先読みしています')

    def test_prefetch_skips_questions_with_registered_explanations(self):
        body = self._body('shouldPrefetchAi')
        self.assertIn('hasRegisteredExplanation(result)', body,
                      '登録解説がある問題でも先読みしています')

    def test_tapping_during_prefetch_does_not_generate_twice(self):
        body = self._body('requestAiExplanation')
        self.assertIn("slot.status === 'loading'", body,
                      '先読みの最中に押されたときの待ち合わせがありません')
        self.assertIn('state.aiWanted = true', body,
                      '押されたことを覚えていないため、二重に生成します')

    def test_prefetch_result_is_dropped_when_the_question_changes(self):
        body = self._body('resetAiExplanation')
        self.assertIn('clearAiPrefetch()', body,
                      '前の問題の先読み結果を持ち越しています')

    def test_regenerate_is_sent_as_force(self):
        body = self._body('requestAiExplanation')
        self.assertIn('force: regenerate', body,
                      '「もう一度作る」がサーバーへ伝わっていません')

    def test_source_is_shown_honestly(self):
        body = self._body('aiSourceLabel')
        self.assertIn('保存済み', body, '再表示であることを画面に出していません')


if __name__ == '__main__':
    unittest.main()
