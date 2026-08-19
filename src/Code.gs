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
  // learning / review は従来どおり。unanswered は未回答だけを消化する独立モード。
  MODES: ['learning', 'review', 'unanswered'],
  MAX_CLIENT_EXCLUDES: 30,
  // 未回答の残数は毎回数えると重いので、この秒数だけ結果を使い回す。
  // 回答を書き込んだ直後は submitAnswer 側で破棄するので、数字が古いまま残らない。
  UNANSWERED_CACHE_SECONDS: 300
});

/*
 * 読み取る列を必要最小限に絞るための一覧。
 *
 * 03_問題台帳には解説（explanation_*）や根拠（answer_evidence）など、
 * 1セルが数百〜数千文字になる列がある。出題の判定にはどれも使わないため、
 * ここに挙げた列だけを読む。読む量が減るぶん表示が速くなり、
 * 回答前に解説や根拠をサーバーへ載せないという意味でも安全側になる。
 */
const QUESTION_PICK_COLUMNS = Object.freeze([
  'question_id', 'question_text', 'correct_option', 'answer_type',
  'primary_node_id', 'verification_status', 'active', 'difficulty', 'question_image_refs',
  'option_a', 'option_b', 'option_c', 'option_d',
  'option_e', 'option_f', 'option_g', 'option_h'
]);

/** 出題の優先順位づけに使う04_学習ログの列だけ。 */
const LOG_PICK_COLUMNS = Object.freeze([
  'question_id', 'counts_for_mastery', 'is_correct', 'confidence', 'answered_at'
]);

/*
 * 02_マインドマップの読み取りに使う列だけ。
 *
 * このシートには理解度(M列)をはじめ、計算式の入った列が並んでいる。
 * 全列を読むと、使わない列の数式まで再計算の完了を待つことになり、
 * ホーム画面が返ってこなくなる。表示と出題順に使う列だけを読む。
 */
const MINDMAP_PICK_COLUMNS = Object.freeze([
  'node_id', 'topic', 'major_area', 'mastery_pct', 'mastery_level',
  'weighted_accuracy', 'required_unique_questions', 'primary_unique_answered_count',
  'latest_unique_correct_count', 'next_review_at', 'last_answered_at', 'progress_eligible',
  // ジャンル別の可視化で使う「その論点にprimaryで紐づくverified/activeな問題数」。
  // 21列目にあり、上の列（20列目・22列目）に挟まれているので、
  // maxGap でまとめ読みしている範囲の中に既に入っている。
  // ＝この列を足しても、読み取りの回数も運ぶセルの数も1つも増えない。
  'primary_verified_question_count'
]);

/** 未回答かどうかの判定に必要な列だけ。 */
const UNANSWERED_QUESTION_COLUMNS = Object.freeze([
  'question_id', 'question_text', 'correct_option', 'verification_status', 'active'
]);
const UNANSWERED_LOG_COLUMNS = Object.freeze(['question_id', 'counts_for_mastery']);

function doGet() {
  return HtmlService.createTemplateFromFile('Index')
    .evaluate()
    .setTitle('E資格 2週間合格 学習アプリ')
    .addMetaTag('viewport', 'width=device-width, initial-scale=1, viewport-fit=cover');
}

function include(filename) {
  return HtmlService.createHtmlOutputFromFile(filename).getContent();
}

/**
 * ホーム画面の上半分（学習状況と理解度基準）だけを返す。
 *
 * ホームの表示は、読むシートごとに3つの呼び出しへ分けている。
 * 1つが遅くても他が表示され、どこで待たされているのかも切り分けられる。
 *
 *   getInitialData()        06_ダッシュボード + 00_設定
 *   getWeaknessData()       02_マインドマップ（理解度の数式が並ぶ重いシート）
 *   getUnansweredSummary()  03_問題台帳 + 04_学習ログ
 */
function getInitialData() {
  const startedAt = Date.now();
  const data = {
    dashboard: readDashboard_(),
    ruleVersion: readSettingValue_('mastery_rule_version') || '',
    generatedAt: formatDateTime_(new Date())
  };
  logElapsed_('getInitialData', startedAt);
  return data;
}

/** ホーム画面の「未回答問題を優先して解く（N問）」の残数だけを返す。 */
function getUnansweredSummary() {
  const startedAt = Date.now();
  const summary = readUnansweredSummary_();
  logElapsed_('getUnansweredSummary', startedAt);
  return summary;
}

/**
 * ホーム画面の「弱い論点」だけを返す。
 *
 * 02_マインドマップは理解度の数式が並ぶシートで、読むと再計算の完了を待つ。
 * ここだけ独立した呼び出しにして、遅くても他の表示を止めないようにする。
 * 結果は数分だけ使い回し、回答を書き込んだ直後は捨てる。
 */
function getWeaknessData(limit) {
  const startedAt = Date.now();
  const count = Math.min(Math.max(Number(limit) || WEAKNESS_DEFAULT_LIMIT, 1), WEAKNESS_CACHE_SIZE);

  const cached = readWeaknessCache_();
  if (cached) return cached.slice(0, count);

  const weaknesses = readWeaknesses_(WEAKNESS_CACHE_SIZE);
  writeWeaknessCache_(weaknesses);
  logElapsed_('getWeaknessData', startedAt);
  return weaknesses.slice(0, count);
}

const WEAKNESS_DEFAULT_LIMIT = 8;
const WEAKNESS_CACHE_SIZE = 50;
const WEAKNESS_CACHE_KEY = 'weakness_v1';
const WEAKNESS_CACHE_SECONDS = 300;

function readWeaknessCache_() {
  try {
    const raw = CacheService.getUserCache().get(WEAKNESS_CACHE_KEY);
    const parsed = raw ? JSON.parse(raw) : null;
    return Array.isArray(parsed) ? parsed : null;
  } catch (e) {
    return null;
  }
}

function writeWeaknessCache_(weaknesses) {
  try {
    CacheService.getUserCache().put(
      WEAKNESS_CACHE_KEY, JSON.stringify(weaknesses), WEAKNESS_CACHE_SECONDS
    );
  } catch (e) {
    // 使い回せなくても読み直せばよいので、失敗しても動作は変えない。
  }
}

/** 回答を書き込んだ直後に呼ぶ。次にホームを開いたとき理解度が新しくなる。 */
function clearWeaknessCache_() {
  try {
    CacheService.getUserCache().remove(WEAKNESS_CACHE_KEY);
  } catch (e) {}
}

