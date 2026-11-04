#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
score_kb.py —— 知识库质量评分：从"一致性体检"（audit_kb.py）更进一步，评"整理效果好不好"。

四个维度，每篇笔记各打 0-1 分，库级取平均后给出总评等级：

  1. 采集完整性  正文/ASR/OCR 是否按笔记类型采到位（视频必须有逐字稿，缺了会显著扣分）
  2. 归档完整性  索引、卡片、分类三者是否齐全且一致
  3. 总结可读性  知识卡结构是否完整（一句话核心/核心要点/行动项/溯源），要点数是否在 3-8
  4. 信息保留度  知识卡对原文的覆盖比例（过低=丢信息，过高=不是摘要）

用法：
  python3 scripts/score_kb.py              # 评分 + 追加到 logs/score_history.jsonl
  python3 scripts/score_kb.py --detail     # 显示每篇明细（默认只列最差 5 篇）
  python3 scripts/score_kb.py --no-log     # 只看结果，不写历史

等级：A ≥ 0.90 · B ≥ 0.75 · C ≥ 0.60 · D < 0.60
判定标准见 references/scoring.md。
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
INDEX = ROOT / "index"
CARDS = ROOT / "cards"
RAW = ROOT / "raw"
LOGS = ROOT / "logs"
NOTES = INDEX / "notes.json"
HISTORY = LOGS / "score_history.jsonl"

DIMS = ("acquisition", "archival", "readability", "retention")
DIM_NAMES = {
    "acquisition": "采集完整性",
    "archival": "归档完整性",
    "readability": "总结可读性",
    "retention": "信息保留度",
}


def load_notes() -> dict:
    if not NOTES.exists():
        print("index/notes.json 不存在，先入库再评分。")
        sys.exit(1)
    return json.loads(NOTES.read_text(encoding="utf-8")).get("notes", {})


def strip_frontmatter(text: str) -> str:
    m = re.match(r"^---\n.*?\n---\n?", text, re.S)
    return text[m.end():] if m else text


def score_acquisition(e: dict) -> float:
    """正文/ASR/OCR 覆盖度。视频笔记的 ASR 权重最高（通常占信息量的大头）。"""
    body = e.get("body_len", 0)
    asr = e.get("asr_len", 0)
    ocr = e.get("ocr_len", 0)
    has_body = 1.0 if body > 0 else 0.0
    if e.get("note_type", "video") == "video":
        return round(0.4 * has_body + 0.5 * min(asr / 200, 1.0) + 0.1 * min(ocr / 50, 1.0), 3)
    return round(0.8 * has_body + 0.2 * min(ocr / 50, 1.0), 3)


def score_archival(e: dict, card: Path | None) -> float:
    """索引在（必在，遍历源）+ 卡片存在 + 分类三方一致。"""
    if not card:
        return 0.0
    s = 0.5
    text = card.read_text(encoding="utf-8")
    m = re.search(r"^category:\s*(.+)$", text, re.M)
    fm = m.group(1).strip() if m else ""
    cat_name = card.parent.name.split("-", 1)[-1]
    if fm == card.parent.name and e.get("category_name") == cat_name:
        s += 0.5
    elif fm == card.parent.name or e.get("category_name") == cat_name:
        s += 0.25
    return s


def score_readability(card: Path | None) -> float:
    """卡片结构完整度：一句话核心 / 核心要点(3-8条) / 行动项 / 溯源链接。"""
    if not card:
        return 0.0
    body = strip_frontmatter(card.read_text(encoding="utf-8"))
    s = 0.0
    m = re.search(r"##\s*一句话核心\s*\n+\s*(.+)", body)
    if m and len(m.group(1).strip()) >= 15:
        s += 0.25
    elif m:
        s += 0.10
    points = re.findall(r"^\d+\.\s", body, re.M)
    if 3 <= len(points) <= 8:
        s += 0.35
    elif points:
        s += 0.20
    if re.search(r"##\s*行动项", body) and re.search(r"^[-*]\s", body, re.M):
        s += 0.20
    if re.search(r"##\s*原文与溯源", body) and re.search(r"xiaohongshu\.com|http", body):
        s += 0.20
    return round(min(s, 1.0), 3)


