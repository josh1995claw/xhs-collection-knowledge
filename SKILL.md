---
name: xhs-collection-knowledge
description: 把小红书收藏夹批量整理成本地知识库的通用流水线。通过独立 Chrome 实例驱动网页版「点点」AI，将专辑/收藏笔记按模板转成结构化全文，落盘、去重、分类、生成知识卡与检索索引。支持增量采集、标题去重、BM25 检索。当用户提到以下任何一种情况时触发：小红书收藏夹有新增笔记需要整理归档、更新小红书收藏/知识库、整理收藏夹、点点整理、收藏夹知识库、同步小红书收藏、xhs collect、收藏新增笔记入库。也包括"小红书收藏更新了""收藏夹新加了笔记""帮我整理一下新收藏"等自然表达。
---

# 小红书收藏夹知识化流水线

## 适用范围与个人化边界

**本 skill 是自包含的通用采集流水线**：Chrome CDP 自动化 → 点点结构化输出 → 解析入库 → 分类索引 → 文档生成 → 质量评分。适用于任何想把自己的小红书收藏整理成本地知识库的人。

**包结构**：

```
xhs-collection-knowledge/
├── SKILL.md            本文档：流程主指南
├── README.md           对外说明与快速上手
├── references/         标准细则
│   ├── io-standards.md   输入输出标准（三层契约：点点文本 / raw+索引 / 知识卡+检索）
│   └── scoring.md        质量评分与验证机制（四维细则 + 等级 + 日志）
└── scripts/            全部可执行脚本（Python 标准库 + Node 内置，零第三方依赖）
```

**个人知识库的管理规范不在本 skill 内**，而在实际使用的知识库项目 `${KB_ROOT}/docs/` 目录下（fork 本 skill 后自行建立）：

| 文档 | 内容 | 何时用 |
|---|---|---|
| `docs/01-知识库管理规范.md` | 数据流、核心原则、文件职责、质量约定、数据规范（note_id 后备/沙箱写入/分类名） | 了解库怎么运作 |
| `docs/02-知识分类架构.md` | 分类定义、归类规则（按用途归不按词频归） | 新笔记归类时查 |
| `docs/04-交接说明.md` | 接管者指南、例行动作、决策权边界、版本记录、当前状态快照 | 每次采集后更新状态 |
| `docs/05-云端备份与同步规范.md` | 云同步流程、增量规则、恢复方法 | 云端备份时查 |

**边界规则**：通用流程的改进进 SKILL.md；个人分类架构、同步配置、状态快照进 docs/。不要把后者混进前者。

## 前置条件

- macOS + Chrome（任意近期版本）
- 用户已登录小红书；登录态 profile 备份在 `${KB_ROOT}/secrets/chrome-profile-backup.tar.gz`（首次需手动准备）
- Node.js ≥ 18（原生 WebSocket，无需 puppeteer）
- Python 3（标准库即可，无第三方依赖）
- 项目根目录 `${KB_ROOT}`（本文档用此变量代指，实际路径由用户决定）

## 关键约束（踩过的坑，务必遵守）

1. **点点单条回复有长度上限**：一篇视频笔记全文 ≈ 2500 字，一次只能稳定输出 **2 篇**。必须分批要：首轮说明"只输出第 X 到 Y 篇"，后续用「继续输出笔记 A 到 B，严格同样模板，不要重复」接力。附件留在会话上下文，续传无需重新导入。
2. **点点会话过长会丢附件**：一次导入超过约 18 篇后，点点开始回复"没有收到附件数据"——这是上下文窗口溢出，不是笔记不存在。**每次最多导入 18 篇**；超过的分批新建会话采集。
3. **侧边栏含「沪ICP备」**：不能用它在 innerText 里截正文，要用 `.scroll-container` 的 innerText。
4. **选择弹窗的「取消」会关闭整个弹窗**，不是清空勾选。清空勾选应逐个点复选框或重开弹窗。
5. **Chrome 只能在 /tmp 下正常启动**（macOS 执行沙箱限制；用户手动运行不受限）。profile 用 `--user-data-dir=/tmp/xhs-chrome`，每次启动前从 secrets 备份还原，退出后回写备份。
6. **收藏夹序号不是断点**：新收藏会使旧序号后移。去重靠 note_id 或标题（见第 8 条）。
7. **SQLite 在 ~/Documents、~/Desktop 下会 disk I/O error**：索引一律用 JSON（notes.json）。
8. **note_id 与标题去重**：小红书有反爬，点点输出的 note_id 常为 "unknown"。去重策略：①优先用 24 位 note_id（真实 ID）；②拿不到时用标题 MD5 生成 `gen_` 前缀后备 ID（确定性，可去重）；③标题相同视为重复，不重复入库。**不需要纠结获取真实 note_id，标题足以区分笔记。**
9. **沙箱内禁止 `tmp.replace()` 写 index 文件**：macOS 沙箱对 `~/Documents` 下的原子替换有保护，会报权限错误。所有 JSON 索引一律用 `write_text()` 直接覆写。
10. **Chrome 必须用 `run_in_background` 保活**：`&` 后台方式在 shell 退出时会被回收。启动后等 6 秒再连 CDP。