/**
 * どこで待たされているのかを実行ログへ残す。
 * 出力先はApps Scriptの実行トランスクリプトだけで、画面にもシートにも出さない。
 */
function logElapsed_(label, startedAt) {
  try {
    console.log('[perf] ' + label + ': ' + (Date.now() - startedAt) + 'ms');
  } catch (e) {}
}

/* ------------------------- ジャンル別の弱点の可視化 -------------------------
 *
 * 役割の境界（ここを越えない）:
 * - 表示だけを行う。出題・採点・理解度・学習ログには一切関わらない。
 * - 理解度をApps Script側で計算し直さない。02_マインドマップの値をそのまま畳むだけ。
 * - APP_CONFIG.MODES へは追加しない。これは学習モードではなく画面である。
 *
 * 「ジャンル」= 02_マインドマップの major_area。
 * このシートは2階層で、level 1 の5行（数学的基礎 / 機械学習 / 深層学習の基礎 /
 * 深層学習の応用 / 開発・運用環境）が大分類、level 2 の22行が論点にあたる。
 * 親子で major_area は同じ文字列なので、葉ノードの major_area だけで畳める。
 *
 * node_id の接頭辞では畳まないこと。
 * APP-* の親は DL-APP、DL-* の親は DL-BASE で、接頭辞と大分類は一致しない。
 *
 * 数える元はすべて02_マインドマップのシート数式の値:
 *   総問題数   primary_verified_question_count（verified かつ active な primary 接続問題）
 *   回答済み   primary_unique_answered_count（04_学習ログ U列 counts_for_mastery が根拠）
 *   最新正解   latest_unique_correct_count（各問題の最新の正式回答が正解のもの）
 * 22論点の合計は 06_ダッシュボードの「verified問題数」「ユニーク回答済み問題数」と
 * 一致する。ここで別の数え方を作らないこと。
 */

const GENRE_CACHE_KEY = 'genre_breakdown_v1';
const GENRE_MISTAKE_CACHE_KEY = 'genre_mistakes_v1';
const GENRE_CACHE_SECONDS = 300;
const GENRE_UNKNOWN_LABEL = '未分類';

/**
 * ジャンル別の内訳を返す。
 *
 * 読むのは02_マインドマップだけで、範囲は getWeaknessData と同一。
 * primary_verified_question_count は既にまとめ読みしている範囲の中にあるため、
 * この画面のために増える読み取りは1回もない。
 */
function getGenreBreakdown() {
  const startedAt = Date.now();

  const cached = readGenreBreakdownCache_();
  if (cached) return cached;

  const data = buildGenreBreakdown_(readMindmapLeafNodes_());
  writeGenreBreakdownCache_(data);
  logElapsed_('getGenreBreakdown', startedAt);
  return data;
}

/** 葉ノードを major_area ごとに畳む。理解度の計算はしない（足すだけ）。 */
function buildGenreBreakdown_(nodes) {
  const genres = [];
  const byArea = {};

  (nodes || []).forEach(node => {
    const area = String(node.major_area || '').trim() || GENRE_UNKNOWN_LABEL;
    if (!byArea[area]) {
      byArea[area] = {
        major_area: area,
        topic_count: 0,
        total: 0,
        answered: 0,
        unanswered: 0,
        latest_correct: 0,
        latest_wrong: 0,
        topics: []
      };
      genres.push(byArea[area]);
    }

    const genre = byArea[area];
    const topic = genreCounts_(node);
    genre.topic_count++;
    genre.total += topic.total;
    genre.answered += topic.answered;
    genre.unanswered += topic.unanswered;
    genre.latest_correct += topic.latest_correct;
    genre.latest_wrong += topic.latest_wrong;
    genre.topics.push(topic);
  });

  genres.forEach(genre => {
    genre.topics.sort(compareGenreTopics_);
    addGenreRates_(genre);
  });
  genres.sort(compareGenres_);

  return {
    genres: genres,
    totals: addGenreRates_(genres.reduce((acc, g) => {
      acc.topic_count += g.topic_count;
      acc.total += g.total;
      acc.answered += g.answered;
      acc.unanswered += g.unanswered;
      acc.latest_correct += g.latest_correct;
      acc.latest_wrong += g.latest_wrong;
      return acc;
    }, {
      major_area: '合計', topic_count: 0, total: 0, answered: 0,
      unanswered: 0, latest_correct: 0, latest_wrong: 0
    })),
    generatedAt: formatDateTime_(new Date())
  };
}

/** 1論点ぶんの内訳。すべて02_マインドマップの値をそのまま使う。 */
function genreCounts_(node) {
  const total = numberOrZero_(node.primary_verified_question_count);
  const answered = numberOrZero_(node.primary_unique_answered_count);
  const latestCorrect = numberOrZero_(node.latest_unique_correct_count);

  const counts = {
    node_id: String(node.node_id || ''),
    topic: String(node.topic || ''),
    total: total,
    answered: answered,
    // 回答済みが総数を超えることは無い想定だが、負の未回答は表示できないので0で止める。
    unanswered: Math.max(0, total - answered),
    latest_correct: latestCorrect,
    latest_wrong: Math.max(0, answered - latestCorrect),
    mastery_pct: numberOrZero_(node.mastery_pct)
  };
  return addGenreRates_(counts);
}

/** 割合を足す。分母が0のときは0%にする（表示できない値を作らない）。 */
function addGenreRates_(counts) {
  counts.answered_pct = counts.total ? Math.round(counts.answered / counts.total * 100) : 0;
  counts.unanswered_pct = counts.total ? 100 - counts.answered_pct : 0;
  counts.correct_pct = counts.answered ? Math.round(counts.latest_correct / counts.answered * 100) : 0;
  counts.wrong_pct = counts.answered ? 100 - counts.correct_pct : 0;
  return counts;
}

/** 弱いジャンルほど先に出す。同点でも毎回同じ順番になるようにする。 */
function compareGenres_(a, b) {
  if (a.correct_pct !== b.correct_pct) return a.correct_pct - b.correct_pct;
  if (a.answered_pct !== b.answered_pct) return a.answered_pct - b.answered_pct;
  return String(a.major_area).localeCompare(String(b.major_area));
}

/** ジャンルの中の論点は、既存の弱点リストと同じ「理解度が低い順」に並べる。 */
function compareGenreTopics_(a, b) {
  if (a.mastery_pct !== b.mastery_pct) return a.mastery_pct - b.mastery_pct;
  if (a.correct_pct !== b.correct_pct) return a.correct_pct - b.correct_pct;
  return String(a.node_id).localeCompare(String(b.node_id));
}

