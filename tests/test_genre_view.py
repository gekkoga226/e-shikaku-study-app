"""ジャンル別の弱点画面の守り。

守っている約束:

  - 「ジャンル」は 02_マインドマップ の major_area で畳む。
    node_id の接頭辞では畳まない（APP-* の親は DL-APP、DL-* の親は DL-BASE で、
    接頭辞と大分類は一致しない）。
  - 理解度・回答済み・正解数をApps Script側で計算し直さない。
    シートの primary_verified_question_count / primary_unique_answered_count /
    latest_unique_correct_count を足すだけ。
  - progress_eligible でない行（level 1 の大分類そのもの）を混ぜない。
    混ぜると同じ問題を親子で二重に数えてしまう。
  - この画面のために 02_マインドマップ の読み取りを増やさない。
    primary_verified_question_count は既にまとめ読みしている範囲の中にある。
  - 「これまでに間違えた数」は押されたときだけ 04_学習ログ を読み、
    1セルが長くなる notes 列を跨がない。
  - 帯グラフの黄色「正解したが自信なし」は、最新の正式回答が
    is_correct=TRUE かつ confidence=1 の問題だけを数える。
    緑（latest_unique_correct_count）の内訳なので、緑を超えることはない。
  - 判定根拠は 04_学習ログ U列 counts_for_mastery だけ（未回答優先モードと同じ）。
  - 学習モードではないので APP_CONFIG.MODES へは足さない。

Nodeで実際に関数を動かし、どのシートのどの範囲を読んだかを記録して確かめる。
スプレッドシートへは接続しない。
"""

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from gas_source import html_script, read, strip_comments  # noqa: E402
from js_runtime import gas_bundle, js_value, require_node, run  # noqa: E402

CODE = read('Code.gs')
CLIENT = html_script('Client.html')
INDEX = read('Index.html')

# 実物の 02_マインドマップ の列順をそのまま写したもの。
# 読み飛ばし・まとめ読みの検証は、実際の列位置と同じでないと意味がない。
# （M列＝13列目が mastery_pct、21列目が primary_verified_question_count）
MINDMAP_HEADERS = [
    'node_id', 'parent_node_id', 'level', 'major_area', 'topic', 'description',
    'syllabus_ref', 'source_count', 'verified_question_count',
    'attempted_unique_count', 'attempts', 'weighted_accuracy', 'mastery_pct',
    'mastery_level', 'last_answered_at', 'next_review_at', 'priority', 'status',
    'is_leaf', 'progress_eligible', 'primary_verified_question_count',
    'required_unique_questions', 'primary_unique_answered_count',
    'latest_unique_correct_count',
    # 右端に足された監査用の重い列。ここまで読んではいけない。
    'audit_formula_a', 'audit_memo',
]

# 実物の 04_学習ログ の列順（U列＝21列目が counts_for_mastery、20列目が notes）。
LOG_HEADERS = [
    'attempt_id', 'answered_at', 'session_id', 'question_id', 'primary_node_id',
    'secondary_node_ids', 'mode', 'user_answer', 'correct_answer', 'is_correct',
    'confidence', 'response_seconds', 'error_type', 'difficulty',
    'verification_status', 'mastery_before', 'mastery_after', 'next_review_at',
    'write_status', 'notes', 'counts_for_mastery',
]

MINDMAP_COL = {h: i + 1 for i, h in enumerate(MINDMAP_HEADERS)}
LOG_COL = {h: i + 1 for i, h in enumerate(LOG_HEADERS)}

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
  '02_マインドマップ': makeSheet('02_マインドマップ', MINDMAP_HEADERS, MINDMAP_ROWS),
  '04_学習ログ': makeSheet('04_学習ログ', LOG_HEADERS, LOG_ROWS)
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

/** データ行に届いた読み取りだけを返す（見出し行だけの読み取りは数えない）。 */
function dataReads(sheet) {
  return READS.filter(r => r.sheet === sheet && r.row + r.numRows - 1 >= 2);
}

