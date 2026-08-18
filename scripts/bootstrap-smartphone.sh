#!/usr/bin/env bash
#
# ============================================================
#  E資格 学習Webアプリ  初回セットアップ（スマホ + Codespaces 用）
# ============================================================
#
#  使い方:
#      bash scripts/bootstrap-smartphone.sh
#
#  このスクリプトができること（自動）:
#      - 環境の確認（Node.js 20以上 など）
#      - 必要な部品のインストール（npm install）
#      - 検査（構文・テスト・機密情報・必須ファイル）
#      - Apps Scriptプロジェクトの作成、または既存プロジェクトへの接続
#      - Apps Scriptへの反映（push）
#      - 版の作成（version）
#      - 初回Webアプリ公開（deployment）
#      - GitHub Secrets への登録補助
#      - 最終的なWebアプリURLの表示
#
#  このスクリプトが「代行しない」こと（あなた本人の操作）:
#      - Googleへのログインと権限の許可
#      - Apps Script API の有効化
#      - Googleアカウントのパスワード・2段階認証
#
#  何度実行しても大丈夫です。途中で止めた場合は、もう一度実行してください。
#
set -euo pipefail

# ------------------------------------------------------------
# 表示まわり
# ------------------------------------------------------------
if [[ -t 1 ]]; then
  BOLD=$'\033[1m'; GREEN=$'\033[32m'; YELLOW=$'\033[33m'; RED=$'\033[31m'; CYAN=$'\033[36m'; RESET=$'\033[0m'
else
  BOLD=""; GREEN=""; YELLOW=""; RED=""; CYAN=""; RESET=""
fi

STEP_NO=0
step() {
  STEP_NO=$((STEP_NO + 1))
  echo ""
  echo "${BOLD}${CYAN}============================================================${RESET}"
  echo "${BOLD}${CYAN} STEP ${STEP_NO}. $*${RESET}"
  echo "${BOLD}${CYAN}============================================================${RESET}"
}
ok()    { echo "${GREEN}OK${RESET}   $*"; }
warn()  { echo "${YELLOW}注意${RESET} $*"; }
die()   { echo "${RED}エラー${RESET} $*" >&2; exit 1; }
note()  { echo "     $*"; }

ask_yes_no() {
  # $1 = 質問文 / 戻り値0 = はい
  local answer
  while true; do
    read -r -p "$1 [y/N]: " answer </dev/tty || answer="n"
    case "${answer}" in
      y|Y|yes|YES|はい) return 0 ;;
      n|N|no|NO|""|いいえ) return 1 ;;
      *) echo "y または n を入力してください。" ;;
    esac
  done
}

pause_until_done() {
  echo ""
  read -r -p "$1 が終わったら Enter キーを押してください: " _ </dev/tty || true
}

# ------------------------------------------------------------
# 位置
# ------------------------------------------------------------
REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "${REPO_ROOT}"

CLASP_BIN="${REPO_ROOT}/node_modules/.bin/clasp"
# clasp のログイン情報の保存先をはっきり決めておく（後でSecretsへ登録するため）。
export clasp_config_auth="${HOME}/.clasprc.json"

echo ""
echo "${BOLD}E資格 学習Webアプリ / 初回セットアップを始めます${RESET}"
echo "作業フォルダ: ${REPO_ROOT}"

# ============================================================
step "必要な環境がそろっているか確認します"
# ============================================================
command -v node >/dev/null 2>&1 || die "Node.js が見つかりません。GitHub Codespaces で開き直してください。"
command -v npm  >/dev/null 2>&1 || die "npm が見つかりません。GitHub Codespaces で開き直してください。"
command -v git  >/dev/null 2>&1 || die "git が見つかりません。"

NODE_MAJOR="$(node -p 'process.versions.node.split(".")[0]')"
if [[ "${NODE_MAJOR}" -lt 20 ]]; then
  die "Node.js 20以上が必要です（今: $(node -v)）。.devcontainer 付きの Codespaces で開き直してください。"
fi
ok "Node.js $(node -v)"
ok "npm v$(npm -v)"

if command -v python3 >/dev/null 2>&1; then
  ok "Python $(python3 -V 2>&1 | awk '{print $2}')"
else
  warn "python3 が見つかりません。テストの一部を実行できません。"
fi

if [[ ! -d src ]]; then
  die "src フォルダがありません。リポジトリの直下で実行してください。"