/*
 * 「これまでに一度でも間違えた問題数」だけを別の呼び出しにする。
 *
 * 画面の帯グラフは「最新の正式回答が不正解」（理解度v2と同じ最新の状態）で描く。
 * こちらは押されたときだけ読む。初回表示を待たせないため。
 *
 * 04_学習ログはE列に primary_node_id を持っているので、
 * 03_問題台帳を読まずに論点まで辿れる。返すのは論点ごとの件数だけで、
 * ジャンルへの畳み込みはクライアントが既に持っている対応表で行う
 * （02_マインドマップを二度読まないため）。
 */
const GENRE_LOG_COLUMNS = Object.freeze([
  'question_id', 'primary_node_id', 'is_correct', 'counts_for_mastery'
]);

/*
 * 04_学習ログでの位置は question_id=4 / primary_node_id=5 / is_correct=10 /
 * counts_for_mastery=21（U列）。4〜10はまとめて読み、21は別に読む。
 * 20列目の notes は1セルが長くなるため、跨がない値にしてある。
 */
const GENRE_LOG_MAX_GAP = 4;

function getGenreMistakeHistory() {
  const startedAt = Date.now();

  const cached = readGenreMistakeCache_();
  if (cached) return cached;

  const data = buildGenreMistakeHistory_(
    readColumns_(APP_CONFIG.SHEETS.LOG, GENRE_LOG_COLUMNS, { maxGap: GENRE_LOG_MAX_GAP })
  );
  writeGenreMistakeCache_(data);
  logElapsed_('getGenreMistakeHistory', startedAt);
  return data;
}

/**
 * 一度でも不正解の正式回答をした問題を、question_id単位で数える。
 * 同じ問題を何度間違えても1問。判定根拠は04_学習ログU列だけ（未回答モードと同じ）。
 */
function buildGenreMistakeHistory_(logs) {
  const nodeByQuestion = {};

  (logs || []).forEach(row => {
    if (!truthy_(row.counts_for_mastery)) return;
    if (truthy_(row.is_correct)) return;
    const qid = String(row.question_id || '');
    if (!qid || nodeByQuestion.hasOwnProperty(qid)) return;
    nodeByQuestion[qid] = String(row.primary_node_id || '');
  });

  const byNode = {};
  let total = 0;
  Object.keys(nodeByQuestion).forEach(qid => {
    total++;
    const nodeId = nodeByQuestion[qid];
    if (!nodeId) return;
    byNode[nodeId] = (byNode[nodeId] || 0) + 1;
  });

  return { by_node: byNode, total: total };
}

function readGenreBreakdownCache_() {
  try {
    const raw = CacheService.getUserCache().get(GENRE_CACHE_KEY);
    const parsed = raw ? JSON.parse(raw) : null;
    return parsed && Array.isArray(parsed.genres) ? parsed : null;
  } catch (e) {
    return null;
  }
}

function writeGenreBreakdownCache_(data) {
  try {
    CacheService.getUserCache().put(GENRE_CACHE_KEY, JSON.stringify(data), GENRE_CACHE_SECONDS);
  } catch (e) {
    // 使い回せなくても読み直せばよいので、失敗しても動作は変えない。
  }
}

function readGenreMistakeCache_() {
  try {
    const raw = CacheService.getUserCache().get(GENRE_MISTAKE_CACHE_KEY);
    const parsed = raw ? JSON.parse(raw) : null;
    return parsed && parsed.by_node ? parsed : null;
  } catch (e) {
    return null;
  }
}

function writeGenreMistakeCache_(data) {
  try {
    CacheService.getUserCache().put(
      GENRE_MISTAKE_CACHE_KEY, JSON.stringify(data), GENRE_CACHE_SECONDS
    );
  } catch (e) {}
}

/** 回答を書き込んだ直後に呼ぶ。次に開いたときジャンル別の数字が新しくなる。 */
function clearGenreBreakdownCache_() {
  try {
    const cache = CacheService.getUserCache();
    cache.remove(GENRE_CACHE_KEY);
    cache.remove(GENRE_MISTAKE_CACHE_KEY);
  } catch (e) {}
}

/**
 * request:
 * {
 *   mode: "learning" | "review" | "unanswered",
 *   excludeQuestionIds: ["..."]
 * }
 */
function getNextQuestion(request) {
  request = request || {};
  const mode = normalizeMode_(request.mode);
  const excludes = new Set((request.excludeQuestionIds || []).slice(-APP_CONFIG.MAX_CLIENT_EXCLUDES));

  const formal = readColumns_(APP_CONFIG.SHEETS.QUESTIONS, QUESTION_PICK_COLUMNS).filter(isFormalQuestion_);
  const logs = readColumns_(APP_CONFIG.SHEETS.LOG, LOG_PICK_COLUMNS);

  // 未回答モードは独立した出題経路。既存のPhase8優先ロジックには入らない。
  if (mode === 'unanswered') {
    return nextUnansweredQuestion_(formal, logs, excludes);
  }

  /*
   * 画像を用意できるかどうかの点検（imageSupportAllowsQuestionObject_）は、
   * 1問につきキャッシュ参照2回とSHA-256計算1回を伴う。
   * ここで候補全部にかけると、画像問題148問ぶん＝約450回の呼び出しになり、
   * 1問出すたびに数百回の往復が発生していた。
   *
   * 点検の中身も、並べる基準も変えない。優先順位で並べたあと、
   * 上から順に必要なぶんだけ点検する（＝ふつうは1問ぶんで済む）。
   * 並び順は question_id まで含めた全順序なので、
   * 「先に絞ってから最小を取る」のと「並べてから最初の合格を取る」のは同じ結果になる。
   */
  const questions = formal.filter(q => !excludes.has(String(q.question_id)));

  if (!questions.length) {
    return { ok: false, reason: 'NO_ELIGIBLE_QUESTION' };
  }

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

    for (const item of scored) {
      if (imageSupportAllowsQuestionObject_(item.q)) {
        return publicQuestion_(item.q, node, mode);
      }
    }
  }

  // 想定外にprimaryで選べない場合の安全なフォールバック。
  // masteryへ直接加点するためではなく、正式問題を出題できる状態を保つため。
  const fallback = questions.slice()
    .sort((a, b) => String(a.question_id).localeCompare(String(b.question_id)));
  for (const q of fallback) {
    if (!imageSupportAllowsQuestionObject_(q)) continue;
    const node = nodes.find(n => n.node_id === String(q.primary_node_id || '')) || null;
    return publicQuestion_(q, node, mode);
  }

  // 画像を用意できる問題が1問も無い状態。回答させないほうが安全なので出題しない。
  return { ok: false, reason: 'NO_ELIGIBLE_QUESTION' };
}

