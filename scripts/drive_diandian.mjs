#!/usr/bin/env node
/**
 * 点点自动采集器
 * 作用：在独立 Chrome 实例（调试端口 9222）中，驱动小红书「点点」把收藏专辑的笔记
 *      按模板整理成结构化文本，分批取回并落盘。
 *
 * 用法：
 *   node drive_diandian.mjs --album ai --batch 2 --total 10 --out ../logs/collect.txt
 *
 * 设计要点：
 *  1. 单条回复有长度上限，因此「分批要」：每轮只要 2 篇，写完自动发「继续」。
 *  2. 附件在会话上下文里，继续时无需重新导入。
 *  3. 每轮增量文本单独落盘，中途崩溃也能保住已拿到的部分。
 *
 * 2026-11-03 更新：发送按钮选择器从 .bottom-box-right-submit-button 改为 .wendian-btn--sendable
 */

import fs from 'node:fs';
import path from 'node:path';

const CDP = process.env.CDP_PORT ? `http://127.0.0.1:${process.env.CDP_PORT}` : 'http://127.0.0.1:9222';
const args = Object.fromEntries(
  process.argv.slice(2).reduce((acc, cur, i, arr) => {
    if (cur.startsWith('--')) acc.push([cur.slice(2), arr[i + 1] && !arr[i + 1].startsWith('--') ? arr[i + 1] : true]);
    return acc;
  }, [])
);
const BATCH = Number(args.batch || 2);
const TOTAL = Number(args.total || 10);
const ALBUM = String(args.album || 'ai');
const OUT = path.resolve(args.out || '../logs/collect.txt');

const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
const log = (...a) => console.log(new Date().toISOString().slice(11, 19), ...a);

async function connect() {
  const list = await (await fetch(`${CDP}/json/list`)).json();
  const page = list.find((t) => t.type === 'page' && t.url.includes('xiaohongshu'));
  if (!page) throw new Error('未找到小红书标签页：' + list.map((t) => t.url).join(','));
  const ws = new WebSocket(page.webSocketDebuggerUrl);
  let seq = 0;
  const pending = new Map();
  ws.onmessage = (e) => {
    const m = JSON.parse(e.data);
    if (m.id && pending.has(m.id)) {
      const { res, rej } = pending.get(m.id);
      pending.delete(m.id);
      m.error ? rej(new Error(JSON.stringify(m.error))) : res(m.result);
    }
  };
  await new Promise((r, j) => { ws.onopen = r; ws.onerror = j; });
  const send = (method, params = {}) =>
    new Promise((res, rej) => {
      const id = ++seq;
      pending.set(id, { res, rej });
      ws.send(JSON.stringify({ id, method, params }));
    });
  await send('Runtime.enable');
  await send('Page.enable');
  return {
    send,
    close: () => ws.close(),
    ev: async (expr) => (await send('Runtime.evaluate', { expression: expr, returnByValue: true, awaitPromise: true })).result?.value,
    async evJson(expr) {
      const v = await this.ev(expr);
      try { return JSON.parse(v); } catch { return v; }
    },
  };
}

const click = async (ctx, x, y) => {
  for (const type of ['mousePressed', 'mouseReleased']) {
    await ctx.send('Input.dispatchMouseEvent', { type, x, y, button: 'left', clickCount: 1 });
  }
  await sleep(120);
};

const bodyText = (ctx) =>
  ctx.ev(`(() => {
    // 侧边栏底部也含「沪ICP备」，不能按它截断；直接读对话滚动容器
    const el = document.querySelector('.scroll-container') || document.body;
    return el.innerText || '';
  })()`);

const noteNumbers = (txt) => [...String(txt).matchAll(/笔记\s*(\d+)\s*[：:]/g)].map((m) => +m[1]);

