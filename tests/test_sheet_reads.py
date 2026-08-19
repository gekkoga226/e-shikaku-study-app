"""シート読み取りの重さの守り。

守っている約束:

  - 出題や残数の集計で、03_問題台帳の解説（explanation_*）のような
    長い列まで毎回読まない。読む量が増えるとホーム画面が返ってこなくなる。
  - 必要な列だけを、隣り合う列はまとめて読む。
  - 1回の呼び出しでスプレッドシートを何度も開かない。

readColumns_ は新しい読み取り処理なので、静的検査だけでなく
実際にNodeで動かして「どの範囲を読んだか」まで確かめる。
"""

import re
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from gas_source import function_body, read, strip_comments, strip_literals  # noqa: E402
from js_runtime import collect, js_value, require_node, run  # noqa: E402

CODE = read('Code.gs')

APP_CONFIG_LITERAL = re.search(
    r'const APP_CONFIG = Object\.freeze\(\{[\s\S]*?\n\}\);', CODE
).group(0)

READER_FUNCTIONS = ('readColumns_', 'headerMap_', 'lastLogicalNonEmptyRowInColumnA_')

# 読み取り範囲を記録する偽シート。実際のスプレッドシートへは接続しない。
FAKE_SHEET = """
function makeSheet(grid) {
  const reads = [];
  const sheet = {
    reads: reads,
    getLastRow: () => grid.length,
    getLastColumn: () => grid[0].length,
    getMaxRows: () => grid.length,
    getRange(row, col, numRows, numCols) {
      return {
        getValues() {
          reads.push({ row: row, col: col, numRows: numRows, numCols: numCols });
          const out = [];
          for (let r = 0; r < numRows; r++) {
            const line = [];
            for (let c = 0; c < numCols; c++) {
              line.push(grid[row - 1 + r][col - 1 + c]);
            }
            out.push(line);
          }
          return out;
        }
      };
    }
  };
  return sheet;
}
"""


def run_reader(grid, headers, sheet_name='03_問題台帳'):
    """readColumns_ を実際に動かし、結果と読み取り範囲を返す。"""
    script = '\n'.join([
        APP_CONFIG_LITERAL,
        FAKE_SHEET,
        'const SHEET = makeSheet(%s);' % js_value(grid),
        'function spreadsheet_() { return { getSheetByName: () => SHEET }; }',
        collect(CODE, READER_FUNCTIONS),
        'const rows = readColumns_(%s, %s);' % (js_value(sheet_name), js_value(headers)),
        'console.log(JSON.stringify({ rows: rows, reads: SHEET.reads }));',
    ])
    return run(script)


# 実データに近い並び。解説は問題文と正解のあいだに挟まっている。
GRID = [
    ['question_id', 'question_text', 'explanation_plain', 'correct_option', 'active'],
    ['Q1', '問題1の本文', 'とても長い解説' * 200, 'A', True],
    ['Q2', '問題2の本文', 'とても長い解説' * 200, 'B', True],
    ['Q3', '問題3の本文', 'とても長い解説' * 200, 'C', False],
]


class TestReadColumns(unittest.TestCase):
    """必要な列だけを読めていること。"""

    def setUp(self):
        require_node(self)

    def test_returns_only_the_requested_columns(self):
        result = run_reader(GRID, ['question_id', 'correct_option'])
        self.assertEqual(
            [row['question_id'] for row in result['rows']], ['Q1', 'Q2', 'Q3']
        )
        self.assertEqual(
            [row['correct_option'] for row in result['rows']], ['A', 'B', 'C']
        )
        for row in result['rows']:
            self.assertNotIn('explanation_plain', row,
                             '要求していない列まで返しています')

    def test_heavy_column_between_two_needed_ones_is_never_read(self):
        """解説の列を飛ばして読めていること（ここが表示速度の要）。"""
        result = run_reader(GRID, ['question_id', 'question_text', 'correct_option', 'active'])

        # 先頭は見出し行の読み取り。それ以降がデータの読み取り。
        data_reads = [r for r in result['reads'] if r['row'] != 1]
        covered = set()
        for r in data_reads:
            for offset in range(r['numCols']):
                covered.add(r['col'] + offset)

        self.assertNotIn(3, covered,
                         'explanation_plain（3列目）を読んでしまっています')
        self.assertEqual(covered, {1, 2, 4, 5})

    def test_adjacent_columns_are_read_together(self):
        """隣り合う列は1回にまとめる（読み取り回数を増やさない）。"""
        result = run_reader(GRID, ['question_id', 'question_text', 'correct_option', 'active'])
        data_reads = [r for r in result['reads'] if r['row'] != 1]
        self.assertEqual(len(data_reads), 2,
                         f'読み取り回数が想定より多いです: {data_reads}')

    def test_unknown_header_is_skipped_without_failing(self):
        result = run_reader(GRID, ['question_id', 'does_not_exist'])
        self.assertEqual(len(result['rows']), 3)
        for row in result['rows']:
            self.assertNotIn('does_not_exist', row)

    def test_empty_rows_are_dropped(self):
        grid = [row[:] for row in GRID] + [['', '', '', '', '']]
        result = run_reader(grid, ['question_id', 'correct_option'])
        self.assertEqual(len(result['rows']), 3, '空行が混ざっています')

    def test_sheet_row_is_kept_for_traceability(self):
        result = run_reader(GRID, ['question_id'])
        self.assertEqual([row['_sheet_row'] for row in result['rows']], [2, 3, 4])


