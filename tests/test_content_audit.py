"""「解くのに情報が足りない問題」を洗い出せることの守り。

守っている約束:

  「図・プログラム・空欄の前提など、解くのに必要な材料が
    03_問題台帳に載っていない問題を、監査で名指しできる。」

とくに、この監査が見落としていた形を実際に動かして確かめる:

  図（question_image_refs）はあるのに、
  「プログラム中の（き）に当てはまるもの」と書かれたプログラム本体が
  本文にも画像にも無い —— 画像があるので NEEDS_IMAGE では見つからない。

判定はすべて findQuestionContentGaps_ に閉じているので、
スプレッドシートへ一切触れずにNodeで動かして結果を確認できる。
"""

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from gas_source import function_body, read, strip_comments  # noqa: E402
from js_runtime import gas_bundle, js_value, require_node, run  # noqa: E402

AUDIT_FILES = ('ImageManifest.gs', 'ImageSupport.gs', 'ImageSelfTest.gs')

# 実際に出題されている形（深層モデルのための最適化・図1/図2 + backprop）。
BACKPROP_TEXT = (
    '誤差逆伝播法では、出力層に近い側のパラメータに関する勾配を先に計算し、'
    'その計算結果を再帰的に利用することで入力層に近い側のパラメータに関する勾配を求める。'
    '図1におけるCross Entropy、Softmax、Affineのように各計算ステップごとに勾配を計算し、'
    '連鎖律に基づいて出力層のパラメータに関する勾配を求めるのが汎用的な方法である。'
    'しかし、出力層の活性化関数にソフトマックス関数を、そして誤差関数にクロスエントロピーを'
    '用いる際には、図2のように単一のオペレーションとして扱う方が逆伝播誤差計算が'
    'シンプルになることが知られている。'
    'プログラム中の（き）に当てはまる選択肢を以下のうちから１つ選べ。'
)

# 同じ問題に、本来あるはずのプログラムが載っている場合。
BACKPROP_TEXT_WITH_PROGRAM = BACKPROP_TEXT.replace(
    'プログラム中の（き）に当てはまる選択肢を以下のうちから１つ選べ。',
    'def update(w, grad, learning_rate, batch_size):\n'
    '    return （き）\n'
    'プログラム中の（き）に当てはまる選択肢を以下のうちから１つ選べ。'
)


# 実際に出題されている形（情報理論・（あ）〜（え）の4空欄・選択肢は数式画像）。
ENTROPY_TEXT = (
    '情報理論に関して、事象xに対して確率pがあるとき、'
    'そのエントロピー（平均情報量）の式は（あ）である。'
    'さらに確率qとしたとき、交差エントロピーの式は（い）のように表すことができる。'
    'また、確率分布P,Qがあって、確率変数をp(x),q(x)としたとき、'
    'r(x)が以下のような式を満たすとすると、KLダイバージェンスDKL(p‖q)を表す式は（う）となり、'
    'JSダイバージェンスDJS(p||q)を表す式は（え）となる。'
    '/ 空欄（あ）に当てはまる式を以下のうちから選べ。'
)

# 同じ大問の別の行（KLダイバージェンスDKL(p‖q)＝（う）を問う版）。
# 台帳では（あ）〜（え）ごとに別の question_id・別の画像として分かれている。
ENTROPY_TEXT_KL = ENTROPY_TEXT.replace(
    '/ 空欄（あ）に当てはまる式を以下のうちから選べ。',
    '/ 空欄（う）に当てはまる式を以下のうちから選べ。',
)


def question(**overrides):
    """判定に使う列だけを持つ、1問ぶんの行を作る。"""
    row = {
        'question_id': 'EXAM-TEST-Q001',
        'question_text': '確率的勾配降下法の説明として正しいものを1つ選べ。',
        'primary_node_id': 'DL-OPT',
        'question_image_refs': '',
        'option_a': 'A', 'option_b': 'B', 'option_c': 'C', 'option_d': 'D',
        'explanation_plain': '勾配降下法の説明。',
        'verification_status': 'verified',
        'active': True,
    }
    row.update(overrides)
    return row


