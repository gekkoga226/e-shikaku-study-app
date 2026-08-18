/**
 * E資格 学習Webアプリ 完全版 v2
 *
 * 役割:
 * - Google Sheetsを唯一の正しい理解度計算元として扱う
 * - verified / active の問題だけを出題する
 * - 正解は回答前にブラウザへ送らない
 * - 回答ログを書いた後、02_マインドマップのmastery_pctを読み戻す
 * - 同じ問題を何度解いてもcoverageを水増ししない（シート側 mastery v2 数式に委譲）
 * - 画像必須問題は ImageSupport.gs に委譲
 */

const APP_CONFIG = Object.freeze({
  SPREADSHEET_ID: '1hAsu5eR3dtHu6S34joxyCaNmLZBtSnivIguVqJ61bh8',
  TIMEZONE: 'Asia/Tokyo',
  SHEETS: {
    SETTINGS: '00_設定',
    MINDMAP: '02_マインドマップ',
    QUESTIONS: '03_問題台帳',
    LOG: '04_学習ログ',
    DASHBOARD: '06_ダッシュボード'
  },
  MAX_CLIENT_EXCLUDES: 30
});

function doGet() {
  return HtmlService.createTemplateFromFile('Index')
    .evaluate()
    .setTitle('E資格 2週間合格 学習アプリ')
    .addMetaTag('viewport', 'width=device-width, initial-scale=1, viewport-fit=cover');
}

function include(filename) {
  return HtmlService.createHtmlOutputFromFile(filename).getContent();
}

function getInitialData() {
  return {
    dashboard: readDashboard_(),
    weaknesses: readWeaknesses_(8),
    ruleVersion: readSettingValue_('mastery_rule_version') || '',
    generatedAt: formatDateTime_(new Date())
  };
}

function getWeaknessData() {
  return readWeaknesses_(50);
}

/**
 * request:
 * {
 *   mode: "learning" | "review",
 *   excludeQuestionIds: ["..."]
 * }
 */
function getNextQuestion(request) {
  request = request || {};
  const mode = request.mode === 'review' ? 'review' : 'learning';
  const excludes = new Set((request.excludeQuestionIds || []).slice(-APP_CONFIG.MAX_CLIENT_EXCLUDES));

  const questions = readObjects_(APP_CONFIG.SHEETS.QUESTIONS)
    .filter(isFormalQuestion_)
    .filter(q => !excludes.has(String(q.question_id)))
    .filter(q => imageSupportAllowsQuestionObject_(q));

  if (!questions.length) {
    return { ok: false, reason: 'NO_ELIGIBLE_QUESTION' };
  }

  const logs = readObjects_(APP_CONFIG.SHEETS.LOG);
  const latest = latestFormalLogByQuestion_(logs);
  const nodes = readMindmapLeafNodes_();
  const today = startOfDay_(new Date());

  const rankedNodes = nodes.slice().sort((a, b) => {
    const adue = isDue_(a.next_review_at, today) ? 0 : 1;
    const bdue = isDue_(b.next_review_at, today) ? 0 : 1;
    if (mode === 'review' && adue !== bdue) return adue - bdue;
    if (a.mastery_pct !== b.mastery_pct) return a.mastery_pct - b.mastery_pct;
    if (a.weighted_accuracy !== b.weighted_accuracy) return a.weighted_accuracy - b.weighted_accuracy;
    const acov = a.required_unique_questions ? a.primary_unique_answered_count / a.required_unique_questions : 1;
    const bcov = b.required_unique_questions ? b.primary_unique_answered_count / b.required_unique_questions : 1;
    if (acov !== bcov) return acov - bcov;
    return String(a.node_id).localeCompare(String(b.node_id));
  });

  for (const node of rankedNodes) {
    const primary = questions.filter(q => String(q.primary_node_id || '') === node.node_id);
    if (!primary.length) continue;

    const due = isDue_(node.next_review_at, today);
    const scored = primary.map(q => {
      const l = latest[String(q.question_id)];
      let bucket = 50;

      if (mode === 'review') {
        if (due && l && !truthy_(l.is_correct)) bucket = 0;
        else if (due && l && Number(l.confidence || 0) <= 1) bucket = 1;
        else if (!l) bucket = 5;
        else if (!truthy_(l.is_correct)) bucket = 10;
        else if (Number(l.confidence || 0) <= 1) bucket = 15;
      } else {
        if (due && l && !truthy_(l.is_correct)) bucket = 0;
        else if (!l) bucket = 2;
        else if (l && !truthy_(l.is_correct)) bucket = 8;
        else if (l && Number(l.confidence || 0) <= 1) bucket = 12;
        else bucket = 30;
      }

      return { q, bucket, last: l ? dateFromCell_(l.answered_at) : null };
    });

    scored.sort((a, b) => {
      if (a.bucket !== b.bucket) return a.bucket - b.bucket;
      const at = a.last ? a.last.getTime() : 0;
      const bt = b.last ? b.last.getTime() : 0;
      if (at !== bt) return at - bt;
      return String(a.q.question_id).localeCompare(String(b.q.question_id));
    });

    if (scored.length) {
      return publicQuestion_(scored[0].q, node, mode);
    }
  }

  // 想定外にprimaryで選べない場合の安全なフォールバック。
  // masteryへ直接加点するためではなく、正式問題を出題できる状態を保つため。
  const fallback = questions.sort((a, b) => String(a.question_id).localeCompare(String(b.question_id)))[0];
  const node = nodes.find(n => n.node_id === String(fallback.primary_node_id || '')) || null;
  return publicQuestion_(fallback, node, mode);
}

