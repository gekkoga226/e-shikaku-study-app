/**
 * 画像問題サポート
 *
 * ルール:
 * - question_image_refsが空なら通常問題
 * - 画像必須なら、ブラウザ側で全画像の読み込み成功を確認するまで問題を表示しない
 * - Drive/ZIP/画像/manifestのどれか1つでも失敗したら、その問題は一時スキップ
 * - 正解情報はこのAPIから返さない
 */
const IMAGE_CONFIG = Object.freeze({
  MAX_SOURCE_ZIP_BYTES: 5 * 1024 * 1024,
  MAX_SINGLE_IMAGE_BYTES: 750 * 1024,
  MAX_QUESTION_IMAGE_BYTES: 1250 * 1024,
  USER_SKIP_SECONDS: 10 * 60,
  SCRIPT_SKIP_SECONDS: 5 * 60
});

function imageSupportAllowsQuestionObject_(q) {
  const refs = parseImageRefs_(q.question_image_refs);
  if (!refs.length) return true;

  const qid = String(q.question_id || '');
  if (!qid) return false;
  if (isImageQuestionTemporarilySkipped_(qid)) return false;

  const manifest = IMAGE_ROLE_MAP_BY_QUESTION_ID[qid];
  if (!manifest) return false;
  if (manifest.v !== 1) return false;
  if (manifest.s !== imageRefsSignature_(refs)) return false;

  const covered = new Set();
  (manifest.q || []).forEach(i => covered.add(Number(i)));
  Object.keys(manifest.o || {}).forEach(letter => {
    (manifest.o[letter] || []).forEach(i => covered.add(Number(i)));
  });

  if (covered.size !== refs.length) return false;
  for (let i = 0; i < refs.length; i++) {
    if (!covered.has(i)) return false;
  }
  return true;
}

function getQuestionImageBundle(questionId) {
  const qid = String(questionId || '').trim();
  if (!qid) return { ok: false, error_code: 'MISSING_QUESTION_ID' };

  try {
    // 読み取り経路と同じハンドルを使う（1回の実行でスプレッドシートを開くのは1度だけ）。
    const qSheet = spreadsheet_().getSheetByName(APP_CONFIG.SHEETS.QUESTIONS);
    const q = findObjectById_(qSheet, 'question_id', qid);

    if (!q || !isFormalQuestion_(q)) throw new Error('QUESTION_NOT_FORMAL');

    const refs = parseImageRefs_(q.question_image_refs);
    if (!refs.length) {
      return { ok: true, question_id: qid, question_images: [], option_images: {} };
    }

    if (!imageSupportAllowsQuestionObject_(q)) throw new Error('IMAGE_MANIFEST_MISMATCH');

    const fileId = extractDriveFileId_(q.source_url);
    if (!fileId) throw new Error('SOURCE_FILE_ID_NOT_FOUND');

    const sourceFile = DriveApp.getFileById(fileId);
    const sourceBlob = sourceFile.getBlob();
    const sourceBytes = sourceBlob.getBytes();
    if (sourceBytes.length > IMAGE_CONFIG.MAX_SOURCE_ZIP_BYTES) throw new Error('SOURCE_ZIP_TOO_LARGE');

    const pieces = Utilities.unzip(sourceBlob);
    const byName = {};
    pieces.forEach(blob => {
      byName[normalizeZipPath_(blob.getName())] = blob;
    });

    const encoded = [];
    let totalBytes = 0;

    refs.forEach(ref => {
      const blob = byName[normalizeZipPath_(ref)];
      if (!blob) throw new Error('IMAGE_REF_NOT_FOUND:' + ref);

      const bytes = blob.getBytes();
      if (bytes.length > IMAGE_CONFIG.MAX_SINGLE_IMAGE_BYTES) throw new Error('IMAGE_TOO_LARGE:' + ref);
      totalBytes += bytes.length;
      if (totalBytes > IMAGE_CONFIG.MAX_QUESTION_IMAGE_BYTES) throw new Error('QUESTION_IMAGES_TOO_LARGE');

      const mime = mimeFromPath_(ref);
      if (!mime) throw new Error('UNSUPPORTED_IMAGE_TYPE:' + ref);

      encoded.push({
        ref_index: encoded.length,
        data_url: 'data:' + mime + ';base64,' + Utilities.base64Encode(bytes)
      });
    });

    const manifest = IMAGE_ROLE_MAP_BY_QUESTION_ID[qid];
    const questionImages = (manifest.q || []).map(i => encoded[Number(i)]).filter(Boolean);
    const optionImages = {};
    Object.keys(manifest.o || {}).forEach(letter => {
      optionImages[letter] = (manifest.o[letter] || []).map(i => encoded[Number(i)]).filter(Boolean);
    });

    return {
      ok: true,
      question_id: qid,
      question_images: questionImages,
      option_images: optionImages,
      image_count: encoded.length
    };
  } catch (e) {
    markImageQuestionSkipped_(qid);
    console.warn('Image bundle rejected for ' + qid + ': ' + String(e && e.message ? e.message : e));
    return {
      ok: false,
      question_id: qid,
      error_code: 'IMAGE_UNAVAILABLE'
    };
  }
}

function reportImageLoadFailure(questionId, detail) {
  const qid = String(questionId || '').trim();
  if (qid) markImageQuestionSkipped_(qid);
  console.warn('Client image load failure: ' + qid + ' / ' + String(detail || '').slice(0, 300));
  return { ok: true };
}

function parseImageRefs_(raw) {
  if (!raw) return [];
  return String(raw).split(';')
    .map(normalizeZipPath_)
    .filter(Boolean);
}

function normalizeZipPath_(s) {
  return String(s || '')
    .trim()
    .replace(/\\/g, '/')
    .replace(/^\.\//, '');
}

function imageRefsSignature_(refs) {
  const digest = Utilities.computeDigest(
    Utilities.DigestAlgorithm.SHA_256,
    refs.join('\n'),
    Utilities.Charset.UTF_8
  );
  return digest.map(b => {
    const v = b < 0 ? b + 256 : b;
    return ('0' + v.toString(16)).slice(-2);
  }).join('').slice(0, 16);
}

function extractDriveFileId_(url) {
  const s = String(url || '');
  const m1 = s.match(/\/d\/([A-Za-z0-9_-]{10,})/);
  if (m1) return m1[1];
  const m2 = s.match(/[?&]id=([A-Za-z0-9_-]{10,})/);
  return m2 ? m2[1] : '';
}

function mimeFromPath_(path) {
  const p = String(path || '').toLowerCase();
  if (p.endsWith('.png')) return 'image/png';
  if (p.endsWith('.jpg') || p.endsWith('.jpeg')) return 'image/jpeg';
  if (p.endsWith('.gif')) return 'image/gif';
  if (p.endsWith('.webp')) return 'image/webp';
  return '';
}

function markImageQuestionSkipped_(qid) {
  try {
    CacheService.getUserCache().put('imgskip:' + qid, '1', IMAGE_CONFIG.USER_SKIP_SECONDS);
  } catch (e) {}
  try {
    CacheService.getScriptCache().put('imgskip:' + qid, '1', IMAGE_CONFIG.SCRIPT_SKIP_SECONDS);
  } catch (e) {}
}

function isImageQuestionTemporarilySkipped_(qid) {
  try {
    if (CacheService.getUserCache().get('imgskip:' + qid)) return true;
  } catch (e) {}
  try {
    if (CacheService.getScriptCache().get('imgskip:' + qid)) return true;
  } catch (e) {}
  return false;
}