def gaps_for(rows):
    """findQuestionContentGaps_ をNodeで動かして、問題ごとの理由を返す。"""
    script = '\n'.join([
        gas_bundle(AUDIT_FILES),
        'const rows = %s;' % js_value(rows),
        'const out = rows.map(r => findQuestionContentGaps_(r));',
        'console.log(JSON.stringify(out));',
    ])
    return run(script)


class TestMissingProgram(unittest.TestCase):
    """画像はあるのにプログラムが載っていない問題を見つけること。"""

    def test_program_referenced_but_not_included_is_flagged(self):
        require_node(self)
        [reasons] = gaps_for([question(
            question_text=BACKPROP_TEXT,
            question_image_refs='images/a3-q009.png',
        )])
        self.assertIn(
            'NEEDS_PROGRAM', reasons,
            'プログラムを指しているのに本文にプログラムが無い問題を見落としています',
        )
        self.assertIn(
            'BLANK_NOT_FOUND', reasons,
            '名指しされた（き）が本文のどこにも無いことを見落としています',
        )

    def test_program_included_is_not_flagged(self):
        require_node(self)
        [reasons] = gaps_for([question(
            question_text=BACKPROP_TEXT_WITH_PROGRAM,
            question_image_refs='images/a3-q009.png',
        )])
        self.assertNotIn('NEEDS_PROGRAM', reasons,
                         'プログラムが載っている問題を誤って報告しています')
        self.assertNotIn('BLANK_NOT_FOUND', reasons,
                         '本文に（き）がある問題を誤って報告しています')

    def test_program_word_without_body_needs_image_still_separate(self):
        """画像が無い場合は NEEDS_IMAGE も同時に付くこと。"""
        require_node(self)
        [reasons] = gaps_for([question(
            question_text=BACKPROP_TEXT,
            question_image_refs='',
        )])
        self.assertIn('NEEDS_IMAGE', reasons)
        self.assertIn('NEEDS_PROGRAM', reasons)


class TestBlankReference(unittest.TestCase):
    """空欄を名指ししているのに、その空欄が本文に無い問題を見つけること。"""

    def test_blank_present_twice_is_not_flagged(self):
        require_node(self)
        [reasons] = gaps_for([question(
            question_text=(
                'ニューラルネットワークの学習では、勾配の（あ）を用いて'
                'パラメータを更新する。文中の（あ）に当てはまるものを1つ選べ。'
            ),
        )])
        self.assertNotIn('BLANK_NOT_FOUND', reasons)

    def test_blank_only_in_instruction_is_flagged(self):
        require_node(self)
        [reasons] = gaps_for([question(
            question_text=(
                'ニューラルネットワークの学習について、'
                '文中の（あ）に当てはまるものを以下のうちから1つ選べ。'
            ),
        )])
        self.assertIn('BLANK_NOT_FOUND', reasons)

    def test_multiple_blanks_are_checked_independently(self):
        require_node(self)
        script = '\n'.join([
            gas_bundle(AUDIT_FILES),
            'const text = %s;' % js_value(
                '学習率は（あ）であり、バッチサイズは大きいほど（い）。'
                '（あ）に当てはまるものと（う）に当てはまるものを1つずつ選べ。'
            ),
            'console.log(JSON.stringify(findUnresolvedBlanks_(text)));',
        ])
        self.assertEqual(run(script), ['う'],
                         '本文にある空欄と無い空欄を取り違えています')


class TestExistingReasonsStillWork(unittest.TestCase):
    """もともと見つけられていた不足を、引き続き見つけられること。"""

    def test_needs_image(self):
        require_node(self)
        [reasons] = gaps_for([question(
            question_text='図に示すネットワークの出力として正しいものを1つ選べ。',
        )])
        self.assertIn('NEEDS_IMAGE', reasons)

    def test_option_placeholder(self):
        require_node(self)
        [reasons] = gaps_for([question(
            option_a='[Aの画像選択肢]', option_b='[Bの画像選択肢]',
            option_c='[Cの画像選択肢]', option_d='[Dの画像選択肢]',
        )])
        self.assertIn('OPTION_PLACEHOLDER', reasons)

    def test_missing_context(self):
        require_node(self)
        [reasons] = gaps_for([question(question_text='（あ）に入る語句を選べ。')])
        self.assertIn('MISSING_CONTEXT', reasons)

    def test_no_explanation(self):
        require_node(self)
        [reasons] = gaps_for([question(explanation_plain='')])
        self.assertIn('NO_EXPLANATION', reasons)

    def test_healthy_question_is_not_flagged(self):
        require_node(self)
        [reasons] = gaps_for([question()])
        self.assertEqual(reasons, [], '不足の無い問題を報告しています: %s' % reasons)


