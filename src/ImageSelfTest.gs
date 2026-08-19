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

/**
 * 「解くのに足りない情報がある問題」を洗い出す監査（読み取りだけ）。
 *
 * アプリの不具合ではなく、03_問題台帳の中身が足りていない問題を見つけるためのもの。
 * スプレッドシートへは一切書き込みません。
 *
 * 見つける種類:
 *   NEEDS_IMAGE       図やグラフを指しているのに question_image_refs が空
 *   MISSING_CONTEXT   （あ）などの空欄を指しているのに、本文にその前提が書かれていない
 *   OPTION_PLACEHOLDER 選択肢が画像前提の目印のままで、画像が登録されていない
 *   NO_EXPLANATION    解説が1つも登録されていない（AI補助解説の材料が無い）
 *
 * 使い方: Apps Scriptエディタでこの関数を実行し、ログに出る一覧を確認してください。
 */
function runQuestionContentAudit() {
  // 「図を見ないと解けない」ことを示す言い回し。
  const IMAGE_WORDS = /(図|グラフ|画像|イラスト|以下の表|下表|次の表)/;
  // 空欄補充であることを示す言い回し。
  const BLANK_WORDS = /([（(][ぁ-んァ-ヶA-Za-z][）)]|空欄|穴埋め|[［\[]\s*[）)]?\s*[］\]])/;
  // 選択肢が画像前提であることを示す内部用の目印。
  const OPTION_PLACEHOLDER = /(画像選択肢|選択肢画像)/;
  // 本文がこれより短いと、前提の文章が入っていない可能性が高い。
  const SHORT_TEXT_CHARS = 60;

  const findings = [];
  const counts = { NEEDS_IMAGE: 0, MISSING_CONTEXT: 0, OPTION_PLACEHOLDER: 0, NO_EXPLANATION: 0 };

  const questions = readObjects_(APP_CONFIG.SHEETS.QUESTIONS).filter(isFormalQuestion_);

  questions.forEach(q => {
    const qid = String(q.question_id || '');
    const text = String(q.question_text || '');
    const hasImage = parseImageRefs_(q.question_image_refs).length > 0;

    const optionText = 'ABCDEFGH'.split('')
      .map(letter => String(q['option_' + letter.toLowerCase()] || ''))
      .join('\n');

    const reasons = [];

    if (!hasImage && IMAGE_WORDS.test(text)) reasons.push('NEEDS_IMAGE');
    if (!hasImage && OPTION_PLACEHOLDER.test(optionText)) reasons.push('OPTION_PLACEHOLDER');
    // 空欄を指しているのに本文が短い＝前提の文章が取り込まれていない。
    if (BLANK_WORDS.test(text) && text.length < SHORT_TEXT_CHARS && !hasImage) {
      reasons.push('MISSING_CONTEXT');
    }

    const hasExplanation = ['explanation_plain', 'explanation_formal',
      'explanation_calculation', 'explanation_options']
      .some(key => String(q[key] || '').trim());
    if (!hasExplanation) reasons.push('NO_EXPLANATION');

    if (!reasons.length) return;

    reasons.forEach(reason => { counts[reason] += 1; });
    findings.push({
      question_id: qid,
      primary_node_id: String(q.primary_node_id || ''),
      reasons: reasons.join('+'),
      text_length: text.length,
      has_image: hasImage,
      question_text: text.slice(0, 120)
    });
  });

  findings.sort((a, b) => a.question_id.localeCompare(b.question_id));

  console.log('正式問題: ' + questions.length + '問 / 要確認: ' + findings.length + '問');
  console.log('内訳: ' + JSON.stringify(counts));
  console.log('--- 以下をコピーして共有してください（question_id と理由だけ） ---');
  console.log('question_id\treasons\ttext_length\thas_image');
  findings.forEach(f => {
    console.log([f.question_id, f.reasons, f.text_length, f.has_image].join('\t'));
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