class TestHotPathsUseNarrowReads(unittest.TestCase):
    """遅かった経路が、全列読み取りに戻っていないこと。"""

    def test_next_question_reads_only_what_it_needs(self):
        body = strip_comments(function_body(CODE, 'getNextQuestion'))
        self.assertIn('readColumns_(', body, '出題が列を絞らずに読んでいます')
        self.assertNotIn('readObjects_(', body, '全列読み取りが残っています')

    def test_question_columns_exclude_the_heavy_text(self):
        columns = re.search(
            r'const QUESTION_PICK_COLUMNS = Object\.freeze\(\[([\s\S]*?)\]\)', CODE
        )
        self.assertIsNotNone(columns, 'QUESTION_PICK_COLUMNS が見つかりません')
        listed = columns.group(1)

        for forbidden in ('explanation_plain', 'explanation_formal',
                          'explanation_calculation', 'explanation_options',
                          'answer_evidence'):
            self.assertNotIn(forbidden, listed,
                             f'出題時に {forbidden} まで読もうとしています')

        # 出題と可否判定に必要な列は残っていること。
        for required in ('question_id', 'question_text', 'verification_status',
                         'active', 'question_image_refs', 'primary_node_id'):
            self.assertIn(required, listed, f'{required} が読み取り対象から漏れています')

    def test_log_columns_are_limited(self):
        columns = re.search(
            r'const LOG_PICK_COLUMNS = Object\.freeze\(\[([\s\S]*?)\]\)', CODE
        )
        self.assertIsNotNone(columns, 'LOG_PICK_COLUMNS が見つかりません')
        listed = columns.group(1)
        self.assertIn('counts_for_mastery', listed,
                      '正式回答の判定に必要な列がありません')
        self.assertNotIn('notes', listed, '出題に使わない列まで読んでいます')


class TestSpreadsheetIsOpenedOnce(unittest.TestCase):
    """1回の呼び出しでスプレッドシートを何度も開かないこと。"""

    def test_readers_share_one_handle(self):
        source = strip_literals(CODE)
        opens = re.findall(r'SpreadsheetApp\.openById\(', source)
        # 共有ハンドル(spreadsheet_)と、書き込み系の3経路だけ。
        self.assertLessEqual(
            len(opens), 4,
            f'openById の呼び出しが多すぎます（{len(opens)}箇所）。'
            '読み取りは spreadsheet_() を通してください',
        )

        for name in ('readDashboard_', 'readSettingValue_', 'readObjects_'):
            body = strip_comments(function_body(CODE, name))
            self.assertNotIn('SpreadsheetApp.openById', body,
                             f'{name} が毎回スプレッドシートを開いています')
            self.assertIn('spreadsheet_()', body,
                          f'{name} が共有ハンドルを使っていません')

    def test_handle_is_cached(self):
        body = strip_comments(function_body(CODE, 'spreadsheet_'))
        self.assertIn('SPREADSHEET_HANDLE_', body, 'ハンドルを使い回していません')


class TestLogScanIsBounded(unittest.TestCase):
    """04_学習ログの走査が、空行まで舐めないこと。"""

    def test_scan_stops_at_the_last_row_with_content(self):
        body = strip_comments(function_body(CODE, 'lastLogicalNonEmptyRowInColumnA_'))
        self.assertIn('getLastRow()', body,
                      'getMaxRows() のぶんだけ空行を走査しています')
        self.assertRegex(body, r'Math\.min\(\s*sheet\.getMaxRows\(\)',
                         '走査範囲に上限をかけていません')


if __name__ == '__main__':
    unittest.main(verbosity=2)