fi
ok "アプリ本体 src/ を確認しました"

# ============================================================
step "必要な部品をインストールします（npm install）"
# ============================================================
if [[ -f package-lock.json ]]; then
  npm ci --no-audit --no-fund
else
  npm install --no-audit --no-fund
fi
[[ -x "${CLASP_BIN}" ]] || die "clasp のインストールに失敗しました。"
ok "clasp $("${CLASP_BIN}" --version) を用意しました"

# ============================================================
step "コードの検査をします（壊れたまま公開しないため）"
# ============================================================
if npm run --silent verify; then
  ok "検査に合格しました"
else
  die "検査に失敗しました。この状態では公開しません。上のエラーを直してください。"
fi

# ============================================================
step "Google へログインします（あなた本人の操作が必要です）"
# ============================================================
echo "ここは自動化できません。"
echo "Googleのログインと「このアプリにあなたのスプレッドシートを触らせてよいか」の許可は、"
echo "あなた本人がGoogleの画面で行う必要があります。"
echo ""

if "${CLASP_BIN}" show-authorized-user >/dev/null 2>&1; then
  ok "すでにGoogleへログイン済みです"
  "${CLASP_BIN}" show-authorized-user || true
  if ask_yes_no "別のGoogleアカウントでログインし直しますか？"; then
    NEED_LOGIN=1
  else
    NEED_LOGIN=0
  fi
else
  NEED_LOGIN=1
fi

if [[ "${NEED_LOGIN}" == "1" ]]; then
  echo ""
  echo "${BOLD}これから次のことが起きます:${RESET}"
  echo "  1. 画面に長いURLが表示されます"
  echo "  2. そのURLをスマホのブラウザで開きます（長押し→リンクを開く）"
  echo "  3. E資格のスプレッドシートを持っているGoogleアカウントでログインします"
  echo "  4. 「許可」を押します"
  echo "  5. 画面に出るコードをコピーして、このターミナルへ貼り付けます"
  echo ""
  echo "${YELLOW}--no-localhost を付けて実行します（スマホ/Codespacesではこれが必要です）${RESET}"
  pause_until_done "内容の確認"
  "${CLASP_BIN}" login --no-localhost </dev/tty || die "Googleログインが完了しませんでした。もう一度実行してください。"
  ok "Googleログインが完了しました"
fi

if [[ ! -f "${clasp_config_auth}" ]]; then
  die "ログイン情報ファイルが見つかりません: ${clasp_config_auth}"
fi

# ============================================================
step "Apps Script API が有効か確認します"
# ============================================================
echo "Apps Script API とは、GitHubから Apps Script を書き換えるための入口です。"
echo "Googleの設定画面で1回だけ「オン」にする必要があります。"
echo ""

api_ok() { "${CLASP_BIN}" --json list-scripts >/dev/null 2>&1; }

if api_ok; then
  ok "Apps Script API は有効です"
else
  warn "Apps Script API がまだ有効でないようです。"
  echo ""
  echo "${BOLD}スマホで次の操作をしてください:${RESET}"
  echo "  1. ブラウザで  https://script.google.com/home/usersettings  を開く"
  echo "  2. 「Google Apps Script API」のスイッチを ${BOLD}オン${RESET} にする"
  echo "  3. このターミナルへ戻る"
  pause_until_done "スイッチをオンにする操作"
  if api_ok; then
    ok "Apps Script API が有効になりました"
  else
    die "まだ有効になっていません。数十秒待ってから、もう一度このスクリプトを実行してください。"
  fi
fi

# ============================================================
step "Apps Script プロジェクトを用意します"
# ============================================================
write_clasp_json() {
  # $1 = scriptId
  node -e '
    const fs = require("fs");
    const scriptId = process.argv[1];
    fs.writeFileSync(".clasp.json", JSON.stringify({ scriptId, rootDir: "src" }, null, 2) + "\n");
  ' "$1"
}

if [[ -f .clasp.json ]]; then
  EXISTING_ID="$(node -p 'JSON.parse(require("fs").readFileSync(".clasp.json","utf8")).scriptId || ""')"
  EXISTING_ROOT="$(node -p 'JSON.parse(require("fs").readFileSync(".clasp.json","utf8")).rootDir || ""')"
  ok "すでに接続先が設定されています"
  note "scriptId = ${EXISTING_ID}"
  if [[ "${EXISTING_ROOT}" != "src" ]]; then
    warn "rootDir が src ではありません。src に直します。"
    write_clasp_json "${EXISTING_ID}"
  fi
