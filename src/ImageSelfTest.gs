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
