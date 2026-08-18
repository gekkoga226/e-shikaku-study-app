#!/usr/bin/env bash
#
# Apps Scriptへ送る必須ファイルが揃っているかを確認する。
# 1つでも欠けると、自動デプロイでアプリが壊れるため CI で落とす。
#
set -euo pipefail

SRC_DIR="src"

REQUIRED_FILES=(
  "appsscript.json"
  "Code.gs"
  "ImageManifest.gs"
  "ImageSupport.gs"
  "ImageSelfTest.gs"
  "Index.html"
  "Styles.html"
  "Client.html"
)

missing=0
for f in "${REQUIRED_FILES[@]}"; do
  if [[ -s "${SRC_DIR}/${f}" ]]; then
    printf 'OK      %s/%s\n' "${SRC_DIR}" "${f}"
  else
    printf 'MISSING %s/%s\n' "${SRC_DIR}" "${f}" >&2
    missing=$((missing + 1))
  fi
done

if [[ "${missing}" -gt 0 ]]; then
  echo "必須ファイルが ${missing} 件不足しています。デプロイできません。" >&2
  exit 1
fi

# .claspignore は「全部除外 → 必要なものだけ許可」の形式。
# 必須ファイルが許可されていないと push から漏れるので合わせて確認する。
for f in "${REQUIRED_FILES[@]}"; do
  if ! grep -qxF "!${f}" .claspignore; then
    echo "\`!${f}\` が .claspignore にありません。push対象から漏れます。" >&2
    exit 1
  fi
done

echo "必須ファイル確認: すべて揃っています。"
