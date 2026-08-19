"""画像4択（A〜Dそのものが画像）の対応の守り。

守っている約束:

  A → Aの画像 / B → Bの画像 / C → Cの画像 / D → Dの画像

  並び順から推測せず、ImageManifest.gs の o（選択肢の文字 → 画像index）だけを根拠にする。

確かめ方:

  ImageManifest.gs（実データ監査から生成）
    → ImageSupport.gs と同じ対応づけ
    → Client.html の renderOptions を実際に実行
    → 描かれたDOMのA/B/C/Dに、どの画像が入ったか

を、実際にNodeで動かして確認する。
data/image_audit.csv の ref_count とも突き合わせるので、
manifestの数字が実データから外れていれば落ちる。
"""

import csv
import json
import re
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from gas_source import (  # noqa: E402
    function_source,
    html_script,
    read,
    strip_comments,
)
from js_runtime import js_value, require_node, run  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
CLIENT = html_script('Client.html')

# ユーザーが確認対象として挙げた実問題。
IMAGE_OPTION_QUESTIONS = (
    'EXAM-A1-Q004',
    'EXAM-A1-Q010',
    'EXAM-A2-Q005',
    'EXAM-A2-Q010',
    'EXAM-A4-Q006',
    'EXAM-A4-Q008',
    'EXAM-B2-Q043',
    'EXAM-B3-Q003',
    # manifestのキー順が A,B,C,D になっていない問題。
    # 「順番で決め打ちしていないこと」の回帰テストとして必ず含める。
    'EXAM-A1-Q051',
)


def load_manifest():
    text = read('ImageManifest.gs')
    match = re.search(r'Object\.freeze\((\{.*\})\);', text, re.S)
    assert match, 'ImageManifest.gs の定義が読めません'
    return json.loads(match.group(1))


def load_audit():
    path = ROOT / 'data/image_audit.csv'
    with path.open(encoding='utf-8-sig', newline='') as handle:
        return {row['question_id']: row for row in csv.DictReader(handle)}


MANIFEST = load_manifest()
AUDIT = load_audit()

# ImageSupport.gs が manifest から option_images を組み立てている実装。
# ハーネスの再現がずれないよう、この行の存在も別テストで確認している。
BUNDLE_SIMULATION = """
function buildBundle(manifest, refCount) {
  const encoded = [];
  for (let i = 0; i < refCount; i++) {
    encoded.push({ ref_index: i, data_url: 'REF#' + i });
  }
  const questionImages = (manifest.q || []).map(i => encoded[Number(i)]).filter(Boolean);
  const optionImages = {};
  Object.keys(manifest.o || {}).forEach(letter => {
    optionImages[letter] = (manifest.o[letter] || []).map(i => encoded[Number(i)]).filter(Boolean);
  });
  return { question_images: questionImages, option_images: optionImages };
}
"""

