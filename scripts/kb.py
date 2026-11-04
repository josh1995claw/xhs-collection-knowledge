#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
kb —— 人工检索知识库的快捷入口（与 ai_context.py 区分）

  kb 关键词              检索，输出精简列表（序号/标题/分类/命中片段/卡片路径）
  kb open <序号|关键词>   打开该笔记的知识卡（系统默认 md 编辑器）
  kb raw  <序号|关键词>   打开原始归档全文
  kb url  <序号|关键词>   浏览器打开小红书原笔记
  kb cat  <序号|关键词>   终端打印知识卡全文
  kb ls [--cat 代码]     列出笔记（可按分类过滤）
  kb cats                列出全部分类及篇数
  kb stats               库统计
  kb rebuild             重建索引与分类页
  kb check [--fix]       全库体检（索引/分类/资源/备份一致性）
  kb score [--detail]    质量评分（采集/归档/总结/保留度四维 + 历史记录）
  kb res [sync|check|list]  已安装 skills ↔ resources/ 镜像（可选组件）
  kb backup              生成 IMA 云端备份包，并显示待上传文件（可选组件）
  kb help                本帮助

无参数时进入交互模式：提示输入关键词，检索后可输入序号打开卡片。

示例：
  kb 质感
  kb open 3
  kb ls --cat 09
