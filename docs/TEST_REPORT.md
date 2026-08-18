> **このファイルについて（GitHub自動デプロイ版での位置づけ）**
>
> これは、もとの配布ZIP `E資格_学習Webアプリ_完全版_v2.zip` に入っていた説明書です。
> 内容（アプリの考え方・守っているルール・確認項目）はそのまま有効なので残しています。
>
> ただし **セットアップ手順だけは新しくなりました。**
> 手作業でApps Scriptへコピー＆ペーストする必要はありません。
>
> - スマホだけでのセットアップ → リポジトリ直下の **SMARTPHONE_SETUP.md**
> - 全体の構成と自動デプロイの仕組み → リポジトリ直下の **README.md**
>
> Apps Scriptへ送られるファイルは `src/` にまとめてあります。

---

# TEST REPORT

生成日: 2026-08-16

## Live data / source audit
- 03_問題台帳 全問題: 432
- verified + active + question_text + correct_option: 421
- question_image_refsあり: 148
- 画像問題のうち formal eligible: 142
- 問題画像のみ: 93
- 画像選択肢のみ: 25
- 問題画像 + 画像選択肢: 30
- 登録済み画像refのZIP内欠落: 0
- 最大単一画像: 436,998 bytes (EXAM-A2-Q063)
- 1問題あたり最大画像合計: 788,912 bytes (EXAM-A2-Q063)
- EXAM-A4-Q039: 2画像 / q=[0,1] / signature=9c6d67191a37caa0

## Static source tests
- Python package test: PASS
- Apps Script .gs JavaScript syntax check (Node): PASS
- Client.html JavaScript syntax check: PASS
- ImageManifest entries: 148 / PASS
- publicQuestion_ から correct_option を返さない: PASS
- 画像 preload 成功後に renderQuestion: PASS
- DOM挿入後の image.onerror でも回答停止・スキップ: 実装確認PASS

## Mastery v2 compatibility
- Apps Script側に「正解なら+20」の加算処理なし
- submitAnswerは回答ログ書き込み後に SpreadsheetApp.flush()
- 02_マインドマップ M列 mastery_pct を読み戻して結果表示
- 04_学習ログ P/Q はスナップショットとしてのみ書き込む
- 同一問題再回答のcoverage処理はSheet側のv2数式へ委譲
- ログ書き込みは論理的な次の空き行を探索

## Runtime limitation
この環境から現在デプロイ中の既存Apps Scriptソース本文へ直接アクセスできないため、
実ブラウザ上の最終E2Eは、新しいApps Scriptプロジェクトへ貼り付けてデプロイ後に実施します。

同梱関数:
- runSelfTest()
- runImageSupportSelfTest()
- runImageBundleSmokeTest()

上記3つは04_学習ログへ学習回答を書きません。