FAKE_DOM = """
function makeEl(tag) {
  const el = {
    tag: tag,
    className: '',
    dataset: {},
    textContent: '',
    src: '',
    alt: '',
    type: '',
    innerHTML: '',
    attributes: {},
    children: [],
    listeners: {}
  };
  el.classList = {
    add: name => { el.className = (el.className + ' ' + name).trim(); },
    remove: () => {},
    toggle: () => {}
  };
  el.setAttribute = (key, value) => { el.attributes[key] = value; };
  el.addEventListener = (type, fn) => {
    el.listeners[type] = el.listeners[type] || [];
    el.listeners[type].push(fn);
  };
  el.append = (...kids) => { kids.forEach(kid => el.children.push(kid)); };
  el.appendChild = kid => { el.children.push(kid); return kid; };
  return el;
}

const document = { createElement: makeEl };
const AREA = makeEl('div');
const $ = id => {
  if (id === 'optionsArea') return AREA;
  throw new Error('想定外のid: ' + id);
};

const CALLS = { selected: [], zoomed: [], rejected: [] };
function selectAnswer(letter) { CALLS.selected.push(letter); }
function openLightbox(src, label) { CALLS.zoomed.push({ src: src, label: label }); }
function rejectRenderedImage(q, reason) { CALLS.rejected.push(reason); }

function imagesIn(el) {
  let found = el.tag === 'img' ? [{ src: el.src, alt: el.alt }] : [];
  el.children.forEach(kid => { found = found.concat(imagesIn(kid)); });
  return found;
}

function describeRows() {
  return AREA.children.map(row => {
    const button = row.children.find(kid => kid.className.indexOf('answer-btn') >= 0) || null;
    const zoom = row.children.find(kid => kid.className.indexOf('zoom-btn') >= 0) || null;
    return {
      option: button ? button.dataset.option : null,
      // 画像が回答ボタンの内側にあること = 画像タップでもその選択肢が選ばれる
      images_inside_button: button ? imagesIn(button) : [],
      images_outside_button: imagesIn(row).length - (button ? imagesIn(button).length : 0),
      text: button ? textOf(button) : '',
      has_zoom: !!zoom,
      zoom_inside_button: button ? imagesIn(button).length >= 0 && hasChild(button, zoom) : false
    };
  });
}

function hasChild(el, target) {
  if (!target) return false;
  if (el === target) return true;
  return el.children.some(kid => hasChild(kid, target));
}

function textOf(el) {
  let out = el.className.indexOf('option-text') >= 0 ? el.textContent : '';
  el.children.forEach(kid => { out += textOf(kid); });
  return out;
}

function clickOption(index) {
  const row = AREA.children[index];
  const button = row.children.find(kid => kid.className.indexOf('answer-btn') >= 0);
  (button.listeners.click || []).forEach(fn => fn({}));
}

function clickZoom(index) {
  const row = AREA.children[index];
  const zoom = row.children.find(kid => kid.className.indexOf('zoom-btn') >= 0);
  const event = { stopPropagation: () => {}, preventDefault: () => {} };
  (zoom.listeners.click || []).forEach(fn => fn(event));
}
"""


def placeholder_options(letters):
    return {letter: '[' + letter + 'の画像選択肢]' for letter in letters}


def render(question_id, options, extra_driver=''):
    """実際の manifest を使って renderOptions を動かし、描かれた内容を返す。"""
    manifest = MANIFEST[question_id]
    ref_count = int(AUDIT[question_id]['ref_count'])

    script = '\n'.join([
        FAKE_DOM,
        BUNDLE_SIMULATION,
        client_source(),
        'const manifest = %s;' % js_value(manifest),
        'const bundle = buildBundle(manifest, %d);' % ref_count,
        'const q = { question_id: %s, options: %s };' % (
            js_value(question_id), js_value(options)
        ),
        'renderOptions(q, bundle.option_images);',
        extra_driver,
        'console.log(JSON.stringify({ rows: describeRows(), calls: CALLS }));',
    ])
    return run(script)


def client_source():
    placeholder = re.search(
        r'const OPTION_IMAGE_PLACEHOLDER =[\s\S]*?;', CLIENT
    ).group(0)
    return '\n'.join([
        placeholder,
        function_source(CLIENT, 'isOptionImagePlaceholder'),
        function_source(CLIENT, 'renderOptions'),
    ])


