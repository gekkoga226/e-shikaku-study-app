#!/usr/bin/env bash
#
# 機密情報がリポジトリへ混入していないかを確認する。
#
# 見るもの:
#   1. コミットしてはいけないファイル名（.clasprc.json など）
#   2. 認証トークンらしき文字列（Googleのrefresh token / APIキー / 秘密鍵 など）
#
# 検査対象は「Gitが管理しているファイル」だけ。
# node_modules や .git は見ない。
#
set -uo pipefail

fail=0

# ---------------------------------------------------------------
# 1. 追跡してはいけないファイル
# ---------------------------------------------------------------
FORBIDDEN_GLOBS=(
  '.clasprc.json'
  '*/.clasprc.json'
  '.clasp.json'
  '*/.clasp.json'
  'client_secret*.json'
  'credentials.json'
  'service-account*.json'
  '*.pem'
  '*.p12'
  '.env'
)

if git rev-parse --is-inside-work-tree >/dev/null 2>&1; then
  # TRACKED  : Gitが実際に管理しているファイル（コミット済み/ステージ済み）
  # SCANNABLE: それに加えて、まだコミットしていないファイルも含める。
  #            .gitignore で除外済みのものは対象外（= 正しく守られている）。
  mapfile -t TRACKED < <(git ls-files)
  mapfile -t SCANNABLE < <(git ls-files --cached --others --exclude-standard)
else
  echo "Gitリポジトリではないため、ファイル一覧をディレクトリから作成します。"
  mapfile -t TRACKED < <(find . -type f -not -path './.git/*' -not -path './node_modules/*' | sed 's|^\./||')
  SCANNABLE=("${TRACKED[@]}")
fi

for glob in "${FORBIDDEN_GLOBS[@]}"; do
  for f in "${TRACKED[@]}"; do
    # shellcheck disable=SC2053
    if [[ "${f}" == ${glob} ]]; then
      echo "NG: 認証情報ファイルがGit管理下にあります: ${f}" >&2
      fail=1
    fi
  done
done

# ---------------------------------------------------------------
# 2. 認証トークンらしき文字列
# ---------------------------------------------------------------
# 説明用に単語（refresh_token など）を書いた文書で誤検知しないよう、
# 「実際の値の形」だけを検出する。
PATTERNS=(
  'ya29\.[0-9A-Za-z_\-]{25,}'                          # Google access token
  '1//[0-9A-Za-z_\-]{30,}'                             # Google refresh token
  'AIza[0-9A-Za-z_\-]{35}'                             # Google API key
  'GOCSPX-[0-9A-Za-z_\-]{20,}'                         # Google OAuth client secret
  '"private_key"[[:space:]]*:[[:space:]]*"-{3,}'       # service account key
  '-----BEGIN [A-Z ]{0,20}PRIVATE KEY-----'            # PEM秘密鍵
  '"refresh_token"[[:space:]]*:[[:space:]]*"[^"]{20,}"' # 値入りrefresh_token
  'ghp_[0-9A-Za-z]{30,}'                               # GitHub personal access token
  'github_pat_[0-9A-Za-z_]{40,}'                       # GitHub fine-grained token
)

for f in "${SCANNABLE[@]}"; do
  [[ -f "${f}" ]] || continue
  # バイナリはスキップ
  if ! grep -Iq . "${f}" 2>/dev/null; then
    continue
  fi
  for pat in "${PATTERNS[@]}"; do
    if grep -nEq "${pat}" "${f}" 2>/dev/null; then
      # 値そのものはログへ出さない。ファイル名と行番号だけ知らせる。
      lines=$(grep -nE "${pat}" "${f}" | cut -d: -f1 | tr '\n' ',' | sed 's/,$//')
      echo "NG: 認証情報らしき文字列を検出しました: ${f} (行: ${lines})" >&2
      fail=1
    fi
  done
done

if [[ "${fail}" -ne 0 ]]; then
  echo "" >&2
  echo "機密情報チェックに失敗しました。値はログへ出していません。" >&2
  echo "該当ファイルから削除し、GitHub Secrets へ登録してください。" >&2
  exit 1
fi

echo "機密情報チェック: 問題ありません。(${#SCANNABLE[@]} ファイルを検査)"
