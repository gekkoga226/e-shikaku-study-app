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

# E資格 学習Webアプリ 完全版 v2

## 対象
Googleスプレッドシート:
`E資格_学習履歴管理シート_MVP`

Spreadsheet ID:
`1hAsu5eR3dtHu6S34joxyCaNmLZBtSnivIguVqJ61bh8`

## 構成
- Google Sheets: 問題・ログ・理解度の唯一の正本
- Google Drive: 修了試験画像ZIP
- Google Apps Script: サーバー処理
- HTML Service: スマホ向けWeb UI

## 理解度
`mastery_rule_version = v2_2026-08-16`

アプリでは理解度を独自計算しません。
回答ログを書いた後に `02_マインドマップ!M` を読み戻します。

## 画像
2026-08-16時点の実データ監査に基づく `ImageManifest.gs` を同梱。

監査値:
- 全問題: 432
- question_image_refsあり: 148
- 画像問題のverified/active: 142
- 問題画像のみ: 93
- 画像選択肢のみ: 25
- 両方: 30
- 登録済み画像refのZIP内欠落: 0
- EXAM-A4-Q039: context + question の2画像として対応

## 重要
画像付き問題を03_問題台帳へ新規追加した場合は、
`ImageManifest.gs` の更新監査が必要です。
manifestがない・署名が違う画像問題は安全側にスキップします。

## 最初に読む
1. START_HERE.md
2. INSTALL_非エンジニア向け.md
3. TEST_CHECKLIST.md