class TestOptionLetterToImageMapping(unittest.TestCase):
    """A/B/C/D と画像の対応。"""

    def setUp(self):
        require_node(self)

    def test_manifest_matches_the_audited_reference_count(self):
        """manifestのindexが、実データの画像枚数の範囲に収まっていること。"""
        for qid in IMAGE_OPTION_QUESTIONS:
            self.assertIn(qid, MANIFEST, f'{qid} が ImageManifest.gs にありません')
            self.assertIn(qid, AUDIT, f'{qid} が data/image_audit.csv にありません')

            entry = MANIFEST[qid]
            ref_count = int(AUDIT[qid]['ref_count'])
            self.assertEqual(entry['s'], AUDIT[qid]['signature'],
                             f'{qid} の画像リスト署名が監査値と違います')

            used = list(entry.get('q', []))
            for letter in ('A', 'B', 'C', 'D'):
                used.extend(entry.get('o', {}).get(letter, []))
            self.assertEqual(sorted(used), list(range(ref_count)),
                             f'{qid} の画像割り当てが実データの枚数と合いません')

    def test_each_letter_gets_its_own_image(self):
        """A/B/C/D が、それぞれ別の画像を1枚ずつ指していること。"""
        for qid in IMAGE_OPTION_QUESTIONS:
            options = MANIFEST[qid].get('o', {})
            indexes = {}
            for letter in ('A', 'B', 'C', 'D'):
                self.assertIn(letter, options, f'{qid} の選択肢 {letter} に画像がありません')
                self.assertEqual(len(options[letter]), 1,
                                 f'{qid} の選択肢 {letter} の画像枚数が1枚ではありません')
                indexes[letter] = options[letter][0]

            self.assertEqual(len(set(indexes.values())), 4,
                             f'{qid} で同じ画像が複数の選択肢へ割り当てられています: {indexes}')
            for index in indexes.values():
                self.assertNotIn(index, MANIFEST[qid].get('q', []),
                                 f'{qid} で問題画像が選択肢画像として使われています')

    def test_rendered_options_match_the_manifest(self):
        """描画結果のA/B/C/Dが、manifestの指す画像とぴったり一致すること。"""
        for qid in IMAGE_OPTION_QUESTIONS:
            with self.subTest(question_id=qid):
                result = render(qid, placeholder_options(['A', 'B', 'C', 'D']))
                rows = result['rows']

                self.assertEqual([row['option'] for row in rows], ['A', 'B', 'C', 'D'],
                                 f'{qid} の選択肢がA/B/C/Dの順に並んでいません')
                self.assertEqual(result['calls']['rejected'], [],
                                 f'{qid} が画像不足として弾かれています')

                for row in rows:
                    letter = row['option']
                    expected = ['REF#%d' % i for i in MANIFEST[qid]['o'][letter]]
                    actual = [image['src'] for image in row['images_inside_button']]
                    self.assertEqual(
                        actual, expected,
                        f'{qid} の選択肢 {letter} に別の画像が入っています',
                    )
                    self.assertIn(letter, row['images_inside_button'][0]['alt'],
                                  f'{qid} の選択肢 {letter} の代替テキストが違います')

    def test_image_is_inside_the_answer_button(self):
        """画像部分をタップしてもその選択肢を選べること（画像はボタンの内側）。"""
        result = render('EXAM-A2-Q005', placeholder_options(['A', 'B', 'C', 'D']))
        for row in result['rows']:
            self.assertTrue(row['images_inside_button'],
                            '選択肢画像が回答ボタンの外に置かれています')
            self.assertEqual(row['images_outside_button'], 0,
                             '回答ボタンの外に選択肢画像があります')

    def test_bundle_simulation_matches_image_support(self):
        """ハーネスの対応づけが ImageSupport.gs の実装と同じであること。"""
        body = strip_comments(read('ImageSupport.gs'))
        self.assertIn(
            "optionImages[letter] = (manifest.o[letter] || []).map(i => encoded[Number(i)]).filter(Boolean);",
            body,
            'ImageSupport.gs の選択肢画像の対応づけが変わっています',
        )
        self.assertIn(
            "const questionImages = (manifest.q || []).map(i => encoded[Number(i)]).filter(Boolean);",
            body,
            'ImageSupport.gs の問題画像の対応づけが変わっています',
        )

    def test_client_looks_up_images_by_letter_not_by_position(self):
        body = strip_comments(function_source(CLIENT, 'renderOptions'))
        self.assertIn('optionImages[letter]', body,
                      '選択肢画像を文字（A/B/C/D）で引いていません')
        self.assertNotRegex(
            body, r'optionImages\[\s*(?:index|idx|i)\s*\]',
            '選択肢画像を並び順で引いています',
        )