function submitAnswer(payload) {
  payload = payload || {};
  const qid = String(payload.questionId || '').trim();
  const userAnswer = String(payload.userAnswer || '').trim().toUpperCase();
  const confidence = Number(payload.confidence || 0);
  const responseSeconds = payload.responseSeconds == null ? '' : Number(payload.responseSeconds);
  const mode = payload.mode === 'review' ? 'review' : 'learning';
  const sessionId = String(payload.sessionId || makeSessionId_());

  if (!qid) throw new Error('questionId がありません。');
  if (!/^[A-H]$/.test(userAnswer)) throw new Error('回答は A〜H のいずれかで指定してください。');
  if (![1, 2, 3].includes(confidence)) throw new Error('confidence は 1〜3 で指定してください。');

  const lock = LockService.getScriptLock();
  if (!lock.tryLock(10000)) throw new Error('回答処理が重なっています。数秒後にもう一度お試しください。');

  try {
    const ss = SpreadsheetApp.openById(APP_CONFIG.SPREADSHEET_ID);
    const qSheet = ss.getSheetByName(APP_CONFIG.SHEETS.QUESTIONS);
    const logSheet = ss.getSheetByName(APP_CONFIG.SHEETS.LOG);
    const mapSheet = ss.getSheetByName(APP_CONFIG.SHEETS.MINDMAP);

    const q = findObjectById_(qSheet, 'question_id', qid);
    if (!q || !isFormalQuestion_(q)) throw new Error('この問題は現在、正式回答対象ではありません。');

    const allowed = availableOptionLetters_(q);
    if (!allowed.includes(userAnswer)) throw new Error('存在しない選択肢が指定されました。');

    const correct = String(q.correct_option || '').trim().toUpperCase();
    const isCorrect = userAnswer === correct;
    const nodeId = String(q.primary_node_id || '');
    const nodeRow = findRowByValue_(mapSheet, 1, nodeId);
    if (!nodeRow) throw new Error('primary_node_id に対応する論点が見つかりません。');

    const masteryBefore = numberOrZero_(mapSheet.getRange(nodeRow, 13).getValue()); // M
    const now = new Date();
    const existingReview = mapSheet.getRange(nodeRow, 16).getValue(); // P
    const nextReview = computeNextReview_(isCorrect, existingReview, now);

    const targetRow = findLogicalNextLogRow_(logSheet);
    ensureLogCapacity_(logSheet, targetRow);

    const attemptId = makeAttemptId_(qid, now);
    const notes = [
      'Webアプリ正式回答。',
      'mastery_rule_version=' + (readSettingValue_('mastery_rule_version') || 'unknown') + '。',
      '理解度は02_マインドマップM列を正とし、P/Q列は監査用スナップショット。'
    ].join('');

    const rowValues = [
      attemptId,                // A attempt_id
      now,                      // B answered_at
      sessionId,               // C session_id
      qid,                     // D question_id
      nodeId,                  // E primary_node_id
      q.secondary_node_ids || '', // F
      mode,                    // G
      userAnswer,              // H
      correct,                 // I
      isCorrect,               // J
      confidence,              // K
      responseSeconds === '' || Number.isNaN(responseSeconds) ? '' : responseSeconds, // L
      isCorrect ? 'none' : 'knowledge_gap', // M
      q.difficulty || 'standard', // N
      q.verification_status,   // O
      masteryBefore,           // P
      '',                      // Q: flush後に読み戻して入れる
      nextReview || '',        // R
      'success',               // S
      notes                    // T
    ];

    logSheet.getRange(targetRow, 1, 1, 20).setValues([rowValues]);
    logSheet.getRange(targetRow, 2).setNumberFormat('yyyy-mm-dd hh:mm:ss');

    // U列の正式集計判定式が未設置の場合だけ補う。
    const uCell = logSheet.getRange(targetRow, 21);
    if (!uCell.getFormula()) {
      uCell.setFormula(
        '=IF(D' + targetRow + '="","",AND(' +
        'O' + targetRow + '="verified",' +
        'S' + targetRow + '="success",' +
        'G' + targetRow + '<>"system_test",' +
        'INDEX(\'00_設定\'!$B:$B,MATCH("phase2_a3_import_complete",\'00_設定\'!$A:$A,0))=TRUE,' +
        'B' + targetRow + '>=INDEX(\'00_設定\'!$B:$B,MATCH("mastery_tracking_start_at",\'00_設定\'!$A:$A,0))' +
        '))'
      );
    }

    // 最終回答日時と、明示されている誤答翌日復習ルールだけを更新。
    mapSheet.getRange(nodeRow, 15).setValue(now).setNumberFormat('yyyy-mm-dd hh:mm:ss'); // O
    if (nextReview === '') {
      mapSheet.getRange(nodeRow, 16).clearContent();
    } else if (nextReview instanceof Date) {
      mapSheet.getRange(nodeRow, 16).setValue(nextReview).setNumberFormat('yyyy/mm/dd');
    } else {
      mapSheet.getRange(nodeRow, 16).setValue(nextReview);
    }

    SpreadsheetApp.flush();

    // mastery v2 はSheet数式が再計算した値を読み戻す。
    const masteryAfter = numberOrZero_(mapSheet.getRange(nodeRow, 13).getValue());
    logSheet.getRange(targetRow, 17).setValue(masteryAfter); // Q snapshot
    SpreadsheetApp.flush();

    return {
      ok: true,
      attempt_id: attemptId,
      question_id: qid,
      is_correct: isCorrect,
      correct_answer: correct,
      user_answer: userAnswer,
      explanation_plain: q.explanation_plain || '',
      explanation_formal: q.explanation_formal || '',
      explanation_calculation: q.explanation_calculation || '',
      explanation_options: q.explanation_options || '',
      mastery_before: masteryBefore,
      mastery_after: masteryAfter,
      next_review_at: formatMaybeDate_(nextReview),
      node_id: nodeId,
      node_name: getNodeNameByRow_(mapSheet, nodeRow),
      counts_for_mastery: truthy_(uCell.getValue())
    };
  } finally {
    lock.releaseLock();
  }
}