/** データ行に届いた読み取りが覆った列番号。 */
function readColumnsOf(sheet) {
  const covered = new Set();
  dataReads(sheet).forEach(r => {
    for (let i = 0; i < r.numCols; i++) covered.add(r.col + i);
  });
  return Array.from(covered).sort((a, b) => a - b);
}
"""


def mindmap_row(node_id, major_area, topic=None, level=2, eligible=True,
                total=0, answered=0, correct=0, mastery=0):
    values = {
        'node_id': node_id,
        'parent_node_id': '' if level == 1 else major_area,
        'level': level,
        'major_area': major_area,
        'topic': topic if topic is not None else node_id + 'の論点',
        'description': '説明' * 30,
        'syllabus_ref': 'ref',
        'source_count': 3,
        'verified_question_count': total,
        'attempted_unique_count': answered,
        'attempts': answered,
        'weighted_accuracy': round(correct / answered * 100) if answered else 0,
        'mastery_pct': mastery,
        'mastery_level': 'B',
        'last_answered_at': '',
        'next_review_at': '',
        'priority': 1,
        'status': 'active',
        'is_leaf': 'TRUE' if eligible else 'FALSE',
        'progress_eligible': 'TRUE' if eligible else 'FALSE',
        'primary_verified_question_count': total,
        'required_unique_questions': 5,
        'primary_unique_answered_count': answered,
        'latest_unique_correct_count': correct,
        'audit_formula_a': '監査用の重い列' * 50,
        'audit_memo': 'メモ' * 50,
    }
    return [values.get(h, '') for h in MINDMAP_HEADERS]


# 実データと同じ形。level 1 の大分類行と、接頭辞が大分類と一致しない論点を含む。
#   APP-IMG / APP-DET の大分類は「深層学習の応用」
#   DL-FFN / DL-CNN の大分類は「深層学習の基礎」
DEFAULT_MINDMAP = [
    mindmap_row('DL-BASE', '深層学習の基礎', level=1, eligible=False, total=0, answered=1),
    mindmap_row('DL-FFN', '深層学習の基礎', total=49, answered=9, correct=6, mastery=60),
    mindmap_row('DL-CNN', '深層学習の基礎', total=18, answered=8, correct=7, mastery=88),
    mindmap_row('DL-APP', '深層学習の応用', level=1, eligible=False, total=0, answered=1),
    mindmap_row('APP-IMG', '深層学習の応用', total=10, answered=4, correct=3, mastery=60),
    mindmap_row('APP-DET', '深層学習の応用', total=33, answered=8, correct=2, mastery=25),
    mindmap_row('OPS', '開発・運用環境', level=1, eligible=False, total=0, answered=1),
    mindmap_row('OPS-ENV', '開発・運用環境', total=10, answered=6, correct=3, mastery=50),
]


def log_row(attempt_id, question_id, node_id, is_correct, counts=True, confidence=3):
    values = {
        'attempt_id': attempt_id,
        'answered_at': '2026-08-18 10:00:00',
        'session_id': 'S1',
        'question_id': question_id,
        'primary_node_id': node_id,
        'secondary_node_ids': '',
        'mode': 'learning',
        'user_answer': 'A',
        'correct_answer': 'A' if is_correct else 'B',
        'is_correct': 'TRUE' if is_correct else 'FALSE',
        'confidence': confidence,
        'response_seconds': 30,
        'error_type': 'none',
        'difficulty': 'standard',
        'verification_status': 'verified',
        'mastery_before': 0,
        'mastery_after': 20,
        'next_review_at': '',
        'write_status': 'success',
        # 1セルが長くなる列。ここを跨いで読んではいけない。
        'notes': '長い備考' * 200,
        'counts_for_mastery': 'TRUE' if counts else 'FALSE',
    }
    return [values.get(h, '') for h in LOG_HEADERS]


DEFAULT_LOG = [
    log_row('A1', 'Q1', 'DL-FFN', False),
    # 同じ問題をもう一度間違えても1問として数える
    log_row('A2', 'Q1', 'DL-FFN', False),
    # 誤答のあと復習で正解にした問題も「これまでに間違えた」には残る
    log_row('A3', 'Q2', 'DL-FFN', False),
    log_row('A4', 'Q2', 'DL-FFN', True),
    log_row('A5', 'Q3', 'APP-DET', False),
    # 正解だけの問題は数えない
    log_row('A6', 'Q4', 'APP-DET', True),
    # counts_for_mastery が FALSE の行は、誤答でも数えない
    log_row('A7', 'Q5', 'APP-IMG', False, counts=False),
]


def run_genre(driver, mindmap_rows=None, log_rows=None):
    """ジャンル別画面の関数を実際に動かし、読み取り範囲とキャッシュ操作を返す。"""
    script = '\n'.join([
        'const MINDMAP_HEADERS = %s;' % js_value(MINDMAP_HEADERS),
        'const LOG_HEADERS = %s;' % js_value(LOG_HEADERS),
        'const MINDMAP_ROWS = %s;' % js_value(
            mindmap_rows if mindmap_rows is not None else DEFAULT_MINDMAP),
        'const LOG_ROWS = %s;' % js_value(
            log_rows if log_rows is not None else DEFAULT_LOG),
        FAKE_SHEETS,
        gas_bundle(),
        driver,
    ])
    return run(script)


class TestGenreIsMajorArea(unittest.TestCase):
    """ジャンルは major_area で畳む。node_id の接頭辞では畳まない。"""

    def setUp(self):
        require_node(self)

    def test_grouped_by_major_area(self):
        out = run_genre("""
            const data = getGenreBreakdown();
            console.log(JSON.stringify(data.genres.map(g => ({
              area: g.major_area,
              topics: g.topics.map(t => t.node_id)
            }))));
        """)
        groups = {g['area']: g['topics'] for g in out}
        self.assertEqual(set(groups), {'深層学習の基礎', '深層学習の応用', '開発・運用環境'})
        self.assertEqual(sorted(groups['深層学習の基礎']), ['DL-CNN', 'DL-FFN'])
        self.assertEqual(sorted(groups['深層学習の応用']), ['APP-DET', 'APP-IMG'])

    def test_node_id_prefix_is_not_used(self):
        """APP-* が DL-* と同じジャンルへ落ちないこと（接頭辞で畳んだ証拠）。"""
        out = run_genre("""
            const data = getGenreBreakdown();
            const byNode = {};
            data.genres.forEach(g => g.topics.forEach(t => { byNode[t.node_id] = g.major_area; }));
            console.log(JSON.stringify(byNode));
        """)
        self.assertEqual(out['APP-IMG'], '深層学習の応用')
        self.assertEqual(out['DL-FFN'], '深層学習の基礎')
        self.assertNotEqual(out['APP-IMG'], out['DL-FFN'],
                            'node_id の接頭辞でジャンルを決めています')

    def test_parent_rows_are_excluded(self):
        """progress_eligible でない大分類の行を混ぜないこと（二重計上の防止）。"""
        out = run_genre("""
            const data = getGenreBreakdown();
            const ids = [];
            data.genres.forEach(g => g.topics.forEach(t => ids.push(t.node_id)));
            console.log(JSON.stringify({ ids: ids, topic_count: data.totals.topic_count }));
        """)
        for parent in ('DL-BASE', 'DL-APP', 'OPS'):
            self.assertNotIn(parent, out['ids'], f'大分類の行 {parent} が論点として出ています')
        self.assertEqual(out['topic_count'], 5, '論点の数が変わっています')


class TestGenreNumbers(unittest.TestCase):
    """数え方はシートの値を足すだけ。独自計算をしない。"""

    def setUp(self):
        require_node(self)

    def test_counts_come_straight_from_the_sheet(self):
        out = run_genre("""
            const data = getGenreBreakdown();
            const g = data.genres.filter(x => x.major_area === '深層学習の基礎')[0];
            console.log(JSON.stringify(g));
        """)
        # DL-FFN(49/9/6) + DL-CNN(18/8/7)
        self.assertEqual(out['total'], 67)
        self.assertEqual(out['answered'], 17)
        self.assertEqual(out['unanswered'], 50)
        self.assertEqual(out['latest_correct'], 13)
        self.assertEqual(out['latest_wrong'], 4)

    def test_rates_are_derived_from_the_counts(self):
        out = run_genre("""
            const data = getGenreBreakdown();
            const g = data.genres.filter(x => x.major_area === '開発・運用環境')[0];
            console.log(JSON.stringify(g));
        """)
        # OPS-ENV 10問中6問回答・3問正解
        self.assertEqual(out['answered_pct'], 60)
        self.assertEqual(out['unanswered_pct'], 40)
        self.assertEqual(out['correct_pct'], 50)
        self.assertEqual(out['wrong_pct'], 50)

    def test_totals_match_the_sum_of_genres(self):
        out = run_genre("""
            const data = getGenreBreakdown();
            const sum = key => data.genres.reduce((a, g) => a + g[key], 0);
            const topicSum = key => data.genres
              .reduce((a, g) => a + g.topics.reduce((b, t) => b + t[key], 0), 0);
            console.log(JSON.stringify({
              totals: data.totals,
              genreSum: { total: sum('total'), answered: sum('answered'),
                          latest_correct: sum('latest_correct') },
              topicSum: { total: topicSum('total'), answered: topicSum('answered'),
                          latest_correct: topicSum('latest_correct') }
            }));
        """)
        for key in ('total', 'answered', 'latest_correct'):
            self.assertEqual(out['totals'][key], out['genreSum'][key],
                             f'合計とジャンル合計が {key} で食い違っています')
            self.assertEqual(out['totals'][key], out['topicSum'][key],
                             f'合計と論点合計が {key} で食い違っています')
        self.assertEqual(out['totals']['total'], 120)
        self.assertEqual(out['totals']['answered'], 35)

    def test_unanswered_and_wrong_are_never_negative(self):
        """回答済みが総数を超えるような値でも、表示できない負の数を作らないこと。"""
        rows = [mindmap_row('X1', 'ジャンルX', total=3, answered=8, correct=9)]
        out = run_genre("""
            const g = getGenreBreakdown().genres[0];
            console.log(JSON.stringify(g));
        """, mindmap_rows=rows)
        self.assertGreaterEqual(out['unanswered'], 0)
        self.assertGreaterEqual(out['latest_wrong'], 0)

    def test_weakest_genre_comes_first(self):
        out = run_genre("""
            console.log(JSON.stringify(getGenreBreakdown().genres.map(g => g.major_area)));
        """)
        # 深層学習の応用は正答率 5/12=42%、開発・運用環境は 50%、深層学習の基礎は 76%
        self.assertEqual(out[0], '深層学習の応用', '正答率が低いジャンルが先頭に来ていません')
        self.assertEqual(out[-1], '深層学習の基礎')


class TestGenreReadsAreCheap(unittest.TestCase):
    """この画面のために 02_マインドマップ の読み取りを増やさない。"""

    def setUp(self):
        require_node(self)

    def test_only_the_mindmap_is_read(self):
        out = run_genre("""
            getGenreBreakdown();
            console.log(JSON.stringify({
              sheets: Array.from(new Set(READS.map(r => r.sheet)))
            }));
        """)
        self.assertEqual(out['sheets'], ['02_マインドマップ'],
                         '02_マインドマップ以外のシートまで読んでいます')

    def test_audit_columns_are_never_read(self):
        out = run_genre("""
            getGenreBreakdown();
            console.log(JSON.stringify({ covered: readColumnsOf('02_マインドマップ') }));
        """)
        for column in (MINDMAP_COL['audit_formula_a'], MINDMAP_COL['audit_memo']):
            self.assertNotIn(column, out['covered'],
                             f'使わない{column}列目まで読んでいます')

    def test_one_round_trip_over_the_data_rows(self):
        out = run_genre("""
            getGenreBreakdown();
            console.log(JSON.stringify({ reads: dataReads('02_マインドマップ').length }));
        """)
        self.assertLessEqual(out['reads'], 2,
                             f'02_マインドマップを{out["reads"]}回に分けて読んでいます')

    def test_the_new_column_costs_no_extra_reads(self):
        """総問題数の列を足しても、弱点リストと読み取り回数・範囲が同じであること。"""
        out = run_genre("""
            getWeaknessData(8);
            const weakness = dataReads('02_マインドマップ').length;
            READS.length = 0;
            getGenreBreakdown();
            const genre = dataReads('02_マインドマップ').length;
            console.log(JSON.stringify({ weakness: weakness, genre: genre }));
        """)
        self.assertEqual(out['genre'], out['weakness'],
                         'ジャンル別画面のほうが02_マインドマップを多く読んでいます')

    def test_total_question_count_is_actually_read(self):
        out = run_genre("""
            console.log(JSON.stringify(readMindmapLeafNodes_()[0]));
        """)
        self.assertIn('primary_verified_question_count', out,
                      '総問題数の列が読み取り対象から漏れています')
        self.assertEqual(out['primary_verified_question_count'], 49)


class TestGenreCache(unittest.TestCase):
    """数分だけ使い回し、回答を書き込んだ直後は捨てる。"""

    def setUp(self):
        require_node(self)

    def test_second_call_does_not_read_the_sheet_again(self):
        out = run_genre("""
            getGenreBreakdown();
            READS.length = 0;
            getGenreBreakdown();
            console.log(JSON.stringify({ reads: READS.length }));
        """)
        self.assertEqual(out['reads'], 0, 'キャッシュが効かず毎回シートを読んでいます')

    def test_cache_is_short_lived(self):
        out = run_genre("""
            getGenreBreakdown();
            console.log(JSON.stringify(CACHE_OPS.filter(o => o.op === 'put')));
        """)
        self.assertTrue(out, 'キャッシュへ書いていません')
        for op in out:
            self.assertLessEqual(op['seconds'], 600,
                                 '理解度の表示を長く使い回しすぎています')

    def test_clearing_makes_it_read_again(self):
        out = run_genre("""
            getGenreBreakdown();
            getGenreMistakeHistory();
            clearGenreBreakdownCache_();
            READS.length = 0;
            getGenreBreakdown();
            getGenreMistakeHistory();
            console.log(JSON.stringify({ reads: READS.length }));
        """)
        self.assertGreater(out['reads'], 0,
                           '回答後に捨てたはずのジャンル別の数字が古いまま残ります')

    def test_submit_answer_clears_the_genre_cache(self):
        """回答を書き込む処理から、ジャンル別のキャッシュ破棄を呼んでいること。"""
        body = strip_comments(CODE)
        submit = body[body.index('function submitAnswer('):]
        submit = submit[:submit.index('\nfunction ')]
        self.assertIn('clearGenreBreakdownCache_()', submit,
                      '回答後にジャンル別の数字が更新されません')


class TestGenreMistakeHistory(unittest.TestCase):
    """「これまでに間違えた問題数」は04_学習ログのU列だけを根拠にする。"""

    def setUp(self):
        require_node(self)

    def test_counts_each_question_once(self):
        out = run_genre("""
            console.log(JSON.stringify(getGenreMistakeHistory()));
        """)
        # Q1(2回誤答) と Q2(誤答→正解) で DL-FFN は2問、Q3 で APP-DET は1問
        self.assertEqual(out['by_node']['DL-FFN'], 2, '同じ問題の誤答を重複して数えています')
        self.assertEqual(out['by_node']['APP-DET'], 1)
        self.assertEqual(out['total'], 3)

    def test_rows_excluded_by_the_sheet_formula_are_ignored(self):
        """counts_for_mastery が FALSE の行は、誤答でも数えない。"""
        out = run_genre("""
            console.log(JSON.stringify(getGenreMistakeHistory()));
        """)
        self.assertNotIn('APP-IMG', out['by_node'],
                         'counts_for_mastery=FALSE の誤答まで数えています')

    def test_correct_only_questions_are_not_counted(self):
        out = run_genre("""
            console.log(JSON.stringify(getGenreMistakeHistory()));
        """)
        self.assertEqual(out['by_node'].get('APP-DET'), 1,
                         '正解だけの問題まで誤答に数えています')

    def test_ever_wrong_is_at_least_the_latest_wrong(self):
        """「一度でも」は「最新が不正解」を必ず含む（少なくなることはない）。

        02_マインドマップと04_学習ログが同じ状態を指すように揃えた組で確かめる。
          DL-FFN  Q1 誤答のまま / Q2 誤答→復習で正解  → 最新の誤答1問・これまで2問
          APP-DET Q3 誤答のまま                        → 最新の誤答1問・これまで1問
        """
        rows = [
            mindmap_row('DL-FFN', '深層学習の基礎', total=10, answered=2, correct=1),
            mindmap_row('APP-DET', '深層学習の応用', total=5, answered=2, correct=1),
        ]
        out = run_genre("""
            const breakdown = getGenreBreakdown();
            const mistakes = getGenreMistakeHistory();
            const rows = [];
            breakdown.genres.forEach(g => g.topics.forEach(t => rows.push({
              node_id: t.node_id,
              latest_wrong: t.latest_wrong,
              ever_wrong: Number((mistakes.by_node || {})[t.node_id] || 0)
            })));
            console.log(JSON.stringify(rows));
        """, mindmap_rows=rows)

        found = {row['node_id']: row for row in out}
        self.assertEqual(set(found), {'DL-FFN', 'APP-DET'})
        for row in out:
            self.assertGreaterEqual(row['ever_wrong'], row['latest_wrong'],
                                    'これまでに間違えた数が最新の誤答数より少なくなっています')

        # 復習で正解にできた問題があるぶん、「これまで」のほうが多くなる
        self.assertEqual(found['DL-FFN']['latest_wrong'], 1)
        self.assertEqual(found['DL-FFN']['ever_wrong'], 2)
        # 復習していない論点では2つの数え方が一致する
        self.assertEqual(found['APP-DET']['latest_wrong'], 1)
        self.assertEqual(found['APP-DET']['ever_wrong'], 1)

    def test_only_the_log_is_read(self):
        out = run_genre("""
            getGenreMistakeHistory();
            console.log(JSON.stringify({
              sheets: Array.from(new Set(READS.map(r => r.sheet)))
            }));
        """)
        self.assertEqual(out['sheets'], ['04_学習ログ'],
                         '02_マインドマップを二度読んでいます')

    def test_the_long_notes_column_is_never_read(self):
        out = run_genre("""
            getGenreMistakeHistory();
            console.log(JSON.stringify({ covered: readColumnsOf('04_学習ログ') }));
        """)
        self.assertNotIn(LOG_COL['notes'], out['covered'],
                         '1セルが長い notes 列まで読んでいます')
        self.assertIn(LOG_COL['counts_for_mastery'], out['covered'],
                      'U列 counts_for_mastery を読んでいません')

    def test_reads_are_split_but_few(self):
        out = run_genre("""
            getGenreMistakeHistory();
            console.log(JSON.stringify({ reads: dataReads('04_学習ログ').length }));
        """)
        self.assertLessEqual(out['reads'], 3,
                             f'04_学習ログを{out["reads"]}回に分けて読んでいます')


class TestGenreUnsureCorrect(unittest.TestCase):
    """帯グラフの黄色「正解したが自信なし」。

    たまたま当たっただけの問題を緑から切り出すのが目的なので、
    数えるのは「最新の正式回答が正解」かつ「その回答の自信が1」のものだけ。
    """

    # 自信度の組み合わせを一通り並べたログ。
    #   Q1 正解・自信なし          → 数える
    #   Q2 正解・自信あり          → 数えない
    #   Q3 誤答・自信なし          → 数えない（黄色ではなく赤）
    #   Q4 自信なしで正解→自信ありで正解  → 最新が自信ありなので数えない
    #   Q5 自信ありで正解→自信なしで正解  → 最新が自信なしなので数える
    #   Q6 自信なしで正解→誤答     → 最新が誤答なので数えない
    #   Q7 正解・自信なしだが counts_for_mastery=FALSE → 数えない
    #   Q8 正解・自信度が空（古い行）→ 「自信なし」と決めつけない
    CONFIDENCE_LOG = [
        log_row('B1', 'Q1', 'DL-FFN', True, confidence=1),
        log_row('B2', 'Q2', 'DL-FFN', True, confidence=3),
        log_row('B3', 'Q3', 'DL-FFN', False, confidence=1),
        log_row('B4', 'Q4', 'APP-DET', True, confidence=1),
        log_row('B5', 'Q4', 'APP-DET', True, confidence=2),
        log_row('B6', 'Q5', 'APP-DET', True, confidence=3),
        log_row('B7', 'Q5', 'APP-DET', True, confidence=1),
        log_row('B8', 'Q6', 'APP-IMG', True, confidence=1),
        log_row('B9', 'Q6', 'APP-IMG', False, confidence=1),
        log_row('B10', 'Q7', 'APP-IMG', True, counts=False, confidence=1),
        log_row('B11', 'Q8', 'OPS-ENV', True, confidence=''),
    ]

    def setUp(self):
        require_node(self)

    def unsure(self):
        return run_genre("""
            console.log(JSON.stringify(getGenreUnsureCorrect()));
        """, log_rows=self.CONFIDENCE_LOG)

    def test_only_correct_answers_with_confidence_one_are_counted(self):
        out = self.unsure()
        self.assertEqual(out['by_node']['DL-FFN'], 1,
                         '自信ありの正解や誤答まで黄色に数えています')
        self.assertEqual(out['by_node']['APP-DET'], 1)
        self.assertEqual(out['total'], 2)

    def test_the_latest_formal_answer_decides(self):
        """途中で自信が変わった問題は、最新の行だけで決める。

        帯グラフの緑は「最新の正式回答が正解」なので、
        その内訳である黄色も同じ時点で見ないと辻褄が合わなくなる。
        """
        out = self.unsure()
        # Q4（自信なし→自信あり）を数えていれば APP-DET は2になる
        self.assertEqual(out['by_node']['APP-DET'], 1,
                         '自信ありで解き直した問題まで黄色のままにしています')

    def test_questions_whose_latest_answer_is_wrong_are_not_counted(self):
        out = self.unsure()
        self.assertNotIn('APP-IMG', out['by_node'],
                         '最新が誤答の問題を黄色に数えています')

    def test_rows_excluded_by_the_sheet_formula_are_ignored(self):
        out = self.unsure()
        self.assertNotIn('APP-IMG', out['by_node'],
                         'counts_for_mastery=FALSE の行まで数えています')

    def test_empty_confidence_is_not_treated_as_unsure(self):
        """自信度は1〜3。空の古い行は0になるので「自信なし」と決めつけない。"""
        out = self.unsure()
        self.assertNotIn('OPS-ENV', out['by_node'],
                         '自信度が空の古い行を「自信なし」と決めつけています')

    def test_never_more_than_the_latest_correct_count(self):
        """黄色は緑の内訳。論点ごとに緑（最新正解）を超えない。

        02_マインドマップと04_学習ログが同じ状態を指すように揃えた組で確かめる。
          DL-FFN  Q1 正解(自信なし) / Q2 正解(自信あり) / Q3 誤答 → 正解2・黄色1
          APP-DET Q4 正解(自信あり) / Q5 正解(自信なし)          → 正解2・黄色1
        """
        rows = [
            mindmap_row('DL-FFN', '深層学習の基礎', total=10, answered=3, correct=2),
            mindmap_row('APP-DET', '深層学習の応用', total=10, answered=2, correct=2),
        ]
        out = run_genre("""
            const breakdown = getGenreBreakdown();
            const unsure = getGenreUnsureCorrect();
            const rows = [];
            breakdown.genres.forEach(g => g.topics.forEach(t => rows.push({
              node_id: t.node_id,
              latest_correct: t.latest_correct,
              unsure: Number((unsure.by_node || {})[t.node_id] || 0)
            })));
            console.log(JSON.stringify(rows));
        """, mindmap_rows=rows, log_rows=self.CONFIDENCE_LOG)

        found = {row['node_id']: row for row in out}
        self.assertEqual(set(found), {'DL-FFN', 'APP-DET'})
        for row in out:
            self.assertLessEqual(row['unsure'], row['latest_correct'],
                                 '黄色が緑（最新正解）より多くなっています')
        self.assertEqual(found['DL-FFN']['unsure'], 1)
        self.assertEqual(found['APP-DET']['unsure'], 1)

    def test_only_the_log_is_read(self):
        out = run_genre("""
            getGenreUnsureCorrect();
            console.log(JSON.stringify({
              sheets: Array.from(new Set(READS.map(r => r.sheet)))
            }));
        """)
        self.assertEqual(out['sheets'], ['04_学習ログ'],
                         '02_マインドマップを二度読んでいます')

    def test_the_long_notes_column_is_never_read(self):
        out = run_genre("""
            getGenreUnsureCorrect();
            console.log(JSON.stringify({ covered: readColumnsOf('04_学習ログ') }));
        """)
        self.assertNotIn(LOG_COL['notes'], out['covered'],
                         '1セルが長い notes 列まで読んでいます')
        self.assertIn(LOG_COL['confidence'], out['covered'],
                      'K列 confidence を読んでいません')
        self.assertIn(LOG_COL['counts_for_mastery'], out['covered'],
                      'U列 counts_for_mastery を読んでいません')

    def test_confidence_costs_no_extra_read(self):
        """confidence は is_correct の隣なので、読み取り回数は誤答数と同じままになる。"""
        out = run_genre("""
            getGenreMistakeHistory();
            const mistakes = dataReads('04_学習ログ').length;
            READS.length = 0;
            CACHE.clear();
            getGenreUnsureCorrect();
            console.log(JSON.stringify({
              mistakes: mistakes, unsure: dataReads('04_学習ログ').length
            }));
        """)
        self.assertEqual(out['unsure'], out['mistakes'],
                         '自信度を読むために04_学習ログの読み取りが増えています')

    def test_second_call_does_not_read_the_sheet_again(self):
        out = run_genre("""
            getGenreUnsureCorrect();
            const first = dataReads('04_学習ログ').length;
            getGenreUnsureCorrect();
            console.log(JSON.stringify({
              first: first, second: dataReads('04_学習ログ').length
            }));
        """)
        self.assertEqual(out['first'], out['second'],
                         '2回目も04_学習ログを読み直しています')

    def test_answering_clears_the_cache(self):
        """回答すると自信度も変わるので、次に開いたとき数え直させる。"""
        body = strip_comments(CODE)
        start = body.index('function clearGenreBreakdownCache_(')
        end = body.index('\nfunction ', start + 1)
        self.assertIn('GENRE_UNSURE_CACHE_KEY', body[start:end],
                      '回答後も古い「自信なし正解」が残ります')


class TestGenreUnsureCorrectIsOnTheChart(unittest.TestCase):
    """黄色が実際に棒グラフへ出ていること。"""

    def test_client_asks_for_the_counts(self):
        script = strip_comments(CLIENT)
        self.assertIn('.getGenreUnsureCorrect()', script,
                      '「正解したが自信なし」を呼んでいません')

    def test_the_bar_has_a_yellow_segment(self):
        script = strip_comments(CLIENT)
        start = script.index('function genreBar(')
        end = script.index('function genreNumbers(')
        self.assertIn("'unsure'", script[start:end],
                      '帯グラフに黄色の区画がありません')

    def test_yellow_is_taken_out_of_the_green(self):
        """黄色は緑の内訳。帯の合計が総問題数を超えないよう緑から引く。"""
        script = strip_comments(CLIENT)
        start = script.index('function genreBar(')
        end = script.index('function genreNumbers(')
        self.assertIn('- unsure', script[start:end],
                      '黄色を足したぶん帯が総問題数を超えます')

    def test_yellow_never_exceeds_the_green(self):
        script = strip_comments(CLIENT)
        start = script.index('function unsureCorrectOf(')
        end = script.index('function genreEverWrong(', start)
        self.assertIn('Math.min(', script[start:end],
                      '緑より多い黄色を描いてしまう可能性があります')

    def test_the_first_render_does_not_wait_for_the_log(self):
        """内訳は後から足す。02_マインドマップだけで帯グラフを先に出す。"""
        script = strip_comments(CLIENT)
        start = script.index('function loadGenreBreakdown(')
        end = script.index('function loadGenreMistakes(')
        body = script[start:end]
        self.assertLess(body.index('renderGenreBreakdown()'),
                        body.index('loadGenreUnsureCorrect()'),
                        '04_学習ログを読み終えるまで帯グラフが出ません')

    def test_answering_discards_the_screens_own_copy(self):
        script = strip_comments(CLIENT)
        start = script.index('function forgetGenreData(')
        end = script.index('function loadGenreBreakdown(')
        self.assertIn('state.genreUnsure = null', script[start:end],
                      '回答後も古い「自信なし正解」が画面に残ります')

    def test_the_colour_is_defined(self):
        styles = read('Styles.html')
        self.assertIn('.genre-seg.unsure', styles, '黄色の色指定がありません')

    def test_the_legend_explains_the_colour(self):
        self.assertIn('genre-legend', INDEX, '色の意味を説明する凡例がありません')
        self.assertIn('自信なし', INDEX, '凡例に「自信なし」の説明がありません')


class TestGenreIsNotALearningMode(unittest.TestCase):
    """これは画面であって出題モードではない。出題側へ影響させない。"""

    def setUp(self):
        require_node(self)

    def test_modes_are_unchanged(self):
        out = run_genre("""
            console.log(JSON.stringify({
              modes: APP_CONFIG.MODES.slice(),
              normalized: normalizeMode_('genre')
            }));
        """)
        self.assertEqual(out['modes'], ['learning', 'review', 'unanswered'],
                         '出題モードが増えています')
        self.assertEqual(out['normalized'], 'learning')

    def test_genre_functions_do_not_write(self):
        """書き込みの呼び出しを含まないこと（表示だけの画面）。"""
        body = strip_comments(CODE)
        for name in ('getGenreBreakdown', 'buildGenreBreakdown_',
                     'getGenreMistakeHistory', 'buildGenreMistakeHistory_'):
            start = body.index('function %s(' % name)
            end = body.index('\nfunction ', start + 1)
            source = body[start:end]
            for forbidden in ('setValue', 'setValues', 'appendRow', 'flush('):
                self.assertNotIn(forbidden, source,
                                 f'{name} がスプレッドシートへ書き込もうとしています')


class TestGenreScreenIsWiredUp(unittest.TestCase):
    """画面から実際に呼べること。"""

    def test_index_has_the_view_and_buttons(self):
        for element in ('genreView', 'genreBtn', 'genreList', 'genreTotals',
                        'genreMistakeBtn', 'genreHomeBtn'):
            self.assertIn('id="%s"' % element, INDEX,
                          f'{element} が画面にありません')

    def test_client_calls_the_new_functions(self):
        script = strip_comments(CLIENT)
        self.assertIn('.getGenreBreakdown()', script,
                      'ジャンル別の内訳を呼んでいません')
        self.assertIn('.getGenreMistakeHistory()', script,
                      'これまでに間違えた数を呼んでいません')

    def test_mistake_history_is_only_loaded_on_demand(self):
        """初回表示では04_学習ログを読まないこと（押されたときだけ読む）。"""
        script = strip_comments(CLIENT)
        start = script.index('function showGenreView(')
        end = script.index('function loadGenreMistakes(')
        self.assertNotIn('.getGenreMistakeHistory()', script[start:end],
                         '画面を開いただけで04_学習ログを読んでいます')

    def test_answering_discards_the_screens_own_copy(self):
        """回答したら、画面が持っている集計も捨てること。

        サーバー側は submitAnswer でキャッシュを捨てるが、
        「これまでに間違えた数」は画面側の変数にも残っている。
        捨て忘れると、新しい内訳の隣に古い誤答数が並ぶ。
        """
        script = strip_comments(CLIENT)
        start = script.index('function submitCurrentAnswer(')
        end = script.index('function renderResult(')
        self.assertIn('forgetGenreData()', script[start:end],
                      '回答後もジャンル別の古い数字が画面に残ります')

        start = script.index('function forgetGenreData(')
        end = script.index('function loadGenreBreakdown(')
        body = script[start:end]
        self.assertIn('state.genreMistakes = null', body)
        self.assertIn('state.genre = null', body)

    def test_waiting_notice_is_used(self):
        script = strip_comments(CLIENT)
        start = script.index('function loadGenreBreakdown(')
        end = script.index('function loadGenreMistakes(')
        self.assertIn('startWaitingNotice(', script[start:end],
                      '待たされていることが画面に出ません')


if __name__ == '__main__':
    unittest.main()