/* --------------------------- 未回答問題優先モード ---------------------------
 *
 * 未回答の定義（この1か所だけで決める）:
 *   03_問題台帳で verification_status=verified かつ active=TRUE の正式問題のうち、
 *   04_学習ログで counts_for_mastery=TRUE となる行を question_id 単位で1件も持たないもの。
 *
 * counts_for_mastery は04_学習ログU列のシート数式そのもの。
 * provisional / inactive / system_test / write失敗 / mastery tracking開始前 は
 * その数式が既に FALSE にしているため、ここで独自の除外条件を作らない。
 *
 * このモードは理解度の計算方法を一切変えない。出題順を変えるだけ。
 */

/**
 * 未回答モードの出題。回答済み問題は1問も混ぜない。
 */
function nextUnansweredQuestion_(formalQuestions, logs, excludes) {
  const answered = answeredQuestionIdSet_(logs);
  const unanswered = formalQuestions.filter(q => !answered.has(String(q.question_id)));

  if (!unanswered.length) {
    // 回答済み問題へは絶対にフォールバックしない。
    return {
      ok: false,
      reason: 'ALL_QUESTIONS_ANSWERED',
      mode: 'unanswered',
      remaining_unanswered: 0,
      message: '未回答の正式問題はすべて解答済みです。'
    };
  }

  const candidates = unanswered.filter(q => !excludes.has(String(q.question_id)));

  const nodeById = {};
  readMindmapLeafNodes_().forEach(node => { nodeById[node.node_id] = node; });

  // 画像を用意できるかの点検は、優先順位で並べたあと上から順に、
  // 必要なぶんだけ行う（getNextQuestion と同じ考え方）。
  // 点検の中身も優先順位も変えないので、選ばれる問題は従来と同じ。
  const ordered = candidates.slice().sort((a, b) => compareUnansweredCandidates_(a, b, nodeById));
  let picked = null;
  for (const candidate of ordered) {
    if (imageSupportAllowsQuestionObject_(candidate)) {
      picked = candidate;
      break;
    }
  }

  if (!picked) {
    // 未回答は残っているが、いまは表示できない（画像取得に失敗した直後など）。
    // ここでも回答済み問題へは戻らない。
    return {
      ok: false,
      reason: 'NO_ELIGIBLE_UNANSWERED_QUESTION',
      mode: 'unanswered',
      remaining_unanswered: unanswered.length,
      message: 'いま表示できる未回答問題がありません。少し時間をおいてからお試しください。'
    };
  }

  const node = nodeById[String(picked.primary_node_id || '')] || null;

  const payload = publicQuestion_(picked, node, 'unanswered');
  payload.remaining_unanswered = unanswered.length;
  return payload;
}

/**
 * 未回答候補どうしの優先順位。
 * 1. coverage（primary_unique_answered_count / required_unique_questions）が不足している論点
 * 2. 不足量が大きい論点
 * 3. 理解度が低い論点
 * 4. question_id（同点でも毎回同じ順番になるようにする）
 */
function compareUnansweredCandidates_(a, b, nodeById) {
  const nodeA = nodeById[String(a.primary_node_id || '')] || null;
  const nodeB = nodeById[String(b.primary_node_id || '')] || null;

  const shortA = coverageShortage_(nodeA);
  const shortB = coverageShortage_(nodeB);
  if ((shortA > 0) !== (shortB > 0)) return shortA > 0 ? -1 : 1;
  if (shortA !== shortB) return shortB - shortA;

  const masteryA = nodeA ? nodeA.mastery_pct : 0;
  const masteryB = nodeB ? nodeB.mastery_pct : 0;
  if (masteryA !== masteryB) return masteryA - masteryB;

  return String(a.question_id).localeCompare(String(b.question_id));
}

/** required_unique_questions にあと何問足りないか（シートの値をそのまま使う）。 */
function coverageShortage_(node) {
  if (!node) return 0;
  const required = numberOrZero_(node.required_unique_questions);
  if (required <= 0) return 0;
  return Math.max(0, required - numberOrZero_(node.primary_unique_answered_count));
}

/**
 * counts_for_mastery=TRUE の行を1件でも持つ question_id の集合。
 * 判定根拠は04_学習ログU列の数式だけ。
 */
function answeredQuestionIdSet_(logs) {
  const answered = new Set();
  (logs || []).forEach(row => {
    if (!truthy_(row.counts_for_mastery)) return;
    const qid = String(row.question_id || '');
    if (qid) answered.add(qid);
  });
  return answered;
}

/**
 * ホーム画面の「未回答問題を優先して解く（N問）」に出す残数。
 *
 * 数え方は出題側と同じ（isFormalQuestion_ と answeredQuestionIdSet_）。
 * 判定に使う列だけを読み、結果は数分だけ使い回す。
 */
function readUnansweredSummary_() {
  const cached = readUnansweredSummaryCache_();
  if (cached) return cached;

  const formal = readColumns_(APP_CONFIG.SHEETS.QUESTIONS, UNANSWERED_QUESTION_COLUMNS)
    .filter(isFormalQuestion_);
  const answered = answeredQuestionIdSet_(
    readColumns_(APP_CONFIG.SHEETS.LOG, UNANSWERED_LOG_COLUMNS)
  );
  const remaining = formal.filter(q => !answered.has(String(q.question_id))).length;

  const summary = {
    formal_total: formal.length,
    answered_unique: formal.length - remaining,
    remaining: remaining
  };
  writeUnansweredSummaryCache_(summary);
  return summary;
}

const UNANSWERED_CACHE_KEY = 'unanswered_summary_v1';

function readUnansweredSummaryCache_() {
  try {
    const raw = CacheService.getUserCache().get(UNANSWERED_CACHE_KEY);
    return raw ? JSON.parse(raw) : null;
  } catch (e) {
    return null;
  }
}

function writeUnansweredSummaryCache_(summary) {
  try {
    CacheService.getUserCache().put(
      UNANSWERED_CACHE_KEY,
      JSON.stringify(summary),
      APP_CONFIG.UNANSWERED_CACHE_SECONDS
    );
  } catch (e) {
    // 使い回せなくても数え直せばよいので、失敗しても動作は変えない。
  }
}