## 流程

### 第 1 步：启动浏览器

```bash
cd /tmp && tar -xzf ${KB_ROOT}/secrets/chrome-profile-backup.tar.gz 2>/dev/null
rm -f /tmp/xhs-chrome/Singleton*
"/Applications/Google Chrome.app/Contents/MacOS/Google Chrome" \
  --user-data-dir=/tmp/xhs-chrome --remote-debugging-port=9222 \
  --no-sandbox --disable-gpu --no-first-run --no-default-browser-check \
  --window-size=1400,950 "https://www.xiaohongshu.com/ai_chat"
# 等待 http://127.0.0.1:9222/json/list 出现 xiaohongshu 标签页（约 6 秒）
```

> 如果在 WorkBuddy agent 环境中运行，用 `run_in_background: true` 启动 Chrome，不要用 `&`。

### 第 2 步：选笔记并导入（增量策略）

流程：点 `.ai-input-action-btn`（+号）→ 点「我的收藏」→ 弹窗内点「专辑」tab → 点目标专辑卡片进入 → 点「全选」→ 校验「已选中 N/M」→ 点「导入」。

**增量采集策略（重要）**：

1. 采集前先查库已有数：`python3 scripts/kb.py stats`
2. 全选后看「已选中 N/M」里的 M（收藏夹总数）
3. **M - 已有数 = 新增数**。如果收藏夹包含之前已采集的笔记（通常如此），M 会等于已有数 + 新增数。
4. 如果新增数 ≤ 18，全选导入即可（已采集的会自动去重）。
5. 如果新增数 > 18，**只勾选新笔记**（取消已采集的勾选），分批新建会话采集。
6. 如果 M < 已有数，说明有笔记失效被删除（小红书失效笔记可能仍占名额但无法查看）。这种情况下 M = 已有数，无需采集。

> **失效笔记**：小红书删除的笔记可能仍占收藏夹名额。如果「已选中 N/M」的 M 恒等于已有数，说明没有新增，不用采集。

### 第 3 步：分批采集

```bash
cd ${KB_ROOT}/scripts
node drive_diandian.mjs --skip-select --batch 2 --total <新增数> --out ../logs/collect_YYYYMMDD.txt
# 中断后续跑：加 --resume（自动检测会话中已有的笔记序号，从缺口继续）
```

### 第 4 步：入库与加工

```bash
python3 scripts/xhs_store.py import ../logs/collect_YYYYMMDD.txt   # 解析+去重+raw归档
python3 scripts/build_knowledge.py                                  # 分类+总览+分类页
python3 scripts/build_index.py                                      # BM25 检索索引
python3 scripts/search.py 关键词                                     # 检索验证
```

**去重机制**：
- 优先按 24 位 note_id 去重（真实 ID）
- note_id 为 "unknown" 时，用标题 MD5 生成 `gen_` 前缀后备 ID（`xhs_store.py` 自动处理）
- 标题相同的笔记视为重复，保留更完整的版本

> **新库首次运行**：`build_knowledge.py` 若发现没有 `index/taxonomy.json`，会自动生成一份默认分类模板（01 方法论 / 02 工具与效率 / 03 技术与原理 / 04 数据与分析 / 05 设计与体验 / 06 行业与商业 / 99 待分类）。按自己的领域改写 `name` 与 `keywords`（`name` 不能含空格），再跑 `kb rebuild` 即可。分类架构细则见 `references/io-standards.md`。

### 第 5 步：写知识卡与更新文档（每次采集后必须做）

**知识卡**：逐篇读 raw 原文，由 AI 通读后提炼写卡到 `cards/<分类>/`。不要只靠脚本拼模板——要点需通读后提炼，修正 ASR 同音错字。卡格式与合格线见 **`references/io-standards.md`** 的「输出标准」。可分批用 Agent 并行写卡（如每批 6 篇）。

