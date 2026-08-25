/**
 * 画像の非破壊テスト。
 * 04_学習ログへは書きません。
 */
function runImageSupportSelfTest() {
  const results = [];
  const push = (name, ok, detail) => results.push({ name, ok: !!ok, detail: String(detail || '') });

  try {
    const questions = readObjects_(APP_CONFIG.SHEETS.QUESTIONS);
    const imageRows = questions.filter(q => parseImageRefs_(q.question_image_refs).length > 0);
    push('Image rows', imageRows.length === 148, imageRows.length);

    const formalImageRows = imageRows.filter(isFormalQuestion_);
    push('Formal image rows', formalImageRows.length === 142, formalImageRows.length);

    let manifestOk = 0;
    let signatureOk = 0;
    imageRows.forEach(q => {
      const qid = String(q.question_id || '');
      const refs = parseImageRefs_(q.question_image_refs);
      const m = IMAGE_ROLE_MAP_BY_QUESTION_ID[qid];
      if (m) manifestOk++;
      if (m && m.s === imageRefsSignature_(refs)) signatureOk++;
    });

    push('Manifest entries', manifestOk === imageRows.length, manifestOk + '/' + imageRows.length);
    push('Manifest signatures', signatureOk === imageRows.length, signatureOk + '/' + imageRows.length);

    const special = IMAGE_ROLE_MAP_BY_QUESTION_ID['EXAM-A4-Q039'];
    push(
      'A4-Q039 context image',
      !!special && JSON.stringify(special.q) === JSON.stringify([0,1]),
      special ? JSON.stringify(special.q) : 'missing'
    );

    return {
      ok: results.every(r => r.ok),
      results,
      note: 'Drive ZIPの実展開は実際に問題を表示するときに検証します。このセルフテストは学習ログを変更しません。'
    };
  } catch (e) {
    return { ok: false, results, error: String(e && e.message ? e.message : e) };
  }
}

/**
 * 画像4択（A〜Dそのものが画像）の対応を確認する非破壊テスト。
 *
 * 見るのは ImageManifest.gs の o（選択肢ごとの画像index）だけで、
 * 「並び順から推測」はしません。A/B/C/D がそれぞれ別の画像を、
 * 重複なく指していることを確認します。
 * 04_学習ログへは書きません。
 */
function runImageOptionMappingSelfTest() {
  const samples = [
    'EXAM-A1-Q004', 'EXAM-A1-Q010',
    'EXAM-A2-Q005', 'EXAM-A2-Q010',
    'EXAM-A4-Q006', 'EXAM-A4-Q008',
    'EXAM-B2-Q043', 'EXAM-B3-Q003'
  ];

  const results = samples.map(qid => {
    const manifest = IMAGE_ROLE_MAP_BY_QUESTION_ID[qid];
    if (!manifest || !manifest.o) {
      return { question_id: qid, ok: false, detail: 'manifest missing' };
    }

    const letters = ['A', 'B', 'C', 'D'];
    const used = {};
    let ok = true;
    const mapping = {};

    letters.forEach(letter => {
      const indexes = manifest.o[letter];
      if (!indexes || indexes.length !== 1) {
        ok = false;
        mapping[letter] = 'missing';
        return;
      }
      const index = Number(indexes[0]);
      mapping[letter] = index;
      if (used[index]) ok = false; // 同じ画像が2つの選択肢へ割り当てられている
      used[index] = true;
    });

    const questionIndexes = (manifest.q || []).map(Number);
    questionIndexes.forEach(index => {
      if (used[index]) ok = false; // 問題画像と選択肢画像の取り違え
    });

    return { question_id: qid, ok: ok, detail: JSON.stringify(mapping) };
  });

  return {
    ok: results.every(r => r.ok),
    results,
    note: 'ImageManifestのA〜D対応だけを確認します。04_学習ログには書きません。'
  };
}

