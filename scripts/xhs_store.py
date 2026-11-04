#!/usr/bin/env python3
"""把点点导出的整理文本入库：JSON 索引 + raw Markdown 归档。

用法：
    python3 xhs_store.py import <txt|json> [--source 备注]
    python3 xhs_store.py status
    python3 xhs_store.py list [--limit 20]

去重设计：
    收藏夹默认「最近收藏在前」，新收藏会把所有序号往后推，所以用序号当断点不可靠。
    改以 note_id（24 位 hex）作主键：每次请点点整理「最近 N 条」，重复项自动被过滤，
    只要 N 覆盖住这段时间的新增量即可。序号只作参考记录，不参与判断。

    索引用 JSON 而非 SQLite：这个规模（几百到几千条）JSON 完全够用，且无锁、
    可读、可 git 版本化，迁移和备份都简单。
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from datetime import datetime
from pathlib import Path

BASE = Path(__file__).resolve().parent.parent
INDEX_PATH = BASE / "index" / "notes.json"
RAW_DIR = BASE / "raw"
SAMPLES = BASE / "samples"

sys.path.insert(0, str(Path(__file__).resolve().parent))
from xhs_parse import parse_text  # noqa: E402

EMPTY_INDEX = {"version": 1, "updated_at": None, "notes": {}, "batches": []}


def load_index() -> dict:
    if not INDEX_PATH.exists():
        return json.loads(json.dumps(EMPTY_INDEX))
    try:
        data = json.loads(INDEX_PATH.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        raise SystemExit(f"索引文件损坏：{INDEX_PATH}，请先备份再修复")
    data.setdefault("notes", {})
    data.setdefault("batches", [])
    return data


def save_index(index: dict) -> None:
    index["updated_at"] = datetime.now().strftime("%Y-%m-%d %H:%M")
    # 直接写入（沙箱禁止 tmp.replace，改用直写 + 备份）
    INDEX_PATH.write_text(json.dumps(index, ensure_ascii=False, indent=2), encoding="utf-8")


def safe_filename(text: str, limit: int = 18) -> str:
    t = re.sub(r'[\\/:*?"<>|\n\r\t]', "", text).strip()
    t = re.sub(r"\s+", " ", t)
    return t[:limit].rstrip()


def render_raw(note: dict, source: str) -> str:
    tags = note.get("tags") or []
    note_type = "视频笔记" if note.get("has_video") else "图文笔记"
    lines = [
        "---",
        f"note_id: {note.get('note_id') or ''}",
        f"seq: {note.get('index')}",
        f"title: {note.get('title') or ''}",
        f"tags: [{', '.join(tags)}]",
        f"note_type: {'video' if note.get('has_video') else 'image'}",
        f"url: {note.get('url') or ''}",
        f"source: {source}",
        f"imported_at: {datetime.now().strftime('%Y-%m-%d %H:%M')}",
        f"char_body: {len(note.get('body') or '')}",
        f"char_asr: {len(note.get('asr') or '')}",
        f"char_ocr: {len(note.get('ocr') or '')}",
        "---",
        "",
        f"# {note.get('title') or '(无标题)'}",
        "",
        f"> 收藏夹第 {note.get('index')} 条 · {note_type} · 经点点整理",
        "",
    ]

    def section(title: str, body: str) -> None:
        if body and body.strip():
            lines.extend([f"## {title}", "", body.strip(), ""])

    section("正文", note.get("body", ""))
    section("视频要点", note.get("video_points", ""))
    section("语音逐字稿（ASR）", note.get("asr", ""))
    section("画面文字（OCR）", note.get("ocr", ""))
    section("相关参考", note.get("references", ""))
    section("评论区补充", note.get("comments", ""))
    return "\n".join(lines)


def import_file(path: Path, source: str = "点点") -> dict:
    text = path.read_text(encoding="utf-8")
    if path.suffix.lower() == ".json":
        parsed = json.loads(text)
        notes = parsed["notes"]
    else:
        parsed = parse_text(text)
        notes = parsed["notes"]

    index = load_index()
    now = datetime.now().strftime("%Y-%m-%d %H:%M")
    new_count = dup_count = 0
    seqs: list[int] = []

    for note in notes:
        nid = (note.get("note_id") or "").strip()
        title = note.get("title") or ""
        if not nid or nid.lower() == "unknown":
            # 点点有时输出"笔记 ID：unknown"，用标题哈希生成确定性后备 ID
            import hashlib
            nid = "gen_" + hashlib.md5(title.encode("utf-8")).hexdigest()[:20]
            note["note_id"] = nid
        if note.get("index"):
            seqs.append(int(note["index"]))
        note["url"] = note.get("url") or f"https://www.xiaohongshu.com/explore/{nid}"

        existed = nid in index["notes"]
        if existed:
            dup_count += 1
            # 保留旧元数据（可能来自更完整的批次），仅补缺失的序号
            old = index["notes"][nid]
            if not old.get("seq") and note.get("index"):
                old["seq"] = note.get("index")
            continue
        new_count += 1

        raw_path = RAW_DIR / f"{nid}-{safe_filename(note.get('title') or '')}.md"
        if not existed:
            RAW_DIR.mkdir(parents=True, exist_ok=True)
            raw_path.write_text(render_raw(note, source), encoding="utf-8")

        entry = {
            "seq": note.get("index"),
            "title": note.get("title"),
            "tags": note.get("tags") or [],
            "note_type": "video" if note.get("has_video") else "image",
            "url": note["url"],
            "source": source,
            "imported_at": now,
            "body_len": len(note.get("body") or ""),
            "asr_len": len(note.get("asr") or ""),
            "ocr_len": len(note.get("ocr") or ""),
            "status": index["notes"].get(nid, {}).get("status", "archived"),
            "value_score": index["notes"].get(nid, {}).get("value_score"),
            "raw_path": raw_path.relative_to(BASE).as_posix(),
            "card_path": index["notes"].get(nid, {}).get("card_path"),
        }
        if existed:
            entry["imported_at"] = index["notes"][nid].get("imported_at", now)
        index["notes"][nid] = entry

    batch = {
        "id": len(index["batches"]) + 1,
        "source": source,
        "note_count": len(notes),
        "new_count": new_count,
        "dup_count": dup_count,
        "seq_min": min(seqs) if seqs else None,
        "seq_max": max(seqs) if seqs else None,
        "imported_at": now,
    }
    index["batches"].append(batch)
    save_index(index)

    return {
        "file": path.name,
        "total": len(notes),
        "new": new_count,
        "dup": dup_count,
        "seq_min": batch["seq_min"],
        "seq_max": batch["seq_max"],
        "warnings": parsed.get("warnings", []),
    }


def status() -> None:
    index = load_index()
    notes = index["notes"]
    total = len(notes)
    if total == 0:
        print("知识库为空。用 import 导入第一批。")
        return

    video = sum(1 for n in notes.values() if n["note_type"] == "video")
    body = sum(n["body_len"] for n in notes.values())
    asr = sum(n["asr_len"] for n in notes.values())
    ocr = sum(n["ocr_len"] for n in notes.values())
    max_seq = max((n["seq"] or 0) for n in notes.values())
    last = index["batches"][-1] if index["batches"] else None

    print(f"已归档       {total} 条（视频 {video} / 图文 {total - video}）")
    print(f"语料规模     正文 {body} 字 · ASR {asr} 字 · OCR {ocr} 字")
    if last:
        print(
            f"最近导入     {last['source']}（{last['imported_at']}）"
            f" 新 {last['new_count']} / 重复 {last['dup_count']}"
        )
    print(f"覆盖序号     第 1 至 {max_seq} 条")

    suggests = max(20, max_seq + 10)
    print()
    print(f"下次让点点整理：「最近 {suggests} 条，按之前的格式整理」")
    if last and last["dup_count"] and not last["new_count"]:
        print("注意：上次全是被过滤的重复项，说明窗口偏小，建议再放大。")


def list_notes(limit: int) -> None:
    index = load_index()
    rows = sorted(index["notes"].items(), key=lambda kv: kv[1]["seq"] or 0)[:limit]
    print(f"{'序号':<4} {'类型':<6} {'正文':>5} {'ASR':>5} {'OCR':>5}  标题")
    print("-" * 78)
    for nid, n in rows:
        print(
            f"{n['seq'] or '-':<4} {n['note_type']:<6} {n['body_len']:>5} "
            f"{n['asr_len']:>5} {n['ocr_len']:>5}  {(n['title'] or '')[:34]}"
        )


def register_card(note_id: str, card_path: str, topic: str, score: float | None) -> None:
    index = load_index()
    if note_id not in index["notes"]:
        raise SystemExit(f"索引里没有 {note_id}，请先 import")
    p = Path(card_path)
    if not p.is_absolute():
        p = BASE / card_path
    if not p.exists():
        raise SystemExit(f"卡片文件不存在：{p}")
    index["notes"][note_id]["card_path"] = p.relative_to(BASE).as_posix()
    index["notes"][note_id]["topic"] = topic
    index["notes"][note_id]["status"] = "carded"
    if score is not None:
        index["notes"][note_id]["value_score"] = score
    save_index(index)
    print(f"[登记] {note_id} -> {index['notes'][note_id]['card_path']}（{topic}）")


def build_toc() -> None:
    index = load_index()
    notes = index["notes"]
    if not notes:
        print("知识库为空。")
        return

    by_topic: dict[str, list[tuple[str, dict]]] = {}
    pending: list[tuple[str, dict]] = []
    for nid, n in notes.items():
        if n.get("status") == "carded" and n.get("card_path"):
            by_topic.setdefault(n.get("topic") or "未分类", []).append((nid, n))
        else:
            pending.append((nid, n))

    lines = [
        "# 小红书收藏夹知识库",
        "",
        f"> 共 {len(notes)} 条笔记 · {sum(len(v) for v in by_topic.values())} 张知识卡"
        f" · 更新于 {index.get('updated_at') or '-'}",
        "",
        "## 已提炼",
        "",
    ]
    for topic in sorted(by_topic):
        items = sorted(by_topic[topic], key=lambda kv: kv[1].get("seq") or 0)
        lines.append(f"### {topic}（{len(items)}）")
        lines.append("")
        for nid, n in items:
            lines.append(f"- [{n['title']}]({n['card_path']}) — 第 {n['seq']} 条")
        lines.append("")

    if pending:
        lines.append(f"## 待提炼（{len(pending)}）")
        lines.append("")
        for nid, n in sorted(pending, key=lambda kv: kv[1].get("seq") or 0):
            kind = "视频" if n["note_type"] == "video" else "图文"
            lines.append(
                f"- {n['title']} — 第 {n['seq']} 条 · {kind}"
                f" · 正文{n['body_len']}/ASR{n['asr_len']}/OCR{n['ocr_len']}"
            )
        lines.append("")

    out = BASE / "00-总目录.md"
    out.write_text("\n".join(lines), encoding="utf-8")
    print(f"[总目录] {out.relative_to(BASE)} · 已提炼 {sum(len(v) for v in by_topic.values())} 篇 · 待提炼 {len(pending)} 篇")


def paste_from_clipboard() -> None:
    """直接接住剪贴板里点点的输出（Mac 通用剪贴板：iPhone 复制，这里直接取）。"""
    import subprocess

    try:
        txt = subprocess.run(
            ["pbpaste"], capture_output=True, text=True, timeout=10
        ).stdout
    except Exception as e:
        raise SystemExit(f"读取剪贴板失败：{e}")
    if not txt.strip():
        raise SystemExit("剪贴板是空的。先在手机上复制点点的整理结果。")

    SAMPLES.mkdir(parents=True, exist_ok=True)
    p = SAMPLES / f"clip_{datetime.now().strftime('%Y%m%d_%H%M%S')}.txt"
    p.write_text(txt, encoding="utf-8")
    print(f"[剪贴板] 已保存 {p.name}（{len(txt)} 字）")
    r = import_file(p, "点点-剪贴板")
    print(f"[导入] 共 {r['total']} 条，新增 {r['new']}，重复 {r['dup']}")
    if r["seq_min"]:
        print(f"[范围] 收藏夹第 {r['seq_min']}–{r['seq_max']} 条")


def main() -> int:
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)

    p_imp = sub.add_parser("import", help="导入点点导出文本")
    p_imp.add_argument("path")
    p_imp.add_argument("--source", default="点点")

    sub.add_parser("status", help="查看归档进度与下次建议")
    p_list = sub.add_parser("list", help="列出已归档笔记")
    p_list.add_argument("--limit", type=int, default=20)

    p_card = sub.add_parser("card", help="登记一张知识卡")
    p_card.add_argument("note_id")
    p_card.add_argument("path")
    p_card.add_argument("--topic", default="未分类")
    p_card.add_argument("--score", type=float, default=None)

    sub.add_parser("toc", help="生成总目录")
    sub.add_parser("paste", help="从剪贴板接住点点输出并入库")

    args = ap.parse_args()

    if args.cmd == "import":
        p = Path(args.path)
        if not p.is_absolute() and not p.exists():
            p = SAMPLES / args.path
        r = import_file(p, args.source)
        print(f"[导入] {r['file']}：共 {r['total']} 条，新增 {r['new']}，重复 {r['dup']}")
        if r["seq_min"]:
            print(f"[范围] 收藏夹第 {r['seq_min']}–{r['seq_max']} 条")
        for w in r["warnings"]:
            print(f"[WARN] {w}")
    elif args.cmd == "status":
        status()
    elif args.cmd == "list":
        list_notes(args.limit)
    elif args.cmd == "card":
        register_card(args.note_id, args.path, args.topic, args.score)
    elif args.cmd == "toc":
        build_toc()
    elif args.cmd == "paste":
        paste_from_clipboard()
    return 0


if __name__ == "__main__":
    sys.exit(main())
