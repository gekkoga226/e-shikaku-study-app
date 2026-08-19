"""回答処理の速さの守り（回答してから理解度が出るまでの待ち時間）。

守っている約束:

  - 04_学習ログの空き行探しで、getMaxRows() ぶんの空行まで舐めない。
    それでいて、書き込む行は今までと同じ行であること。
  - 1回の回答でスプレッドシートを何度も開かない。
  - 02_マインドマップの同じ行へ、用のたびに往復しない。
  - 次回復習日(P列)は、値が変わるときだけ書く。
  - そのうえで、理解度v2の確定手順
    「回答前のM列を読む → 04_学習ログへ書く → flush() → M列を読み直す」
    が崩れていないこと。

静的検査ではなく、Nodeで submitAnswer を丸ごと動かして
「どのシートへ、どの順番で、何回さわったか」を記録して確かめる。
スプレッドシートへは接続しない。
"""

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from gas_source import read  # noqa: E402
from js_runtime import collect, gas_bundle, js_value, require_node, run  # noqa: E402

CODE = read('Code.gs')

# 03_問題台帳の列並び（実データと同じく、解説が途中に挟まる）
QUESTION_HEADERS = [
    'question_id', 'question_text', 'option_a', 'option_b', 'option_c', 'option_d',
    'correct_option', 'answer_type', 'explanation_plain', 'explanation_formal',
    'explanation_calculation', 'explanation_options', 'answer_evidence',
    'primary_node_id', 'secondary_node_ids', 'difficulty', 'verification_status',
    'active', 'question_image_refs', 'source_url',
]

MINDMAP_HEADERS = [
    'node_id', 'level', 'parent_id', 'major_area', 'topic', 'weight',
    'required_unique_questions', 'primary_unique_answered_count',
    'latest_unique_correct_count', 'weighted_accuracy', 'coverage_pct',
    'confidence_pct', 'mastery_pct', 'mastery_level', 'last_answered_at',
    'next_review_at', 'progress_eligible',
]

LOG_HEADERS = [
    'attempt_id', 'answered_at', 'session_id', 'question_id', 'primary_node_id',
    'secondary_node_ids', 'mode', 'user_answer', 'correct_option_snapshot',
    'is_correct', 'confidence', 'response_seconds', 'error_type', 'difficulty',
    'verification_status', 'mastery_before', 'mastery_after', 'next_review_at',
    'write_status', 'notes', 'counts_for_mastery',
]