/** 回答を書き込んだ直後に呼ぶ。次にホームを開いたとき残数が1問減る。 */
function clearUnansweredSummaryCache_() {
  try {
    CacheService.getUserCache().remove(UNANSWERED_CACHE_KEY);
  } catch (e) {}
}

function normalizeMode_(mode) {
  const value = String(mode || '').trim();
  return APP_CONFIG.MODES.indexOf(value) >= 0 ? value : 'learning';
}

function submitAnswer(payload) {
  payload = payload || {};
  const qid = String(payload.questionId || '').trim();
  const userAnswer = String(payload.userAnswer || '').trim().toUpperCase();
  const confidence = Number(payload.confidence || 0);
  const responseSeconds = payload.responseSeconds == null ? '' : Number(payload.responseSeconds);
  // 未回答モードで解いた回答も、通常の正式回答条件を満たせば理解度へ反映される。
  // （U列の数式が除外するのは mode="system_test" だけ）
  const mode = normalizeMode_(payload.mode);
  const sessionId = String(payload.sessionId || makeSessionId_());

  if (!qid) throw new Error('questionId がありません。');
  if (!/^[A-H]$/.test(userAnswer)) throw new Error('回答は A〜H のいずれかで指定してください。');
  if (![1, 2, 3].includes(confidence)) throw new Error('confidence は 1〜3 で指定してください。');

  const lock = LockService.getScriptLock();
  if (!lock.tryLock(10000)) throw new Error('回答処理が重なっています。数秒後にもう一度お試しください。');

  try {
    // 読み取り経路と同じハンドルを使う。以前はここで別に開いていたため、
    // 1回の回答でスプレッドシートを2回開いていた（notesのルール版取得で再度開かれる）。
    const ss = spreadsheet_();
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

    // 同じ行のE列(topic)とP列(next_review_at)は1回の読み取りでまとめて取る。
    // 以前は復習日と論点名で別々に往復していた。
    // M列(理解度)だけは確定手順どおり単独で読む（上の1行を動かさない）。
    const nodeRowValues = mapSheet.getRange(nodeRow, 1, 1, 16).getValues()[0];
    const nodeName = String(nodeRowValues[4] || ''); // E topic
    const existingReview = nodeRowValues[15]; // P next_review_at
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

    // P列は「変わるときだけ」書く。
    // computeNextReview_ は期限が動かないとき既存の値をそのまま返すので、
    // 以前は同じ値を毎回書き直していた（往復1〜2回ぶんの無駄）。
    // 書かない＝現状のまま、なので結果は従来と同じ。
    if (nextReview !== existingReview) {
      if (nextReview instanceof Date) {
        mapSheet.getRange(nodeRow, 16).setValue(nextReview).setNumberFormat('yyyy/mm/dd');
      } else if (nextReview === '') {
        mapSheet.getRange(nodeRow, 16).clearContent(); // 消化した期限を消す
      } else {
        mapSheet.getRange(nodeRow, 16).setValue(nextReview);
      }
    }

    SpreadsheetApp.flush();

    // mastery v2 はSheet数式が再計算した値を読み戻す。
    const masteryAfter = numberOrZero_(mapSheet.getRange(nodeRow, 13).getValue());
    logSheet.getRange(targetRow, 17).setValue(masteryAfter); // Q snapshot
    SpreadsheetApp.flush();

    // 1問解いたぶん、ホーム画面の未回答残数と弱点リストを取り直させる。
    clearUnansweredSummaryCache_();
    clearWeaknessCache_();
    clearGenreBreakdownCache_();

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
      node_name: nodeName,
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

    // 未回答モードの残数（読むだけ。ログには書かない）。
    const unanswered = readUnansweredSummary_();
    push(
      'Unanswered summary',
      unanswered.formal_total === eligibleCount && unanswered.remaining <= unanswered.formal_total,
      unanswered.remaining + ' / ' + unanswered.formal_total
    );

    /*
     * ジャンル別画面の内訳（読むだけ。ログには書かない）。
     *
     * 内訳そのものの整合（回答済み<=総数、最新正解<=回答済み）を確認し、
     * 06_ダッシュボードおよび未回答モードの数え方との突き合わせを detail に出す。
     * 総問題数は02_マインドマップの primary_verified_question_count を採用しているため、
     * 03_問題台帳の isFormalQuestion_ 基準とは一致しない場合がある。
     * どちらが正しいかを機械が決められないので、ここでは失敗にせず両方の数字を残す。
     */
    const genre = getGenreBreakdown();
    const verifiedLabel = readDashboard_().filter(x => x.label === 'verified問題数')[0];
    push(
      'Genre breakdown',
      genre.genres.length > 0
        && genre.totals.total > 0
        && genre.totals.answered <= genre.totals.total
        && genre.totals.latest_correct <= genre.totals.answered,
      genre.genres.length + 'ジャンル / ' + genre.totals.topic_count + '論点'
        + ' / 総数' + genre.totals.total
        + '（06_ダッシュボード: ' + (verifiedLabel ? verifiedLabel.value : '不明')
        + ' / 03_問題台帳の正式問題: ' + unanswered.formal_total + '）'
        + ' / 回答済み' + genre.totals.answered
        + '（未回答モードの数え方: ' + unanswered.answered_unique + '）'
    );

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
  // 本文は日本語800〜1200文字を求めている。日本語はおおむね1文字1トークン以上で、
  // LaTeXの記号もトークンを食う。さらに今のモデルは答える前に内部で考え、
  // その思考ぶんもこの上限に含まれる。1600では考えている途中で打ち切られ、
  // 解説が文の途中で切れていた。実際に必要な量の数倍を確保しておく。
  MAX_OUTPUT_TOKENS: 8192,
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
  '- 数式は、書いたほうが理解しやすいところでは使ってください。',
  '  ただし式を置いただけで終わらせず、記号が何を指すのかを必ず日本語で説明してください。',
  '- 数式はLaTeXで書き、文中に挟むときは \\( と \\) で、',
  '  行を分けて見せるときは \\[ と \\] で囲んでください。',
  '  ドル記号（$）は金額と紛らわしいので数式の区切りに使わないでください。',
  '  例: 平均は \\( \\frac{1}{n}\\sum_{i=1}^{n} x_i \\) で求めます。ここで n はデータの個数です。',
  '  数式を使わないほうが分かりやすい場面では、無理に使わなくて構いません。',
  '- 与えられた情報に書かれていないことは推測で断定せず、',
  '  「登録された解説にはここまでしか書かれていません」と正直に述べてください。',
  '- 画像が渡された場合は、その画像に実際に写っているものだけを根拠にしてください。',
  '',
  '書き方（表示側はプレーンテキストなので、記法はそのまま文字として出てしまいます）:',
  '- マークダウン記法を使わないでください。',
  '  アスタリスク（*）による強調や箇条書き、シャープ（#）の見出し、',
  '  ハイフンだけの区切り線（---）、バッククォートは一切使わないでください。',
  '- 箇条書きは行頭に「・」を置いてください。',
  '- 強調したいときも装飾記号は使わず、ことばの選び方と語順で伝えてください。',
  '',
  'ふるまい:',
  '- 褒めたり励ましたりしないでください。',
  '  「おめでとうございます」「素晴らしいです」のような評価のことばは書かないでください。',
  '- 正解・不正解や自信度そのものに言及せず、内容の説明だけを淡々と書いてください。',
  '- 前置きや自己紹介は書かず、いきなり本題から始めてください。',
  '',
  '出力の構成（この見出しをそのまま使い、この順番で書く）:',
  '【まず一言】',
  '【なぜそうなる？】',
  '【他の選択肢との違い】',
  '【覚え方】',
  '',
  '全体で日本語800〜1200文字程度。読みやすい長さの文で書いてください。'
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
      truncated: cached.truncated === true,
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

  // 途中で切れた解説をキャッシュすると、1時間ずっと切れたものが再表示される。
  // 切れたときは残さず、「もう一度作る」で作り直せるようにする。
  if (generated.truncated !== true) {
    aiCachePut_(cacheKey, { model: model, text: generated.text });
  }

  return {
    ok: true,
    attempt_id: attemptId,
    model: model,
    text: generated.text,
    truncated: generated.truncated === true,
    cached: false,
    disclaimer: AI_DISCLAIMER
  };
}

/**
 * 04_学習ログに write_status=success で記録済みの回答だけを認める。
 * 見つからなければ、正解に関する情報を含めずに失敗を返す。
 */
function readAnsweredAttempt_(attemptId) {
  // 読み取りだけなので共有ハンドルを使う（AI解説1回につき1度の接続で済む）。
  const ss = spreadsheet_();
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
    // 例外の中身を捨てると原因が分からなくなる。キーは含まれないので、そのまま残す。
    const detail = aiErrorText_(e);
    console.error('Gemini呼び出しで例外が発生しました: ' + detail);
    if (/permission|scope|authoriz|権限/i.test(detail)) {
      return aiFailure_(
        'AI_NOT_AUTHORIZED',
        '外部サービスへの接続がまだ承認されていません。Apps Scriptエディタで checkAiSetup を1度実行し、表示される権限を承認してください。' + aiDetailSuffix_(detail)
      );
    }
    return aiFailure_(
      'AI_NETWORK_ERROR',
      'AIサービスへ接続できませんでした。時間をおいて、もう一度お試しください。' + aiDetailSuffix_(detail)
    );
  }

  const status = response.getResponseCode();
  const rawBody = response.getContentText();
  // Gemini はエラー理由を本文に入れてくる。これを隠すと設定ミスの特定ができない。
  const apiDetail = aiDetailSuffix_(aiApiErrorMessage_(rawBody));
  if (status < 200 || status >= 300) {
    console.error('Gemini がエラーを返しました（HTTP ' + status + '）: ' + aiApiErrorMessage_(rawBody));
  }

  if (status === 400) {
    return aiFailure_('AI_BAD_REQUEST', 'AIサービスがリクエストを受け付けませんでした。GEMINI_MODEL の設定を確認してください。' + apiDetail);
  }
  if (status === 401 || status === 403) {
    return aiFailure_('AI_AUTH_ERROR', 'APIキーが正しくないか、権限がありません。GEMINI_API_KEY を確認してください。' + apiDetail);
  }
  if (status === 404) {
    return aiFailure_('AI_MODEL_NOT_FOUND', 'モデルを利用できませんでした。GEMINI_MODEL の設定を確認してください。' + apiDetail);
  }
  if (status === 429) {
    return aiFailure_('AI_RATE_LIMITED', 'AIの利用上限に達しました。少し時間をおいてからお試しください。' + apiDetail);
  }
  if (status < 200 || status >= 300) {
    return aiFailure_('AI_HTTP_ERROR', 'AIサービスがエラーを返しました（HTTP ' + status + '）。' + apiDetail);
  }

  let data;
  try {
    data = JSON.parse(rawBody);
  } catch (e) {
    return aiFailure_('AI_BAD_RESPONSE', 'AIの応答を読み取れませんでした。');
  }

  const feedback = data.promptFeedback || {};
  if (feedback.blockReason) {
    return aiFailure_('AI_BLOCKED', 'AI側の安全フィルタにより、この問題の補助解説は作れませんでした。');
  }

  const candidates = data.candidates || [];
  const candidate = candidates.length ? (candidates[0] || {}) : {};
  const content = candidate.content || {};
  // thought: true の part はモデルの内部の考えであって解説本文ではない。
  // これを混ぜると、本文の前に思考の断片が出てしまう。
  const raw = (content.parts || [])
    .filter(p => p && p.thought !== true)
    .map(p => String(p.text || ''))
    .join('')
    .trim();

  const finishReason = String(candidate.finishReason || '').toUpperCase();
  if (finishReason === 'SAFETY' || finishReason === 'PROHIBITED_CONTENT') {
    return aiFailure_('AI_BLOCKED', 'AI側の安全フィルタにより、この問題の補助解説は作れませんでした。');
  }
  if (finishReason === 'RECITATION') {
    return aiFailure_('AI_BLOCKED', 'AIが引用制限により補助解説を作れませんでした。もう一度お試しください。');
  }

  if (!raw) {
    if (finishReason === 'MAX_TOKENS') {
      return aiFailure_(
        'AI_TRUNCATED_EMPTY',
        'AIが考えている途中で長さの上限に達し、解説を作れませんでした。もう一度お試しください。'
      );
    }
    return aiFailure_('AI_EMPTY_RESPONSE', 'AIから補助解説が返りませんでした。もう一度お試しください。');
  }

  // 上限に当たった場合、本文は文の途中で終わっている。黙って出すと
  // 「なぜか途中で切れている解説」に見えるので、切れたことを画面へ伝える。
  return { ok: true, text: aiPlainText_(raw), truncated: finishReason === 'MAX_TOKENS' };
}