/*
 * 「解くのに足りない情報がある問題」を見分けるための言い回し。
 *
 * 監査の判定はすべてここに集めてあり、
 * 本文の書き方が変わったときはこの表だけを直せば済むようにしている。
 */
const CONTENT_AUDIT_RULES = Object.freeze({
  // 「図を見ないと解けない」ことを示す言い回し。
  IMAGE_WORDS: /(図|グラフ|画像|イラスト|以下の表|下表|次の表)/,
  // 空欄補充であることを示す言い回し。
  BLANK_WORDS: /([（(][ぁ-んァ-ヶA-Za-z][）)]|空欄|穴埋め|[［\[]\s*[）)]?\s*[］\]])/,
  // 「プログラムを見ないと解けない」ことを示す言い回し。
  PROGRAM_WORDS: /(プログラム|ソースコード|擬似コード|疑似コード|コード中|次のコード|以下のコード)/,
  /*
   * 本文の中に実際にプログラムが載っているかどうかの目印。
   *
   * 「backprop関数」のような “プログラムの話をしているだけ” の文と、
   * 本当にプログラムが貼られている本文とを区別するために使う。
   * def / return / for ... in のように、日本語の文章には出てこない書き方だけを見る。
   */
  PROGRAM_BODY: /(\bdef\s+\w+\s*\(|\breturn\b|\bimport\b|\bfor\s+\w+\s+in\b|\bwhile\s*\(|\bnp\.\w|\btorch\.\w|\btf\.\w|\bprint\s*\(|\w\s*\+=|\w\s*-=|\w\s*\*=)/,
  // 上の目印が無くても、ASCIIの代入が2つ以上並んでいればプログラムとみなす。
  PROGRAM_ASSIGNMENT: /[A-Za-z_]\w*\s*=\s*[^=]/g,
  // 本文がここで終わっていれば、文章は途中で切れていない。
  SENTENCE_END: /[。．.！!？?」』）)】］\]]$/,
  // 本文がこれより短いと、前提の文章が入っていない可能性が高い。
  SHORT_TEXT_CHARS: 60
});

/** 空欄記号の中身（ひらがな・カタカナ・英数字1〜3文字）を囲む括弧。 */
const BLANK_OPEN = '（(［\\[【';
const BLANK_CLOSE = '）)］\\]】';
const BLANK_INNER = '[ぁ-んァ-ヶA-Za-z0-9]{1,3}';

/**
 * 本文が「（き）に当てはまるものを選べ」のように名指ししている空欄を返す。
 *
 * 「（き）」が本文の他の場所（＝空欄を含む文章やプログラム）にも出てくるなら、
 * 解くのに必要な前提は本文に載っている。
 * 名指しした1か所にしか出てこないときは、前提の文章やプログラムが
 * 台帳へ取り込まれていない可能性が高い。
 */
function findUnresolvedBlanks_(text) {
  const source = String(text || '');
  const referencePattern = new RegExp(
    '[' + BLANK_OPEN + ']\\s*(' + BLANK_INNER + ')\\s*[' + BLANK_CLOSE + ']' +
    '\\s*(に当てはまる|にあてはまる|に入る|にはいる|に該当する|に入れる)',
    'g'
  );

  const unresolved = [];
  const seen = {};
  let match;
  while ((match = referencePattern.exec(source)) !== null) {
    const label = match[1];
    if (seen[label]) continue;
    seen[label] = true;

    const occurrencePattern = new RegExp(
      '[' + BLANK_OPEN + ']\\s*' + label.replace(/[.*+?^${}()|[\]\\]/g, '\\$&') +
      '\\s*[' + BLANK_CLOSE + ']',
      'g'
    );
    const occurrences = source.match(occurrencePattern) || [];
    if (occurrences.length <= 1) unresolved.push(label);
  }
  return unresolved;
}

/** 本文にプログラムそのものが載っているか。 */
function containsProgramBody_(text) {
  const source = String(text || '');
  if (CONTENT_AUDIT_RULES.PROGRAM_BODY.test(source)) return true;
  const assignments = source.match(CONTENT_AUDIT_RULES.PROGRAM_ASSIGNMENT) || [];
  return assignments.length >= 2;
}

