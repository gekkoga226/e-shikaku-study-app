"""ホーム画面の速さの守り（理解度・弱点・残数の表示）。

守っている約束:

  - 02_マインドマップを全列読みしない。このシートには理解度(M列)をはじめ
    計算式の入った列が並んでいて、全部読むと使わない列の再計算まで待つことになる。
  - ホームの表示は、読むシートごとに独立した呼び出しに分ける。
    1つが遅くても、他の表示と学習の開始を止めない。
      getInitialData()        06_ダッシュボード + 00_設定
      getWeaknessData()       02_マインドマップ
      getUnansweredSummary()  03_問題台帳 + 04_学習ログ
  - 弱点リストは数分だけ使い回し、回答を書き込んだ直後は捨てる
    （回答したのに理解度が古いまま、を起こさない）。
  - 並び替えの基準は変えない。読む列を絞っても、出てくる論点と順番は同じ。

Nodeで実際に関数を動かし、どのシートのどの範囲を読んだかを記録して確かめる。
スプレッドシートへは接続しない。
"""

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from gas_source import function_body, html_script, read, strip_comments  # noqa: E402
from js_runtime import gas_bundle, js_value, require_node, run  # noqa: E402

CODE = read('Code.gs')
CLIENT = html_script('Client.html')

# 実データに近い並び。右端には理解度計算用の重い列が足されているものとする。
MINDMAP_HEADERS = [
    'node_id', 'level', 'parent_id', 'major_area', 'topic', 'weight',
    'required_unique_questions', 'primary_unique_answered_count',
    'latest_unique_correct_count', 'weighted_accuracy', 'coverage_pct',
    'confidence_pct', 'mastery_pct', 'mastery_level', 'last_answered_at',
    'next_review_at', 'progress_eligible',
    'audit_formula_a', 'audit_formula_b', 'audit_memo',
]

DASHBOARD_HEADERS = ['label', 'value', 'definition']

FAKE_SHEETS = """
const READS = [];

function makeSheet(name, headers, rows) {
  const grid = [headers.slice()].concat(rows.map(r => r.slice()));
  return {
    getName: () => name,
    getLastRow: () => grid.length,
    getLastColumn: () => headers.length,
    getMaxRows: () => grid.length,
    getRange(row, col, numRows, numCols) {
      numRows = numRows == null ? 1 : numRows;
      numCols = numCols == null ? 1 : numCols;
      const read = () => {
        READS.push({ sheet: name, row, col, numRows, numCols });
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
      };
      return {
        getValues: read,
        getDisplayValues: () => read().map(r => r.map(String)),
        getValue: () => read()[0][0]
      };
    }
  };
}

const SHEETS = {
  '00_設定': makeSheet('00_設定', ['key', 'value'], SETTING_ROWS),
  '02_マインドマップ': makeSheet('02_マインドマップ', MINDMAP_HEADERS, MINDMAP_ROWS),
  '06_ダッシュボード': makeSheet('06_ダッシュボード', DASHBOARD_HEADERS, DASHBOARD_ROWS)
};

const SpreadsheetApp = {
  openById: () => ({ getSheetByName: n => SHEETS[n] || null }),
  flush: () => {}
};

const CACHE = new Map();
const CACHE_OPS = [];
const makeCache = () => ({
  get: k => { CACHE_OPS.push({ op: 'get', key: k }); return CACHE.has(k) ? CACHE.get(k) : null; },
  put: (k, v, s) => { CACHE_OPS.push({ op: 'put', key: k, seconds: s }); CACHE.set(k, v); },
  remove: k => { CACHE_OPS.push({ op: 'remove', key: k }); CACHE.delete(k); }
});
const CacheService = { getUserCache: makeCache, getScriptCache: makeCache };

const Utilities = {
  formatDate: (d) => new Date(d).toISOString().slice(0, 19).replace('T', ' '),
  getUuid: () => 'uuid'
};

const console = {
  log: (...args) => process.stdout.write(args.join(' ') + String.fromCharCode(10)),
  warn: () => {},
  error: () => {}
};
"""

