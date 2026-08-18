# e-shikaku-study-app

E資格の勉強用 学習Webアプリ（Google Apps Script）と、
その **GitHub自動デプロイ構成** です。

`main` ブランチが更新されると、GitHub Actions が検査を行い、
合格した場合だけ Google Apps Script のWebアプリへ自動反映します。

> **初めての方へ**
> パソコンは不要です。スマートフォンだけでセットアップできます。
> 手順は **[SMARTPHONE_SETUP.md](SMARTPHONE_SETUP.md)** を上から順に読んでください。

---

## 1. これは何か

| 部品 | 役割 |
|---|---|
| Googleスプレッドシート | 問題・回答ログ・理解度の**唯一の正しい計算元** |
| Google Drive | 修了試験の画像ZIP |
| Google Apps Script | サーバー処理（出題・採点・画像取り出し） |
| HTML Service | スマホ向けのWeb画面 |
| GitHub | コードの保管庫 |
| GitHub Actions | 自動で検査してApps Scriptへ反映するロボット |

対象スプレッドシート: `E資格_学習履歴管理シート_MVP`
理解度ルール: `mastery_rule_version = v2_2026-08-16`

回答後の画面には、押したときだけ動く **AI補助解説（Gemini）** があります。
出題・採点・理解度・学習ログはAIを一切使わず、これまでどおりスプレッドシートの
確定ロジックだけで決まります。使い始めるための1回だけの設定は
**[docs/AI_SETUP_非エンジニア向け.md](docs/AI_SETUP_非エンジニア向け.md)** を読んでください。

---

## 2. フォルダ構成

```
.
├── src/                      Apps Scriptへ送るファイル（これだけが送られます）
│   ├── appsscript.json       Apps Scriptの設定（webapp: MYSELF / USER_DEPLOYING）
│   ├── Code.gs               出題・採点・ログ書き込みの司令塔＋AI補助解説
│   ├── ImageManifest.gs      どの画像が問題画像／選択肢画像かの設計図
│   ├── ImageSupport.gs       Drive ZIPから画像を安全に取り出す部品
│   ├── ImageSelfTest.gs      画像機能の非破壊テスト
│   ├── Index.html            画面の骨組み
│   ├── Styles.html           見た目（スマホ表示）
│   └── Client.html           画面の操作
│
├── scripts/
│   ├── bootstrap-smartphone.sh  初回セットアップ（これ1つで完結）
│   ├── deploy.sh                push → version → deployment更新
│   ├── check-required-files.sh  必須ファイル確認
│   ├── check-secrets.sh         機密情報の混入チェック
│   └── check-gas-syntax.mjs     構文検査
│
├── tests/
│   ├── test_package.py          同梱テスト（ZIP版から継承）
│   ├── test_mastery_rules.py    理解度v2の守り
│   ├── test_image_policy.py     画像必須ルールの守り
│   ├── test_answer_secrecy.py   正解漏洩防止の守り
│   ├── test_ai_explanation.py   AI補助解説の守り（回答後だけ／キー非公開）
│   └── test_repo_config.py      設定・権限・ワークフローの守り
│
├── .github/
│   ├── actions/verify/          共通の検査ステップ
│   └── workflows/
│       ├── ci.yml               Pull Request での自動検査
│       └── deploy.yml           main への push で自動デプロイ
│
├── docs/                     もとのZIPに入っていた説明書
├── data/image_audit.csv      画像監査の記録
└── SMARTPHONE_SETUP.md       スマホだけでのセットアップ手順書
```

---

## 3. セットアップ

GitHub Codespaces のターミナルで、次の1行だけ:

```bash
bash scripts/bootstrap-smartphone.sh
```

詳しい画面操作は [SMARTPHONE_SETUP.md](SMARTPHONE_SETUP.md) にあります。

---

## 4. 自動デプロイの流れ

```
main への push
   └─ verify ジョブ（必須ファイル / 構文 / 機密情報 / Pythonテスト）
        └─ 成功したときだけ deploy ジョブ
             ├─ clasp push
             ├─ clasp create-version
             └─ clasp update-deployment（既存のWebアプリURLを更新）
```

検査に失敗した場合、Apps Script には **何も送られません**。

### 必要な GitHub Secrets

| 名前 | 中身 | 必須 |
|---|---|---|
| `CLASPRC_JSON` | `~/.clasprc.json` の中身（Googleのログイン情報） | 必須 |
| `CLASP_JSON` | `.clasp.json` の中身（`scriptId`） | 必須 |
| `DEPLOYMENT_ID` | 更新するWebアプリのデプロイID | 推奨 |

`DEPLOYMENT_ID` を登録しない場合、Webアプリのデプロイが1つだけなら
自動で見つけて更新します。2つ以上ある場合は安全のため停止します。

---

## 5. 開発時に守っているルール

このリポジトリのテストは、次の約束が壊れていないことを機械的に確認します。

### 理解度 v2

- Apps Script側で理解度を独自計算しない（「正解したら +20」は禁止）
- 回答処理は
  **回答前のM列を読む → 04_学習ログへ書く → `SpreadsheetApp.flush()` → M列を読み直す**
- `04_学習ログ` P/Q列は監査用スナップショットであり、現在理解度の計算元にしない
- `04_学習ログ` U列 `counts_for_mastery` の数式を壊さない
- `appendRow()` を使わず、論理的な次の空き行へ書く
- `02_マインドマップ` M列を固定値で上書きしない

### 画像問題

- 画像が必要な問題は
  **Drive ZIP取得 → 必要画像抽出 → ブラウザ側preload成功 → 表示 → 回答可能** の順
- 途中で1つでも失敗したら、回答させず、学習ログへ記録せず、その問題をスキップする

### 正解漏洩防止

- 回答前のクライアントへ `correct_option` などを送らない
- 正誤判定はサーバー側で行う

### 権限

- `oauthScopes` は `spreadsheets` と `drive.readonly` のみ
- Webアプリは `access: MYSELF` / `executeAs: USER_DEPLOYING`

---

## 6. ローカル（Codespaces）でのコマンド

```bash
npm run verify        # 検査をすべて実行（Apps Scriptへは送らない）
npm run check:files   # 必須ファイル確認
npm run check:syntax  # 構文検査
npm run check:secrets # 機密情報チェック
npm run test:python   # Pythonテスト
npm run deploy        # 手動で Apps Script へ反映
```

---

## 7. Apps Script側の非破壊テスト

Apps Scriptのエディタから実行できます。いずれも `04_学習ログ` には書き込みません。

- `runSelfTest()` — シート構成と理解度ルールの確認
- `runImageSupportSelfTest()` — 画像manifestの整合性確認
- `runImageBundleSmokeTest()` — Drive権限と実画像展開の確認

---

## 8. セキュリティ

- `.clasprc.json` / `.clasp.json` は `.gitignore` 済み。コミットしないでください。
- 認証情報は GitHub Secrets にのみ置きます。ワークフローは値をログへ出しません。
- スプレッドシートの学習データはGitHubへコピーしません。
- CI の `check:secrets` が、トークンらしき文字列の混入を毎回チェックします。
