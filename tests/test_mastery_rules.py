"""理解度 v2 の守り。

このテストが守っているのは次の約束:

  1. Apps Script側で理解度を独自計算しない（「正解したら +20」を復活させない）
  2. 回答処理は
       回答前のM列を読む → 04_学習ログへ書く → flush() → M列を読み直す
     の順序である
  3. 04_学習ログ P/Q（監査用スナップショット）を現在理解度の計算元にしない
  4. 04_学習ログ U列 counts_for_mastery の数式を壊さない
  5. appendRow() で物理末尾へ追加せず、論理的な次の空き行へ書く
  6. 02_マインドマップ M列を固定値で上書きしない

いずれもコードの構造を静的に確認する。スプレッドシートは一切変更しない。
"""

import re
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from gas_source import all_gas_sources, function_body, read, strip_literals  # noqa: E402

# 02_マインドマップ の mastery_pct は M列 = 13列目
MASTERY_COLUMN = 13
# 04_学習ログ の mastery_before / mastery_after は P列(16) / Q列(17)
LOG_MASTERY_BEFORE_COLUMN = 16
LOG_MASTERY_AFTER_COLUMN = 17


class TestNoLegacyMasteryMath(unittest.TestCase):
    """「1問正解したら +20」系の独自計算が復活していないこと。"""

    FORBIDDEN_PATTERNS = [
        r'mastery\w*\s*\+=\s*\d',
        r'mastery\w*\s*=\s*mastery\w*\s*\+\s*\d',
        r'\+\s*20\s*\)?\s*;?\s*//.*mastery',
        r'MASTERY_(GAIN|STEP|INCREMENT|BONUS)',
        r'setValue\(\s*mastery\w*\s*\+',
    ]

    def test_no_local_mastery_increment(self):
        for name, source in all_gas_sources().items():
            body = strip_literals(source)
            for pattern in self.FORBIDDEN_PATTERNS:
                self.assertIsNone(
                    re.search(pattern, body, re.IGNORECASE),
                    f'{name}: 理解度をApps Script側で加算する処理が見つかりました '
                    f'(パターン: {pattern})',
                )

    def test_mastery_column_is_never_written(self):
        """02_マインドマップ M列(13) へ setValue しないこと。"""
        for name, source in all_gas_sources().items():
            body = strip_literals(source)
            hits = re.findall(
                r'getRange\(\s*[^),]+,\s*%d\s*\)\s*\.\s*(setValue|setFormula|clearContent)'
                % MASTERY_COLUMN,
                body,
            )
            self.assertEqual(
                hits, [],
                f'{name}: 理解度のM列({MASTERY_COLUMN}列目)を書き換えています: {hits}',
            )