else
  echo "接続先の Apps Script プロジェクトを決めます。"
  echo ""
  echo "  A) 新しく作る    … おすすめ。今あるアプリを壊しません。"
  echo "  B) 既存につなぐ  … すでにApps ScriptプロジェクトがあってそのURLを更新したい場合。"
  echo ""
  CHOICE=""
  while [[ ! "${CHOICE}" =~ ^[AaBb]$ ]]; do
    read -r -p "A か B を入力してください: " CHOICE </dev/tty || CHOICE="A"
  done

  if [[ "${CHOICE}" =~ ^[Bb]$ ]]; then
    echo ""
    echo "Apps Scriptの画面URLの、次の部分がスクリプトIDです:"
    echo "  https://script.google.com/d/${BOLD}ここがスクリプトID${RESET}/edit"
    SCRIPT_ID=""
    while [[ -z "${SCRIPT_ID}" ]]; do
      read -r -p "スクリプトIDを貼り付けてください: " SCRIPT_ID </dev/tty || true
      SCRIPT_ID="$(printf '%s' "${SCRIPT_ID}" | tr -d '[:space:]')"
    done
    write_clasp_json "${SCRIPT_ID}"
    ok "既存プロジェクトへ接続しました"
  else
    DEFAULT_TITLE="E資格 学習Webアプリ v2"
    read -r -p "新しいプロジェクト名 [${DEFAULT_TITLE}]: " TITLE </dev/tty || TITLE=""
    TITLE="${TITLE:-${DEFAULT_TITLE}}"

    # 重要:
    #   clasp create-script は「作った直後の空プロジェクト」をローカルへ取り込む。
    #   そのまま src/ を指定すると appsscript.json が上書きされ、Code.js が増えてしまう。
    #   そこで、いったん一時フォルダで作成し、scriptId だけを受け取る。
    TMP_CREATE_DIR="$(mktemp -d)"
    trap 'rm -rf "${TMP_CREATE_DIR}"' EXIT
    CREATE_JSON="$(cd "${TMP_CREATE_DIR}" && "${CLASP_BIN}" --json create-script --type standalone --title "${TITLE}")" \
      || die "Apps Scriptプロジェクトの作成に失敗しました。"
    SCRIPT_ID="$(printf '%s' "${CREATE_JSON}" | node -e '
      let raw=""; process.stdin.on("data",d=>raw+=d);
      process.stdin.on("end",()=>{ process.stdout.write(JSON.parse(raw).scriptId || ""); });
    ')"
    rm -rf "${TMP_CREATE_DIR}"
    trap - EXIT
    [[ -n "${SCRIPT_ID}" ]] || die "スクリプトIDを取得できませんでした。"
    write_clasp_json "${SCRIPT_ID}"
    ok "新しいApps Scriptプロジェクトを作成しました"
    note "scriptId = ${SCRIPT_ID}"
    note "編集画面 = https://script.google.com/d/${SCRIPT_ID}/edit"
  fi
fi

# .clasp.json は認証情報ではないが、Gitへは入れない運用にしている。
if git check-ignore -q .clasp.json; then
  ok ".clasp.json はGitへコミットされません（.gitignore 済み）"
else
  warn ".clasp.json が .gitignore で除外されていません。確認してください。"
fi

# ============================================================
step "Apps Script へ反映し、初回のWebアプリを公開します"
# ============================================================
echo "ここで行うこと:"
echo "  1. src/ の中身を Apps Script へ送る"
echo "  2. 巻き戻せるように「版」を作る"
echo "  3. WebアプリのURLを作る（初回のみ）／既にあれば更新する"
echo ""

DEPLOY_LOG="$(mktemp)"
set +e
CREATE_IF_MISSING=1 \
DEPLOY_DESCRIPTION="bootstrap $(date -u '+%Y-%m-%dT%H:%M:%SZ')" \
  bash scripts/deploy.sh 2>&1 | tee "${DEPLOY_LOG}"
DEPLOY_STATUS="${PIPESTATUS[0]}"
set -e

if [[ "${DEPLOY_STATUS}" -ne 0 ]]; then
  rm -f "${DEPLOY_LOG}"
  die "反映に失敗しました。上のメッセージを確認してください。"
fi