/**
 * 1問ぶんの「情報の不足」を判定する（読み取りだけ・シートに触らない）。
 *
 * 返すのは理由の配列。空配列なら不足なし。
 * この関数だけで判定が完結するので、Node上のテストからも同じ結果を確かめられる。
 */
function findQuestionContentGaps_(q) {
  const text = String(q.question_text || '');
  const hasImage = parseImageRefs_(q.question_image_refs).length > 0;

  const reasons = [];

  if (!hasImage && CONTENT_AUDIT_RULES.IMAGE_WORDS.test(text)) reasons.push('NEEDS_IMAGE');

  /*
   * 「[Aの画像選択肢]」のような目印のままで、その選択肢の画像が無い。
   *
   * 画像が1枚も無い問題だけでなく、画像はあるのに一部の選択肢ぶんだけ
   * 足りていない問題も報告する。4択のうち1つにしか画像が割り当たっていない
   * ような行は、画像があるぶん「画像が無い問題」としては見つからないため。
   */
  const manifest = IMAGE_ROLE_MAP_BY_QUESTION_ID[String(q.question_id || '')];
  if (!optionImagesCoverPlaceholders_(q, manifest)) reasons.push('OPTION_PLACEHOLDER');

  /*
   * 「プログラム中の（き）」のように、プログラムを見ないと解けないと言っているのに
   * 本文にプログラムが1行も無い。画像があっても報告する。
   * 図だけが取り込まれてプログラムの掲載が落ちている問題は、
   * 画像があるぶん NEEDS_IMAGE では見つからないため。
   */
  if (CONTENT_AUDIT_RULES.PROGRAM_WORDS.test(text) && !containsProgramBody_(text)) {
    reasons.push('NEEDS_PROGRAM');
  }

  // 名指しされた空欄が本文のどこにも無い＝空欄を含む前提が落ちている。
  if (findUnresolvedBlanks_(text).length) reasons.push('BLANK_NOT_FOUND');

  // 空欄を指しているのに本文が短い＝前提の文章が取り込まれていない。
  if (CONTENT_AUDIT_RULES.BLANK_WORDS.test(text) &&
      text.length < CONTENT_AUDIT_RULES.SHORT_TEXT_CHARS && !hasImage) {
    reasons.push('MISSING_CONTEXT');
  }

  // 本文が文の途中で終わっている＝取り込みの途中で切れている。
  const trimmed = text.trim();
  if (trimmed && !CONTENT_AUDIT_RULES.SENTENCE_END.test(trimmed)) {
    reasons.push('TRUNCATED_TEXT');
  }

  const hasExplanation = ['explanation_plain', 'explanation_formal',
    'explanation_calculation', 'explanation_options']
    .some(key => String(q[key] || '').trim());
  if (!hasExplanation) reasons.push('NO_EXPLANATION');

  return reasons;
}

/**
 * 「解くのに足りない情報がある問題」を洗い出す監査（読み取りだけ）。
 *
 * アプリの不具合ではなく、03_問題台帳の中身が足りていない問題を見つけるためのもの。
 * スプレッドシートへは一切書き込みません。
 *
 * 見つける種類:
 *   NEEDS_IMAGE        図やグラフを指しているのに question_image_refs が空
 *   NEEDS_PROGRAM      プログラムを指しているのに、本文にプログラムが載っていない
 *   BLANK_NOT_FOUND    「（き）に当てはまる」の（き）が本文のどこにも無い
 *   MISSING_CONTEXT    （あ）などの空欄を指しているのに、本文にその前提が書かれていない
 *   TRUNCATED_TEXT     本文が文の途中で終わっている
 *   OPTION_PLACEHOLDER 選択肢が画像前提の目印のままで、画像が登録されていない
 *   NO_EXPLANATION     解説が1つも登録されていない（AI補助解説の材料が無い）
 *
 * 同じ画像を共有している問題（1つの大問を空欄ごとに分けたもの）は
 * まとめて直すことになるので、siblings 列に仲間の question_id を出します。
 *
 * 使い方: Apps Scriptエディタでこの関数を実行し、ログに出る一覧を確認してください。
 */