# 変更前の読み方（シート全体を読む）。並び順の突き合わせ用。
LEGACY_WEAKNESS = """
function legacyWeaknesses(limit) {
  return readObjects_(APP_CONFIG.SHEETS.MINDMAP)
    .filter(r => truthy_(r.progress_eligible))
    .map(r => ({
      node_id: String(r.node_id || ''),
      topic: String(r.topic || ''),
      mastery_pct: numberOrZero_(r.mastery_pct),
      weighted_accuracy: numberOrZero_(r.weighted_accuracy)
    }))
    .sort((a, b) => {
      if (a.mastery_pct !== b.mastery_pct) return a.mastery_pct - b.mastery_pct;
      if (a.weighted_accuracy !== b.weighted_accuracy) return a.weighted_accuracy - b.weighted_accuracy;
      return String(a.topic).localeCompare(String(b.topic));
    })
    .slice(0, limit)
    .map(x => x.node_id);
}
"""


def mindmap_row(node_id, topic=None, mastery=0, accuracy=0, eligible=True):
    values = {
        'node_id': node_id,
        'level': 3,
        'major_area': '深層学習',
        'topic': topic if topic is not None else '論点' + node_id,
        'required_unique_questions': 5,
        'primary_unique_answered_count': 2,
        'latest_unique_correct_count': 1,
        'weighted_accuracy': accuracy,
        'coverage_pct': 40,
        'confidence_pct': 50,
        'mastery_pct': mastery,
        'mastery_level': 'B',
        'last_answered_at': '',
        'next_review_at': '',
        'progress_eligible': eligible,
        'audit_formula_a': '監査用の重い列' * 50,
        'audit_formula_b': '監査用の重い列' * 50,
        'audit_memo': 'メモ' * 50,
    }
    return [values.get(h, '') for h in MINDMAP_HEADERS]


DEFAULT_MINDMAP = [
    mindmap_row('N1', mastery=80, accuracy=90),
    mindmap_row('N2', mastery=10, accuracy=20),
    mindmap_row('N3', mastery=40, accuracy=30),
    mindmap_row('N4', mastery=10, accuracy=10),
    mindmap_row('N5', mastery=0, accuracy=0, eligible=False),
]

DEFAULT_DASHBOARD = [
    ['論点着手率', '42%', '着手した論点の割合'],
    ['平均理解度', '35%', '全論点の平均'],
    ['問題消化率', '18%', '解いた問題の割合'],
    ['未回答verified問題数', '308', 'まだ解いていない問題'],
]


def run_home(driver, mindmap_rows=None, dashboard_rows=None):
    """ホーム系の関数を実際に動かし、読み取り範囲とキャッシュ操作を返す。"""
    script = '\n'.join([
        'const MINDMAP_HEADERS = %s;' % js_value(MINDMAP_HEADERS),
        'const DASHBOARD_HEADERS = %s;' % js_value(DASHBOARD_HEADERS),
        'const MINDMAP_ROWS = %s;' % js_value(
            mindmap_rows if mindmap_rows is not None else DEFAULT_MINDMAP),
        'const DASHBOARD_ROWS = %s;' % js_value(
            dashboard_rows if dashboard_rows is not None else DEFAULT_DASHBOARD),
        'const SETTING_ROWS = [["mastery_rule_version", "v2_2026-08-16"]];',
        FAKE_SHEETS,
        gas_bundle(),
        LEGACY_WEAKNESS,
        driver,
    ])
    return run(script)


