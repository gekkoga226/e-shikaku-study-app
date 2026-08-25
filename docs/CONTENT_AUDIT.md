# 解くのに情報が足りない問題を洗い出す

「図がないと解けないのに図がない」「プログラム中の（き）と書いてあるのに
プログラムが載っていない」——こうした不足は**アプリの不具合ではなく、
`03_問題台帳` の中身の不足**です。アプリ側では直せないので、
不足している問題を名指しして、台帳を直せるようにするのがこの監査の役割です。

この監査は**読み取りだけ**です。`03_問題台帳` にも `04_学習ログ` にも書き込みません。

---

## 1. 実行のしかた

1. スプレッドシートを開き、`拡張機能` → `Apps Script`
2. 上部の関数選択で `runQuestionContentAudit` を選び、`実行`
3. 下の「実行ログ」に一覧が出るので、そのままコピーする

出力はこの形です（タブ区切りなので、そのままシートへ貼れます）。

```
正式問題: 600問 / 要確認: 38問
内訳: {"NEEDS_IMAGE":3,"NEEDS_PROGRAM":9,"BLANK_NOT_FOUND":11,...}
--- 以下をコピーして共有してください ---
question_id      reasons                        text_length  has_image  blanks  shared_image_with
EXAM-A3-Q009     NEEDS_PROGRAM+BLANK_NOT_FOUND  412          true       き      EXAM-A3-Q010,EXAM-A3-Q011
```

---

## 2. 理由の読み方と直し方

| 理由 | 意味 | 直し方 |
|---|---|---|
| `NEEDS_IMAGE` | 本文が図・グラフを指しているのに `question_image_refs` が空 | 画像を登録するか、図を使わない本文に書き直す |
| `NEEDS_PROGRAM` | 本文がプログラムを指しているのに、本文にプログラムが1行も載っていない | 元問題のプログラムを `question_text` へ貼る（画像で持たせる場合は `question_image_refs` へ追加） |
| `BLANK_NOT_FOUND` | 「（き）に当てはまる」の（き）が、本文の他のどこにも出てこない | 空欄を含む段落やプログラムを `question_text` へ補う |
| `MISSING_CONTEXT` | `（あ）` などの空欄を指しているのに、本文が短く前提が無い | 元の問題文（前提の段落）を本文へ補う |
| `TRUNCATED_TEXT` | 本文が句点などで終わっておらず、文の途中で切れている | 元問題から本文を取り直す |
| `OPTION_PLACEHOLDER` | 選択肢が `[Aの画像選択肢]` のままなのに、その選択肢の画像が無い | 選択肢画像を登録するか、文字の選択肢に書き直す |
| `NO_EXPLANATION` | `explanation_*` がすべて空 | 解説を登録する（AI補助解説の材料にもなる） |

### `has_image` が `true` でも報告されることがあります

`NEEDS_PROGRAM` `BLANK_NOT_FOUND` `OPTION_PLACEHOLDER` は、**画像がある問題でも報告します**。
図だけが取り込まれて、同じ問題に付いていたプログラムや前提の段落が
落ちている——という不足が実際にあるためです。

`OPTION_PLACEHOLDER` も同じで、**4択のうち一部にしか画像が割り当たっていない**問題を
報告します。画像が1枚もある以上「画像が無い問題」としては見つからないためです。
判定には実際の選択肢テキストを使うので、A・C・Dが文字の選択肢でBだけ画像、という
正常な問題は報告しません。

その代わり、空欄そのものが図の中に描かれている問題では
`BLANK_NOT_FOUND` が誤って付くことがあります。
画像を開いて空欄が図の中にあれば、その問題は直す必要はありません。

### `shared_image_with` は「一緒に直す仲間」です

元は1つの大問だったものを、空欄ごとに別々の行へ分けている問題があります。
それらは同じ画像を指したままなので、`shared_image_with` にまとまって出ます。
**前提が落ちていれば仲間も同じように落ちている**ので、まとめて直してください。

---

## 3. まず見るべき候補（画像を共有している20グループ・65問）

`data/image_audit.csv` を見ると、同じ画像を共有している問題が
次の20グループ・65問あります。1つの大問を空欄ごとに分けた問題なので、
「前提の文章やプログラムが落ちる」不足が最も起きやすい場所です。