function runQuestionContentAudit() {
  const findings = [];
  const counts = {
    NEEDS_IMAGE: 0,
    NEEDS_PROGRAM: 0,
    BLANK_NOT_FOUND: 0,
    MISSING_CONTEXT: 0,
    TRUNCATED_TEXT: 0,
    OPTION_PLACEHOLDER: 0,
    NO_EXPLANATION: 0
  };

  const questions = readObjects_(APP_CONFIG.SHEETS.QUESTIONS).filter(isFormalQuestion_);

  /*
   * 同じ画像を使っている問題どうしを結びつける。
   *
   * 1つの大問を空欄ごとに分けた問題は、同じ図を指したまま台帳の別々の行になっている。
   * 前提が落ちていれば仲間も同じように落ちているので、
   * 1問見つけたら仲間もまとめて確認できるようにする。
   */
  const byImageRefs = {};
  questions.forEach(q => {
    const refs = parseImageRefs_(q.question_image_refs);
    if (!refs.length) return;
    const key = refs.join('|');
    if (!byImageRefs[key]) byImageRefs[key] = [];
    byImageRefs[key].push(String(q.question_id || ''));
  });

  questions.forEach(q => {
    const qid = String(q.question_id || '');
    const text = String(q.question_text || '');
    const refs = parseImageRefs_(q.question_image_refs);
    const reasons = findQuestionContentGaps_(q);
    if (!reasons.length) return;

    reasons.forEach(reason => {
      if (counts[reason] === undefined) counts[reason] = 0;
      counts[reason] += 1;
    });

    const siblings = (byImageRefs[refs.join('|')] || []).filter(id => id && id !== qid);

    findings.push({
      question_id: qid,
      primary_node_id: String(q.primary_node_id || ''),
      reasons: reasons.join('+'),
      text_length: text.length,
      has_image: refs.length > 0,
      unresolved_blanks: findUnresolvedBlanks_(text).join(','),
      shared_image_with: siblings.join(','),
      question_text: text.slice(0, 120)
    });
  });

  findings.sort((a, b) => a.question_id.localeCompare(b.question_id));

  console.log('正式問題: ' + questions.length + '問 / 要確認: ' + findings.length + '問');
  console.log('内訳: ' + JSON.stringify(counts));
  console.log('--- 以下をコピーして共有してください ---');
  console.log('question_id\treasons\ttext_length\thas_image\tblanks\tshared_image_with');
  findings.forEach(f => {
    console.log([
      f.question_id, f.reasons, f.text_length, f.has_image,
      f.unresolved_blanks, f.shared_image_with
    ].join('\t'));
  });

  return {
    ok: true,
    formal_total: questions.length,
    flagged: findings.length,
    counts: counts,
    findings: findings,
    note: 'この監査は読み取りだけです。03_問題台帳にも04_学習ログにも書き込みません。'
  };
}

/**
 * Drive権限と実画像展開のスモークテスト。
 * 学習ログには書きません。
 */
function runImageBundleSmokeTest() {
  const samples = ['EXAM-A3-Q001', 'EXAM-A4-Q039'];
  const results = samples.map(qid => {
    const r = getQuestionImageBundle(qid);
    return {
      question_id: qid,
      ok: !!(r && r.ok),
      question_image_count: r && r.question_images ? r.question_images.length : 0,
      option_image_letters: r && r.option_images ? Object.keys(r.option_images) : [],
      error_code: r && r.error_code ? r.error_code : ''
    };
  });
  return {
    ok: results.every(x => x.ok),
    results,
    note: 'このテストは画像をDrive ZIPから読むだけで、04_学習ログには書きません。'
  };
}
