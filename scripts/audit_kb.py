#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
audit_kb.py —— 全库一体检：数据一致性 + 资源完整性 + 云端备份状态

一次跑完四类检查，任何一项不达标都以非零退出码结束，便于接进 `kb check`。

  python3 scripts/audit_kb.py            # 只体检
  python3 scripts/audit_kb.py --fix      # 修可自动修的：card_path 过期、卡片 frontmatter 分类

检查项：
  A 索引一致性  notes.json 里的 card_path / raw_path 是否指向真实文件
  B 分类一致性  卡片物理目录 = frontmatter category = notes.json category（人工目录优先）
  C 索引新鲜度  chunks/bm25 是否落后于 notes.json 与 cards/
  D 资源完整性  本机 skill 安装目录 ↔ resources/skills ↔ MANIFEST（可选组件）
  E 备份状态    sync/ima/manifest vs .ima_state（本地是否已覆盖到云端）
"""
from __future__ import annotations

import argparse
import json
import re

import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

INDEX = ROOT / "index"
CARDS = ROOT / "cards"
RAW = ROOT / "raw"
NOTES = INDEX / "notes.json"
IMA_MANIFEST = ROOT / "sync" / "ima" / "manifest.json"
IMA_STATE = ROOT / "sync" / "ima" / ".ima_state.json"


def load(p: Path, default=None):
    if p.exists():
        try:
            return json.loads(p.read_text(encoding="utf-8"))
        except Exception:
            return default if default is not None else {}
    return default if default is not None else {}


def cards_on_disk() -> dict[str, Path]:
    """note_id -> 卡片路径（以物理目录为权威）。"""
    out = {}
    for p in sorted(CARDS.glob("*/*.md")):
        m = re.search(r"^note_id:\s*(\S+)", p.read_text(encoding="utf-8"), re.M)
        if m:
            out[m.group(1).strip()] = p
    return out


def check_index(ns: dict, disk: dict) -> tuple[list[str], list[str]]:
    errs, fixable = [], []
    for nid, e in ns.items():
        cp = e.get("card_path")
        if cp and not (ROOT / cp).exists():
            errs.append(f"card_path 过期：{nid[:8]} → {cp}")
            fixable.append(nid)
        if nid not in disk:
            errs.append(f"缺少知识卡：{nid[:8]} {e.get('title', '')[:24]}")
        rp = e.get("raw_path")
        if rp and not (ROOT / rp).exists():
            errs.append(f"raw 缺失：{nid[:8]} → {rp}")
    return errs, fixable


def check_category(ns: dict, disk: dict) -> list[str]:
    errs = []
    for cp in sorted(CARDS.glob("*/*.md")):
        text = cp.read_text(encoding="utf-8")
        nid = re.search(r"^note_id:\s*(\S+)", text, re.M)
        cat = re.search(r"^category:\s*(.+)$", text, re.M)
        nid = nid.group(1).strip() if nid else ""
        fm = cat.group(1).strip() if cat else "（缺失）"
        if fm != cp.parent.name:
            errs.append(f"frontmatter 分类不符：{cp.name[:30]} fm={fm} 目录={cp.parent.name}")
        nj = ns.get(nid, {}).get("category_name")
        if nj and nj != cp.parent.name.split("-", 1)[-1]:
            errs.append(f"索引分类不符：{cp.name[:30]} 目录={cp.parent.name} 索引={nj}")
    return errs


def check_freshness() -> list[str]:
    if not NOTES.exists():
        return ["index/notes.json 不存在"]
    src = [NOTES.stat().st_mtime]
    src += [p.stat().st_mtime for p in CARDS.glob("*/*.md")]
    newest_src = max(src)
    errs = []
    for f in ("chunks.jsonl", "bm25.json"):
        p = INDEX / f
        if not p.exists():
            errs.append(f"索引缺失：index/{f}（先 kb rebuild）")
        elif p.stat().st_mtime < newest_src:
            errs.append(f"索引过期：index/{f} 早于最近的内容改动（先 kb rebuild）")
    return errs


def check_resources():
    """返回 None 表示该组件未安装，跳过。"""
    if not (ROOT / "scripts" / "sync_resources.py").exists():
        return None
    try:
        import sync_resources as sr
    except Exception as e:  # noqa
        return [f"无法加载 sync_resources：{e}"]
    src = sr.scan_src()
    dest = {d.name for d in sr.skill_dirs(sr.DEST)}
    reg = sr.load_json(sr.REGISTRY, {"skills": {}}).get("skills", {})
    manifest = {s["name"]: s for s in sr.load_json(sr.MANIFEST, {}).get("skills", [])}
    errs = []
    for n in sorted(set(src) - dest):
        errs.append(f"资源未镜像：{n}")
    for n, info in src.items():
        if n in dest and sr.digest(sr.DEST / n)[0] != info["sha256"]:
            errs.append(f"资源镜像落后：{n}")
        if n not in manifest:
            errs.append(f"清单未收录：{n}")
        if reg.get(n, {}).get("purpose", "⚠️ 待补充") == "⚠️ 待补充":
            errs.append(f"用途未登记：{n}（resources/skills/REGISTRY.json）")
    return errs


def check_backup():
    """返回 None 表示该组件未安装，跳过。"""
    if not (ROOT / "scripts" / "ima_sync.py").exists():
        return None
    if not IMA_MANIFEST.exists():
        return ["IMA 导入包未生成（先 python3 scripts/ima_sync.py build）"]
    man = load(IMA_MANIFEST)
    state = load(IMA_STATE, {})
    if not state:
        return [f"IMA 尚未建立云端基线：{len(man.get('files', []))} 个文件待上传"]
    cur = {f["name"]: f["sha256"] for f in man.get("files", [])}
    prev = state.get("files", {})
    diff = [n for n in cur if prev.get(n) != cur[n]]
    gone = [n for n in prev if n not in cur]
    if diff or gone:
        return [f"IMA 待补传 {len(diff) + len(gone)} 个文件（build 后上传，再 ima_sync.py mark）"]
    return []


def fix(ns: dict, disk: dict) -> list[str]:
    done = []
    changed = False
    for nid, cp in disk.items():
        e = ns.get(nid)
        if not e:
            continue
        rel = cp.relative_to(ROOT).as_posix()
        if e.get("card_path") != rel:
            e["card_path"] = rel
            changed = True
            done.append(f"修正 card_path：{nid[:8]} → {rel}")
    if changed:
        data = load(NOTES)
        data["notes"] = ns
        NOTES.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    for cp in sorted(CARDS.glob("*/*.md")):
        text = cp.read_text(encoding="utf-8")
        dirname = cp.parent.name
        m = re.match(r"^---\n(.*?)\n---\n?", text, re.S)
        if not m:
            continue
        fm = m.group(1)
        if re.search(r"^category:\s*(.+)$", fm, re.M):
            new_fm = re.sub(r"^category:\s*.+$", f"category: {dirname}", fm, count=1, flags=re.M)
        else:
            new_fm = fm.rstrip() + f"\ncategory: {dirname}"
        if new_fm != fm:
            cp.write_text(f"---\n{new_fm}\n---\n" + text[m.end():], encoding="utf-8")
            done.append(f"修正卡片分类：{cp.name[:34]} → {dirname}")
    return done


def main() -> int:
    ap = argparse.ArgumentParser(description="知识库一致性体检")
    ap.add_argument("--fix", action="store_true", help="修正 card_path 与卡片 frontmatter 分类")
    a = ap.parse_args()

    ns = load(NOTES, {}).get("notes", {})
    disk = cards_on_disk()

    if a.fix:
        done = fix(ns, disk)
        ns = load(NOTES, {}).get("notes", {})
        disk = cards_on_disk()
        if done:
            print(f"✓ 已修复 {len(done)} 处：")
            for d in done:
                print("  - " + d)
            print()

    sections = [
        ("A 索引一致性", check_index(ns, disk)[0]),
        ("B 分类一致性", check_category(ns, disk)),
        ("C 索引新鲜度", check_freshness()),
        ("D 资源完整性", check_resources()),
        ("E 云端备份", check_backup()),
    ]
    total = 0
    for name, errs in sections:
        if errs is None:
            print(f"— {name}：跳过（可选组件未安装）")
            continue
        if errs:
            total += len(errs)
            print(f"✗ {name}：{len(errs)} 项")
            for e in errs[:8]:
                print("    - " + e)
            if len(errs) > 8:
                print(f"    …另有 {len(errs) - 8} 项")
        else:
            print(f"✓ {name}：通过")
    print()
    stats = f"{len(ns)} 篇笔记 · 卡片 {len(disk)} 张 · 检查时间 {datetime.now():%Y-%m-%d %H:%M}"
    if total:
        print(f"共 {total} 项待处理 · {stats}")
        print("可自动修复的：python3 scripts/audit_kb.py --fix（资源类需 sync_resources.py sync）")
        return 1
    print(f"全库健康 · {stats}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