function runSelfTest() {
  const results = [];
  const push = (name, ok, detail) => results.push({ name, ok: !!ok, detail: String(detail || '') });

  try {
    const ss = SpreadsheetApp.openById(APP_CONFIG.SPREADSHEET_ID);
    push('Spreadsheet open', !!ss, ss.getName());

    const expected = [
      APP_CONFIG.SHEETS.SETTINGS,
      APP_CONFIG.SHEETS.MINDMAP,
      APP_CONFIG.SHEETS.QUESTIONS,
      APP_CONFIG.SHEETS.LOG,
      APP_CONFIG.SHEETS.DASHBOARD
    ];
    expected.forEach(name => push('Sheet ' + name, !!ss.getSheetByName(name), name));

    const rule = readSettingValue_('mastery_rule_version');
    push('Mastery v2', String(rule).indexOf('v2_') === 0, rule);

    const qHeaders = headerMap_(ss.getSheetByName(APP_CONFIG.SHEETS.QUESTIONS));
    ['question_id','question_text','correct_option','primary_node_id','verification_status','active']
      .forEach(h => push('03 header ' + h, !!qHeaders[h], h));

    const lHeaders = headerMap_(ss.getSheetByName(APP_CONFIG.SHEETS.LOG));
    ['attempt_id','question_id','is_correct','mastery_before','mastery_after','counts_for_mastery']
      .forEach(h => push('04 header ' + h, !!lHeaders[h], h));

    const mapHeaders = headerMap_(ss.getSheetByName(APP_CONFIG.SHEETS.MINDMAP));
    ['mastery_pct','required_unique_questions','primary_unique_answered_count','latest_unique_correct_count']
      .forEach(h => push('02 header ' + h, !!mapHeaders[h], h));

    // mastery計算の本体がM列で数式になっていることを確認（ログへ書かない）。
    const mapSheet = ss.getSheetByName(APP_CONFIG.SHEETS.MINDMAP);
    const formula = mapSheet.getRange(2, 13).getFormula();
    push('M列 is formula', !!formula, formula ? 'OK' : 'formula missing');

    const eligibleCount = readObjects_(APP_CONFIG.SHEETS.QUESTIONS).filter(isFormalQuestion_).length;
    push('Formal questions > 0', eligibleCount > 0, eligibleCount);

    return {
      ok: results.every(r => r.ok),
      results,
      note: 'このテストは04_学習ログへ何も書きません。'
    };
  } catch (e) {
    return { ok: false, results, error: String(e && e.message ? e.message : e) };
  }
}

/* --------------------------- AI補助解説（Gemini） ---------------------------
 *
 * 役割の境界（ここを越えない）:
 * - 出題・採点・理解度・学習ログは、これまでどおり既存の確定ロジックだけが担当する。
 * - この節が扱うのは「すでに採点が終わった1回の回答」を、あとから言い換えることだけ。
 * - 02_マインドマップ / 04_学習ログ へは一切書き込まない（読むだけ）。
 * - APIキーはスクリプトプロパティからのみ読み、クライアントへは絶対に返さない。
 * - 04_学習ログに write_status=success の回答が実在するときだけ動く。
 *   （回答前に呼ばれても、正解や解説を1文字も返さない）
 */