class TestMindmapIsNotReadWhole(unittest.TestCase):
    """02_マインドマップを全列読みしないこと。"""

    def setUp(self):
        require_node(self)

    def test_audit_columns_are_never_read(self):
        out = run_home("""
            const items = readMindmapLeafNodes_();
            const covered = new Set();
            // データ行に届く読み取りだけを見る（見出し行だけの読み取りは対象外）。
            // シート全体を1回で読む書き方は row=1 から始まるので、
            // 「row!==1」で除くと見逃してしまう。
            READS.filter(r => r.sheet === '02_マインドマップ'
                              && r.row + r.numRows - 1 >= 2).forEach(r => {
              for (let i = 0; i < r.numCols; i++) covered.add(r.col + i);
            });
            console.log(JSON.stringify({
              count: items.length,
              covered: Array.from(covered).sort((a, b) => a - b)
            }));
        """)
        # 右端の監査用3列（18,19,20）を読んでいないこと
        for column in (18, 19, 20):
            self.assertNotIn(column, out['covered'],
                             f'使わない{column}列目まで読んでいます')
        self.assertEqual(out['count'], 4, 'progress_eligible の絞り込みが変わっています')

    def test_needed_columns_are_all_read(self):
        out = run_home("""
            const items = readMindmapLeafNodes_();
            console.log(JSON.stringify(items[0]));
        """)
        for key in ('node_id', 'topic', 'major_area', 'mastery_pct', 'mastery_level',
                    'weighted_accuracy', 'required_unique_questions',
                    'primary_unique_answered_count', 'latest_unique_correct_count',
                    'next_review_at', 'last_answered_at'):
            self.assertIn(key, out, f'{key} が読み取り対象から漏れています')

    def test_scattered_columns_do_not_cost_extra_round_trips(self):
        """飛び飛びの列でも、往復は増やさない（隙間はまとめて読む）。"""
        out = run_home("""
            readMindmapLeafNodes_();
            const reads = READS.filter(r => r.sheet === '02_マインドマップ'
                                            && r.row + r.numRows - 1 >= 2);
            console.log(JSON.stringify({ reads: reads.length }));
        """)
        self.assertLessEqual(out['reads'], 2,
                             f'02_マインドマップを{out["reads"]}回に分けて読んでいます')


class TestWeaknessOrderIsUnchanged(unittest.TestCase):
    """読む列を絞っても、出てくる論点と順番が変わらないこと。"""

    def setUp(self):
        require_node(self)

    def test_same_order_as_reading_the_whole_sheet(self):
        out = run_home("""
            const actual = getWeaknessData(8).map(x => x.node_id);
            const legacy = legacyWeaknesses(8);
            console.log(JSON.stringify({ actual: actual, legacy: legacy }));
        """)
        self.assertEqual(out['actual'], out['legacy'],
                         '弱点の並びが以前と変わっています')
        self.assertEqual(out['actual'][0], 'N4',
                         '理解度が同じときは正答率が低いほうが先に来ます')

    def test_ineligible_nodes_are_excluded(self):
        out = run_home("""
            console.log(JSON.stringify(getWeaknessData(50).map(x => x.node_id)));
        """)
        self.assertNotIn('N5', out, 'progress_eligible でない論点が出ています')

    def test_default_limit_is_small(self):
        out = run_home("""
            console.log(JSON.stringify({ count: getWeaknessData().length }));
        """)
        self.assertLessEqual(out['count'], 8, 'ホームに出す件数が増えています')


class TestHomeIsSplitBySheet(unittest.TestCase):
    """ホームの表示が、読むシートごとに分かれていること。"""

    def setUp(self):
        require_node(self)

    def test_initial_data_does_not_touch_the_mindmap(self):
        """理解度基準と学習状況の表示が、重いシートを待たないこと。"""
        out = run_home("""
            getInitialData();
            const sheets = Array.from(new Set(READS.map(r => r.sheet)));
            console.log(JSON.stringify({ sheets: sheets }));
        """)
        self.assertNotIn('02_マインドマップ', out['sheets'],
                         'getInitialData が理解度の数式シートを読んでいます')
        self.assertIn('06_ダッシュボード', out['sheets'])

    def test_initial_data_still_returns_the_rule_version(self):
        out = run_home("""
            const data = getInitialData();
            console.log(JSON.stringify({
              rule: data.ruleVersion,
              dashboard: data.dashboard.length
            }));
        """)
        self.assertEqual(out['rule'], 'v2_2026-08-16')
        self.assertEqual(out['dashboard'], 4)

    def test_client_loads_each_part_separately(self):
        body = strip_comments(function_body(CLIENT, 'loadHomeData'))
        self.assertIn('.getInitialData()', body, '学習状況の読み込みがありません')
        self.assertIn('loadWeaknesses()', body, '弱点を別呼び出しにしていません')
        self.assertIn('loadUnansweredSummary()', body, '残数を別呼び出しにしていません')
        self.assertNotIn('renderWeaknesses(', body,
                         '弱点の表示が学習状況の読み込みに巻き込まれています')

    def test_weakness_loader_only_asks_for_weaknesses(self):
        body = strip_comments(function_body(CLIENT, 'loadWeaknesses'))
        self.assertIn('.getWeaknessData()', body)
        for forbidden in ('.getInitialData(', '.getUnansweredSummary(',
                          'renderDashboard('):
            self.assertNotIn(forbidden, body,
                             f'弱点の読み込みが {forbidden} を巻き込んでいます')

    def test_slow_sections_tell_the_reader_they_are_waiting(self):
        """黙って止まって見えると、遅いのか壊れたのか分からない。"""
        body = strip_comments(function_body(CLIENT, 'startWaitingNotice'))
        self.assertIn('秒経過', body, '待っている時間を画面に出していません')
        self.assertIn('clearInterval', body, '表示後もタイマーが残ります')

        for name in ('loadHomeData', 'loadWeaknesses'):
            section = strip_comments(function_body(CLIENT, name))
            self.assertIn('startWaitingNotice(', section,
                          f'{name} が待ち時間を表示していません')
            self.assertIn('waiting.stop()', section,
                          f'{name} が終了時にタイマーを止めていません')


