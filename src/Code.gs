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