# 何をどの順にさわったかを記録する偽スプレッドシート。
FAKE_GOOGLE = """
const OPS = [];
const OPENS = { count: 0 };

function makeSheet(name, headers, rows, options) {
  options = options || {};
  const grid = [headers.slice()].concat(rows.map(r => r.slice()));
  let maxRows = options.maxRows || grid.length;
  const lastRowOverride = options.lastRow || 0;

  const cell = (r, c) => {
    while (grid.length < r) grid.push(headers.map(() => ''));
    const row = grid[r - 1];
    while (row.length < c) row.push('');
    return row;
  };

  return {
    getName: () => name,
    getLastRow: () => lastRowOverride || grid.length,
    getLastColumn: () => headers.length,
    getMaxRows: () => maxRows,
    insertRowsAfter: (after, count) => {
      OPS.push({ sheet: name, op: 'insertRowsAfter' });
      maxRows += count;
    },
    getRange(row, col, numRows, numCols) {
      numRows = numRows == null ? 1 : numRows;
      numCols = numCols == null ? 1 : numCols;
      const range = {
        getValues() {
          OPS.push({ sheet: name, op: 'getValues', row, col, numRows, numCols });
          const out = [];
          for (let r = 0; r < numRows; r++) {
            const line = [];
            for (let c = 0; c < numCols; c++) {
              const source = grid[row - 1 + r];
              const v = source ? source[col - 1 + c] : '';
              line.push(v === undefined ? '' : v);
            }
            out.push(line);
          }
          return out;
        },
        getValue() { return range.getValues()[0][0]; },
        getDisplayValues() { return range.getValues().map(r => r.map(String)); },
        getFormula() {
          OPS.push({ sheet: name, op: 'getFormula', row, col });
          return '';
        },
        setFormula(f) {
          OPS.push({ sheet: name, op: 'setFormula', row, col });
          cell(row, col)[col - 1] = f;
          return range;
        },
        setValues(values) {
          OPS.push({ sheet: name, op: 'setValues', row, col, numRows, numCols });
          for (let r = 0; r < values.length; r++) {
            const target = cell(row + r, col + values[r].length - 1);
            for (let c = 0; c < values[r].length; c++) target[col - 1 + c] = values[r][c];
          }
          return range;
        },
        setValue(v) {
          OPS.push({ sheet: name, op: 'setValue', row, col });
          cell(row, col)[col - 1] = v;
          return range;
        },
        clearContent() {
          OPS.push({ sheet: name, op: 'clearContent', row, col });
          cell(row, col)[col - 1] = '';
          return range;
        },
        setNumberFormat() {
          OPS.push({ sheet: name, op: 'setNumberFormat', row, col });
          return range;
        },
        setNumberFormats() {
          OPS.push({ sheet: name, op: 'setNumberFormats', row, col });
          return range;
        }
      };
      return range;
    },
    dump: () => grid
  };
}

const SHEETS = {
  '00_設定': makeSheet('00_設定', ['key', 'value'], SETTING_ROWS),
  '02_マインドマップ': makeSheet('02_マインドマップ', MINDMAP_HEADERS, MINDMAP_ROWS),
  '03_問題台帳': makeSheet('03_問題台帳', QUESTION_HEADERS, QUESTION_ROWS),
  '04_学習ログ': makeSheet('04_学習ログ', LOG_HEADERS, LOG_ROWS, LOG_OPTIONS)
};

const SpreadsheetApp = {
  openById() {
    OPENS.count += 1;
    OPS.push({ op: 'openById' });
    return { getName: () => 'テスト', getSheetByName: n => SHEETS[n] || null };
  },
  flush() { OPS.push({ op: 'flush' }); }
};

const CACHE = new Map();
const makeCache = () => ({
  get: k => (CACHE.has(k) ? CACHE.get(k) : null),
  put: (k, v) => CACHE.set(k, v),
  remove: k => CACHE.delete(k)
});
const CacheService = { getUserCache: makeCache, getScriptCache: makeCache };

const LockService = {
  getScriptLock: () => ({ tryLock: () => true, releaseLock: () => {} })
};

const Utilities = {
  formatDate: (d) => new Date(d).toISOString().slice(0, 19).replace('T', ' '),
  getUuid: () => 'abcdefgh-1234-5678-9012-abcdefabcdef'
};

// GAS側の console.warn / console.error は黙らせ、結果の出力だけ本物へ流す。
const console = {
  log: (...args) => process.stdout.write(args.join(' ') + String.fromCharCode(10)),
  warn: () => {},
  error: () => {}
};
"""


def question_row(qid='Q1', node_id='N1', correct='A'):
    values = {
        'question_id': qid,
        'question_text': '本文',
        'option_a': 'あ', 'option_b': 'い', 'option_c': 'う', 'option_d': 'え',
        'correct_option': correct,
        'answer_type': 'single',
        'explanation_plain': 'やさしい解説',
        'explanation_formal': '正式な解説',
        'primary_node_id': node_id,
        'secondary_node_ids': '',
        'difficulty': 'standard',
        'verification_status': 'verified',
        'active': True,
        'question_image_refs': '',
        'source_url': '',
    }
    return [values.get(h, '') for h in QUESTION_HEADERS]


def mindmap_row(node_id='N1', topic='論点A', mastery=40, next_review=''):
    values = {
        'node_id': node_id,
        'topic': topic,
        'major_area': '深層学習',
        'mastery_pct': mastery,
        'next_review_at': next_review,
        'progress_eligible': True,
    }
    return [values.get(h, '') for h in MINDMAP_HEADERS]