/**
 * 対になっていない数式の区切り記号を、ただの文字として無害化する。
 *
 * 解説が長さの上限で切れると、末尾が「\( a = 」のように開いたままになる。
 * このまま画面のKaTeXへ渡すと、開いた \( から次に現れる \) までが
 * ひとつの数式として扱われ、あいだにある説明の段落がまるごと消える。
 * 実際、コサイン類似度の解説で本文の冒頭が消え、「{v}\)」だけが残っていた。
 *
 * そこで、対になっていて、かつ空行をまたがない区切りだけを数式として残す。
 * 数式が段落をまたぐことは無いので、空行を越えたら閉じ忘れとみなしてよい。
 * 無害化するのは区切り記号だけで、中身の文字は消さない。
 */
function aiRepairMathDelimiters_(text) {
  const source = String(text || '');
  const CLOSER_OF = { '\\(': '\\)', '\\[': '\\]' };
  const out = [];
  let i = 0;

  while (i < source.length) {
    const token = source.substr(i, 2);
    const closer = CLOSER_OF[token];

    if (closer) {
      const bodyStart = i + 2;
      const closeAt = source.indexOf(closer, bodyStart);
      const breakAt = source.slice(bodyStart).search(/\n[ \t]*\n/);
      const paragraphEnd = breakAt === -1 ? -1 : bodyStart + breakAt;

      const paired = closeAt !== -1 && (paragraphEnd === -1 || closeAt < paragraphEnd);
      if (paired) {
        out.push(source.slice(i, closeAt + 2));  // 中身ごとそのまま残す
        i = closeAt + 2;
      } else {
        i += 2;  // 開いたままの記号は落とし、続きは文章として読ませる
      }
      continue;
    }

    // ここへ来る \) と \] は、対応する開始記号が無い迷子。
    if (token === '\\)' || token === '\\]') {
      i += 2;
      continue;
    }

    out.push(source[i]);
    i += 1;
  }

  return out.join('');
}