class TestTruncatedText(unittest.TestCase):
    """本文が途中で切れている問題を見つけること。"""

    def test_text_cut_mid_sentence_is_flagged(self):
        require_node(self)
        [reasons] = gaps_for([question(
            question_text='誤差逆伝播法では、出力層に近い側のパラメータに関する勾配を先に計算し',
        )])
        self.assertIn('TRUNCATED_TEXT', reasons)

    def test_text_ending_with_period_is_not_flagged(self):
        require_node(self)
        [reasons] = gaps_for([question()])
        self.assertNotIn('TRUNCATED_TEXT', reasons)


class TestAuditReport(unittest.TestCase):
    """監査の一覧が、直すのに必要な手がかりを添えて出ること。"""

    def run_audit(self, rows):
        script = '\n'.join([
            gas_bundle(AUDIT_FILES),
            'globalThis.APP_CONFIG = { SHEETS: { QUESTIONS: "03_問題台帳" } };',
            'const rows = %s;' % js_value(rows),
            'globalThis.readObjects_ = () => rows;',
            'globalThis.isFormalQuestion_ = q => q.verification_status === "verified" && q.active;',
            'const result = runQuestionContentAudit();',
            'console.log(JSON.stringify(result));',
        ])
        return run(script)

    def test_shared_image_siblings_are_listed(self):
        """同じ図を使う仲間（1つの大問を空欄ごとに分けたもの）を一緒に出すこと。"""
        require_node(self)
        rows = [
            question(question_id='EXAM-A3-Q009', question_text=BACKPROP_TEXT,
                     question_image_refs='images/a3-q009.png'),
            question(question_id='EXAM-A3-Q010', question_text=BACKPROP_TEXT,
                     question_image_refs='images/a3-q009.png'),
            question(question_id='EXAM-A3-Q011', question_text=BACKPROP_TEXT,
                     question_image_refs='images/a3-q009.png'),
            question(question_id='EXAM-A3-Q099'),
        ]
        result = self.run_audit(rows)

        self.assertEqual(result['formal_total'], 4)
        flagged = {f['question_id']: f for f in result['findings']}
        self.assertEqual(
            sorted(flagged), ['EXAM-A3-Q009', 'EXAM-A3-Q010', 'EXAM-A3-Q011'],
            '不足のある問題だけを報告できていません',
        )
        self.assertEqual(
            flagged['EXAM-A3-Q009']['shared_image_with'],
            'EXAM-A3-Q010,EXAM-A3-Q011',
            '同じ図を使う仲間を一覧に出していません',
        )
        self.assertEqual(flagged['EXAM-A3-Q009']['unresolved_blanks'], 'き')
        self.assertEqual(result['counts']['NEEDS_PROGRAM'], 3)

    def test_audit_never_writes(self):
        """監査が書き込み系のAPIを一切呼ばないこと。"""
        source = strip_comments(read('ImageSelfTest.gs'))
        body = function_body(source, 'runQuestionContentAudit')
        for forbidden in ('setValue', 'appendRow', 'getRange(', 'SpreadsheetApp'):
            self.assertNotIn(forbidden, body,
                             '監査がスプレッドシートへ書き込もうとしています: %s' % forbidden)