const AI_CONFIG = Object.freeze({
  API_KEY_PROPERTY: 'GEMINI_API_KEY',
  MODEL_PROPERTY: 'GEMINI_MODEL',
  DEFAULT_MODEL: 'gemini-3.5-flash',
  ENDPOINT_BASE: 'https://generativelanguage.googleapis.com/v1beta/models/',
  CACHE_PREFIX: 'aiexpl:',
  CACHE_SECONDS: 3600,
  MAX_CACHE_CHARS: 90000,
  MAX_IMAGE_PARTS: 8,
  MAX_OUTPUT_TOKENS: 1600,
  TEMPERATURE: 0.3
});

const AI_DISCLAIMER = 'AI補助解説は説明の言い換えだけを行います。正誤判定・理解度・学習ログはスプレッドシートの確定ロジックが決めており、AIは一切変更しません。';

const AI_SYSTEM_INSTRUCTION = [
  'あなたは、E資格を独学している非エンジニアの学習者に付き添う家庭教師です。',
  'すでに採点が終わった1問について、あとから噛み砕いて説明することだけが仕事です。',
  '',
  '絶対に守ること:',
  '- 採点結果と「登録されている正解」は確定済みの事実です。これを絶対基準として扱い、',
  '  あなたの判断で正解を変更・否定・訂正してはいけません。',
  '  もし解説に疑問があっても、正解は登録どおりであるという前提で説明を組み立ててください。',
  '- あなたは採点をしません。理解度（%）やスコアを新しく作らないでください。',
  '- 学習者は非エンジニアです。専門用語をいきなり使わず、まず日常のことばで言い換え、',
  '  そのあとに正式な用語を出してください。',
  '  例:「数字を縦横に並べた表」→「これを正式には行列（matrix）と呼びます」',
  '- 数式は最小限にし、使うときは記号の意味を日本語で説明してください。',
  '- 与えられた情報に書かれていないことは推測で断定せず、',
  '  「登録された解説にはここまでしか書かれていません」と正直に述べてください。',
  '- 画像が渡された場合は、その画像に実際に写っているものだけを根拠にしてください。',
  '',
  '出力の構成（この見出しをそのまま使い、この順番で書く）:',
  '【まず一言】',
  '【なぜそうなる？】',
  '【他の選択肢との違い】',
  '【覚え方】',
  '',
  '全体で日本語800〜1200文字程度。箇条書きを適度に使い、読みやすくしてください。'
].join('\n');

/**
 * 回答後の補助解説だけをAIへ依頼する。
 *
 * request: { attemptId: "ATT-WEB-..." }
 *
 * 返り値（成功）: { ok:true, attempt_id, model, text, cached, disclaimer }
 * 返り値（失敗）: { ok:false, error_code, message }
 * どちらの場合もAPIキーは含めない。
 */
function getAiExplanation(request) {
  request = request || {};
  const attemptId = String(request.attemptId || '').trim();
  if (!attemptId) {
    return aiFailure_('MISSING_ATTEMPT_ID', 'AI解説を出すには、どの回答についてかを示すIDが必要です。');
  }

  // 同じattemptの解説は1時間キャッシュし、連打でAPI使用量が増えないようにする。
  // このキャッシュはユーザー単位で、回答済みの確認を通ったあとにしか書かれない。
  // つまりキャッシュに存在する時点で「回答済み」が保証されている。
  const cacheKey = AI_CONFIG.CACHE_PREFIX + attemptId;
  const cached = aiCacheGet_(cacheKey);
  if (cached && cached.text) {
    return {
      ok: true,
      attempt_id: attemptId,
      model: String(cached.model || ''),
      text: String(cached.text),
      cached: true,
      disclaimer: AI_DISCLAIMER
    };
  }

  // ここを通らない限り、正解も解説も1文字も外へ出さない。
  const found = readAnsweredAttempt_(attemptId);
  if (!found.ok) return found;

  const properties = PropertiesService.getScriptProperties();
  const apiKey = properties.getProperty(AI_CONFIG.API_KEY_PROPERTY);
  if (!apiKey) {
    return aiFailure_(
      'AI_KEY_NOT_CONFIGURED',
      'GEMINI_API_KEY がスクリプトプロパティに登録されていません。Apps Scriptのプロジェクト設定から登録してください。'
    );
  }

  const model = String(properties.getProperty(AI_CONFIG.MODEL_PROPERTY) || '').trim() || AI_CONFIG.DEFAULT_MODEL;
  const parts = buildAiPromptParts_(found.attempt, found.question);
  const generated = callGeminiGenerateContent_(model, apiKey, parts);
  if (!generated.ok) return generated;

  aiCachePut_(cacheKey, { model: model, text: generated.text });

  return {
    ok: true,
    attempt_id: attemptId,
    model: model,
    text: generated.text,
    cached: false,
    disclaimer: AI_DISCLAIMER
  };
}

/**
 * 04_学習ログに write_status=success で記録済みの回答だけを認める。
 * 見つからなければ、正解に関する情報を含めずに失敗を返す。
 */