class TestWeaknessCache(unittest.TestCase):
    """弱点リストの使い回しと、回答直後の捨て方。"""

    def setUp(self):
        require_node(self)

    def test_second_call_does_not_read_the_sheet_again(self):
        out = run_home("""
            getWeaknessData(8);
            const first = READS.filter(r => r.sheet === '02_マインドマップ').length;
            READS.length = 0;
            const second = getWeaknessData(8);
            console.log(JSON.stringify({
              first: first,
              secondReads: READS.filter(r => r.sheet === '02_マインドマップ').length,
              ids: second.map(x => x.node_id)
            }));
        """)
        self.assertGreater(out['first'], 0, '1回目はシートを読む必要があります')
        self.assertEqual(out['secondReads'], 0,
                         '2回目もシートを読み直しています')
        self.assertEqual(out['ids'][0], 'N4', '使い回した結果が壊れています')

    def test_cache_is_dropped_after_an_answer(self):
        out = run_home("""
            getWeaknessData(8);
            CACHE_OPS.length = 0;
            clearWeaknessCache_();
            const removed = CACHE_OPS.filter(o => o.op === 'remove').map(o => o.key);
            READS.length = 0;
            getWeaknessData(8);
            console.log(JSON.stringify({
              removed: removed,
              rereads: READS.filter(r => r.sheet === '02_マインドマップ').length
            }));
        """)
        self.assertEqual(len(out['removed']), 1,
                         f'キャッシュの削除が1回で済んでいません: {out["removed"]}')
        self.assertGreater(out['rereads'], 0,
                           '回答後もキャッシュを使い続けています')

    def test_answer_clears_the_weakness_cache(self):
        body = strip_comments(function_body(CODE, 'submitAnswer'))
        self.assertIn('clearWeaknessCache_()', body,
                      '回答後にホームの弱点リストを取り直させていません')

    def test_selection_never_uses_the_cached_list(self):
        """出題の優先順位は、使い回した値ではなくシートの現在値で決める。"""
        body = strip_comments(function_body(CODE, 'readMindmapLeafNodes_'))
        self.assertNotIn('Cache', body,
                         '出題に使う理解度をキャッシュから読んでいます')


class TestReadColumnsGapOption(unittest.TestCase):
    """隙間をまたぐ読み方が、長い列のあるシートへ広がっていないこと。"""

    def test_question_sheet_keeps_strict_skipping(self):
        body = strip_comments(function_body(CODE, 'getNextQuestion'))
        self.assertNotIn('maxGap', body,
                         '03_問題台帳の読み取りで解説の列をまたごうとしています')

    def test_summary_read_keeps_strict_skipping(self):
        body = strip_comments(function_body(CODE, 'readUnansweredSummary_'))
        self.assertNotIn('maxGap', body,
                         '残数の集計で長い列をまたごうとしています')

    def test_gap_merging_is_off_by_default(self):
        body = strip_comments(function_body(CODE, 'readColumns_'))
        self.assertRegex(body, r'maxGap\s*=[^\n]*:\s*0',
                         '既定で隙間をまたいでしまいます')


if __name__ == '__main__':
    unittest.main(verbosity=2)