/**
 * 画面はプレーンテキストで表示するため、マークダウン記法は文字として見えてしまう。
 * AIへの指示だけに頼らず、ここでも取り除いておく。
 */
function aiPlainText_(text) {
  // 数式は記法の除去対象から外す。LaTeX には \(a^*+b^*\) のように
  // マークダウンと紛らわしい記号が入りうるため、先に退避しておく。
  //
  // 中身は「空行をまたがない」ものだけを数式とみなす。閉じ忘れや、
  // 長さ上限で途中まで届いた \( があると、次に現れる \) まで段落をいくつも
  // 飲み込み、そのあいだの説明文が数式に化けて画面から消えてしまう。
  const math = [];
  const stashed = aiRepairMathDelimiters_(text).replace(
    /\\\[[\s\S]*?\\\]|\\\([\s\S]*?\\\)/g,
    matched => {
      if (/\n[ \t]*\n/.test(matched)) return matched;
      math.push(matched);
      return '\u0000' + (math.length - 1) + '\u0000';
    }
  );

  const cleaned = stashed
    .split('\n')
    .map(line => {
      let out = line;
      out = out.replace(/^\s*#+\s+/, '');                      // 見出し
      out = out.replace(/^(\s*)[*+-]\s+/, '$1・');              // 箇条書き
      out = out.replace(/^\s*([*_-])(\s*\1)(\s*\1)+\s*$/, ''); // 区切り線
      out = out.replace(/\*\*([^*]+)\*\*/g, '$1');             // 太字
      // 斜体。前後に空白を挟むもの（掛け算の 2 * 3 など）は記法ではないので残す。
      out = out.replace(/(^|[^*])\*(\S([^*\n]*\S)?)\*/g, '$1$2');
      // コード（\u0060 はバッククォート。文字そのものは書かない）。
      out = out.replace(/\u0060([^\u0060\n]+)\u0060/g, '$1');
      return out;
    })
    .join('\n')
    .replace(/\n\n\n+/g, '\n\n')
    .trim();

  // 退避しておいた数式を元に戻す。
  return cleaned.replace(/\u0000(\d+)\u0000/g, (whole, index) => math[Number(index)]);
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

function aiErrorText_(e) {
  if (!e) return '';
  if (e.message) return String(e.message);
  return String(e);
}

/** Gemini のエラー本文から人が読める理由だけを取り出す。 */
function aiApiErrorMessage_(rawBody) {
  try {
    const parsed = JSON.parse(String(rawBody || ''));
    const error = parsed.error || {};
    return String(error.message || '');
  } catch (e) {
    return '';
  }
}

/** 画面へ出す補足。長すぎる技術文はここで切り詰める。 */
function aiDetailSuffix_(detail) {
  const text = String(detail || '').trim();
  if (!text) return '';
  const trimmed = text.length > 300 ? text.slice(0, 300) + '…' : text;
  return '（詳細: ' + trimmed + '）';
}

/**
 * AI補助解説の設定を、Apps Scriptエディタから1回で点検する。
 * 学習データには一切触れず、権限の承認とキー・モデルの確認だけを行う。
 * エディタの「実行」から呼ぶと、不足している権限の承認画面が出る。
 */
function checkAiSetup() {
  const properties = PropertiesService.getScriptProperties();
  const apiKey = properties.getProperty(AI_CONFIG.API_KEY_PROPERTY);
  const model = String(properties.getProperty(AI_CONFIG.MODEL_PROPERTY) || '').trim() || AI_CONFIG.DEFAULT_MODEL;

  if (!apiKey) {
    console.error('GEMINI_API_KEY がスクリプトプロパティに登録されていません。');
    return { ok: false, error_code: 'AI_KEY_NOT_CONFIGURED' };
  }
  // 鍵そのものは出さず、登録されている事実だけを記録する。
  console.log('GEMINI_API_KEY: 登録済み（' + String(apiKey).length + '文字）');
  console.log('GEMINI_MODEL: ' + model);

  const result = callGeminiGenerateContent_(model, apiKey, [{ text: '接続確認です。「OK」とだけ返してください。' }]);
  if (result.ok) {
    console.log('Gemini への接続に成功しました。応答: ' + result.text);
  } else {
    console.error('Gemini への接続に失敗しました: ' + result.error_code + ' / ' + result.message);
  }
  return result;
}

/* ----------------------------- internal helpers ----------------------------- */

function readDashboard_() {
  const sh = spreadsheet_().getSheetByName(APP_CONFIG.SHEETS.DASHBOARD);
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
  // このシートの値はどれも短いので、間に挟まる数列はまとめて読んでしまう。
  // （読み飛ばすのは、右端に足された監査用の列のような遠い列だけでよい）
  return readColumns_(APP_CONFIG.SHEETS.MINDMAP, MINDMAP_PICK_COLUMNS, { maxGap: 8 })
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
      primary_verified_question_count: numberOrZero_(r.primary_verified_question_count),
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

/*
 * スプレッドシートを開く回数を1回にまとめる。
 *
 * openById は呼ぶたびに通信が発生する。1回の画面表示で5回開いていたため、
 * その分だけ待ち時間が増えていた。実行が終われば変数ごと消えるので、
 * 古いデータを持ち続けることはない。
 */
let SPREADSHEET_HANDLE_ = null;

function spreadsheet_() {
  if (!SPREADSHEET_HANDLE_) {
    SPREADSHEET_HANDLE_ = SpreadsheetApp.openById(APP_CONFIG.SPREADSHEET_ID);
  }
  return SPREADSHEET_HANDLE_;
}

/**
 * 指定した見出しの列だけを読む。
 *
 * readObjects_ はシート全体（全列）を読むため、解説のような長い文章まで
 * 毎回運んでしまう。ここでは必要な列だけを、隣り合う列はまとめて
 * 1回の読み取りにして取り出す。
 */
function readColumns_(sheetName, headers, options) {
  const sh = spreadsheet_().getSheetByName(sheetName);
  if (!sh) return [];

  // 読み飛ばす列が少しだけのときは、まとめて読んだほうが速い。
  // 往復1回（数十ms）に対し、短いセルを数百個よけいに運ぶのは一瞬で終わる。
  // 03_問題台帳のように1セルが数千文字の列を挟む場合は既定の0のまま使い、
  // 02_マインドマップのように短い値ばかりのシートだけ隙間をまたぐ。
  const maxGap = options && Number(options.maxGap) > 0 ? Number(options.maxGap) : 0;

  const lastRow = sheetName === APP_CONFIG.SHEETS.LOG
    ? lastLogicalNonEmptyRowInColumnA_(sh)
    : sh.getLastRow();
  if (lastRow < 2) return [];

  const map = headerMap_(sh);
  const wanted = [];
  headers.forEach(header => {
    if (map[header]) wanted.push({ header: header, col: map[header] });
  });
  if (!wanted.length) return [];
  wanted.sort((a, b) => a.col - b.col);

  // 連続している列はひとまとめにして読む（読み取り回数を減らすため）。
  // 間に挟まる不要な列は範囲を切って飛ばす。
  const runs = [];
  wanted.forEach(item => {
    const current = runs.length ? runs[runs.length - 1] : null;
    if (current && item.col - current.end - 1 <= maxGap) {
      current.end = item.col;
      current.items.push(item);
    } else {
      runs.push({ start: item.col, end: item.col, items: [item] });
    }
  });

  const count = lastRow - 1;
  const rows = new Array(count);
  for (let i = 0; i < count; i++) rows[i] = { _sheet_row: i + 2 };

  runs.forEach(run => {
    const values = sh.getRange(2, run.start, count, run.end - run.start + 1).getValues();
    run.items.forEach(item => {
      const offset = item.col - run.start;
      for (let i = 0; i < count; i++) rows[i][item.header] = values[i][offset];
    });
  });

  return rows.filter(row => headers.some(h => row[h] !== '' && row[h] != null));
}

function readObjects_(sheetName) {
  const ss = spreadsheet_();
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

/**
 * 論理的な次の空き行（A列がまだ空の、いちばん上の行）を返す。
 *
 * 走査は「値のある最終行」までで打ち切る。getMaxRows() はU列の数式などで
 * 実データよりずっと大きくなるため、以前はそのぶんだけ確実に空と分かっている
 * 行まで読んでいた（実測で1回の回答あたり約1600セルの無駄読み）。
 * getLastRow() より下の行にはA列の値が存在しないので、
 * そこで見つからなければ次の行が空き行になる。見つかる行は従来と同じ。
 */
function findLogicalNextLogRow_(sheet) {
  const max = Math.min(sheet.getMaxRows(), Math.max(sheet.getLastRow(), 1));
  if (max < 2) return 2;
  const values = sheet.getRange(2, 1, max - 1, 1).getValues();
  for (let i = 0; i < values.length; i++) {
    if (values[i][0] === '' || values[i][0] == null) return i + 2;
  }
  return max + 1;
}

function lastLogicalNonEmptyRowInColumnA_(sheet) {
  // U列の数式などが下まで入っていると getMaxRows() は実データよりずっと大きい。
  // 値のある最終行（getLastRow）より下にA列のデータは無いので、そこまでで足りる。
  const max = Math.min(sheet.getMaxRows(), Math.max(sheet.getLastRow(), 1));
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

/*
 * 00_設定の値は、回答のたびに読み直すほど頻繁には変わらない。
 * mastery_rule_version は回答1件ごとにnotesへ書くために読んでいて、
 * そのために毎回シートへ往復していた。数分だけ使い回す。
 * 判定に使うのは表示用の文字列だけで、理解度の計算には使わない。
 */
const SETTING_CACHE_KEY_PREFIX = 'setting_v1:';
const SETTING_CACHE_SECONDS = 300;

function readSettingValue_(key) {
  const cached = readSettingCache_(key);
  if (cached !== null) return cached;

  const sh = spreadsheet_().getSheetByName(APP_CONFIG.SHEETS.SETTINGS);
  const last = sh.getLastRow();
  const values = sh.getRange(1, 1, last, 2).getValues();
  let found = '';
  for (let i = 0; i < values.length; i++) {
    if (String(values[i][0]) === key) {
      found = values[i][1];
      break;
    }
  }

  // 使い回すのは中身のある文字列だけ。
  // 数値・日付・真偽値は型が変わると扱いが変わるので、そのつど読み直す。
  // 見つからなかった場合も残さない（あとから設定されたらすぐ効くように）。
  if (typeof found === 'string' && found !== '') {
    writeSettingCache_(key, found);
  }
  return found;
}

function readSettingCache_(key) {
  try {
    const raw = CacheService.getScriptCache().get(SETTING_CACHE_KEY_PREFIX + key);
    return raw ? raw : null;
  } catch (e) {
    return null;
  }
}

function writeSettingCache_(key, value) {
  try {
    CacheService.getScriptCache().put(
      SETTING_CACHE_KEY_PREFIX + key, value, SETTING_CACHE_SECONDS
    );
  } catch (e) {
    // 使い回せなくても読み直せばよいので、失敗しても動作は変えない。
  }
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