function readAnsweredAttempt_(attemptId) {
  const ss = SpreadsheetApp.openById(APP_CONFIG.SPREADSHEET_ID);
  const logSheet = ss.getSheetByName(APP_CONFIG.SHEETS.LOG);
  if (!logSheet) {
    return aiFailure_('LOG_SHEET_NOT_FOUND', '学習ログを読み取れませんでした。');
  }

  const attempt = findObjectById_(logSheet, 'attempt_id', attemptId);
  if (!attempt) {
    return aiFailure_('ATTEMPT_NOT_FOUND', 'この回答は学習ログに存在しません。先に問題へ回答してください。');
  }

  if (String(attempt.write_status || '').trim().toLowerCase() !== 'success') {
    return aiFailure_('ATTEMPT_NOT_SUCCESS', '回答の記録が完了していないため、AI解説は出せません。');
  }

  const userAnswer = String(aiPick_(attempt, ['user_answer', 'user_option', 'selected_option']) || '')
    .trim().toUpperCase();
  if (!/^[A-H]$/.test(userAnswer)) {
    return aiFailure_('ATTEMPT_NOT_ANSWERED', 'この記録にはまだ選択した回答が入っていません。');
  }

  const qid = String(attempt.question_id || '').trim();
  if (!qid) {
    return aiFailure_('ATTEMPT_WITHOUT_QUESTION', 'この記録には問題IDがありません。');
  }

  const qSheet = ss.getSheetByName(APP_CONFIG.SHEETS.QUESTIONS);
  const question = findObjectById_(qSheet, 'question_id', qid);
  if (!question) {
    return aiFailure_('QUESTION_NOT_FOUND', '対象の問題を03_問題台帳から読み取れませんでした。');
  }

  return { ok: true, attempt: attempt, question: question };
}

/**
 * Geminiへ渡す parts を組み立てる。
 * 先頭にテキスト、必要なら既存の画像取得を再利用して inline_data を続ける。
 */
function buildAiPromptParts_(attempt, question) {
  const letters = availableOptionLetters_(question);
  const correct = String(question.correct_option || '').trim().toUpperCase();
  const userAnswer = String(aiPick_(attempt, ['user_answer', 'user_option', 'selected_option']) || '')
    .trim().toUpperCase();
  const isCorrect = userAnswer === correct;

  const lines = [];
  lines.push('■ 状況（すでに採点済み）');
  lines.push('採点はスプレッドシートの確定ロジックが行い、結果は変更されません。');
  lines.push('学習者が選んだ選択肢: ' + userAnswer);
  lines.push('登録されている正解（絶対基準・変更禁止）: ' + correct);
  lines.push('採点結果: ' + (isCorrect ? '正解' : '不正解'));
  lines.push('学習者の自信度(1〜3): ' + String(attempt.confidence || ''));
  lines.push('');

  lines.push('■ 論点ID');
  lines.push(String(question.primary_node_id || '（未設定）'));
  lines.push('');

  lines.push('■ 問題文');
  lines.push(String(question.question_text || '（本文なし）'));
  lines.push('');

  lines.push('■ 選択肢');
  letters.forEach(letter => {
    const body = String(question['option_' + letter.toLowerCase()] || '').trim();
    lines.push(letter + ': ' + (body || '（本文なし・画像のみ）'));
  });
  lines.push('');

  const registered = [
    ['やさしい解説', question.explanation_plain],
    ['正式な解説', question.explanation_formal],
    ['計算の解説', question.explanation_calculation],
    ['選択肢ごとの解説', question.explanation_options]
  ].filter(pair => String(pair[1] || '').trim());

  lines.push('■ すでに登録されている解説（これと矛盾しないこと）');
  if (registered.length) {
    registered.forEach(pair => {
      lines.push('- ' + pair[0] + ': ' + String(pair[1]).trim());
    });
  } else {
    lines.push('- 登録された解説はありません。問題文と選択肢と正解だけを根拠にしてください。');
  }
  lines.push('');

  lines.push('■ 依頼');
  lines.push('上の内容を、非エンジニアの学習者にも分かるように噛み砕いて説明してください。');
  lines.push('正解は登録どおりです。別の選択肢が正しいという説明はしないでください。');

  const parts = [{ text: lines.join('\n') }];
  appendAiImageParts_(parts, question);
  return parts;
}

/**
 * 画像問題では既存の getQuestionImageBundle を内部で再利用し、inline_data として渡す。
 * 画像を取得できなくても、回答済みならテキストだけで解説を試みる。
 */