class TestZoomIsSeparateFromAnswering(unittest.TestCase):
    """拡大操作と回答選択の分離。"""

    def setUp(self):
        require_node(self)

    def test_zoom_button_does_not_select_the_option(self):
        result = render(
            'EXAM-A4-Q006',
            placeholder_options(['A', 'B', 'C', 'D']),
            extra_driver='clickZoom(1);',
        )
        self.assertEqual(result['calls']['selected'], [],
                         '拡大しただけでA/B/C/Dが選択されています')
        self.assertEqual(len(result['calls']['zoomed']), 1, '拡大が動いていません')
        self.assertEqual(result['calls']['zoomed'][0]['src'], 'REF#2',
                         '選択肢Bの拡大でB以外の画像を開いています')

    def test_answer_button_selects_the_option(self):
        result = render(
            'EXAM-A4-Q006',
            placeholder_options(['A', 'B', 'C', 'D']),
            extra_driver='clickOption(2);',
        )
        self.assertEqual(result['calls']['selected'], ['C'],
                         '回答ボタンを押しても選択されていません')
        self.assertEqual(result['calls']['zoomed'], [],
                         '回答を選んだだけで拡大表示が開いています')

    def test_zoom_button_is_outside_the_answer_button(self):
        result = render('EXAM-A2-Q010', placeholder_options(['A', 'B', 'C', 'D']))
        for row in result['rows']:
            self.assertTrue(row['has_zoom'], '画像選択肢に拡大ボタンがありません')
            self.assertFalse(row['zoom_inside_button'],
                             '拡大ボタンが回答ボタンの内側にあります')

    def test_zoom_handler_stops_propagation(self):
        body = strip_comments(function_source(CLIENT, 'renderOptions'))
        self.assertRegex(
            body,
            r"zoom\.addEventListener\('click', e => \{[\s\S]{0,160}?e\.stopPropagation\(\)",
            '拡大ボタンの操作が回答ボタンへ伝わってしまいます',
        )


class TestOptionPlaceholderText(unittest.TestCase):
    """内部用プレースホルダの扱い。"""

    def setUp(self):
        require_node(self)

    def test_placeholder_is_hidden_when_the_image_is_shown(self):
        result = render('EXAM-A2-Q005', placeholder_options(['A', 'B', 'C', 'D']))
        for row in result['rows']:
            self.assertEqual(row['text'], '',
                             '内部用のプレースホルダが画面へ出ています: ' + row['text'])
            self.assertTrue(row['images_inside_button'],
                            'プレースホルダを隠したのに画像がありません')

    def test_real_option_text_is_never_hidden(self):
        result = render(
            'EXAM-A2-Q005',
            {'A': '正規化する', 'B': '標準化する', 'C': '対数を取る', 'D': '何もしない'},
        )
        self.assertEqual(
            [row['text'] for row in result['rows']],
            ['正規化する', '標準化する', '対数を取る', '何もしない'],
            '本物の選択肢テキストが消えています',
        )

    def test_placeholder_without_an_image_stops_the_question(self):
        """画像が無い状態で、文字だけ隠して回答させないこと。"""
        script = '\n'.join([
            FAKE_DOM,
            client_source(),
            "const q = { question_id: 'X', options: %s };"
            % js_value(placeholder_options(['A', 'B', 'C', 'D'])),
            # Cだけ画像が取れなかった状況
            "renderOptions(q, { A: [{ data_url: 'a' }], B: [{ data_url: 'b' }], D: [{ data_url: 'd' }] });",
            'console.log(JSON.stringify({ rows: describeRows(), calls: CALLS }));',
        ])
        result = run(script)
        self.assertEqual(result['calls']['rejected'], ['OPTION_IMAGE_MISSING:C'],
                         '画像が無い画像選択肢のまま回答できてしまいます')
        self.assertEqual(result['calls']['selected'], [])

    def test_placeholder_patterns(self):
        """実際に使われうる書き方をまとめて判定できること。"""
        script = '\n'.join([
            client_source().split('function renderOptions')[0],
            'const samples = %s;' % js_value([
                '[Aの画像選択肢]', '［Bの画像選択肢］', '[Cの画像選択肢]', '[Dの画像選択肢]',
                '[画像選択肢A]', '[選択肢Aの画像]', '（Aの画像）', 'Aの画像選択肢',
                '正規化する', 'Aは画像を正規化する処理である', '', '画像認識の説明',
            ]),
            'console.log(JSON.stringify(samples.map(isOptionImagePlaceholder)));',
        ])
        self.assertEqual(
            run(script),
            [True, True, True, True, True, True, True, True, False, False, False, False],
            'プレースホルダの判定が想定と違います',
        )


if __name__ == '__main__':
    unittest.main(verbosity=2)