async function findTextPoint(ctx, text, opts = {}) {
  return ctx.evJson(`(() => {
    const want = ${JSON.stringify(text)};
    const minY = ${opts.minY || 0}, maxY = ${opts.maxY || 99999};
    let best = null;
    document.querySelectorAll('*').forEach((el) => {
      const t = (el.textContent || '').trim();
      if (t === want || (${opts.exact === false} && t.includes(want) && t.length < want.length + 8)) {
        const r = el.getBoundingClientRect();
        if (r.width > 0 && r.height > 0 && r.y > minY && r.y < maxY) {
          best = { x: r.x + r.width / 2, y: r.y + r.height / 2, tag: el.tagName };
        }
      }
    });
    return best;
  })()`);
}

/** 阶段1：点 + 号 → 我的收藏 */
async function openFavorites(ctx) {
  await ctx.ev(`(() => { const b = document.querySelector('.ai-input-action-btn'); if (b) b.scrollIntoView({block:'center'}); return !!b; })()`);
  const plus = await ctx.evJson(`(() => {
    const b = document.querySelector('.ai-input-action-btn');
    if (!b) return null;
    const r = b.getBoundingClientRect();
    return { x: r.x + r.width / 2, y: r.y + r.height / 2 };
  })()`);
  if (!plus) throw new Error('未找到输入框 + 按钮');
  await click(ctx, plus.x, plus.y);
  await sleep(1500);

  const fav = await findTextPoint(ctx, '我的收藏');
  if (!fav) throw new Error('未找到「我的收藏」菜单项');
  await click(ctx, fav.x, fav.y);
  await sleep(2500);
  log('已打开收藏面板');
}

/** 阶段2：切到「专辑」→ 选中目标专辑 → 全选 */
async function selectAlbum(ctx) {
  const tab = await ctx.evJson(`(() => {
    let hit = null;
    document.querySelectorAll('.xhs-ai-note-modal__sub-tab, [class*="sub-tab"]').forEach((el) => {
      if ((el.textContent || '').includes('专辑')) {
        const r = el.getBoundingClientRect();
        if (r.width > 0) hit = { x: r.x + r.width / 2, y: r.y + r.height / 2 };
      }
    });
    return hit;
  })()`);
  if (!tab) throw new Error('未找到「专辑」标签');
  await click(ctx, tab.x, tab.y);
  await sleep(2000);

  // 若目标专辑未展开，点它进入
  const album = await findTextPoint(ctx, ALBUM, { exact: false });
  if (album) {
    await click(ctx, album.x, album.y);
    await sleep(2000);
    log(`已进入专辑「${ALBUM}」`);
  }

  // 点全选（同一面板可能有多个「全选」，取离专辑行最近的）
  const btns = await ctx.evJson(`(() => {
    const out = [];
    document.querySelectorAll('*').forEach((el) => {
      if (el.children.length <= 1 && (el.textContent || '').trim() === '全选') {
        const r = el.getBoundingClientRect();
        if (r.width > 0) out.push({ x: r.x + r.width / 2, y: r.y + r.height / 2 });
      }
    });
    return out;
  })()`);
  if (!btns || !btns.length) throw new Error('未找到「全选」按钮');
  await click(ctx, btns[0].x, btns[0].y);
  await sleep(1500);

  const st = await ctx.evJson(`(() => {
    let txt = '';
    document.querySelectorAll('*').forEach((el) => {
      const t = (el.textContent || '').trim();
      if (/已选中\s*\d+\s*\/\s*\d+/.test(t) && t.length < 20) txt = t;
    });
    return txt;
  })()`);
  log('选中状态：', st);
  const m = String(st).match(/(\d+)\s*\/\s*(\d+)/);
  const n = m ? +m[1] : 0;
  if (!n) throw new Error('全选失败，未读到选中数量');
  return n;
}