def submit(payload, question_rows=None, mindmap_rows=None, log_rows=None,
           log_max_rows=2000, log_last_row=0):
    """submitAnswer を丸ごと動かし、結果と操作の記録を返す。"""
    question_rows = question_rows if question_rows is not None else [question_row()]
    mindmap_rows = mindmap_rows if mindmap_rows is not None else [mindmap_row()]
    log_rows = log_rows if log_rows is not None else []

    script = '\n'.join([
        'const QUESTION_HEADERS = %s;' % js_value(QUESTION_HEADERS),
        'const MINDMAP_HEADERS = %s;' % js_value(MINDMAP_HEADERS),
        'const LOG_HEADERS = %s;' % js_value(LOG_HEADERS),
        'const QUESTION_ROWS = %s;' % js_value(question_rows),
        'const MINDMAP_ROWS = %s;' % js_value(mindmap_rows),
        'const LOG_ROWS = %s;' % js_value(log_rows),
        'const SETTING_ROWS = [["mastery_rule_version", "v2_2026-08-16"]];',
        'const LOG_OPTIONS = { maxRows: %d, lastRow: %d };' % (log_max_rows, log_last_row),
        FAKE_GOOGLE,
        gas_bundle(),
        'const result = submitAnswer(%s);' % js_value(payload),
        'console.log(JSON.stringify({',
        '  result: result,',
        '  ops: OPS,',
        '  opens: OPENS.count,',
        "  logGrid: SHEETS['04_学習ログ'].dump(),",
        "  mapGrid: SHEETS['02_マインドマップ'].dump()",
        '}));',
    ])
    return run(script)


ANSWER = {
    'questionId': 'Q1',
    'userAnswer': 'A',
    'confidence': 2,
    'responseSeconds': 12,
    'mode': 'learning',
    'sessionId': 'WEB-TEST',
}


def op_index(ops, predicate, label):
    for i, op in enumerate(ops):
        if predicate(op):
            return i
    raise AssertionError('操作が見つかりません: ' + label)


class TestMasteryOrderIsIntact(unittest.TestCase):
    """速くしても、理解度v2の確定手順が崩れていないこと。"""

    def setUp(self):
        require_node(self)

    def test_read_write_flush_read(self):
        out = submit(ANSWER)
        ops = out['ops']

        before = op_index(
            ops,
            lambda o: o.get('sheet') == '02_マインドマップ' and o['op'] == 'getValues'
            and o.get('col') == 13 and o.get('numCols') == 1,
            '回答前のM列読み取り',
        )
        write = op_index(
            ops,
            lambda o: o.get('sheet') == '04_学習ログ' and o['op'] == 'setValues'
            and o.get('numCols') == 20,
            '04_学習ログへの書き込み',
        )
        flush = op_index(ops, lambda o: o['op'] == 'flush', 'flush()')
        after = len(ops) - 1 - op_index(
            list(reversed(ops)),
            lambda o: o.get('sheet') == '02_マインドマップ' and o['op'] == 'getValues'
            and o.get('col') == 13 and o.get('numCols') == 1,
            '回答後のM列読み直し',
        )

        self.assertLess(before, write, '回答前の理解度読み取りが書き込みより後になっています')
        self.assertLess(write, flush, 'ログ書き込みの前に flush() が呼ばれています')
        self.assertLess(flush, after, 'flush() の前に理解度を読み直しています')

    def test_mastery_before_and_after_come_from_the_mindmap(self):
        out = submit(ANSWER, mindmap_rows=[mindmap_row(mastery=40)])
        self.assertEqual(out['result']['mastery_before'], 40)
        self.assertEqual(out['result']['mastery_after'], 40)

    def test_mastery_column_is_never_written(self):
        out = submit(ANSWER)
        for op in out['ops']:
            if op.get('sheet') == '02_マインドマップ' and op.get('col') == 13:
                self.assertEqual(op['op'], 'getValues',
                                 f'理解度のM列を書き換えています: {op}')

    def test_answer_is_graded_on_the_server(self):
        wrong = submit(dict(ANSWER, userAnswer='B'))
        self.assertFalse(wrong['result']['is_correct'])
        self.assertEqual(wrong['result']['correct_answer'], 'A')

        right = submit(ANSWER)
        self.assertTrue(right['result']['is_correct'])