**每次采集后必须更新的文档**：

| 文档 | 更新内容 | 怎么更新 |
|---|---|---|
| `cards/<分类>/` | 新笔记的知识卡 | AI 通读原文写卡（可分批用 Agent 并行） |
| `knowledge/00-总览.md` | 库规模、分类导航 | `kb rebuild` 自动生成 |
| `knowledge/<分类>/_index.md` | 分类页 | `kb rebuild` 自动生成 |
| `knowledge/00-资源库/关键资源库.md` | 新发现的资源/skill | AI 补行 |
| `docs/04-交接说明.md` | 当前状态快照、版本记录 | AI 追加（新增几篇、修复什么、踩什么坑） |
| `AGENT.md` | 库规模数字 | AI 更新 |

> **`kb rebuild` 只生成 knowledge/ 下的自动文档，cards/、docs/ 和 AGENT.md 由 AI 更新。** 不要漏掉文档更新——知识库不只是数据，文档是下一个 Agent（或未来的你）接手的依据。

### 第 6 步：资源归档（有新 skill 就必须做）

如果新笔记里出现了可安装的 skill / 工具 / GitHub 项目：

1. 装到本机 agent 平台的 skill 安装目录（各平台路径不同，通常形如 `~/.<platform>/skills/`）
2. 镜像到 `${KB_ROOT}/resources/skills/<name>/`（整目录，不只 SKILL.md）
3. 在 `resources/skills/REGISTRY.json` 补 `purpose` / `source`
4. 在 `knowledge/00-资源库/关键资源库.md` 表格补一行
5. `python3 scripts/sync_resources.py sync && check` 校验三方一致

> 资源完整性规则细节见 `docs/01-知识库管理规范.md` 的「资源完整性」节。**只记录「笔记里提到过、但没装」的资源，等于没记录。**

### 第 7 步：云端备份（可选，按需）

如果配置了云端同步（如 IMA、网盘等）：

```bash
kb backup                                    # 生成同步导入包 + 列出待上传文件
python3 scripts/ima_sync.py status           # 增量比对：+ 新增 / ~ 变更 / = 未变
# 上传变更文件到云端（方式见 docs/05）
python3 scripts/ima_sync.py mark             # 上传成功后记录基线
```

> 云端备份的配置、同步规则、恢复方法见 `docs/05-云端备份与同步规范.md`。未配置云端同步时可跳过此步。

### 第 8 步：收尾

```bash
# 双层验证，缺一不可
kb check                                      # 一致性体检：A 索引 / B 分类 / C 新鲜度 / D 资源 / E 备份 —— 必须全绿
kb score                                      # 质量评分：采集/归档/总结/保留度四维 —— 目标 ≥ B 级

# 回写 profile 备份（保留登录态）
cd /tmp && tar --exclude='xhs-chrome/Singleton*' -czf ${KB_ROOT}/secrets/chrome-profile-backup.tar.gz xhs-chrome
```

## 输入输出标准（三层契约）

**输入**（点点导出文本）→ **中间产物**（raw 归档 + JSON 索引）→ **输出**（知识卡 + BM25 检索）。
每层的字段标准、合格线、验收命令详见 **`references/io-standards.md`**。要点速览：

- 视频笔记必须有非空「语音文案」（ASR 逐字稿），缺失即判采集不完整
- 知识卡五个必需段：标题 / 一句话核心(≥15字) / 核心要点(3-8条) / 行动项(≥1条) / 原文与溯源(含链接)
- raw 归档不可变；卡片、索引、检索输出都以 raw 为事实源

## 脚本工具清单

| 脚本 | 职责 | 常用命令 |
|---|---|---|
| `drive_diandian.mjs` | Chrome CDP 驱动点点采集 | `node drive_diandian.mjs --skip-select --batch 2 --total N --out <file>` |
| `xhs_parse.py` | 点点文本 → 结构化（被 xhs_store 调用） | — |
| `xhs_store.py` | 入库：解析+去重+raw 归档 | `import <txt> --source 备注` / `status` / `list` |
| `build_knowledge.py` | 分类仲裁 + 总览/分类页生成 | 直接运行 |
| `build_index.py` | BM25 索引构建 | 直接运行 |
| `kb.py` | 命令行总入口 | `kb 关键词` / `open N` / `ls` / `cats` / `stats` / `rebuild` / `check` / `score` |
| `search.py` | 检索验证 | `search.py 关键词` |
| `ai_context.py` | 给 AI 的上下文打包 | `manifest` / `"查询词" -n 5` / `--all` |
| `audit_kb.py` | 一致性体检（五段） | `--fix` 自动修 |
| `score_kb.py` | 质量评分（四维+历史） | `--detail` / `--no-log` |