function appendAiImageParts_(parts, question) {
  const qid = String(question.question_id || '');
  if (!String(question.question_image_refs || '').trim()) return parts;

  try {
    const bundle = getQuestionImageBundle(qid);
    if (!bundle || !bundle.ok) return parts;

    let used = 0;
    (bundle.question_images || []).forEach((image, index) => {
      if (used >= AI_CONFIG.MAX_IMAGE_PARTS) return;
      const inline = aiInlineDataFromDataUrl_(image.data_url);
      if (!inline) return;
      parts.push({ text: '■ 問題画像 ' + (index + 1) });
      parts.push(inline);
      used += 1;
    });

    Object.keys(bundle.option_images || {}).sort().forEach(letter => {
      (bundle.option_images[letter] || []).forEach(image => {
        if (used >= AI_CONFIG.MAX_IMAGE_PARTS) return;
        const inline = aiInlineDataFromDataUrl_(image.data_url);
        if (!inline) return;
        parts.push({ text: '■ 選択肢 ' + letter + ' の画像' });
        parts.push(inline);
        used += 1;
      });
    });
  } catch (e) {
    console.warn('AI解説への画像添付を省略しました: ' + qid);
  }

  return parts;
}

function aiInlineDataFromDataUrl_(dataUrl) {
  const match = String(dataUrl || '').match(/^data:([a-zA-Z0-9.+/-]+);base64,(.+)$/);
  if (!match) return null;
  return { inline_data: { mime_type: match[1], data: match[2] } };
}

/**
 * Gemini API をApps Scriptサーバー側から呼ぶ。
 * キーは x-goog-api-key ヘッダーで送り、URLにもレスポンスにも残さない。
 */
function callGeminiGenerateContent_(model, apiKey, parts) {
  const url = AI_CONFIG.ENDPOINT_BASE + encodeURIComponent(model) + ':generateContent';
  const payload = {
    system_instruction: { parts: [{ text: AI_SYSTEM_INSTRUCTION }] },
    contents: [{ role: 'user', parts: parts }],
    generationConfig: {
      temperature: AI_CONFIG.TEMPERATURE,
      maxOutputTokens: AI_CONFIG.MAX_OUTPUT_TOKENS
    }
  };

  let response;
  try {
    response = UrlFetchApp.fetch(url, {
      method: 'post',
      contentType: 'application/json',
      headers: { 'x-goog-api-key': apiKey },
      payload: JSON.stringify(payload),
      muteHttpExceptions: true
    });
  } catch (e) {
    return aiFailure_('AI_NETWORK_ERROR', 'AIサービスへ接続できませんでした。時間をおいて、もう一度お試しください。');
  }

  const status = response.getResponseCode();
  if (status === 400) {
    return aiFailure_('AI_BAD_REQUEST', 'AIサービスがリクエストを受け付けませんでした。GEMINI_MODEL の設定を確認してください。');
  }
  if (status === 401 || status === 403) {
    return aiFailure_('AI_AUTH_ERROR', 'APIキーが正しくないか、権限がありません。GEMINI_API_KEY を確認してください。');
  }
  if (status === 404) {
    return aiFailure_('AI_MODEL_NOT_FOUND', 'モデルを利用できませんでした。GEMINI_MODEL の設定を確認してください。');
  }
  if (status === 429) {
    return aiFailure_('AI_RATE_LIMITED', 'AIの利用上限に達しました。少し時間をおいてからお試しください。');
  }
  if (status < 200 || status >= 300) {
    return aiFailure_('AI_HTTP_ERROR', 'AIサービスがエラーを返しました（HTTP ' + status + '）。');
  }

  let data;
  try {
    data = JSON.parse(response.getContentText());
  } catch (e) {
    return aiFailure_('AI_BAD_RESPONSE', 'AIの応答を読み取れませんでした。');
  }

  const feedback = data.promptFeedback || {};
  if (feedback.blockReason) {
    return aiFailure_('AI_BLOCKED', 'AI側の安全フィルタにより、この問題の補助解説は作れませんでした。');
  }

  const candidates = data.candidates || [];
  const content = candidates.length ? (candidates[0].content || {}) : {};
  const text = (content.parts || []).map(p => String(p.text || '')).join('').trim();
  if (!text) {
    return aiFailure_('AI_EMPTY_RESPONSE', 'AIから補助解説が返りませんでした。もう一度お試しください。');
  }

  return { ok: true, text: text };
}

function aiCacheGet_(key) {
  try {
    const raw = CacheService.getUserCache().get(key);
    if (!raw) return null;
    return JSON.parse(raw);
  } catch (e) {
    return null;
  }
}

function aiCachePut_(key, value) {
  try {
    const raw = JSON.stringify(value);
    if (raw.length > AI_CONFIG.MAX_CACHE_CHARS) return;
    CacheService.getUserCache().put(key, raw, AI_CONFIG.CACHE_SECONDS);
  } catch (e) {
    // キャッシュできなくても本体の動作は変えない。
  }
}

function aiPick_(obj, names) {
  for (let i = 0; i < names.length; i++) {
    const v = obj[names[i]];
    if (v !== undefined && v !== null && String(v) !== '') return v;
  }
  return '';
}

function aiFailure_(code, message) {
  return { ok: false, error_code: String(code), message: String(message) };
}

/* ----------------------------- internal helpers ----------------------------- */

