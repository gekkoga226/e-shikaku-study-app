"""Apps Script / クライアントの関数を、実際にNodeで動かすための小さな土台。

静的検査（文字列が書いてあるか）だけでは
「未回答モードが回答済み問題を選ばないこと」のような
"挙動" を確かめられない。

ここでは Code.gs / Client.html から必要な関数だけを取り出し、
Google側のAPI（SpreadsheetApp など）に触れない形で組み立てて実行する。
スプレッドシートへは一切アクセスしない。
"""

import json
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from gas_source import function_source  # noqa: E402

NODE = shutil.which('node')


def require_node(testcase: unittest.TestCase) -> None:
    if not NODE:
        testcase.skipTest('node が見つからないため、実行テストをスキップします')


def collect(source: str, names) -> str:
    """指定した関数の宣言をそのまま連結する。"""
    return '\n\n'.join(function_source(source, name) for name in names)


def run(script: str):
    """JavaScriptを実行し、最後に出力されたJSONを返す。"""
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / 'harness.mjs'
        path.write_text(script, encoding='utf-8')
        result = subprocess.run(
            [NODE, str(path)], capture_output=True, text=True, timeout=60
        )
    if result.returncode != 0:
        raise AssertionError(
            'ハーネスの実行に失敗しました:\n' + result.stdout + '\n' + result.stderr
        )
    return json.loads(result.stdout.strip().splitlines()[-1])


def js_value(value) -> str:
    """PythonのデータをJavaScriptのリテラルとして埋め込む。"""
    return json.dumps(value, ensure_ascii=False)
