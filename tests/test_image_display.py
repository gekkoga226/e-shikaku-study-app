"""画像表示（見切れ防止）の守り。

守っている約束:

  - 通常表示: カード幅より大きい画像は縮小し、小さい画像は引き伸ばさない。
  - 拡大表示: 画像全体を表示する。画面より大きければ縮小するかスクロールする。
    overflow:hidden 等で画像の一部を切り落とさない。
  - 縦横比を必ず維持する（object-fit: contain / height: auto）。
  - iPhoneのノッチ・ホームバー（safe-area）を考慮する。
"""

import re
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from gas_source import function_body, html_script, read, strip_comments  # noqa: E402

STYLES = read('Styles.html')
INDEX = read('Index.html')


def rule(selector: str) -> str:
    """CSSの `selector { ... }` の中身を返す（同じセレクタが複数あれば連結）。"""
    pattern = re.compile(
        r'(?:^|\})\s*' + re.escape(selector) + r'\s*\{([^}]*)\}', re.M
    )
    blocks = pattern.findall(STYLES)
    if not blocks:
        raise AssertionError(f'CSSに {selector} がありません')
    return '\n'.join(blocks)


class TestInlineImageSizing(unittest.TestCase):
    """通常表示（カードの中）の画像。"""

    def test_question_image_shrinks_but_never_stretches(self):
        body = rule('.question-image')
        self.assertIn('max-width: 100%', body,
                      '問題画像がカード幅を超えても縮まりません')
        self.assertIn('object-fit: contain', body, '縦横比が維持されません')
        self.assertRegex(body, r'height:\s*auto', '高さが自動になっていません')
        self.assertNotRegex(body, r'(?<!max-)width:\s*100%',
                            '小さい画像まで引き伸ばしています（width: 100%）')

    def test_option_image_shrinks_but_never_stretches(self):
        body = rule('.option-image')
        self.assertIn('max-width: 100%', body,
                      '選択肢画像が選択肢の幅を超えても縮まりません')
        self.assertIn('object-fit: contain', body, '縦横比が維持されません')
        self.assertRegex(body, r'height:\s*auto', '高さが自動になっていません')
        self.assertNotRegex(body, r'(?<!max-)width:\s*100%',
                            '小さい画像まで引き伸ばしています（width: 100%）')

    def test_no_fixed_pixel_width_forces_overflow(self):
        for selector in ('.question-image', '.option-image'):
            body = rule(selector)
            self.assertNotRegex(
                body, r'(?<!max-)width:\s*\d+px',
                f'{selector} に固定幅があり、画面からはみ出します',
            )