async function clickImport(ctx) {
  const btn = await ctx.evJson(`(() => {
    let hit = null;
    document.querySelectorAll('button').forEach((el) => {
      if ((el.textContent || '').trim() === '导入') {
        const r = el.getBoundingClientRect();
        if (r.width > 0) hit = { x: r.x + r.width / 2, y: r.y + r.height / 2 };
      }
    });
    return hit;
  })()`);
  if (!btn) throw new Error('未找到「导入」按钮');
  await click(ctx, btn.x, btn.y);
  await sleep(2500);
  log('已点击导入');
}

async function typeAndSend(ctx, text) {
  const ta = await ctx.evJson(`(() => {
    const els = Array.from(document.querySelectorAll('textarea')).filter((t) => t.getBoundingClientRect().width > 300);
    const el = els[els.length - 1];
    if (!el) return null;
    el.focus();
    const r = el.getBoundingClientRect();
    return { x: r.x + r.width / 2, y: r.y + r.height / 2 };
  })()`);
  if (!ta) throw new Error('未找到输入框');
  await click(ctx, ta.x, ta.y);
  await ctx.send('Input.insertText', { text });
  await sleep(700);

  const ok = await ctx.evJson(`(() => {
    const els = Array.from(document.querySelectorAll('textarea')).filter((t) => t.getBoundingClientRect().width > 300);
    const el = els[els.length - 1];
    return el ? el.value.length : 0;
  })()`);
  if (!ok || ok < 5) {
    await ctx.ev(`(() => {
      const els = Array.from(document.querySelectorAll('textarea')).filter((t) => t.getBoundingClientRect().width > 300);
      const el = els[els.length - 1];
      const setter = Object.getOwnPropertyDescriptor(window.HTMLTextAreaElement.prototype, 'value').set;
      setter.call(el, ${JSON.stringify(text)});
      el.dispatchEvent(new Event('input', { bubbles: true }));
      return true;
    })()`);
    await sleep(400);
  }

  const submit = await ctx.evJson(`(() => {
    // 2026-11 更新：点点 UI 改版，发送按钮 class 从 .bottom-box-right-submit-button 变为 .wendian-btn--sendable
    const el = document.querySelector('.wendian-btn--sendable') || document.querySelector('.wendian-btn') || document.querySelector('.bottom-box-right-submit-button') || document.querySelector('.submit-button');
    if (!el) return null;
    const r = el.getBoundingClientRect();
    if (r.width < 5) return null;
    return { x: r.x + r.width / 2, y: r.y + r.height / 2 };
  })()`);
  if (!submit) throw new Error('未找到发送按钮（.wendian-btn--sendable）');
  await click(ctx, submit.x, submit.y);
  log('已发送，等待回复…');
}

/** 等待回复稳定；返回本轮新增文本 */
async function waitReply(ctx, prevLen, minWait = 6000, maxWait = 300000) {
  const t0 = Date.now();
  let last = prevLen;
  let stable = 0;
  let best = '';
  while (Date.now() - t0 < maxWait) {
    await sleep(7000);
    const txt = await bodyText(ctx);
    const len = (txt || '').length;
    if (len > last) { stable = 0; last = len; } else if (len > prevLen) { stable++; }
    if (len > prevLen) best = txt;
    const secs = Math.round((Date.now() - t0) / 1000);
    if (len > prevLen) log(`  [${secs}s] 正文长度 ${len}（新增 ${len - prevLen}）`);
    if (stable >= 3) break;
  }
  return best || (await bodyText(ctx)) || '';
}

function buildPrompt(n, start, end) {
  return [
    `请把我已选中的这 ${n} 篇笔记整理成结构化文档。不要合并，不要点评，不要略写，不要写开场白和结束语。`,
    ``,
    `严格按下面模板输出，字段名一个字都不要改，每篇之间空一行：`,
    ``,
    `笔记 {序号}：{标题}`,
    `笔记 ID：{24位笔记ID，拿不到就写 unknown}`,
    `类型：视频 或 图文`,
    `正文：{笔记正文原文，完整保留}`,
    `视频要点：{视频讲了什么，分条列出；不是视频就写"无"}`,
    `语音文案：{完整口播逐字稿；不是视频就写"无"}`,
    `OCR 识别内容：{画面与图片里的文字；没有就写"无"}`,
    `知识标签：{#标签 #标签}`,
    ``,
    `【重要】单条回复有长度上限，本次只输出笔记 ${start} 到笔记 ${end}，写到第 ${end} 篇结束就停，不要继续写后面的。`,
    `后面我会用"继续"指令让你接着写，到时严格用同样模板、接着上次的序号往下写，不要重复已写过的笔记。`,
  ].join('\n');
}