| 問題数 | 論点 | question_id |
|---|---|---|
| 5 | DL-FFN | EXAM-A2-Q011 EXAM-A2-Q012 EXAM-A2-Q013 EXAM-A2-Q014 EXAM-A2-Q015 |
| 5 | DL-FFN | EXAM-A2-Q019 EXAM-A2-Q020 EXAM-A2-Q022 EXAM-A2-Q023 EXAM-A2-Q024 |
| 5 | DL-FFN | EXAM-A2-Q066 EXAM-A2-Q067 EXAM-A2-Q068 EXAM-A2-Q069 EXAM-A2-Q070 |
| 5 | ML-BASICS | EXAM-A3-Q039 EXAM-A3-Q040 EXAM-A3-Q041 EXAM-A3-Q042 EXAM-A3-Q043 |
| 4 | DL-FFN | EXAM-A2-Q001 EXAM-A2-Q002 EXAM-A2-Q003 EXAM-A2-Q004 |
| 4 | DL-FFN | EXAM-A2-Q032 EXAM-A2-Q033 EXAM-A2-Q034 EXAM-A2-Q035 |
| 4 | DL-REG | EXAM-A3-Q030 EXAM-A3-Q031 EXAM-A3-Q032 EXAM-A3-Q033 |
| 3 | ML-BASICS | EXAM-A1-Q041 EXAM-A1-Q042 EXAM-A1-Q043 |
| 3 | DL-FFN | EXAM-A2-Q037 EXAM-A2-Q038 EXAM-A2-Q039 |
| 3 | DL-OPT | EXAM-A2-Q044 EXAM-A2-Q045 EXAM-A2-Q046 |
| 3 | DL-OPT | EXAM-A2-Q053 EXAM-A2-Q054 EXAM-A2-Q055 |
| 3 | DL-OPT | **EXAM-A3-Q009 EXAM-A3-Q010 EXAM-A3-Q011** |
| 3 | DL-OPT | **EXAM-A3-Q012 EXAM-A3-Q013 EXAM-A3-Q014** |
| 3 | APP-DET | EXAM-B1-Q022 EXAM-B1-Q023 EXAM-B1-Q024 |
| 2 | MATH-PROBSTAT | EXAM-A1-Q007 EXAM-A1-Q008 |
| 2 | ML-BASICS | EXAM-A1-Q057 EXAM-A1-Q058 |
| 2 | DL-OPT | EXAM-A2-Q047 EXAM-A2-Q048 |
| 2 | DL-FFN | EXAM-A2-Q059 EXAM-A2-Q060 |
| 2 | DL-GEN | EXAM-A3-Q017 EXAM-A3-Q018 |
| 2 | APP-LEARN | EXAM-B3-Q017 EXAM-B3-Q018 |

### 選択肢画像が4つそろっていない問題

`src/ImageManifest.gs` と `data/image_audit.csv` を突き合わせると、
**`EXAM-A2-Q027`** だけが `image_type=options`（選択肢そのものが画像）でありながら、
選択肢Bぶんの画像しか登録されていません。
A・C・Dが `[Aの画像選択肢]` のような目印のままなら、選ぶ材料が無い状態です
（この場合、`runQuestionContentAudit()` が `OPTION_PLACEHOLDER` として報告し、
アプリ側は出題しません）。A・C・Dが文字の選択肢なら正常なので、
台帳の `option_a` `option_c` `option_d` を確認してください。

太字の2グループは、報告のあった
「図1・図2（Cross Entropy / Softmax / Affine）＋ `プログラム中の（き）`」の問題が
入っている可能性が高いところです（論点 `DL-OPT`＝深層モデルのための最適化、
図が1枚で3問が共有、選択肢は文字）。
`runQuestionContentAudit()` の結果で `question_id` を確定してください。

---

## 4. 直したあとの確認

`03_問題台帳` を直したら、もう一度 `runQuestionContentAudit()` を実行し、
直した `question_id` が一覧から消えていることを確認してください。

`question_image_refs` を編集した場合は、画像の並びが変わっているので
`runImageSupportSelfTest()` も実行し、`Manifest signatures` が
すべて一致していることを確認してください
（一致しない場合、その問題は安全側でスキップされ、出題されなくなります）。
