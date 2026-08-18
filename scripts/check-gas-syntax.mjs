#!/usr/bin/env node
/**
 * Apps Script ソースの構文検査。
 *
 * 目的:
 *   壊れたJavaScriptがmainへ入って自動デプロイされることを防ぐ。
 *
 * 検査対象:
 *   - src/*.gs                         … そのままJavaScriptとして構文解析
 *   - src/*.html の <script> ブロック   … 中身をJavaScriptとして構文解析
 *   - src/appsscript.json              … JSONとして解析
 *
 * Google Apps Script 固有のグローバル（SpreadsheetApp など）は
 * 実行時に解決されるため、ここでは構文のみを見る。
 */
import fs from 'node:fs';
import path from 'node:path';
import vm from 'node:vm';

const SRC_DIR = 'src';
const failures = [];
let checked = 0;

function checkJavaScript(label, source) {
  checked += 1;
  try {
    // eslint-disable-next-line no-new
    new vm.Script(source, { filename: label });
  } catch (error) {
    failures.push(`${label}: ${error.message}`);
  }
}

function extractScriptBlocks(html) {
  const blocks = [];
  const re = /<script\b[^>]*>([\s\S]*?)<\/script>/gi;
  let match;
  while ((match = re.exec(html)) !== null) {
    const before = html.slice(0, match.index);
    const line = before.split('\n').length;
    blocks.push({ line, code: match[1] });
  }
  return blocks;
}

const entries = fs.readdirSync(SRC_DIR).sort();

for (const name of entries) {
  const full = path.join(SRC_DIR, name);
  if (!fs.statSync(full).isFile()) continue;
  const ext = path.extname(name).toLowerCase();

  if (ext === '.gs' || ext === '.js') {
    checkJavaScript(full, fs.readFileSync(full, 'utf8'));
    continue;
  }

  if (ext === '.html') {
    const html = fs.readFileSync(full, 'utf8');
    const blocks = extractScriptBlocks(html);
    if (blocks.length === 0) {
      console.log(`skip   ${full} (JavaScriptブロックなし)`);
      continue;
    }
    blocks.forEach((block, index) => {
      checkJavaScript(`${full} <script#${index + 1} @line ${block.line}>`, block.code);
    });
    continue;
  }

  if (ext === '.json') {
    checked += 1;
    try {
      JSON.parse(fs.readFileSync(full, 'utf8'));
    } catch (error) {
      failures.push(`${full}: ${error.message}`);
    }
  }
}

if (failures.length) {
  console.error('構文エラーが見つかりました:');
  failures.forEach(f => console.error(`  - ${f}`));
  process.exit(1);
}

console.log(`syntax check OK (${checked} 件を検査しました)`);
