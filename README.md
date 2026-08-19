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

ホーム画面には3つのモードがあります。

| ボタン | 何をするか |
|---|---|
| 弱点から1問解く | 理解度が低い論点を優先して出します（従来どおり） |
| 期限到来の復習をする | 復習期限が来た論点を優先します（従来どおり） |
| 未回答問題を優先して解く（N問） | **まだ一度も正式回答していない問題だけ**を出します |

「未回答」とは、`03_問題台帳` で `verification_status = verified` かつ `active = TRUE` の
問題のうち、`04_学習ログ` に `counts_for_mastery = TRUE` の行が1件もないものです。
判定はシートのU列の数式をそのまま信じるので、過去試験の参考履歴・`provisional`・
`system_test`・書き込み失敗・理解度計測の開始前のログは「回答済み」に数えません。
未回答が0件になったら「未回答の正式問題はすべて解答済みです」と表示し、
回答済み問題へ勝手に戻ることはありません。

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
│   ├── test_package.py             同梱テスト（ZIP版から継承）
│   ├── test_mastery_rules.py       理解度v2の守り
│   ├── test_image_policy.py        画像必須ルールの守り
│   ├── test_image_display.py       画像の見切れ防止（CSS）の守り
│   ├── test_image_option_mapping.py 画像4択 A/B/C/D 対応の守り
│   ├── test_unanswered_mode.py     未回答優先モードの守り
│   ├── test_sheet_reads.py         シート読み取りの重さの守り（表示速度）
│   ├── test_question_speed.py      出題の速さの守り（点検の回数・選ばれる問題の同一性）
│   ├── test_answer_speed.py        回答処理の速さの守り（往復回数・確定手順）
│   ├── test_answer_secrecy.py      正解漏洩防止の守り
│   ├── test_ai_explanation.py      AI補助解説の守り（回答後だけ／キー非公開）
│   └── test_repo_config.py         設定・権限・ワークフローの守り
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

### 未回答優先モード

- 未回答の判定根拠は `04_学習ログ` U列 `counts_for_mastery` だけ（別の条件を作らない）
- 未回答が残っている間、回答済み問題を1問も混ぜない
- 未回答が0件なら完了を返し、回答済み問題へフォールバックしない
- `required_unique_questions` に届いていない論点（coverage不足）の問題を先に出す
- 未回答モードで解いた回答も、正式条件を満たせばそのまま理解度へ反映される
  （U列が除外するのは `mode = "system_test"` だけ）

### 画像問題

- 画像が必要な問題は
  **Drive ZIP取得 → 必要画像抽出 → ブラウザ側preload成功 → 表示 → 回答可能** の順
- 途中で1つでも失敗したら、回答させず、学習ログへ記録せず、その問題をスキップする
- A〜Dそのものが画像の問題は、`ImageManifest.gs` の対応表（選択肢の文字 → 画像index）
  だけを根拠に割り当てる。並び順から推測しない
- 画像を切り落とさない。通常表示も拡大表示も、縦横比を保ったまま画面内へ縮小するか、
  スクロールで端まで見られるようにする
- 選択肢の内部用プレースホルダ（`[Aの画像選択肢]` など）は、その選択肢の画像が
  実際に表示できたときだけ隠す。画像が無いのに文字だけ隠して回答させない
- 画像のタップはその選択肢の選択、「拡大」は拡大だけ（拡大で回答が選ばれない）

### AI補助解説

- 解説ボックスの高さを固定しない（固定すると長い解説が必ず見切れる）
- モデルの内部の思考（`thought` の part）を解説本文として表示しない
- 長さの上限で切れた解説は、切れたことを画面に出し、キャッシュへ残さない
- 対になっていない `\(` `\[` は表示前に取り除く。
  開いたままの区切りを残すと、KaTeXが次の閉じ記号までを1つの数式とみなし、
  あいだの説明文が画面から消える

### 待ち時間（速さ）

理解度・採点・画像の各ルールを1つも変えずに、往復の回数だけを減らす。

- 画像を用意できるかの点検（`imageSupportAllowsQuestionObject_`）を、出題候補の
  全部に先まわしでかけない。優先順位で並べたあと、上から順に必要なぶんだけ点検する。
  点検1回につきキャッシュ参照2回とSHA-256計算1回が走るため、
  148問ぶん先にかけると1問出すのに約450回の呼び出しになる
- 点検を後回しにしても、**選ばれる問題は1問も変わらない**。
  `tests/test_question_speed.py` が、以前の実装と結果を突き合わせて確認する
- `04_学習ログ` の空き行探しは `getMaxRows()` ではなく
  `Math.min(getMaxRows(), getLastRow())` までで打ち切る
  （`getLastRow()` より下の行にA列の値は存在しない）
- 1回の実行でスプレッドシートを何度も開かない。書き込み系も `spreadsheet_()` を使う
- `02_マインドマップ` の同じ行への読み取りは1回にまとめる。
  ただしM列（理解度）の読み取り2箇所は確定手順の一部なので、まとめない
- `next_review_at`（P列）は、値が変わるときだけ書く
- `00_設定` の文字列の値は数分だけ使い回す（回答のたびに読み直さない）。
  理解度の計算には使わない表示・記録用の値だけが対象
