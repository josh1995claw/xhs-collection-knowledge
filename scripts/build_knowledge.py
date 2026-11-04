#!/usr/bin/env python3
"""
自动分类 + 资源抽取 + 总览生成（一条龙）。
用法：python3 scripts/build_knowledge.py
依赖 index/notes.json、raw/、taxonomy.json；产出：
  - index/notes.json 更新 category 字段
  - knowledge/00-总览.md、knowledge/<分类>/_index.md
  - knowledge/00-资源库/资源索引.md（URL/域名/GitHub 汇总）
"""
import json
import re
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
INDEX = ROOT / "index"
KNOW = ROOT / "knowledge"

# 新库没有 taxonomy.json 时自动生成的最小模板（可按需改写：只改这个文件 + kb rebuild）
DEFAULT_TAXONOMY = {
    "version": "1.0",
    "note": "默认分类模板：按需修改 categories 的 name/keywords 后运行 kb rebuild。name 不能含空格。",
    "categories": [
        {"code": "01", "name": "方法论", "dir": "01-方法论", "keywords": ["方法", "框架", "流程", "体系", "原则"]},
        {"code": "02", "name": "工具与效率", "dir": "02-工具与效率", "keywords": ["工具", "插件", "skill", "自动化", "效率", "脚本"]},
        {"code": "03", "name": "技术与原理", "dir": "03-技术与原理", "keywords": ["模型", "算法", "架构", "agent", "prompt", "rag"]},
        {"code": "04", "name": "数据与分析", "dir": "04-数据与分析", "keywords": ["数据", "指标", "统计", "分析", "图表"]},
        {"code": "05", "name": "设计与体验", "dir": "05-设计与体验", "keywords": ["设计", "ui", "ux", "交互", "视觉", "前端"]},
        {"code": "06", "name": "行业与商业", "dir": "06-行业与商业", "keywords": ["行业", "商业", "变现", "市场", "创业"]},
        {"code": "99", "name": "待分类", "dir": "99-待分类", "keywords": []},
    ],
}


