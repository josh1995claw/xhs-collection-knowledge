#!/usr/bin/env python3
"""
构建面向 AI 的检索索引（零依赖，纯 Python BM25）。
- 读 index/notes.json + raw/*.md
- 分块：每篇按二级段落（字段）切块
- 产出：index/chunks.jsonl（分块语料）、index/bm25.json（倒排索引）
用法：python3 scripts/build_index.py
"""
import json
import math
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
INDEX = ROOT / "index"


def tokens(text: str):
    """中文按二元组切，英文/数字按词切。无第三方依赖。"""
    text = text.lower()
    out = []
    for m in re.finditer(r"[a-z0-9][a-z0-9._/-]*", text):
        out.append(m.group(0))
    han = re.findall(r"[\u4e00-\u9fff]+", text)
    for seg in han:
        if len(seg) == 1:
            out.append(seg)
        else:
            out.extend(seg[i:i + 2] for i in range(len(seg) - 1))
    return out


def load_notes():
    p = INDEX / "notes.json"
    if not p.exists():
        return {"notes": {}}
    return json.loads(p.read_text(encoding="utf-8"))


def iter_raw():
    for f in sorted((ROOT / "raw").glob("*.md")):
        text = f.read_text(encoding="utf-8")
        nid_m = re.search(r"^note_id:\s*(\S+)", text, re.M)
        title_m = re.search(r"^#\s+(.+)$", text, re.M)
        yield {
            "file": f.name,
            "note_id": nid_m.group(1) if nid_m else f.stem.split("-")[0],
            "title": title_m.group(1).strip() if title_m else f.stem,
            "text": text,
        }


def chunk_note(note):
    """按 markdown 二级标题/字段行切块。"""
    parts = re.split(r"\n(?=#{1,3}\s|\*\*|笔记\s*\d)", note["text"])
    chunks = []
    for i, part in enumerate(parts):
        part = part.strip()
        if len(part) < 30:
            continue
        chunks.append({"chunk_id": f"{note['note_id'][:12]}-{i}", "note_id": note["note_id"], "title": note["title"], "file": note["file"], "text": part[:2000]})
    if not chunks:
        chunks.append({"chunk_id": f"{note['note_id'][:12]}-0", "note_id": note["note_id"], "title": note["title"], "file": note["file"], "text": note["text"][:2000]})
    return chunks


def build_bm25(chunks, k1=1.5, b=0.75):
    docs = [tokens(c["text"]) for c in chunks]
    n_docs = len(docs)
    avgdl = sum(len(d) for d in docs) / max(n_docs, 1)
    df = {}
    tf_list = []
    for d in docs:
        tf = {}
        for t in d:
            tf[t] = tf.get(t, 0) + 1
        tf_list.append(tf)
        for t in tf:
            df[t] = df.get(t, 0) + 1
    idf = {t: math.log(1 + (n_docs - c + 0.5) / (c + 0.5)) for t, c in df.items()}
    return {"k1": k1, "b": b, "avgdl": avgdl, "n_docs": n_docs, "idf": idf, "tfs": tf_list}


def main():
    notes = load_notes()
    chunks = []
    for note in iter_raw():
        chunks.extend(chunk_note(note))
    if not chunks:
        print("无内容可索引")
        return 1
    (INDEX / "chunks.jsonl").write_text("\n".join(json.dumps(c, ensure_ascii=False) for c in chunks), encoding="utf-8")
    bm = build_bm25(chunks)
    # 压缩 tfs 为 postings
    postings = {}
    for i, tf in enumerate(bm["tfs"]):
        for t, f in tf.items():
            postings.setdefault(t, []).append([i, f])
    out = {"k1": bm["k1"], "b": bm["b"], "avgdl": bm["avgdl"], "n_docs": bm["n_docs"], "idf": bm["idf"], "postings": postings, "chunks": [{"chunk_id": c["chunk_id"], "note_id": c["note_id"], "title": c["title"], "file": c["file"]} for c in chunks]}
    (INDEX / "bm25.json").write_text(json.dumps(out, ensure_ascii=False), encoding="utf-8")
    print(f"索引完成：{bm['n_docs']} 块，{len(postings)} 个词项")
    return 0


if __name__ == "__main__":
    sys.exit(main())