class TestLogRowLookupIsBounded(unittest.TestCase):
    """空き行探しが、空行の海を舐めないこと。"""

    def setUp(self):
        require_node(self)

    def _next_row(self, column_a, max_rows, last_row):
        """findLogicalNextLogRow_ を、以前の実装と並べて動かす。"""
        script = '\n'.join([
            'const COLUMN_A = %s;' % js_value(column_a),
            'const MAX_ROWS = %d;' % max_rows,
            'const LAST_ROW = %d;' % last_row,
            'const READS = [];',
            'const sheet = {',
            '  getMaxRows: () => MAX_ROWS,',
            '  getLastRow: () => LAST_ROW,',
            '  getRange(row, col, numRows) {',
            '    return { getValues() {',
            '      READS.push({ row: row, numRows: numRows });',
            '      const out = [];',
            '      for (let i = 0; i < numRows; i++) {',
            '        const v = COLUMN_A[row - 1 + i];',
            '        out.push([v === undefined ? "" : v]);',
            '      }',
            '      return out;',
            '    } };',
            '  }',
            '};',
            collect(read('Code.gs'), ('findLogicalNextLogRow_',)),
            # 以前の実装（getMaxRows()ぶん全部読む）
            'function legacyNextRow(sheet) {',
            '  const max = sheet.getMaxRows();',
            '  const values = sheet.getRange(2, 1, max - 1, 1).getValues();',
            '  for (let i = 0; i < values.length; i++) {',
            '    if (values[i][0] === "" || values[i][0] == null) return i + 2;',
            '  }',
            '  return max + 1;',
            '}',
            'const actual = findLogicalNextLogRow_(sheet);',
            'const readsBefore = READS.slice();',
            'const legacy = legacyNextRow(sheet);',
            'console.log(JSON.stringify({',
            '  actual: actual, legacy: legacy, reads: readsBefore',
            '}));',
        ])
        return run(script)

    def test_scan_stops_at_the_data(self):
        # 1行目は見出し。データはA2〜A400まで、シートの物理行は2000。
        column_a = ['attempt_id'] + ['ATT-%d' % i for i in range(399)]
        out = self._next_row(column_a, max_rows=2000, last_row=400)

        self.assertEqual(out['actual'], 401, '書き込む行が変わっています')
        self.assertEqual(out['actual'], out['legacy'],
                         '以前の実装と違う行を選んでいます')
        read_rows = sum(r['numRows'] for r in out['reads'])
        self.assertLessEqual(
            read_rows, 400,
            f'空行まで{read_rows}行ぶん読んでいます（データは400行）',
        )

    def test_a_gap_in_the_middle_is_still_filled_first(self):
        """途中に空行があれば、以前と同じくそこへ書く。"""
        # 行1が見出し、行2・行3にデータ、行4が空、行5・行6にデータ。
        column_a = ['attempt_id', 'ATT-1', 'ATT-2', '', 'ATT-4', 'ATT-5']
        out = self._next_row(column_a, max_rows=1000, last_row=6)
        self.assertEqual(out['actual'], 4)
        self.assertEqual(out['actual'], out['legacy'])

    def test_empty_log_starts_at_row_two(self):
        out = self._next_row(['attempt_id'], max_rows=1000, last_row=1)
        self.assertEqual(out['actual'], 2)
        self.assertEqual(out['actual'], out['legacy'])

    def test_row_is_reused_when_the_sheet_has_no_spare_rows(self):
        column_a = ['attempt_id', 'ATT-1', 'ATT-2']
        out = self._next_row(column_a, max_rows=3, last_row=3)
        self.assertEqual(out['actual'], 4)
        self.assertEqual(out['actual'], out['legacy'])

    def test_submit_writes_to_the_first_empty_row(self):
        log_rows = [['ATT-%d' % i] + [''] * (len(LOG_HEADERS) - 1) for i in range(3)]
        out = submit(ANSWER, log_rows=log_rows, log_max_rows=2000, log_last_row=4)
        writes = [o for o in out['ops']
                  if o.get('sheet') == '04_学習ログ' and o['op'] == 'setValues']
        self.assertEqual(writes[0]['row'], 5, '論理的な次の空き行へ書いていません')
        self.assertEqual(out['logGrid'][4][3], 'Q1', '書いた行の question_id が違います')