async function main() {
  const ctx = await connect();
  const state = await ctx.evJson(`(() => ({
    url: location.href,
    hasInput: !!Array.from(document.querySelectorAll('textarea')).find(t => t.getBoundingClientRect().width > 300),
    attachments: Array.from(document.querySelectorAll('[class*="attach"] img, [class*="file"] img')).length
  }))()`);
  log('页面状态：', JSON.stringify(state));

  let picked = 0;
  const needSelect = !args['skip-select'];
  if (needSelect) {
    await openFavorites(ctx);
    picked = await selectAlbum(ctx);
    if (picked < TOTAL) log(`警告：只选中 ${picked} 篇，少于预期 ${TOTAL} 篇`);
    await clickImport(ctx);
  }

  const total = TOTAL;
  const segs = [];
  let prevLen = (await bodyText(ctx)).length;

  // 断点续采：先等在途回复稳定，再读已产出的笔记序号
  let startFrom = 1;
  if (args.resume) {
    let last = -1, stable = 0;
    for (let i = 0; i < 40; i++) {
      await sleep(7000);
      const len = ((await bodyText(ctx)) || '').length;
      if (len > 200 && len === last) stable++; else stable = 0;
      last = len;
      if (stable >= 3) break;
    }
    log('在途回复已稳定，长度', last);
  }
  const existing = [...new Set(noteNumbers(await bodyText(ctx)))].sort((a, b) => a - b);
  if (existing.length && args.resume) {
    // 找出 1..existing.max 中缺失的最小序号
    const maxSeen = Math.max(...existing);
    for (let i = 1; i <= maxSeen; i++) {
      if (!existing.includes(i)) { startFrom = i; break; }
    }
    if (startFrom === 1) startFrom = maxSeen + 1;
    log(`续采：会话中已有笔记 ${existing.join(',')}，从第 ${startFrom} 篇继续`);
  }

  for (let start = startFrom; start <= total; start += BATCH) {
    const end = Math.min(start + BATCH - 1, total);
    const prompt =
      start === 1
        ? buildPrompt(total, start, end)
        : `继续输出笔记 ${start} 到笔记 ${end}，严格使用同样的模板，不要重复前面已经输出的笔记。`;
    log(`--- 第 ${start}-${end} 篇 ---`);
    await typeAndSend(ctx, prompt);
    const txt = await waitReply(ctx, prevLen);
    const got = noteNumbers(txt);
    log(`本轮已见笔记序号：${got.join(',')}`);
    segs.push({ start, end, text: txt });
    prevLen = txt.length;
    fs.writeFileSync(OUT, txt, 'utf8');
    const maxSeen = got.length ? Math.max(...got) : 0;
    if (maxSeen >= total) { log('已覆盖全部笔记，提前结束'); break; }
    await sleep(1500);
  }

  // 末次全文落盘（累积文本即完整对话正文）
  const final = await bodyText(ctx);
  fs.writeFileSync(OUT, final, 'utf8');
  const nums = [...new Set(noteNumbers(final))].sort((a, b) => a - b);
  log(`完成：落盘 ${final.length} 字，笔记序号 ${nums.join(',')}`);
  if (nums.length < total) log(`注意：缺 ${total - nums.length} 篇，需要补采`);
  ctx.close();
}

main().catch((e) => { console.error('FAIL', e.message); process.exit(1); });