可选组件（个人库增强，公开包默认不含）：`sync_resources.py`（skill 资源镜像）、`ima_sync.py`（云端备份）。缺失时 `kb res` / `kb backup` 会提示未安装，体检 D/E 段自动跳过。

## 质量验证与日志

**双层验证**：`kb check`（一致性，硬门槛，非零退出码）+ `kb score`（效果，软目标，四维评分与等级）。评分细则见 **`references/scoring.md`**。

**日志能力**（`logs/` 目录）：

| 文件 | 内容 | 用途 |
|---|---|---|
| `collect_YYYYMMDD.txt` | 当轮点点完整对话落盘 | 采集问题第一现场 |
| `score_history.jsonl` | 每次评分的时间戳/四维均分/等级/低分篇目/内容指纹 | 质量趋势，防"越采越糟" |

`kb score` 默认追加历史记录；每轮收尾的评分结果建议同步写入交接文档。

## 输出模板（发给点点的提示词骨架）

```
请把我已选中的这 N 篇笔记整理成结构化文档。不要合并，不要点评，不要略写，不要写开场白和结束语。
严格按模板输出，字段名一个字都不要改：
笔记 {序号}：{标题}
笔记 ID：{24位ID，拿不到写 unknown}
类型：视频 或 图文
正文：{原文完整保留}
视频要点：{分条列出；不是视频写"无"}
语音文案：{完整逐字稿；不是视频写"无"}
OCR 识别内容：{画面文字；没有写"无"}
知识标签：{#标签}
【重要】单条回复有长度上限，本次只输出笔记 X 到 Y，写到就停。
```

## 故障排查

| 症状 | 原因 | 处置 |
|---|---|---|
| 发送按钮点不到 | 2026-11 UI 改版，按钮 class 从 `.bottom-box-right-submit-button` 变为 `.wendian-btn--sendable` | 脚本已更新选择器；如再失效，用 CDP 列出所有 `button` 找含"问点点"的元素 |
| 回复只出 2 篇就停 | 单条回复长度上限 | 属正常，用「继续」接力 |
| 笔记 19+ 回复"没收到附件" | 点点上下文窗口溢出，导入超过约 18 篇后丢附件 | 每次最多导入 18 篇；超出的新建会话补采 |
| import 后笔记数不增加 | 点点输出 note_id 为 "unknown"，parser 正则匹配不到被跳过 | `xhs_store.py` 已修复：unknown 时用标题哈希生成 `gen_` 前缀后备 ID |
| `save_index` 报权限错误 | 沙箱禁止 `tmp.replace()` 原子替换 `~/Documents` 下文件 | 改用 `INDEX_PATH.write_text()` 直接覆写 |
| 读取正文只有侧边栏 | 用了 body 截断 | 改读 .scroll-container |
| 全选后数量不对 | 命中了侧边栏标签或残留勾选 | 限定在弹窗内找元素；先核对「已选中 N/M」再导入 |
| Chrome 起不来/崩溃 | 不在 /tmp、或残留 SingletonLock | 换 /tmp、删 Singleton*、确认 --no-sandbox |
| Chrome 启动后 6 秒内无响应 | 用 `&` 后台启动被 shell 回收 | 改用 `run_in_background: true` 保活 |
| 登录态丢失 | /tmp 被清理 | 从 secrets 备份还原；再丢则需手机扫码一次 |
| 导入重复笔记 | 未按 note_id/标题去重 | 走 xhs_store.py import，自动去重 |
| 分类名有空格导致审计报错 | taxonomy.json 的 `name` 含空格，与目录名不符 | `name` 不能含空格；`audit_kb.py --fix` 可自动修 |
| 收藏夹总数(M) < 已有数 | 失效笔记被删除但仍占名额 | M 恒等于已有数时无需采集 |

## 性能基准（实测）

- 采集 18 篇（含 9 轮「继续」接力）：约 12 分钟
- 解析+入库：秒级
- 单篇视频笔记语料量：正文 200-400 字、ASR 逐字稿 1500-2500 字、OCR 100-200 字
- IMA 云端同步 8 个文件（200KB）：约 2 分钟
