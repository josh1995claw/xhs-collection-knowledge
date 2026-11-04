# 小红书收藏夹知识库流水线

把小红书收藏夹批量整理成**可检索、可溯源、可评分**的本地知识库。

用 Chrome 自动化驱动小红书网页版 AI「点点」，把收藏的笔记（含视频）转成结构化全文——正文 + 语音逐字稿(ASR) + 画面文字(OCR)——再落盘归档、去重分类、生成知识卡、建 BM25 检索索引，最后做双层质量验证。

## 它解决什么问题

小红书收藏夹是「只存不看」的坟场：视频内容无法搜索、收藏越堆越多、想找某条只记得模糊印象。本项目把收藏变成能用关键词检索、能被 AI 读取、能追溯原文的本地资产。

## 特性

- **完整内容获取**：不只正文，还有视频 ASR 逐字稿与画面 OCR（点点提供）
- **增量采集**：按 note_id / 标题去重，重复跑只更新不重复入库
- **分类可维护**：分类架构存于 `index/taxonomy.json`，改分类不动文件路径
- **双层验证**：`kb check` 查一致性（索引/分类/新鲜度）、`kb score` 评效果（采集/归档/总结/保留度四维）
- **双读者层**：人读知识卡（要点+行动项）+ AI 读上下文包（带出处）
- **零第三方依赖**：Python 3 标准库 + Node.js 内置 WebSocket，无 numpy / 无 puppeteer

## 快速上手

```bash
# 1. 建库目录
mkdir -p ~/my-kb/{raw,index,cards,knowledge,logs,secrets}
cp -R scripts ~/my-kb/scripts

# 2. 准备 Chrome 登录态备份（登录小红书后拷贝 profile 并打包）
#    见 SKILL.md「第 1 步：启动浏览器」

# 3. 采集（Chrome 9222 调试端口启动后）
cd ~/my-kb/scripts
node drive_diandian.mjs --album ai --batch 2 --total 10 --out ../logs/collect_$(date +%Y%m%d).txt

# 4. 入库 → 分类 → 索引
python3 xhs_store.py import ../logs/collect_*.txt --source "首批"
python3 build_knowledge.py
python3 build_index.py

# 5. 写知识卡（AI 通读 raw/ 后按标准写卡到 cards/<分类>/）
#    卡格式见 references/io-standards.md

# 6. 验证
python3 kb.py check      # 一致性
python3 kb.py score      # 效果评分
```

## 目录结构

```
├── SKILL.md                     流程主指南（含关键约束、故障排查）
├── README.md                    本文件
├── references/
│   ├── io-standards.md          输入输出标准（点点模板字段 / raw / 卡片 / 索引）
│   └── scoring.md               质量评分与验证机制（四维细则 + 等级 + 日志）
└── scripts/
    ├── drive_diandian.mjs       Chrome CDP 驱动点点采集
    ├── xhs_parse.py             点点文本解析
    ├── xhs_store.py             入库（去重 + raw 归档）
    ├── build_knowledge.py       分类 + 总览/分类页
    ├── build_index.py           BM25 索引
    ├── search.py                检索验证
    ├── ai_context.py            给 AI 的上下文打包
    ├── kb.py                    命令行总入口
    ├── audit_kb.py              一致性体检（五段）
    └── score_kb.py              质量评分（四维 + 历史）
```

脚本自动以「脚本所在目录的上级」为知识库根目录，把 `scripts/` 放进 `<KB_ROOT>/scripts/` 即可。

## 关于知识库管理规范

本仓库只含**通用采集流水线**。分类架构、云同步、个人状态快照属于各使用者自己的知识库项目，放在其 `docs/` 目录，不混入本包。SKILL.md 的「适用范围与个人化边界」一节写明了对应关系。

## 依赖与限制

- macOS + Chrome（CDP 自动化按 macOS 路径写；其他系统需调整 Chrome 启动参数）
- Node.js ≥ 18、Python 3
- 点点单条回复上限约 2 篇；单次导入上限约 18 篇（上下文窗口），超出分批新建会话
- 小红书 UI 会改版，选择器失效时见 SKILL.md 故障排查表

## 克隆与双平台同步

本技能已发布到两个平台，可任选其一克隆：

```bash
# GitHub（主仓库）
git clone git@github.com:josh1995claw/xhs-collection-knowledge.git
# 或 Gitee（国内镜像）
git clone git@gitee.com:djhjoshuadu/xhs-collection-knowledge.git
```

克隆后得到完整技能包：`SKILL.md` / `README.md` / `references/` / `scripts/` / `LICENSE`。

### 双平台同步（开发者视角）

仓库已配置两个 remote：

| remote | 平台 | 地址 |
|---|---|---|
| `origin` | GitHub（主） | `git@github.com:josh1995claw/xhs-collection-knowledge.git` |
| `gitee` | Gitee（镜像） | `git@gitee.com:djhjoshuadu/xhs-collection-knowledge.git` |

日常推送：

```bash
git push origin main   # 推 GitHub（主）
git push gitee main    # 推 Gitee（镜像）
```

> 使用 SSH 需将本机公钥（`~/.ssh/id_ed25519_eink.pub`）分别加入 GitHub 与 Gitee 的 SSH 设置；本机 `~/.ssh/config` 已将 `github.com` 与 `gitee.com` 映射到该密钥。

## License

MIT License — 见 [LICENSE](./LICENSE)。