- 結果画面を表示しているあいだに次の問題を先読みする。
  先読みが呼ぶのは読み取り専用の `getNextQuestion` / `getQuestionImageBundle` だけで、
  採点・理解度・学習ログには一切関わらない。
  画像問題は先読みの時点で `preloadBundle` を通し、成功したものだけを使う。
  モードや除外リストが変わった先読みは使わずに捨てる

### 正解漏洩防止

- 回答前のクライアントへ `correct_option` などを送らない
- 正誤判定はサーバー側で行う

### 権限

- `oauthScopes` は `spreadsheets` / `drive.readonly` / `script.external_request` の3つのみ
  （`script.external_request` はAI補助解説のGemini呼び出し専用。今回の変更で追加はなし）
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

- `runSelfTest()` — シート構成・理解度ルール・未回答の残数の確認
- `runImageSupportSelfTest()` — 画像manifestの整合性確認
- `runImageOptionMappingSelfTest()` — 画像4択 A/B/C/D の対応確認
- `runImageBundleSmokeTest()` — Drive権限と実画像展開の確認
- `runQuestionContentAudit()` — **解くのに情報が足りない問題の洗い出し**（下記）
- `checkAiSetup()` — `GEMINI_API_KEY` の登録とGeminiへの接続確認

### 解くのに情報が足りない問題を洗い出す

「図を見ないと解けないのに画像が無い」「（あ）とあるのに前提の文章が無い」といった
**03_問題台帳の中身の不足**は、アプリ側では直せません。
`runQuestionContentAudit()` を実行すると、該当する `question_id` の一覧がログに出ます。

| 理由 | 意味 | 直し方 |
|---|---|---|
| `NEEDS_IMAGE` | 本文が図・グラフを指しているのに `question_image_refs` が空 | 画像を登録するか、図を使わない本文に書き直す |
| `MISSING_CONTEXT` | `（あ）` などの空欄を指しているのに、本文にその前提が無い | 元の問題文（前提の段落）を本文へ補う |
| `OPTION_PLACEHOLDER` | 選択肢が `[Aの画像選択肢]` のままで画像が無い | 選択肢画像を登録するか、文字の選択肢に書き直す |
| `NO_EXPLANATION` | `explanation_*` がすべて空 | 解説を登録する（AI補助解説の材料にもなる） |

この監査は**読み取りだけ**で、スプレッドシートには何も書き込みません。

---

## 8. 実機（スマホ／ブラウザ）での確認手順

自動テストはコードの構造と対応表までしか見られません。
デプロイ後に、次の4種類を**実際の画面**で1回ずつ確認してください。

準備: ホーム画面で「未回答問題を優先して解く（N問）」の N が表示されることを確認します。

| # | 種類 | 確認する問題の例 | 見るところ |
|---|---|---|---|
| 1 | 文字だけの問題 | 画像なしの任意の問題 | 問題文と選択肢がすぐ出る／回答できる |
| 2 | 問題画像1枚 | `EXAM-A2-Q001`, `EXAM-A3-Q009` | 画像全体が見える／タップで拡大でき、拡大時も端が切れない |
| 3 | 問題画像 + 画像4択 | **`EXAM-A2-Q005`** または **`EXAM-A4-Q006`** | 上に問題画像、A〜Dそれぞれに違う画像。A/B/C/Dの取り違えがない |
| 4 | 画像4択だけ | `EXAM-A2-Q010`, `EXAM-A1-Q004` | 4つとも画像が出る。`[Aの画像選択肢]` などの文字が見えない |

各画面で必ず確認すること:

1. **拡大の見切れ** — 画像をタップ（選択肢は「拡大」ボタン）して拡大表示にし、
   画像の右端・下端まで見えることを確認する。
   「原寸で見る」に切り替えると、指でスクロールして細部まで読める。
2. **拡大では回答が選ばれない** — 「拡大」を押しても、その選択肢が選択状態
   （枠が青くなる）にならないことを確認する。
3. **画像タップで選べる** — 選択肢の画像そのものをタップすると、その選択肢が選ばれる。
4. **画像が出ない問題は出題されない** — 万一画像を取得できないと
   「画像を確認できなかったため、この問題はスキップしました。」と表示され、
   回答ボタンは出ません（この状態で学習ログには何も書かれません）。
5. **AI補助解説** — 回答したあとにだけ「AIでさらに噛み砕く」が押せること。
   回答前の画面にはこのボタンがないこと。
6. **理解度** — 回答後の「理解度: A% → B%」が、
   `02_マインドマップ` M列の値と一致していること。
7. **次の問題への進み方** — 回答後の結果画面から「次の問題へ」を押したとき、
   すぐに次の問題が出ること（結果を読んでいるあいだに先読みしています）。
   画像問題でも、画像がそろわないまま問題文だけが出ることはありません。
   ホームへ戻ってから別のモードを始めたときに、前のモードの問題が出ないことも確認します。

iPhoneで見るときは、ノッチ側・ホームバー側の両方で画像が隠れていないかを確認してください。

---

## 9. セキュリティ

- `.clasprc.json` / `.clasp.json` は `.gitignore` 済み。コミットしないでください。
- 認証情報は GitHub Secrets にのみ置きます。ワークフローは値をログへ出しません。
- スプレッドシートの学習データはGitHubへコピーしません。
- CI の `check:secrets` が、トークンらしき文字列の混入を毎回チェックします。
