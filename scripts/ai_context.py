#!/usr/bin/env python3
"""
面向 AI 的上下文打包器（零依赖，BM25 检索，不含向量）。

给 AI 用的三种姿势：
  python3 scripts/ai_context.py manifest            # 全库清单，让 AI 知道有什么
  python3 scripts/ai_context.py "查询词" -n 5        # 检索相关块，输出可直接喂 AI
  python3 scripts/ai_context.py --all               # 全量导出（小库适用）
  python3 scripts/ai_context.py "查询词" --json      # 结构化输出，供程序消费

输出默认写入 stdout，可直接粘贴给任意 AI。
"""
import json
import math
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
INDEX = ROOT / "index"
BM = INDEX / "bm25.json"
CHUNKS = INDEX / "chunks.jsonl"
NOTES = INDEX / "notes.json"

CAT_NAME = {}


def tokens(text):
    text = text.lower()
    out = [m.group(0) for m in re.finditer(r"[a-z0-9][a-z0-9._/-]*", text)]
    for seg in re.findall(r"[\u4e00-\u9fff]+", text):
        out.extend(seg[i:i + 2] for i in range(len(seg) - 1)) if len(seg) > 1 else out.append(seg)
    return out


def load():
    bm = json.loads(BM.read_text(encoding="utf-8"))
    chunks = [json.loads(l) for l in CHUNKS.read_text(encoding="utf-8").splitlines() if l.strip()]
    notes = json.loads(NOTES.read_text(encoding="utf-8")) if NOTES.exists() else {"notes": {}}
    return bm, chunks, notes.get("notes", {})


def search(bm, query, top_n):
    qt = tokens(query)
    k1, b, avgdl = bm["k1"], bm["b"], bm["avgdl"]
    idf, postings = bm["idf"], bm["postings"]
    scores = {}
    for t in qt:
        for i, f in postings.get(t, []):
            s = idf[t] * f * (k1 + 1) / (f + k1 * (1 - b + b * avgdl / avgdl))
            scores[i] = scores.get(i, 0) + s
    return sorted(scores.items(), key=lambda x: -x[1])[:top_n]


def note_url(nid):
    return f"https://www.xiaohongshu.com/explore/{nid}" if nid else ""


def cmd_manifest(chunks, notes):
    """全库清单：让 AI 一眼看清库里有什么，再决定查什么。"""
    by_cat = {}
    for nid, e in notes.items():
        by_cat.setdefault(e.get("category", "99"), []).append((nid, e))
    out = ["# 知识库清单（机读入口）", "",
           f"> 共 {len(notes)} 篇笔记 / {len(chunks)} 个文本块 · 来源：小红书收藏夹",
           "> 用法：`python3 scripts/ai_context.py \"查询词\"` 取相关块原文",
           ""]
    for code in sorted(by_cat):
        name = next((e.get("category_name", code) for _, e in by_cat[code]), code)
        out.append(f"## {code} {name}（{len(by_cat[code])}）")
        for nid, e in sorted(by_cat[code], key=lambda x: x[1].get("seq", 0) if isinstance(x[1].get("seq"), int) else 0):
            out.append(f"- {e.get('title','')} | note_id `{nid}` | 正文 {e.get('body_len',0)} 字 · ASR {e.get('asr_len',0)} 字 · OCR {e.get('ocr_len',0)} 字")
        out.append("")
    out.append("## 字段说明")
    out.append("")
    out.append("- `body` 笔记正文；`asr` 视频语音逐字稿；`ocr` 画面文字识别；`references` 参考信息；`comments` 评论区补充")
    out.append("- 每篇完整归档在 `raw/<note_id>-<标题>.md`，人读摘要在 `cards/`")
    return "\n".join(out)


def cmd_search(bm, chunks, notes, query, top_n, as_json=False):
    hits = search(bm, query, top_n)
    if not hits:
        return json.dumps({"query": query, "hits": []}, ensure_ascii=False) if as_json else "无命中结果"
    if as_json:
        return json.dumps({"query": query, "hits": [
            {"score": round(s, 2), "note_id": chunks[i]["note_id"], "title": chunks[i]["title"],
             "file": chunks[i]["file"], "text": chunks[i]["text"]} for i, s in hits]}, ensure_ascii=False, indent=2)
    out = [f"# 检索上下文：{query}", "", f"> 命中 {len(hits)} 块 · 来自小红书收藏夹知识库", ""]
    for rank, (i, s) in enumerate(hits, 1):
        c = chunks[i]
        meta = notes.get(c["note_id"], {})
        out.append(f"## [{rank}] {c['title']}  (相关度 {s:.2f})")
        out.append("")
        out.append(f"- 来源：{note_url(c['note_id'])}")
        out.append(f"- 分类：{meta.get('category_name', meta.get('category','-'))} · 归档：`raw/{c['file']}`")
        out.append(f"- 说明：本块取自该笔记的某个段落，全文见归档文件")
        out.append("")
        out.append(c["text"].strip())
        out.append("")
    return "\n".join(out)


def cmd_all(chunks, notes):
    out = ["# 知识库全量导出（机读）", "", f"> {len(notes)} 篇 / {len(chunks)} 块", ""]
    for nid, e in notes.items():
        out.append(f"## {e.get('title','')}")
        out.append("")
        out.append(f"- note_id：`{nid}` · 分类：{e.get('category_name','-')}")
        out.append(f"- 链接：{note_url(nid)}")
        out.append("")
    out.append("---")
    out.append("")
    for c in chunks:
        out.append(f"### {c['title']} · 块 {c['chunk_id']}")
        out.append("")
        out.append(c["text"].strip())
        out.append("")
    return "\n".join(out)


def main():
    if not BM.exists() or not CHUNKS.exists():
        print("索引不存在，先运行：python3 scripts/build_index.py")
        return 1
    args = [a for a in sys.argv[1:]]
    if not args:
        print(__doc__)
        return 1
    as_json = "--json" in args
    args = [a for a in args if not a.startswith("--") or a == "--all"]
    bm, chunks, notes = load()

    if args and args[0] == "manifest":
        print(cmd_manifest(chunks, notes))
        return 0
    if "--all" in sys.argv:
        print(cmd_all(chunks, notes))
        return 0

    top_n = 5
    if "-n" in sys.argv:
        try:
            top_n = int(sys.argv[sys.argv.index("-n") + 1])
        except (ValueError, IndexError):
            pass
    q = " ".join(a for a in args if a != "-n" and not a.isdigit())
    if not q:
        print(__doc__)
        return 1
    print(cmd_search(bm, chunks, notes, q, top_n, as_json))
    return 0


if __name__ == "__main__":
    sys.exit(main())