DEPLOYMENT_ID="$(grep -oE 'DEPLOYMENT_ID = [A-Za-z0-9_-]+' "${DEPLOY_LOG}" | tail -n 1 | awk '{print $3}')"
WEBAPP_URL="$(grep -oE 'https://script\.google\.com/macros/s/[A-Za-z0-9_-]+/exec' "${DEPLOY_LOG}" | tail -n 1)"
rm -f "${DEPLOY_LOG}"
ok "Apps Script への反映が終わりました"

# ============================================================
step "GitHub Secrets を登録します（2回目以降の自動デプロイのため）"
# ============================================================
echo "GitHub Secrets とは、GitHubに安全にしまっておく「鍵の保管庫」です。"
echo "ここへ入れた値はコードに残らず、実行ログにも出ません。"
echo ""
echo "登録するもの:"
echo "  CLASPRC_JSON   … Googleのログイン情報（あなたの鍵）"
echo "  CLASP_JSON     … どのApps Scriptプロジェクトへ送るかの情報"
echo "  DEPLOYMENT_ID  … 更新するWebアプリの番号"
echo ""

SECRETS_DONE=0
if command -v gh >/dev/null 2>&1 && gh auth status >/dev/null 2>&1; then
  if ask_yes_no "GitHub CLI を使って自動登録しますか？"; then
    set +e
    gh secret set CLASPRC_JSON < "${clasp_config_auth}"
    R1=$?
    gh secret set CLASP_JSON < .clasp.json
    R2=$?
    R3=0
    if [[ -n "${DEPLOYMENT_ID}" ]]; then
      printf '%s' "${DEPLOYMENT_ID}" | gh secret set DEPLOYMENT_ID
      R3=$?
    fi
    set -e
    if [[ "${R1}" -eq 0 && "${R2}" -eq 0 && "${R3}" -eq 0 ]]; then
      SECRETS_DONE=1
      ok "GitHub Secrets を登録しました（値は画面に出していません）"
    else
      warn "自動登録に失敗しました。権限が足りない可能性があります。"
      note "次を実行してから、もう一度このスクリプトを動かすと成功することがあります:"
      note "  gh auth refresh -h github.com -s repo"
    fi
  fi
else
  warn "GitHub CLI (gh) が使えないため、自動登録はできません。"
fi

if [[ "${SECRETS_DONE}" -ne 1 ]]; then
  echo ""
  echo "${BOLD}手動で登録してください（スマホでOK）:${RESET}"
  echo "  GitHubのリポジトリ → Settings → Secrets and variables → Actions → New repository secret"
  echo ""
  echo "  ① Name: CLASPRC_JSON"
  echo "     Secret: 次のコマンドの出力をすべてコピーして貼り付け"
  echo "       cat ${clasp_config_auth}"
  echo ""
  echo "  ② Name: CLASP_JSON"
  echo "     Secret: 次のコマンドの出力をすべてコピーして貼り付け"
  echo "       cat .clasp.json"
  echo ""
  echo "  ③ Name: DEPLOYMENT_ID"
  if [[ -n "${DEPLOYMENT_ID}" ]]; then
    echo "     Secret: ${DEPLOYMENT_ID}"
  else
    echo "     Secret: 上の手順で表示された DEPLOYMENT_ID"
  fi
  echo ""
  echo "${YELLOW}貼り付けた画面のスクリーンショットは保存しないでください。${RESET}"
fi

# ============================================================
step "完了しました"
# ============================================================
echo ""
if [[ -n "${WEBAPP_URL}" ]]; then
  echo "${BOLD}${GREEN}あなたのWebアプリURL:${RESET}"
  echo "  ${BOLD}${WEBAPP_URL}${RESET}"
else
  echo "${BOLD}WebアプリURLは次の場所で確認できます:${RESET}"
  echo "  Apps Scriptの編集画面 → デプロイ → デプロイを管理"
fi
echo ""
echo "${BOLD}次にやること:${RESET}"
echo "  1. 上のURLをスマホのブラウザで開く"
echo "  2. 最初の1回だけGoogleの許可画面が出るので「許可」を押す"
echo "  3. 共有ボタン → 「ホーム画面に追加」でアプリのように使える"
echo ""
echo "${BOLD}これ以降:${RESET}"
echo "  GitHubの main ブランチが更新されると、自動でこのURLの中身が新しくなります。"
echo "  もう一度この設定をやり直す必要はありません。"
echo ""
echo "困ったときは SMARTPHONE_SETUP.md を読んでください。"
