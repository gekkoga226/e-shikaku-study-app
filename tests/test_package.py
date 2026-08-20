"""既存の同梱テスト（ZIP版から継承）。

ZIP版では全ファイルがルート直下にあったが、GitHub管理版では
Apps Scriptへ送るファイルを src/ にまとめている。
検証内容そのものは変えず、参照先だけを src/ に合わせている。
"""

from pathlib import Path
import json
import re
import unittest

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / 'src'


def main():
    assert (SRC / 'Code.gs').exists()
    assert (SRC / 'ImageManifest.gs').exists()
    assert (SRC / 'ImageSupport.gs').exists()
    assert (SRC / 'Index.html').exists()
    assert (SRC / 'Client.html').exists()

    code = (SRC / 'Code.gs').read_text(encoding='utf-8')
    # 引数の数は増えることがある（先読みの判定用の旗など）。
    # ここで確かめたいのは「回答前の応答へ正解を載せていない」ことだけなので、
    # 引数の並びではなく関数の中身を見る。
    m = re.search(r'function publicQuestion_\([^)]*\) \{([\s\S]*?)\n\}', code)
    assert m, 'publicQuestion_ not found'
    assert 'correct_option' not in m.group(1), 'correct_option leaked by publicQuestion_'

    manifest_text = (SRC / 'ImageManifest.gs').read_text(encoding='utf-8')
    j = re.search(r'Object\.freeze\((\{.*\})\);', manifest_text, re.S)
    assert j, 'manifest JSON not found'
    manifest = json.loads(j.group(1))
    assert len(manifest) == 148
    assert manifest['EXAM-A4-Q039']['q'] == [0, 1]
    assert manifest['EXAM-A4-Q039']['s'] == '9c6d67191a37caa0'

    client = (SRC / 'Client.html').read_text(encoding='utf-8')
    assert 'preloadBundle(bundle)' in client
    assert 'renderQuestion(q, bundle, seq)' in client
    assert client.index('preloadBundle(bundle)') < client.index(
        'renderQuestion(q, bundle, seq)', client.index('preloadBundle(bundle)')
    )
    assert 'rejectRenderedImage' in client

    print('package_tests=PASS')
    print('manifest_entries=148')
    print('a4_q039=PASS')
    print('correct_answer_preload_leak=PASS')
    print('image_preload_before_render=PASS')


class TestPackage(unittest.TestCase):
    """`python3 -m unittest` からも同じ検証を実行できるようにする。"""

    def test_package(self):
        main()


if __name__ == '__main__':
    main()