def score_retention(e: dict, card: Path | None) -> float:
    """知识卡对原文的信息覆盖比。过低=丢信息，过高=复述而非提炼。"""
    if not card:
        return 0.0
    raw_total = e.get("body_len", 0) + e.get("asr_len", 0) + e.get("ocr_len", 0)
    if raw_total < 150:  # 短笔记没什么可丢
        return 1.0
    card_len = len(strip_frontmatter(card.read_text(encoding="utf-8")))
    ratio = card_len / raw_total
    if 0.03 <= ratio <= 0.6:
        return 1.0
    if ratio < 0.03:
        return round(ratio / 0.03 * 0.7, 3)
    return 0.85  # 偏长但仍算合格


def find_card(nid: str) -> Path | None:
    hits = list(CARDS.rglob(f"{nid[:8]}*.md"))
    return hits[0] if hits else None


def grade(x: float) -> str:
    return "A" if x >= 0.9 else "B" if x >= 0.75 else "C" if x >= 0.6 else "D"


def main() -> int:
    ap = argparse.ArgumentParser(description="知识库质量评分")
    ap.add_argument("--detail", action="store_true", help="显示每篇明细")
    ap.add_argument("--no-log", action="store_true", help="不写 score_history.jsonl")
    a = ap.parse_args()

    ns = load_notes()
    rows = []
    for nid, e in ns.items():
        card = find_card(nid)
        s = {
            "acquisition": score_acquisition(e),
            "archival": score_archival(e, card),
            "readability": score_readability(card),
            "retention": score_retention(e, card),
        }
        overall = round(sum(s[d] for d in DIMS) / 4, 3)
        rows.append((nid, e.get("title", ""), grade(overall), overall, s))

    dims_avg = {d: round(sum(r[4][d] for r in rows) / len(rows), 3) for d in DIMS} if rows else {}
    overall_avg = round(sum(r[3] for r in rows) / len(rows), 3) if rows else 0

    print("质量评分（四维各 0-1，取均分）")
    print("=" * 60)
    for d in DIMS:
        bar = "█" * int(dims_avg[d] * 20)
        print(f"  {DIM_NAMES[d]}  {dims_avg[d]:.2f}  {bar}")
    print(f"  {'总评'}      {overall_avg:.2f}  等级 {grade(overall_avg)}   （A≥0.9 B≥0.75 C≥0.6 D<0.6）")
    print()

    worst = sorted(rows, key=lambda r: r[3])[:5]
    focus = rows if a.detail else worst
    label = "全库明细" if a.detail else "最需改进（后 5 篇）"
    print(f"{label}：")
    for nid, title, g, ov, s in sorted(focus, key=lambda r: r[3]):
        marks = " ".join(f"{DIM_NAMES[d][:2]}{s[d]:.2f}" for d in DIMS)
        print(f"  [{g}] {ov:.2f}  {title[:30]:32} {marks}")
    if not a.detail and rows:
        print("  （--detail 查看全部）")
    print()

    low = [r for r in rows if r[2] in ("C", "D")]
    if low:
        print(f"注意：{len(low)} 篇评级 C/D，优先检查 ASR 缺失与卡片结构。")
    else:
        print("全部达标。")

    if not a.no_log and rows:
        if not LOGS.exists():  # 沙箱下 mkdir(exist_ok=True) 会抛 EEXIST，先判断
            LOGS.mkdir(parents=True)
        entry = {
            "ts": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "notes": len(rows),
            "overall": overall_avg,
            "grade": grade(overall_avg),
            "dims": dims_avg,
            "low": [nid for nid, _, g, _, _ in low],
            "digest": hashlib.sha256(NOTES.read_bytes()).hexdigest()[:12],
        }
        with open(HISTORY, "a", encoding="utf-8") as f:
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")
        print(f"已记录到 logs/score_history.jsonl（--no-log 可跳过）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