function readDashboard_() {
  const ss = SpreadsheetApp.openById(APP_CONFIG.SPREADSHEET_ID);
  const sh = ss.getSheetByName(APP_CONFIG.SHEETS.DASHBOARD);
  const values = sh.getRange(2, 1, 9, 3).getDisplayValues();
  return values.map(r => ({ label: r[0], value: r[1], definition: r[2] })).filter(x => x.label);
}

function readWeaknesses_(limit) {
  return readMindmapLeafNodes_()
    .sort((a, b) => {
      if (a.mastery_pct !== b.mastery_pct) return a.mastery_pct - b.mastery_pct;
      if (a.weighted_accuracy !== b.weighted_accuracy) return a.weighted_accuracy - b.weighted_accuracy;
      return String(a.topic).localeCompare(String(b.topic));
    })
    .slice(0, limit);
}

function readMindmapLeafNodes_() {
  return readObjects_(APP_CONFIG.SHEETS.MINDMAP)
    .filter(r => truthy_(r.progress_eligible))
    .map(r => ({
      node_id: String(r.node_id || ''),
      topic: String(r.topic || ''),
      major_area: String(r.major_area || ''),
      mastery_pct: numberOrZero_(r.mastery_pct),
      mastery_level: String(r.mastery_level || ''),
      weighted_accuracy: numberOrZero_(r.weighted_accuracy),
      required_unique_questions: numberOrZero_(r.required_unique_questions),
      primary_unique_answered_count: numberOrZero_(r.primary_unique_answered_count),
      latest_unique_correct_count: numberOrZero_(r.latest_unique_correct_count),
      next_review_at: r.next_review_at || '',
      last_answered_at: r.last_answered_at || ''
    }));
}

function publicQuestion_(q, node, mode) {
  const options = {};
  availableOptionLetters_(q).forEach(letter => {
    options[letter] = String(q['option_' + letter.toLowerCase()] || '');
  });

  return {
    ok: true,
    question_id: String(q.question_id),
    question_text: String(q.question_text || ''),
    options,
    answer_type: String(q.answer_type || 'single'),
    primary_node_id: String(q.primary_node_id || ''),
    topic: node ? node.topic : '',
    major_area: node ? node.major_area : '',
    mastery_pct: node ? node.mastery_pct : 0,
    mastery_level: node ? node.mastery_level : '',
    difficulty: String(q.difficulty || 'standard'),
    mode,
    image_required: !!String(q.question_image_refs || '').trim()
  };
}

function isFormalQuestion_(q) {
  return String(q.verification_status || '').trim() === 'verified'
    && truthy_(q.active)
    && !!String(q.question_text || '').trim()
    && /^[A-H]$/.test(String(q.correct_option || '').trim().toUpperCase());
}

function availableOptionLetters_(q) {
  const letters = [];
  'ABCDEFGH'.split('').forEach(letter => {
    const value = q['option_' + letter.toLowerCase()];
    if (value !== '' && value != null) letters.push(letter);
  });

  // 画像だけの選択肢では本文セルが空の場合がある。
  // correct_optionより前の問題登録情報とImageManifestをもとに必要文字を補う。
  const manifest = typeof IMAGE_ROLE_MAP_BY_QUESTION_ID !== 'undefined'
    ? IMAGE_ROLE_MAP_BY_QUESTION_ID[String(q.question_id || '')]
    : null;
  if (manifest && manifest.o) {
    Object.keys(manifest.o).forEach(letter => {
      if (!letters.includes(letter)) letters.push(letter);
    });
  }
  return letters.sort();
}

function latestFormalLogByQuestion_(logs) {
  const out = {};
  logs.forEach(r => {
    if (!truthy_(r.counts_for_mastery)) return;
    const qid = String(r.question_id || '');
    if (!qid) return;
    out[qid] = r; // sheet row order: later row wins
  });
  return out;
}

function readObjects_(sheetName) {
  const ss = SpreadsheetApp.openById(APP_CONFIG.SPREADSHEET_ID);
  const sh = ss.getSheetByName(sheetName);
  const lastRow = sheetName === APP_CONFIG.SHEETS.LOG
    ? lastLogicalNonEmptyRowInColumnA_(sh)
    : sh.getLastRow();
  const lastCol = sh.getLastColumn();
  if (lastRow < 2) return [];
  const values = sh.getRange(1, 1, lastRow, lastCol).getValues();
  const headers = values[0].map(String);
  return values.slice(1).map((row, i) => {
    const obj = { _sheet_row: i + 2 };
    headers.forEach((h, j) => { if (h) obj[h] = row[j]; });
    return obj;
  }).filter(obj => Object.keys(obj).some(k => k !== '_sheet_row' && obj[k] !== '' && obj[k] != null));
}

function headerMap_(sheet) {
  const headers = sheet.getRange(1, 1, 1, sheet.getLastColumn()).getValues()[0];
  const map = {};
  headers.forEach((h, i) => { if (h) map[String(h)] = i + 1; });
  return map;
}