class TestFewerRoundTrips(unittest.TestCase):
    """同じ結果を得るまでの往復を減らしていること。"""

    def setUp(self):
        require_node(self)

    def test_spreadsheet_is_opened_once(self):
        out = submit(ANSWER)
        self.assertEqual(out['opens'], 1,
                         f'1回の回答でスプレッドシートを{out["opens"]}回開いています')

    def test_mindmap_row_is_not_read_over_and_over(self):
        out = submit(ANSWER)
        reads = [o for o in out['ops']
                 if o.get('sheet') == '02_マインドマップ' and o['op'] == 'getValues']
        # node_id列の検索 / 回答前のM列 / 行まとめ読み / 回答後のM列 の4回まで
        self.assertLessEqual(
            len(reads), 4,
            f'02_マインドマップを{len(reads)}回読んでいます: {reads}',
        )

    def test_node_name_is_returned_without_an_extra_read(self):
        out = submit(ANSWER, mindmap_rows=[mindmap_row(topic='誤差逆伝播')])
        self.assertEqual(out['result']['node_name'], '誤差逆伝播')
        # E列(5)だけを狙った単独の読み取りが残っていないこと
        for op in out['ops']:
            if op.get('sheet') == '02_マインドマップ' and op['op'] == 'getValues':
                self.assertFalse(
                    op.get('col') == 5 and op.get('numCols') == 1,
                    '論点名のためだけに往復しています',
                )

    def test_total_sheet_calls_stay_small(self):
        out = submit(ANSWER)
        calls = [o for o in out['ops'] if o.get('sheet')]
        self.assertLessEqual(
            len(calls), 16,
            f'シートへの往復が{len(calls)}回あります: {calls}',
        )


class TestReviewDateIsWrittenOnlyWhenItChanges(unittest.TestCase):
    """次回復習日(P列)の書き込みが、必要なときだけ起きること。"""

    def setUp(self):
        require_node(self)

    def _p_column_ops(self, out):
        return [o for o in out['ops']
                if o.get('sheet') == '02_マインドマップ'
                and o.get('col') == 16
                and o['op'] in ('setValue', 'setValues', 'clearContent')]

    def test_no_write_when_the_review_date_does_not_change(self):
        out = submit(ANSWER, mindmap_rows=[mindmap_row(next_review='')])
        self.assertEqual(self._p_column_ops(out), [],
                         '変わらない復習日をわざわざ書き直しています')
        self.assertEqual(out['result']['next_review_at'], '')

    def test_wrong_answer_sets_tomorrow(self):
        out = submit(dict(ANSWER, userAnswer='B'),
                     mindmap_rows=[mindmap_row(next_review='')])
        ops = self._p_column_ops(out)
        self.assertEqual(len(ops), 1, '誤答の翌日復習が書かれていません')
        self.assertEqual(ops[0]['op'], 'setValue')
        self.assertNotEqual(out['result']['next_review_at'], '')

    def test_due_review_is_cleared_after_a_correct_answer(self):
        out = submit(ANSWER, mindmap_rows=[mindmap_row(next_review='2026-08-01')])
        ops = self._p_column_ops(out)
        self.assertEqual([o['op'] for o in ops], ['clearContent'],
                         '消化した復習期限が消えていません')
        self.assertEqual(out['result']['next_review_at'], '')

    def test_future_review_is_kept_as_is(self):
        out = submit(ANSWER, mindmap_rows=[mindmap_row(next_review='2026-12-31')])
        self.assertEqual(self._p_column_ops(out), [],
                         'まだ来ていない復習期限を書き直しています')

    def test_last_answered_at_is_always_updated(self):
        out = submit(ANSWER)
        writes = [o for o in out['ops']
                  if o.get('sheet') == '02_マインドマップ'
                  and o.get('col') == 15 and o['op'] in ('setValue', 'setValues')]
        self.assertEqual(len(writes), 1, '最終回答日時が更新されていません')


