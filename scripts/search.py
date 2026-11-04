#!/usr/bin/env python3
"""
知识库检索 CLI（BM25，零依赖）。
用法：
  python3 scripts/search.py 实景三维 数据治理   # 关键词检索
  python3 scripts/search.py "agent 架构" -n 5   # 指定条数
"""
import json
import math
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
INDEX = ROOT / "index" / "bm25.json"


def tokens(text):
    text = text.lower()
    out = [m.group(0) for m in re.finditer(r"[a-z0-9][a-z0-9._/-]*", text)]
    for seg in re.findall(r"[\u4e00-\u9fff]+", text):
        out.extend(seg[i:i + 2] for i in range(len(seg) - 1)) if len(seg) > 1 else out.append(seg)
    return out


def main():
    if not INDEX.exists():
        print("索引不存在，先运行 build_index.py")
        return 1
    bm = json.loads(INDEX.read_text(encoding="utf-8"))
    args = [a for a in sys.argv[1:] if not a.startswith("-")]
    top_n = 5
    if "-n" in sys.argv:
        i = sys.argv.index("-n")
        top_n = int(sys.argv[i + 1])
    if not args:
        print(__doc__)
        return 1
    q = tokens(" ".join(args))
    k1, b, avgdl = bm["k1"], bm["b"], bm["avgdl"]
    idf, postings, metas = bm["idf"], bm["postings"], bm["chunks"]
    scores = {}
    for t in q:
        for i, f in postings.get(t, []):
            dl = avgdl  # 近似：块长未知时用平均
            s = idf[t] * f * (k1 + 1) / (f + k1 * (1 - b + b * dl / avgdl))
            scores[i] = scores.get(i, 0) + s
    ranked = sorted(scores.items(), key=lambda x: -x[1])[:top_n]
    if not ranked:
        print("无结果")
        return 0
    for i, s in ranked:
        m = metas[i]
        print(f"[{s:6.2f}] {m['title'][:40]}  ({m['file']})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
