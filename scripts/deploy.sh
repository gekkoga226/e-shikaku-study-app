#!/usr/bin/env bash
#
# Apps Script への反映を行う共通スクリプト。
#
#   0. 更新先のデプロイを先に決める（決められなければ何もせず止まる）
#   1. clasp push              … src/ の中身をApps Scriptへ送る
#   2. clasp create-version    … 巻き戻せるように「版」を固定する
#   3. clasp update-deployment … 既存のWebアプリURLを新しい版へ切り替える
#
# 更新先を先に決めるのは、
# 「送ったのに公開先が分からない」という中途半端な状態を避けるため。
#
# GitHub Actions からも scripts/bootstrap-smartphone.sh からも呼ばれる。
#
# 前提（呼び出し側が用意する）:
#   - .clasp.json が存在する（scriptId と rootDir）
#   - clasp のログイン情報が使える状態である
#
# 環境変数:
#   DEPLOYMENT_ID      更新したいWebアプリのデプロイID。
#                      未指定の場合、既存のWebアプリデプロイが1つだけならそれを使う。
#   CREATE_IF_MISSING  1 のとき、既存デプロイが無ければ新規作成する（初回セットアップ用）。
#   DEPLOY_DESCRIPTION 版とデプロイに付ける説明文。
#
set -euo pipefail

CLASP="npx --no-install clasp"
DESCRIPTION="${DEPLOY_DESCRIPTION:-manual deploy}"
DEPLOYMENT_ID="${DEPLOYMENT_ID:-}"
CREATE_IF_MISSING="${CREATE_IF_MISSING:-0}"
CREATE_NEW_DEPLOYMENT=0

if [[ ! -f .clasp.json ]]; then
  echo "エラー: .clasp.json がありません。" >&2
  echo "  ローカル/Codespaces: bash scripts/bootstrap-smartphone.sh を実行してください。" >&2
  echo "  GitHub Actions     : CLASP_JSON シークレットを登録してください。" >&2
  exit 1
fi

# JSONの読み取りは node で行う（jq が無い環境でも動くようにするため）。
json_get() {
  node -e '
    let raw = "";
    process.stdin.on("data", d => (raw += d));
    process.stdin.on("end", () => {
      let data;
      try { data = JSON.parse(raw); } catch (e) { process.exit(2); }
      const fn = new Function("data", process.argv[1]);
      const out = fn(data);
      if (out === undefined || out === null) process.exit(3);
      process.stdout.write(String(out));
    });
  ' "$1"
}

echo "=============================================="
echo " 0/3  更新するWebアプリを確認します"
echo "=============================================="

if [[ -n "${DEPLOYMENT_ID}" ]]; then
  echo "指定されたデプロイを更新します。"
else
  echo "DEPLOYMENT_ID が指定されていないため、既存デプロイを調べます..."
  DEPLOYMENTS_JSON="$(${CLASP} --json list-deployments)"
  # @HEAD（開発用の、常に最新を指すデプロイ）は版を持たないので除外する。
  CANDIDATES="$(printf '%s' "${DEPLOYMENTS_JSON}" | json_get '
    const list = (data || []).filter(d => d && d.versionNumber != null);
    return list.map(d => d.deploymentId).join("\n");
  ' || true)"
  COUNT="$(printf '%s' "${CANDIDATES}" | grep -c . || true)"

  if [[ "${COUNT}" == "1" ]]; then
    DEPLOYMENT_ID="$(printf '%s' "${CANDIDATES}" | head -n 1)"
    echo "既存デプロイを1つ見つけました。これを更新します。"
    echo "  DEPLOYMENT_ID = ${DEPLOYMENT_ID}"
    echo "  （この値を GitHub Secrets の DEPLOYMENT_ID に登録すると、以後は迷いません）"
  elif [[ "${COUNT}" == "0" ]]; then
    if [[ "${CREATE_IF_MISSING}" == "1" ]]; then
      echo "既存デプロイがないため、あとで新規に作成します（初回のみ）。"
      CREATE_NEW_DEPLOYMENT=1
    else
      echo "エラー: 更新できるWebアプリのデプロイがありません。" >&2
      echo "初回デプロイは scripts/bootstrap-smartphone.sh から作成してください。" >&2
      exit 1
    fi
  else
    echo "エラー: Webアプリのデプロイが複数あり、どれを更新すべきか判断できません。" >&2
    echo "GitHub Secrets の DEPLOYMENT_ID に、更新したいデプロイIDを登録してください。" >&2
    exit 1
  fi
fi

echo ""
echo "=============================================="
echo " 1/3  Apps Script へソースを送ります (clasp push)"
echo "=============================================="
${CLASP} push -f

echo ""
echo "=============================================="
echo " 2/3  版を作ります (clasp create-version)"
echo "=============================================="
VERSION_JSON="$(${CLASP} --json create-version "${DESCRIPTION}")"
VERSION_NUMBER="$(printf '%s' "${VERSION_JSON}" | json_get 'return data.versionNumber;')"

if [[ -z "${VERSION_NUMBER}" ]]; then
  echo "エラー: 版番号を取得できませんでした。" >&2
  exit 1
fi
echo "作成した版: ${VERSION_NUMBER}"

echo ""
echo "=============================================="
echo " 3/3  Webアプリのデプロイを更新します"
echo "=============================================="

if [[ "${CREATE_NEW_DEPLOYMENT}" == "1" ]]; then
  NEW_JSON="$(${CLASP} --json create-deployment -V "${VERSION_NUMBER}" -d "${DESCRIPTION}")"
  DEPLOYMENT_ID="$(printf '%s' "${NEW_JSON}" | json_get 'return data.deploymentId;')"
  [[ -n "${DEPLOYMENT_ID}" ]] || { echo "エラー: デプロイの作成に失敗しました。" >&2; exit 1; }
  echo "新しいデプロイを作成しました。"
  echo "  DEPLOYMENT_ID = ${DEPLOYMENT_ID}"
  echo "  この値を GitHub Secrets の DEPLOYMENT_ID へ登録してください。"
else
  ${CLASP} --json update-deployment -V "${VERSION_NUMBER}" -d "${DESCRIPTION}" "${DEPLOYMENT_ID}" >/dev/null
  echo "デプロイを版 ${VERSION_NUMBER} へ更新しました。"
fi

echo ""
echo "WebアプリURL:"
echo "  https://script.google.com/macros/s/${DEPLOYMENT_ID}/exec"