class TestEntropyQuestionIsComplete(unittest.TestCase):
    """（あ）〜（え）の4空欄を分けた問題が、誤って不足扱いされないこと。

    本文が（あ）を定義したうえで（あ）を問うており、選択肢4つは画像で
    そろっている。解ける問題なので、監査は何も報告してはいけない。
    """

    def test_entropy_question_is_not_flagged(self):
        require_node(self)
        [reasons] = gaps_for([question(
            question_id='EXAM-A1-Q011',
            question_text=ENTROPY_TEXT,
            question_image_refs='images/a1-q011-r.png;images/a1-q011-a.png;'
                                'images/a1-q011-b.png;images/a1-q011-c.png;images/a1-q011-d.png',
            option_a='[Aの画像選択肢]', option_b='[Bの画像選択肢]',
            option_c='[Cの画像選択肢]', option_d='[Dの画像選択肢]',
        )])
        self.assertEqual(
            reasons, [],
            '解ける問題を不足として報告しています: %s' % reasons,
        )

    def test_kl_divergence_variant_is_not_flagged(self):
        """同じ大問の別の行（（う）＝KLダイバージェンスを問う版）も同様に不足なしとなること。

        本文中の「（う）」は定義の文とラベル参照の文の2か所に出てくるので
        BLANK_NOT_FOUND にはならず、選択肢4つに画像がそろっていれば
        OPTION_PLACEHOLDER にもならない。
        """
        require_node(self)
        [reasons] = gaps_for([question(
            question_id='EXAM-A1-Q011',
            question_text=ENTROPY_TEXT_KL,
            question_image_refs='images/a1-q011-r.png;images/a1-q011-a.png;'
                                'images/a1-q011-b.png;images/a1-q011-c.png;images/a1-q011-d.png',
            option_a='[Aの画像選択肢]', option_b='[Bの画像選択肢]',
            option_c='[Cの画像選択肢]', option_d='[Dの画像選択肢]',
        )])
        self.assertEqual(
            reasons, [],
            '解ける問題を不足として報告しています: %s' % reasons,
        )

    def test_referenced_blank_is_defined_in_the_text(self):
        """（あ）は本文で定義されているので、名指しの空欄として残らないこと。"""
        require_node(self)
        script = '\n'.join([
            gas_bundle(AUDIT_FILES),
            'console.log(JSON.stringify(findUnresolvedBlanks_(%s)));' % js_value(ENTROPY_TEXT),
        ])
        self.assertEqual(run(script), [])


class TestOptionImageCoverage(unittest.TestCase):
    """画像はあるのに、一部の選択肢だけ画像が足りない問題を見つけること。"""

    MANIFEST_ONLY_B = {
        'EXAM-A2-Q027': {'v': 1, 'q': [], 'o': {'B': [0]}, 's': 'ignored'}
    }

    def gaps_with_manifest(self, row, manifest):
        """差し替えたmanifestで findQuestionContentGaps_ を動かす。"""
        script = '\n'.join([
            gas_bundle(('ImageSupport.gs', 'ImageSelfTest.gs')),
            'globalThis.IMAGE_ROLE_MAP_BY_QUESTION_ID = %s;' % js_value(manifest),
            'console.log(JSON.stringify(findQuestionContentGaps_(%s)));' % js_value(row),
        ])
        return run(script)

    def test_partial_option_images_are_flagged(self):
        require_node(self)
        reasons = self.gaps_with_manifest(question(
            question_id='EXAM-A2-Q027',
            question_image_refs='images/a2-q027-b.png',
            option_a='[Aの画像選択肢]', option_b='[Bの画像選択肢]',
            option_c='[Cの画像選択肢]', option_d='[Dの画像選択肢]',
        ), self.MANIFEST_ONLY_B)
        self.assertIn(
            'OPTION_PLACEHOLDER', reasons,
            '画像が一部の選択肢にしか無い問題を見落としています',
        )

    def test_text_options_beside_one_image_option_are_not_flagged(self):
        """A/C/Dが文字の選択肢で、Bだけが画像の問題は正常なので報告しないこと。"""
        require_node(self)
        reasons = self.gaps_with_manifest(question(
            question_id='EXAM-A2-Q027',
            question_image_refs='images/a2-q027-b.png',
            option_a='シグモイド関数', option_b='[Bの画像選択肢]',
            option_c='ReLU関数', option_d='恒等関数',
        ), self.MANIFEST_ONLY_B)
        self.assertEqual(reasons, [], '正常な問題を報告しています: %s' % reasons)


if __name__ == '__main__':
    unittest.main()