def load_taxonomy():
    p = INDEX / "taxonomy.json"
    if not p.exists():
        INDEX.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps(DEFAULT_TAXONOMY, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        print(f"未找到 {p.relative_to(ROOT)}，已生成默认分类模板（可编辑后重跑）")
        return DEFAULT_TAXONOMY
    return json.loads(p.read_text(encoding="utf-8"))


def cat_dir_map():
    """分类码 -> 目录名，统一由 taxonomy.json 驱动，避免脚本里硬编码。"""
    return {c["code"]: c["dir"] for c in load_taxonomy()["categories"]}


CAT_DIR = cat_dir_map()


def classify(text, tax):
    low = text.lower()
    scores = {}
    for c in tax["categories"]:
        s = sum(low.count(k.lower()) for k in c["keywords"])
        if s:
            scores[c["code"]] = s
    if not scores:
        return "99"
    best = max(scores.items(), key=lambda x: x[1])
    return best[0]


def card_dir_map():
    """扫 cards/ 下人工归位的卡片，返回 note_id 前8位 -> 分类码。
    人工判定优先级高于关键词自动分类。"""
    m = {}
    for f in (ROOT / "cards").rglob("*.md"):
        pre = f.name[:8]
        if pre and pre[0].isalnum():
            m[pre] = f.parent.name[:2]
    return m


def first_line(text, n=80, skip=None):
    """取正文首句作摘要。跳过 YAML frontmatter、元数据行、归档模板行与标题本身。"""
    body = re.sub(r"\A---\s*\n.*?\n---\s*\n", "", text, flags=re.S)
    meta_keys = r"^(note_id|title|seq|tags|source|collected|category|score|type|status|card_path|raw_path|value_score)\s*[:：]"
    skips = {s.strip() for s in (skip or "").split("|") if s.strip()}
    for line in body.splitlines():
        line = line.strip().lstrip("#*- >")
        if not line or re.match(meta_keys, line):
            continue
        if re.match(r"^收藏夹第\s*\d+\s*条", line) or "经点点整理" in line:
            continue
        if "[话题]" in line or line.startswith("#"):
            continue
        if line in skips or line.startswith("> "):
            continue
        if len(line) > 15:
            return line[:n]
    return ""


def extract_urls(text):
    urls = re.findall(r"https?://[^\s\)\"'<>，。；]+", text)
    return sorted(set(u.rstrip(".,;") for u in urls))


def main():
    tax = load_taxonomy()
    notes = json.loads((INDEX / "notes.json").read_text(encoding="utf-8"))
    all_resources = defaultdict(set)
    entries = []

    card_dirs = card_dir_map()
    conflicts = []
    for nid, meta in notes.get("notes", {}).items():
        raw_files = list((ROOT / "raw").glob(f"{nid}*.md"))
        if not raw_files:
            continue
        text = raw_files[0].read_text(encoding="utf-8")
        auto = classify(text, tax)
        manual = card_dirs.get(nid[:8])
        old = meta.get("category")
        # 优先级：人工卡片目录 > 已有索引值 > 关键词自动判定
        cat = manual or old or auto
        if manual and old and manual != old:
            conflicts.append((nid[:8], meta.get("title", "")[:24], old, manual))
        meta["category"] = cat
        meta["category_name"] = next((c["name"] for c in tax["categories"] if c["code"] == cat), "待分类")
        for u in extract_urls(text):
            all_resources[re.sub(r"^https?://", "", u).split("/")[0]].add(u)
        entries.append({"nid": nid, "title": meta.get("title", ""), "cat": cat, "line": first_line(text, 70, skip=meta.get("title", "")), "seq": meta.get("seq", "?")})

    (INDEX / "notes.json").write_text(json.dumps(notes, ensure_ascii=False, indent=2), encoding="utf-8")

    # 总览
    by_cat = defaultdict(list)
    for e in entries:
        by_cat[e["cat"]].append(e)
    lines = ["# 知识库总览", "",
             f"> 共 {len(entries)} 篇 · 自动生成于 build_knowledge.py · 人工阅读入口", ""]
    orientation = tax.get("orientation")
    if orientation:
        lines += [f"> **方向**：{orientation}", ""]
    lines += ["| 快速操作 | 命令 |", "|---|---|",
              "| 按词检索 | `kb 关键词` |", "| 打开卡片 | `kb open 序号` |",
              "| 列分类 | `kb cats` |", "| 看统计 | `kb stats` |", ""]
    for code in sorted(by_cat):
        name = CAT_DIR.get(code, code)
        lines.append(f"## {name}（{len(by_cat[code])}）")
        lines.append("")
        for e in by_cat[code]:
            card = list((ROOT / "cards").rglob(f"{e['nid'][:8]}*.md"))
            link = f"../cards/{card[0].relative_to(ROOT / 'cards')}" if card else "../raw"
            lines.append(f"- **{e['title'][:40]}** — {e['line'][:60]}…（[卡片]({link})，收藏序号 {e['seq']}）")
        lines.append("")
    empty = [f"{name}" for code, name in CAT_DIR.items() if code not in by_cat and code != "99"]
    if empty:
        lines += ["---", "", f"> 暂无内容、等待新收藏进入的分类：{'、'.join(empty)}", ""]
    (KNOW / "00-总览.md").write_text("\n".join(lines), encoding="utf-8")

    # 分类页
    for code, name in CAT_DIR.items():
        d = KNOW / name
        d.mkdir(parents=True, exist_ok=True)
        items = by_cat.get(code, [])
        content = [f"# {name}", "", f"> {len(items)} 篇 · 自动生成（人工阅读入口，卡片链接可点）"]
        content.append("")
        for e in items:
            card = list((ROOT / "cards").rglob(f"{e['nid'][:8]}*.md"))
            if card:
                rel = card[0].relative_to(ROOT / "cards").as_posix()
                content.append(f"- **{e['title'][:44]}** — {e['line'][:70]}（[卡片](../../cards/{rel})）")
            else:
                content.append(f"- **{e['title'][:44]}** — {e['line'][:70]}（未生成卡片）")
        (d / "_index.md").write_text("\n".join(content) + "\n", encoding="utf-8")

    # 资源库
    res = ["# 关键资源库", "", "> 从全部笔记中自动抽取的链接，按域名归并 · 自动生成", ""]
    for dom in sorted(all_resources):
        res.append(f"## {dom}")
        for u in sorted(all_resources[dom]):
            res.append(f"- {u}")
        res.append("")
    rdir = KNOW / "00-资源库"
    rdir.mkdir(parents=True, exist_ok=True)
    (rdir / "资源索引.md").write_text("\n".join(res) + "\n", encoding="utf-8")

    print(f"分类 {len(entries)} 篇；资源域名 {len(all_resources)} 个")
    for code, items in sorted(by_cat.items()):
        print(f"  {CAT_DIR.get(code, code)}: {len(items)}")
    if conflicts:
        print(f"\n[冲突] {len(conflicts)} 处卡片目录与索引不一致，已按人工卡片目录为准：")
        for nid, title, old, new in conflicts:
            print(f"  {nid} {title}: 索引 {old} -> 采用 {new}")
    # 反向校验：卡片落在不存在的分类目录
    valid = set(CAT_DIR.values())
    stray = sorted({str(f.parent.name) for f in (ROOT / "cards").rglob("*.md") if f.parent.name not in valid})
    if stray:
        print(f"[警告] 卡片存在未定义分类目录：{', '.join(stray)}")


if __name__ == "__main__":
    main()