function findObjectById_(sheet, idHeader, idValue) {
  const headers = headerMap_(sheet);
  const col = headers[idHeader];
  if (!col) return null;
  const row = findRowByValue_(sheet, col, idValue);
  if (!row) return null;
  const values = sheet.getRange(row, 1, 1, sheet.getLastColumn()).getValues()[0];
  const obj = { _sheet_row: row };
  Object.keys(headers).forEach(h => { obj[h] = values[headers[h] - 1]; });
  return obj;
}

function findRowByValue_(sheet, col, value) {
  const last = Math.max(sheet.getLastRow(), 2);
  const values = sheet.getRange(2, col, last - 1, 1).getValues();
  const target = String(value);
  for (let i = 0; i < values.length; i++) {
    if (String(values[i][0]) === target) return i + 2;
  }
  return 0;
}

function findLogicalNextLogRow_(sheet) {
  const max = sheet.getMaxRows();
  const values = sheet.getRange(2, 1, max - 1, 1).getValues();
  for (let i = 0; i < values.length; i++) {
    if (values[i][0] === '' || values[i][0] == null) return i + 2;
  }
  return max + 1;
}

function lastLogicalNonEmptyRowInColumnA_(sheet) {
  const max = sheet.getMaxRows();
  if (max < 2) return 1;
  const values = sheet.getRange(2, 1, max - 1, 1).getValues();
  for (let i = values.length - 1; i >= 0; i--) {
    if (values[i][0] !== '' && values[i][0] != null) return i + 2;
  }
  return 1;
}

function ensureLogCapacity_(sheet, row) {
  if (row <= sheet.getMaxRows()) return;
  sheet.insertRowsAfter(sheet.getMaxRows(), Math.max(100, row - sheet.getMaxRows()));
}

function readSettingValue_(key) {
  const ss = SpreadsheetApp.openById(APP_CONFIG.SPREADSHEET_ID);
  const sh = ss.getSheetByName(APP_CONFIG.SHEETS.SETTINGS);
  const last = sh.getLastRow();
  const values = sh.getRange(1, 1, last, 2).getValues();
  for (let i = 0; i < values.length; i++) {
    if (String(values[i][0]) === key) return values[i][1];
  }
  return '';
}

function computeNextReview_(isCorrect, existingReview, now) {
  if (!isCorrect) {
    const d = startOfDay_(now);
    d.setDate(d.getDate() + 1);
    return d;
  }

  // 正解時の新しい間隔は00_設定で正式定義されていないため勝手に作らない。
  // 期限到来済みの復習を正解した場合だけ、その期限を消化済みとしてクリアする。
  const existing = dateFromCell_(existingReview);
  if (existing && existing.getTime() <= startOfDay_(now).getTime()) return '';
  return existingReview || '';
}

function getNodeNameByRow_(sheet, row) {
  return String(sheet.getRange(row, 5).getValue() || ''); // E topic
}

function truthy_(v) {
  return v === true || String(v).toUpperCase() === 'TRUE' || Number(v) === 1;
}

function numberOrZero_(v) {
  const n = Number(v);
  return Number.isFinite(n) ? n : 0;
}

function dateFromCell_(v) {
  if (!v) return null;
  if (Object.prototype.toString.call(v) === '[object Date]' && !isNaN(v.getTime())) return v;
  const s = String(v).trim().replace(/\//g, '-');
  if (!s) return null;
  const m = s.match(/^(\d{4})-(\d{1,2})-(\d{1,2})(?:[ T](\d{1,2}):(\d{1,2})(?::(\d{1,2}))?)?/);
  if (!m) return null;
  return new Date(Number(m[1]), Number(m[2]) - 1, Number(m[3]), Number(m[4] || 0), Number(m[5] || 0), Number(m[6] || 0));
}

function startOfDay_(d) {
  return new Date(d.getFullYear(), d.getMonth(), d.getDate());
}

function isDue_(v, today) {
  const d = dateFromCell_(v);
  return !!d && startOfDay_(d).getTime() <= today.getTime();
}

function formatDateTime_(d) {
  return Utilities.formatDate(d, APP_CONFIG.TIMEZONE, 'yyyy-MM-dd HH:mm:ss');
}

function formatMaybeDate_(v) {
  const d = dateFromCell_(v);
  return d ? Utilities.formatDate(d, APP_CONFIG.TIMEZONE, 'yyyy/MM/dd') : '';
}

function makeSessionId_() {
  return 'WEB-' + Utilities.formatDate(new Date(), APP_CONFIG.TIMEZONE, 'yyyyMMdd-HHmmss') + '-' + Utilities.getUuid().slice(0, 8);
}

function makeAttemptId_(qid, d) {
  return 'ATT-WEB-' + Utilities.formatDate(d, APP_CONFIG.TIMEZONE, 'yyyyMMdd-HHmmss') + '-' +
    String(qid).replace(/[^A-Za-z0-9-]/g, '').slice(-24) + '-' + Utilities.getUuid().slice(0, 6);
}