"""
import json
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
INDEX = ROOT / "index"
CARDS = ROOT / "cards"
RAW = ROOT / "raw"
BM = INDEX / "bm25.json"
CHUNKS = INDEX / "chunks.jsonl"
NOTES = INDEX / "notes.json"
LAST = INDEX / ".kb_last.json"  # 上一次检索结果的 note_id 顺序，供 kb open <序号> 使用

C = {
    "reset": "\033[0m", "bold": "\033[1m", "dim": "\033[2m",
    "cyan": "\033[36m", "yellow": "\033[33m", "green": "\033[32m", "red": "\033[31m",
}


def c(text, *styles):
    if not sys.stdout.isatty():
        return text
    return "".join(C[s] for s in styles) + text + C["reset"]


# ---------- 数据加载 ----------

def tokens(text):
    text = text.lower()
    out = [m.group(0) for m in re.finditer(r"[a-z0-9][a-z0-9._/-]*", text)]
    for seg in re.findall(r"[\u4e00-\u9fff]+", text):
        out.extend(seg[i:i + 2] for i in range(len(seg) - 1)) if len(seg) > 1 else out.append(seg)
    return out


def load():
    bm = json.loads(BM.read_text(encoding="utf-8"))
    chunks = [json.loads(l) for l in CHUNKS.read_text(encoding="utf-8").splitlines() if l.strip()]
    notes = json.loads(NOTES.read_text(encoding="utf-8")).get("notes", {}) if NOTES.exists() else {}
    return bm, chunks, notes


def search_notes(bm, chunks, query, top_n=10):
    """块级检索，再归并到笔记级（一篇只出现一次，取最佳块）。"""
    qt = tokens(query)
    k1, avgdl = bm["k1"], bm["avgdl"]
    idf, postings = bm["idf"], bm["postings"]
    scores = {}
    for t in qt:
        for i, f in postings.get(t, []):
            s = idf[t] * f * (k1 + 1) / (f + k1)
            scores[i] = scores.get(i, 0) + s
    best = {}
    for i, s in sorted(scores.items(), key=lambda x: -x[1]):
        nid = chunks[i]["note_id"]
        if nid not in best:
            best[nid] = (s, i)
    return sorted(best.items(), key=lambda x: -x[1][0])[:top_n]


def snippet(text, query, width=90):
    """取命中关键词周边的一段文字作摘要。"""
    flat = re.sub(r"\s+", " ", text).strip()
    qs = [q for q in re.split(r"\s+", query) if len(q) > 1]
    pos = -1
    for q in qs:
        pos = flat.lower().find(q.lower())
        if pos >= 0:
            break
    if pos < 0:
        return flat[:width] + ("…" if len(flat) > width else "")
    start = max(0, pos - 30)
    seg = flat[start:start + width]
    seg = re.sub(r"(%s)" % "|".join(re.escape(q) for q in qs if q),
                 lambda m: c(m.group(1), "yellow"), seg, flags=re.I)
    return ("…" if start > 0 else "") + seg + ("…" if start + width < len(flat) else "")


def card_path(nid):
    hits = list(CARDS.rglob(f"{nid[:8]}*.md"))
    return hits[0] if hits else None


def raw_path(nid):
    hits = list(RAW.glob(f"{nid}*.md"))
    return hits[0] if hits else None


# ---------- 命令 ----------

def save_last(nids):
    LAST.write_text(json.dumps(nids, ensure_ascii=False), encoding="utf-8")


def load_last():
    if LAST.exists():
        try:
            return json.loads(LAST.read_text(encoding="utf-8"))
        except Exception:
            return []
    return []


def cmd_search(bm, chunks, notes, query, top_n=10):
    hits = search_notes(bm, chunks, query, top_n)
    if not hits:
        print(c("无命中。", "red"), "试试更短的词，或用", c("kb ls", "cyan"), "浏览全库。")
        return []
    print(c(f"\n命中 {len(hits)} 篇：{query}\n", "bold"))
    listing = []
    for rank, (nid, (score, ci)) in enumerate(hits, 1):
        meta = notes.get(nid, {})
        title = chunks[ci]["title"] or meta.get("title", "")
        catname = meta.get("category_name", meta.get("category", "-"))
        cp = card_path(nid)
        listing.append((rank, nid, title, cp))
        print(c(f"[{rank}] ", "cyan") + c(title[:46], "bold"))
        print(f"     {c(catname, 'green')} · 相关度 {score:.1f} · {nid[:8]}")
        print(f"     {snippet(chunks[ci]['text'], query)}")
        print(f"     卡片: {cp.relative_to(ROOT).as_posix() if cp else c('（未生成）', 'dim')}")
        print()
    print(c("下一步：", "dim") + c("kb open <序号>", "cyan") + c(" 打开卡片 · ", "dim")
          + c("kb raw <序号>", "cyan") + c(" 看原文 · ", "dim")
          + c("kb url <序号>", "cyan") + c(" 开原笔记", "dim"))
    save_last([nid for nid, _ in hits])
    return listing


def cmd_ls(notes, cat=None):
    items = [(nid, e) for nid, e in notes.items() if not cat or e.get("category") == cat]
    items.sort(key=lambda x: x[1].get("seq", 0) if isinstance(x[1].get("seq"), int) else 0)
    print(c(f"\n共 {len(items)} 篇" + (f"（分类 {cat}）" if cat else "") + "\n", "bold"))
    for i, (nid, e) in enumerate(items, 1):
        cp = card_path(nid)
        print(c(f"[{i}] ", "cyan") + f"{e.get('title','')[:46]}")
        print(f"     {c(e.get('category_name', e.get('category','-')), 'green')}"
              f" · 正文 {e.get('body_len',0)} 字 · ASR {e.get('asr_len',0)} 字 · OCR {e.get('ocr_len',0)} 字")
        print(f"     {cp.relative_to(ROOT).as_posix() if cp else c('（未生成卡片）','dim')}")
    save_last([nid for nid, _ in items])
    return items


def cmd_cats(notes):
    by = {}
    for nid, e in notes.items():
        by.setdefault(e.get("category", "99"), []).append(e)
    print(c("\n分类一览\n", "bold"))
    for code in sorted(by):
        name = by[code][0].get("category_name", code)
        print(f"  {c(code, 'cyan')}  {name:<20} {len(by[code])} 篇")
    print()
    print(c("用法：kb ls --cat <分类码>，例如 kb ls --cat 06", "dim"))


def cmd_stats(bm, chunks, notes):
    body = sum(e.get("body_len", 0) for e in notes.values())
    asr = sum(e.get("asr_len", 0) for e in notes.values())
    ocr = sum(e.get("ocr_len", 0) for e in notes.values())
    carded = sum(1 for nid in notes if card_path(nid))
    print(c("\n知识库统计\n", "bold"))
    print(f"  笔记       {len(notes)} 篇")
    print(f"  文本块     {len(chunks)} 个")
    print(f"  词项       {len(bm['idf'])} 个")
    print(f"  正文       {body} 字")
    print(f"  语音逐字稿 {asr} 字")
    print(f"  画面 OCR   {ocr} 字")
    print(f"  合计       {body + asr + ocr} 字")
    print(f"  已生成卡片 {carded}/{len(notes)}")


def resolve(items, key):
    """把序号/关键词/note_id 前缀解析成 note_id。"""
    key = key.strip()
    if key.isdigit():
        idx = int(key)
        if 1 <= idx <= len(items):
            return items[idx - 1][0] if isinstance(items[idx - 1], tuple) else items[idx - 1]
    for nid in (n[0] if isinstance(n, tuple) else n for n in items):
        if nid.startswith(key):
            return nid
    return None


def open_file(path):
    subprocess.run(["open", str(path)], check=False)


def main():
    args = sys.argv[1:]
    if not args:
        args = ["--interactive"]
    cmd = args[0]

    if cmd in ("help", "-h", "--help"):
        print(__doc__)
        return 0
    if cmd == "rebuild":
        for s in ("build_knowledge.py", "build_index.py"):
            subprocess.run([sys.executable, str(ROOT / "scripts" / s)], check=False, cwd=ROOT)
        print(c("\n提示：内容有变，记得 ", "dim") + c("kb backup", "cyan")
              + c(" 生成云端备份包", "dim"))
        return 0
    if cmd == "check":
        return subprocess.run([sys.executable, str(ROOT / "scripts" / "audit_kb.py")] + args[1:],
                              cwd=ROOT).returncode
    if cmd == "score":
        script = ROOT / "scripts" / "score_kb.py"
        if not script.exists():
            print(c("未安装 score_kb.py 评分组件", "red"))
            return 1
        return subprocess.run([sys.executable, str(script)] + args[1:], cwd=ROOT).returncode
    if cmd == "res":
        script = ROOT / "scripts" / "sync_resources.py"
        if not script.exists():
            print(c("未安装可选组件 sync_resources.py（个人库 skill 资源镜像）", "yellow"))
            return 1
        sub = args[1] if len(args) > 1 else "check"
        return subprocess.run([sys.executable, str(script), sub],
                              cwd=ROOT).returncode
    if cmd == "backup":
        script = ROOT / "scripts" / "ima_sync.py"
        if not script.exists():
            print(c("未安装可选组件 ima_sync.py（云端备份）", "yellow"))
            return 1
        for sub in ("build", "status"):
            subprocess.run([sys.executable, str(script), sub], cwd=ROOT)
        return 0

    if not BM.exists() or not CHUNKS.exists():
        print(c("索引不存在，先运行：kb rebuild", "red"))
        return 1

    bm, chunks, notes = load()

    if cmd == "cats":
        cmd_cats(notes)
        return 0
    if cmd == "stats":
        cmd_stats(bm, chunks, notes)
        return 0

    if cmd == "ls":
        cat = None
        if "--cat" in args:
            cat = args[args.index("--cat") + 1]
        cmd_ls(notes, cat)
        return 0

    if cmd in ("open", "raw", "url", "cat"):
        key = " ".join(args[1:]).strip()
        if not key:
            print(c("需要关键词或序号，例如：kb open 3", "red"))
            return 1
        # 纯数字序号：优先用上一次检索/列表的结果；否则按关键词检索出候选
        if key.isdigit():
            last = load_last()
            idx = int(key)
            if 1 <= idx <= len(last):
                nid = last[idx - 1]
            else:
                print(c(f"序号 {idx} 超出上次结果范围（{len(last)} 条），先检索一次再 open，或直接给关键词", "red"))
                return 1
        else:
            items = [(nid, (0, 0)) for nid, _ in search_notes(bm, chunks, key, 20)]
            nid = resolve(items, key)
            if not nid:
                print(c(f"未找到：{key}", "red"))
                return 1
        if cmd == "url":
            subprocess.run(["open", f"https://www.xiaohongshu.com/explore/{nid}"], check=False)
            print(c("已打开原笔记：", "dim") + nid)
            return 0
        p = card_path(nid) if cmd in ("open", "cat") else raw_path(nid)
        if not p:
            print(c("该文件不存在", "red"))
            return 1
        if cmd == "cat":
            print(p.read_text(encoding="utf-8"))
        else:
            open_file(p)
            print(c("已打开：", "dim") + p.relative_to(ROOT).as_posix())
        return 0

    if cmd == "--interactive":
        try:
            q = input(c("检索关键词（直接回车看全库）> ", "cyan")).strip()
        except (EOFError, KeyboardInterrupt):
            print()
            return 0
        if not q:
            cmd_ls(notes)
            return 0
        listing = cmd_search(bm, chunks, notes, q)
        if not listing:
            return 0
        try:
            n = input(c("打开第几篇？（回车退出）> ", "cyan")).strip()
        except (EOFError, KeyboardInterrupt):
            print()
            return 0
        if n.isdigit() and 1 <= int(n) <= len(listing):
            p = card_path(listing[int(n) - 1][1])
            if p:
                open_file(p)
                print(c("已打开：", "dim") + p.relative_to(ROOT).as_posix())
        return 0

    # 默认：关键词检索
    cmd_search(bm, chunks, notes, " ".join(args))
    return 0


if __name__ == "__main__":
    sys.exit(main())
