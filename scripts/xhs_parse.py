#!/usr/bin/env python3
"""解析「点点」导出的收藏夹整理文本，转成结构化 JSON。

点点输出格式（实测）：
    笔记 1：<标题>
    笔记 ID：<24位hex>
    标题：...
    正文：...
    视频讲了这些内容：...
    完整语音文案（ASR）：...
    OCR 识别内容：...
    知识标签：#...
    相关参考信息：...
    评论区补充：...

设计原则：字段缺失不报错，格式漂移尽量容错，解析不了的整块保留在 raw_block。
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

ZW_CHARS = "\u200b\u200c\u200d\u2060\ufeff\u00ad"
ZW_TABLE = str.maketrans("", "", ZW_CHARS)

# 块起始：笔记 1：xxx
BLOCK_RE = re.compile(r"(?m)^[ \t\u3000]*笔记\s*(\d+)\s*[：:][ \t]*(.*)$")

# 字段名 -> 内部键
FIELD_KEYS = {
    "笔记ID": "note_id_text",
    "标题": "title",
    "正文": "body",
    "视频讲了这些内容": "video_points",
    "视频要点": "video_points",
    "完整语音文案ASR": "asr",
    "ASR全文": "asr",
    "语音文案": "asr",
    "OCR识别内容": "ocr",
    "知识标签": "tags_line",
    "相关参考信息": "references",
    "评论区补充": "comments",
    "摘要": "summary",
    "核心观点": "key_points",
    "类型": "note_type",
}

FIELD_RE = re.compile(
    r"("
    r"笔记\s*ID|"
    r"正文|"
    r"视频讲了这些内容|"
    r"视频要点|"
    r"完整语音文案\s*[（(]?\s*ASR\s*[）)]?|"
    r"ASR\s*全文|"
    r"语音文案|"
    r"OCR\s*识别内容|"
    r"知识标签|"
    r"相关参考信息|"
    r"评论区补充|"
    r"摘要|"
    r"核心观点|"
    r"类型"
    r")[ \t]*[：:][ \t]*"
)

# 「标题」只认行首，避免误吃正文里的「……切换标题：……」
TITLE_FIELD_RE = re.compile(r"(?m)^[ \t\u3000]*标题[ \t]*[：:][ \t]*(.*)$")

NOTE_ID_RE = re.compile(r"[0-9a-f]{24}")
HASHTAG_RE = re.compile(r"#([^\s#，,。.、]+)")


def clean(text: str) -> str:
    text = text.translate(ZW_TABLE)
    text = text.replace("\u00a0", " ").replace("\r\n", "\n").replace("\r", "\n")
    text = re.sub(r"[ \t\u3000]+\n", "\n", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def norm_key(raw: str) -> str:
    # 去掉空白与各类括号标点后再查表，兼容「完整语音文案（ASR）」这类写法
    k = re.sub(r"[\s（）()\[\]【】《》:：、,，.。]+", "", raw)
    return FIELD_KEYS.get(k, "other")


def parse_tags(line: str) -> list[str]:
    if not line:
        return []
    return [t for t in HASHTAG_RE.findall(line)]


def parse_block(block: str, index: int, heading: str) -> dict:
    note: dict = {"index": index, "heading": heading, "raw_block": block}

    fields = [
        (m.start(), m.end(), norm_key(m.group(1))) for m in FIELD_RE.finditer(block)
    ]
    for i, (start, end, key) in enumerate(fields):
        stop = fields[i + 1][0] if i + 1 < len(fields) else len(block)
        value = block[end:stop].strip()
        if key in note and note[key]:
            note[key] = note[key] + "\n\n" + value
        else:
            note[key] = value

    id_text = note.pop("note_id_text", "") or ""
    m = NOTE_ID_RE.search(id_text)
    if not m:
        # 内联格式：24 位 ID 可能内嵌在块头行里
        m = NOTE_ID_RE.search(heading)
    note["note_id"] = m.group(0) if m else None

    # 标题：优先行首「标题」字段；否则用块头，并清掉内联格式残留
    heading = heading or ""
    tm = TITLE_FIELD_RE.search(block)
    if tm:
        heading = tm.group(1).strip()
    for marker in ("笔记 ID", "笔记ID", " 类型：", " 正文："):
        heading = heading.split(marker)[0]
    note["title"] = (note.get("title") or heading or "").strip().strip("：: ")
    note["note_type"] = (note.pop("note_type", "") or "").strip()
    note["tags"] = parse_tags(note.pop("tags_line", "") or "")

    for key in ("body", "video_points", "asr", "ocr", "references", "comments"):
        note.setdefault(key, "")

    note["char_counts"] = {
        "body": len(note["body"]),
        "asr": len(note["asr"]),
        "ocr": len(note["ocr"]),
        "total": len(note["body"]) + len(note["asr"]) + len(note["ocr"]),
    }
    note["has_video"] = ("视频" in note.get("note_type", "")) or bool(note["asr"].strip()) or "视频" in heading
    return note


def parse_text(raw: str) -> dict:
    text = clean(raw)
    matches = list(BLOCK_RE.finditer(text))

    if not matches:
        # 没有「笔记 N：」前缀，整篇当作一条
        note = parse_block(text, 1, text.split("\n", 1)[0][:80])
        note["index"] = 1
        return {"note_count": 1, "notes": [note], "warnings": ["未找到块标记，整篇按单条解析"]}

    notes = []
    for i, m in enumerate(matches):
        # 从块标记行本身开始截取：内联格式中 正文/类型/笔记ID 都跟在「笔记 N：」同一行
        start = m.start()
        stop = matches[i + 1].start() if i + 1 < len(matches) else len(text)
        notes.append(parse_block(text[start:stop], int(m.group(1)), m.group(2).strip()))

    warnings = []
    for n in notes:
        if not n["note_id"]:
            warnings.append(f"第 {n['index']} 条无真实笔记 ID，入库时将用标题哈希生成后备 ID（仍可去重）")

    return {"note_count": len(notes), "notes": notes, "warnings": warnings}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("input", help="点点导出文本文件")
    ap.add_argument("-o", "--output", help="输出 JSON 路径；缺省打印到 stdout")
    ap.add_argument("--pretty", action="store_true", help="缩进输出")
    args = ap.parse_args()

    raw = Path(args.input).read_text(encoding="utf-8")
    result = parse_text(raw)

    payload = json.dumps(
        result, ensure_ascii=False, indent=2 if args.pretty else None
    )
    if args.output:
        Path(args.output).write_text(payload, encoding="utf-8")
        print(f"[OK] {result['note_count']} 条 -> {args.output}")
        for n in result["notes"]:
            c = n["char_counts"]
            print(
                f"  #{n['index']} {n['note_id'] or '(无ID)'} | {n['title'][:30]} "
                f"| 正文{c['body']} ASR{c['asr']} OCR{c['ocr']}"
            )
        for w in result["warnings"]:
            print(f"  [WARN] {w}")
    else:
        print(payload)
    return 0


if __name__ == "__main__":
    sys.exit(main())