class TestLightboxDoesNotClip(unittest.TestCase):
    """拡大表示モーダル。"""

    def test_lightbox_never_hides_overflow(self):
        body = rule('.lightbox')
        self.assertNotIn('overflow: hidden', body,
                         '拡大表示で画像の一部を切り落としています')
        self.assertIn('overflow: auto', body,
                      'はみ出した分をスクロールできません')

    def test_lightbox_image_fits_the_screen(self):
        body = rule('.lightbox-image')
        self.assertIn('object-fit: contain', body, '縦横比が維持されません')
        self.assertIn('max-width: var(--lightbox-max-w)', body,
                      '横方向の上限が画面基準になっていません')
        self.assertIn('max-height: var(--lightbox-max-h)', body,
                      '縦方向の上限が画面基準になっていません')
        self.assertRegex(body, r'width:\s*auto', '画像を横へ引き伸ばしています')
        self.assertRegex(body, r'height:\s*auto', '画像を縦へ引き伸ばしています')

    def test_flex_item_can_actually_shrink(self):
        """min-width: 0 が無いと、flexの中で画像が縮まず右端が見切れる。"""
        self.assertIn('min-width: 0', rule('.lightbox-stage'),
                      '拡大表示の枠が縮まないため、画像の右端が見切れます')

    def test_max_size_subtracts_the_bar_and_safe_area(self):
        """見切れの原因（バーの高さとsafe-areaを引き忘れる）を防いでいること。"""
        root = rule(':root')
        self.assertIn('--lightbox-max-h', root, '拡大時の高さ上限が定義されていません')
        self.assertIn('--lightbox-max-w', root, '拡大時の幅上限が定義されていません')

        height = re.search(r'--lightbox-max-h:\s*calc\(([\s\S]*?)\);', STYLES)
        self.assertIsNotNone(height, '高さ上限が calc で計算されていません')
        expression = height.group(1)
        self.assertIn('var(--lightbox-bar)', expression,
                      '上部バーの高さを引いていません')
        self.assertIn('env(safe-area-inset-top', expression,
                      'iPhoneのノッチ分を引いていません')
        self.assertIn('env(safe-area-inset-bottom', expression,
                      'iPhoneのホームバー分を引いていません')

        width = re.search(r'--lightbox-max-w:\s*calc\(([\s\S]*?)\);', STYLES)
        self.assertIsNotNone(width, '幅上限が calc で計算されていません')
        self.assertIn('env(safe-area-inset-left', width.group(1),
                      '横方向のsafe-areaを引いていません')
        self.assertIn('env(safe-area-inset-right', width.group(1),
                      '横方向のsafe-areaを引いていません')

    def test_dynamic_viewport_height_is_preferred(self):
        self.assertIn('100dvh', STYLES,
                      'iPhoneのアドレスバー伸縮で高さがずれます（dvh未使用）')

    def test_padding_uses_safe_area(self):
        body = rule('.lightbox')
        for side in ('top', 'right', 'bottom', 'left'):
            self.assertIn(f'env(safe-area-inset-{side}', body,
                          f'safe-area（{side}）を考慮していません')

    def test_centering_does_not_clip_when_scrolling(self):
        """place-items:center + overflow は、はみ出したとき上端が切れる。"""
        body = rule('.lightbox')
        self.assertNotIn('place-items: center', body,
                         'はみ出したときに画像の上端が切れる中央寄せです')
        self.assertIn('margin: auto', rule('.lightbox-stage'),
                      'margin:auto による中央寄せになっていません')

    def test_zoomed_state_allows_original_size_with_scroll(self):
        body = rule('.lightbox.zoomed .lightbox-image')
        self.assertIn('max-width: none', body, '原寸表示で横方向が制限されています')
        self.assertIn('max-height: none', body, '原寸表示で縦方向が制限されています')

    def test_markup_has_a_stage_and_controls(self):
        self.assertIn('id="lightboxZoomBtn"', INDEX, '原寸表示の切り替えがありません')
        self.assertIn('id="lightboxClose"', INDEX, '閉じるボタンがありません')
        self.assertIn('class="lightbox-stage"', INDEX, '拡大表示の枠がありません')
        self.assertIn('class="lightbox-image"', INDEX, '拡大画像のクラスがありません')


class TestLightboxBehaviour(unittest.TestCase):
    """拡大表示のふるまい。"""

    @classmethod
    def setUpClass(cls):
        cls.script = html_script('Client.html')

    def test_opens_at_full_view_not_zoomed(self):
        body = strip_comments(function_body(self.script, 'openLightbox'))
        self.assertIn('setLightboxZoom(false)', body,
                      '開いた直後に全体表示へ戻していません')

    def test_zoom_toggle_switches_both_ways(self):
        body = strip_comments(function_body(self.script, 'toggleLightboxZoom'))
        self.assertIn('setLightboxZoom(!state.lightboxZoomed)', body,
                      '原寸表示と全体表示を切り替えられません')

    def test_closing_resets_the_zoom(self):
        body = strip_comments(function_body(self.script, 'closeLightbox'))
        self.assertIn('setLightboxZoom(false)', body,
                      '閉じたときに原寸表示のままになります')

    def test_zoom_state_is_a_css_class(self):
        body = strip_comments(function_body(self.script, 'setLightboxZoom'))
        self.assertRegex(body, r"classList\.toggle\('zoomed'",
                         '原寸表示の状態がCSSへ伝わっていません')

    def test_question_image_can_be_enlarged(self):
        body = strip_comments(function_body(self.script, 'renderQuestionImages'))
        self.assertRegex(body, r"addEventListener\('click', \(\) => openLightbox\(",
                         '問題画像を拡大できません')


if __name__ == '__main__':
    unittest.main(verbosity=2)