class TestSubmitAnswerOrder(unittest.TestCase):
    """回答処理の順序が v2 の手順どおりであること。"""

    @classmethod
    def setUpClass(cls):
        cls.raw = function_body(read('Code.gs'), 'submitAnswer')
        cls.body = strip_literals(cls.raw)

    def _index(self, pattern, label):
        match = re.search(pattern, self.body)
        self.assertIsNotNone(match, f'submitAnswer に「{label}」が見つかりません')
        return match.start()

    def test_read_before_write_flush_read_after(self):
        before = self._index(
            r'masteryBefore\s*=[^\n]*getRange\(\s*nodeRow\s*,\s*%d\s*\)' % MASTERY_COLUMN,
            '回答前のM列読み取り',
        )
        write_log = self._index(
            r'logSheet\.getRange\(\s*targetRow\s*,\s*1\s*,\s*1\s*,\s*20\s*\)\s*\.setValues',
            '04_学習ログへの書き込み',
        )
        flush = self._index(r'SpreadsheetApp\.flush\(\)', 'SpreadsheetApp.flush()')
        after = self._index(
            r'masteryAfter\s*=[^\n]*getRange\(\s*nodeRow\s*,\s*%d\s*\)' % MASTERY_COLUMN,
            '回答後のM列読み直し',
        )

        self.assertLess(before, write_log, '回答前の理解度読み取りがログ書き込みより後になっています')
        self.assertLess(write_log, flush, 'ログ書き込みの前に flush() が呼ばれています')
        self.assertLess(flush, after, 'flush() の前に理解度を読み直しています（再計算が反映されません）')

    def test_mastery_after_comes_from_mindmap_not_log(self):
        match = re.search(r'masteryAfter\s*=\s*([^\n;]+)', self.body)
        self.assertIsNotNone(match, 'masteryAfter の代入が見つかりません')
        expression = match.group(1)
        self.assertIn('mapSheet', expression,
                      'masteryAfter は 02_マインドマップ から読む必要があります')
        self.assertNotIn('logSheet', expression,
                         'masteryAfter を 04_学習ログ から読んではいけません')

    def test_log_pq_are_snapshot_only(self):
        """P/Q列は「書く」だけで、理解度の入力として「読む」ことはしない。"""
        reads = re.findall(
            r'logSheet\.getRange\(\s*[^),]+,\s*(?:%d|%d)\s*\)\s*\.getValue'
            % (LOG_MASTERY_BEFORE_COLUMN, LOG_MASTERY_AFTER_COLUMN),
            self.body,
        )
        self.assertEqual(reads, [], '04_学習ログ P/Q列を計算元として読んでいます')

    def test_counts_for_mastery_formula_is_only_filled_when_missing(self):
        """U列の数式は、まだ無いときだけ補う（既存の数式を壊さない）。"""
        self.assertRegex(
            self.body,
            r'if\s*\(\s*!\s*uCell\.getFormula\(\)\s*\)',
            'U列の数式を無条件で上書きしています',
        )
        # setFormula は上のガードより後ろにあること
        guard = self.body.index('uCell.getFormula()')
        set_formula = self.body.index('uCell.setFormula')
        self.assertLess(guard, set_formula, 'U列の数式を確認する前に書き込んでいます')

    def test_uses_logical_next_row_not_append_row(self):
        self.assertIn('findLogicalNextLogRow_(logSheet)', self.body,
                      '論理的な次の空き行の探索が行われていません')
        for name, source in all_gas_sources().items():
            self.assertNotIn(
                '.appendRow(', strip_literals(source),
                f'{name}: appendRow() は物理末尾へ追記するため使用禁止です',
            )


class TestSelfTestsAreNonDestructive(unittest.TestCase):
    """セルフテストが学習ログを汚さないこと。"""

    def test_self_tests_do_not_write_to_log(self):
        for func, filename in (
            ('runSelfTest', 'Code.gs'),
            ('runImageSupportSelfTest', 'ImageSelfTest.gs'),
            ('runImageOptionMappingSelfTest', 'ImageSelfTest.gs'),
            ('runQuestionContentAudit', 'ImageSelfTest.gs'),
            ('runImageBundleSmokeTest', 'ImageSelfTest.gs'),
        ):
            body = strip_literals(function_body(read(filename), func))
            for forbidden in ('setValues(', 'setValue(', 'appendRow(', 'setFormula('):
                self.assertNotIn(
                    forbidden, body,
                    f'{filename}:{func} がスプレッドシートへ書き込もうとしています ({forbidden})',
                )

    def test_submit_answer_is_not_called_by_self_tests(self):
        for func, filename in (
            ('runSelfTest', 'Code.gs'),
            ('runImageSupportSelfTest', 'ImageSelfTest.gs'),
            ('runImageOptionMappingSelfTest', 'ImageSelfTest.gs'),
            ('runQuestionContentAudit', 'ImageSelfTest.gs'),
            ('runImageBundleSmokeTest', 'ImageSelfTest.gs'),
        ):
            body = strip_literals(function_body(read(filename), func))
            self.assertNotIn('submitAnswer(', body,
                             f'{filename}:{func} がテスト目的で正式回答を書き込もうとしています')


if __name__ == '__main__':
    unittest.main(verbosity=2)