class TestSettingLookupIsReused(unittest.TestCase):
    """00_設定の読み取りを、回答のたびに繰り返さないこと。"""

    def setUp(self):
        require_node(self)

    def test_second_answer_does_not_read_the_settings_sheet_again(self):
        script = '\n'.join([
            'const QUESTION_HEADERS = %s;' % js_value(QUESTION_HEADERS),
            'const MINDMAP_HEADERS = %s;' % js_value(MINDMAP_HEADERS),
            'const LOG_HEADERS = %s;' % js_value(LOG_HEADERS),
            'const QUESTION_ROWS = %s;' % js_value([question_row()]),
            'const MINDMAP_ROWS = %s;' % js_value([mindmap_row()]),
            'const LOG_ROWS = [];',
            'const SETTING_ROWS = [["mastery_rule_version", "v2_2026-08-16"]];',
            'const LOG_OPTIONS = { maxRows: 100, lastRow: 1 };',
            FAKE_GOOGLE,
            gas_bundle(),
            'submitAnswer(%s);' % js_value(ANSWER),
            'const firstCount = OPS.filter(o => o.sheet === "00_設定").length;',
            'OPS.length = 0;',
            'submitAnswer(%s);' % js_value(ANSWER),
            'const secondCount = OPS.filter(o => o.sheet === "00_設定").length;',
            'console.log(JSON.stringify({ first: firstCount, second: secondCount }));',
        ])
        out = run(script)
        self.assertGreaterEqual(out['first'], 1, '1回目は設定を読む必要があります')
        self.assertEqual(out['second'], 0,
                         '2回目の回答でも00_設定を読み直しています')

    def test_notes_still_record_the_rule_version(self):
        out = submit(ANSWER)
        notes = out['logGrid'][1][19]  # T列 notes
        self.assertIn('mastery_rule_version=v2_2026-08-16', notes,
                      '学習ログのnotesから理解度ルール版が消えています')


class TestWrittenRowIsUnchanged(unittest.TestCase):
    """書き込む内容そのものは今までと同じであること。"""

    def setUp(self):
        require_node(self)

    def test_log_row_keeps_its_layout(self):
        out = submit(ANSWER, mindmap_rows=[mindmap_row(mastery=40)])
        row = out['logGrid'][1]
        self.assertEqual(row[3], 'Q1', 'D列 question_id')
        self.assertEqual(row[4], 'N1', 'E列 primary_node_id')
        self.assertEqual(row[6], 'learning', 'G列 mode')
        self.assertEqual(row[7], 'A', 'H列 user_answer')
        self.assertEqual(row[8], 'A', 'I列 correct_option')
        self.assertEqual(row[9], True, 'J列 is_correct')
        self.assertEqual(row[10], 2, 'K列 confidence')
        self.assertEqual(row[12], 'none', 'M列 error_type')
        self.assertEqual(row[14], 'verified', 'O列 verification_status')
        self.assertEqual(row[15], 40, 'P列 mastery_before')
        self.assertEqual(row[16], 40, 'Q列 mastery_after')
        self.assertEqual(row[18], 'success', 'S列 write_status')

    def test_counts_for_mastery_formula_is_added_when_missing(self):
        out = submit(ANSWER)
        formulas = [o for o in out['ops']
                    if o.get('sheet') == '04_学習ログ' and o['op'] == 'setFormula']
        self.assertEqual(len(formulas), 1, 'U列の数式が補われていません')
        self.assertEqual(formulas[0]['col'], 21)

    def test_unanswered_count_cache_is_dropped(self):
        out = submit(ANSWER)
        self.assertTrue(out['result']['ok'])
        # キャッシュ削除は結果に出ないので、例外なく完走したことを確認する。
        self.assertEqual(out['result']['question_id'], 'Q1')


if __name__ == '__main__':
    unittest.main(verbosity=2)
