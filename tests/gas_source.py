"""Apps Script ソースを読むための小さな補助。

外部ライブラリを使わずに、
「この関数の中身」「文字列を除いた本文」を取り出せるようにする。
"""

from pathlib import Path
import re

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / 'src'

GAS_FILES = [
    'Code.gs',
    'ImageManifest.gs',
    'ImageSupport.gs',
    'ImageSelfTest.gs',
]


def read(name: str) -> str:
    return (SRC / name).read_text(encoding='utf-8')


def strip_literals(source: str) -> str:
    """文字列リテラルと行コメント・ブロックコメントを空白へ置き換える。

    「コメントに書いてあるだけ」を実装と誤認しないために使う。
    中身は消すが文字数は保つので、位置の比較はそのまま使える。
    """
    out = []
    i = 0
    n = len(source)
    while i < n:
        ch = source[i]
        nxt = source[i + 1] if i + 1 < n else ''

        if ch == '/' and nxt == '/':
            j = source.find('\n', i)
            j = n if j == -1 else j
            out.append(' ' * (j - i))
            i = j
            continue

        if ch == '/' and nxt == '*':
            j = source.find('*/', i + 2)
            j = n if j == -1 else j + 2
            out.append(''.join(c if c == '\n' else ' ' for c in source[i:j]))
            i = j
            continue

        if ch in ('"', "'", '`'):
            quote = ch
            j = i + 1
            while j < n:
                if source[j] == '\\':
                    j += 2
                    continue
                if source[j] == quote:
                    j += 1
                    break
                j += 1
            out.append(''.join(c if c == '\n' else ' ' for c in source[i:j]))
            i = j
            continue

        out.append(ch)
        i += 1

    return ''.join(out)


def strip_comments(source: str) -> str:
    """コメントだけを空白へ置き換える（文字列リテラルは残す）。

    「コメントに書いてあるだけ」を実装と誤認せず、
    かつ文字列リテラルの中身は検査したい場合に使う。
    """
    out = []
    i = 0
    n = len(source)
    while i < n:
        ch = source[i]
        nxt = source[i + 1] if i + 1 < n else ''

        if ch == '/' and nxt == '/':
            j = source.find('\n', i)
            j = n if j == -1 else j
            out.append(' ' * (j - i))
            i = j
            continue

        if ch == '/' and nxt == '*':
            j = source.find('*/', i + 2)
            j = n if j == -1 else j + 2
            out.append(''.join(c if c == '\n' else ' ' for c in source[i:j]))
            i = j
            continue

        if ch in ('"', "'", '`'):
            quote = ch
            j = i + 1
            while j < n:
                if source[j] == '\\':
                    j += 2
                    continue
                if source[j] == quote:
                    j += 1
                    break
                j += 1
            out.append(source[i:j])
            i = j
            continue

        out.append(ch)
        i += 1

    return ''.join(out)


def function_body(source: str, name: str) -> str:
    """トップレベル関数 `name` の本文（{ } の中）を返す。"""
    stripped = strip_literals(source)
    match = re.search(r'\bfunction\s+' + re.escape(name) + r'\s*\(', stripped)
    if not match:
        raise AssertionError(f'関数 {name} が見つかりません')

    start = stripped.index('{', match.end() - 1)
    depth = 0
    for pos in range(start, len(stripped)):
        if stripped[pos] == '{':
            depth += 1
        elif stripped[pos] == '}':
            depth -= 1
            if depth == 0:
                return source[start + 1:pos]
    raise AssertionError(f'関数 {name} の終わりが見つかりません')


def all_gas_sources():
    return {name: read(name) for name in GAS_FILES}


def html_script(name: str) -> str:
    """HTMLファイルの <script> ブロックを連結して返す。"""
    html = read(name)
    blocks = re.findall(r'<script\b[^>]*>([\s\S]*?)</script>', html, re.IGNORECASE)
    if not blocks:
        raise AssertionError(f'{name} に <script> ブロックがありません')
    return '\n'.join(blocks)
