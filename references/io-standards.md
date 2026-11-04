# 输入输出标准

本流水线的三层契约：**输入**（点点导出文本）→ **中间产物**（raw 归档 + 索引）→ **输出**（知识卡 + 检索）。

## 一、输入标准（点点导出文本）

每篇笔记必须是如下结构（发送给点点的模板决定了这一格式，解析器 `xhs_parse.py` 按此解析）：

```
笔记 {序号}：{标题}
笔记 ID：{24位 hex；拿不到写 unknown}
类型：视频 或 图文
正文：{原文完整保留}
视频要点：{分条列出；不是视频写"无"}
语音文案：{完整逐字稿；不是视频写"无"}
OCR 识别内容：{画面文字；没有写"无"}
知识标签：{#标签 空格分隔}
```

**解析容错**（已处理的实际变体）：
- 单条回复内联单行格式（点点有时把整篇压成一行）→ 整块并入块头字段
- 字段名括号全角/半角混用（「语音文案（ASR）」/「语音文案(ASR)」）→ 归一化
- note_id 为 "unknown" → 用标题 MD5 前 20 位生成 `gen_` 前缀后备 ID

**合格线**：视频笔记必须有非空「语音文案」；缺失即判采集不完整（`kb score` 的采集维度会显著扣分）。

## 二、中间产物标准

### raw/<note_id>-<标题>.md（原始归档，不可变）

YAML frontmatter + 正文：

| 字段 | 类型 | 说明 |
|---|---|---|
| `note_id` | str | 主键。真实 24 位 hex，或 `gen_` 前缀后备哈希 |
| `seq` | int | 采集时序号（仅参考，不是断点） |
| `title` | str | 笔记标题 |
| `tags` | list | 知识标签 |
| `note_type` | video/image | 笔记类型 |
| `url` | str | 原笔记链接（unknown 时为 explore/<后备ID> 占位） |
| `source` | str | 来源批次备注 |
| `imported_at` | datetime | 入库时间 |
| `char_body / char_asr / char_ocr` | int | 三类内容字数（评分依据） |

### index/notes.json（主索引）

`notes.<note_id>` 的字段：`seq / title / category / category_name / note_type / url / source / imported_at / body_len / asr_len / ocr_len / raw_path / card_path`。

### index/taxonomy.json（分类架构，唯一机读源）

`categories[].{code, name, dir, keywords}`。**name 不能含空格**，必须等于目录名中 `-` 后的部分。

## 三、输出标准

### cards/<分类>/<note_id>-<标题>.md（知识卡）

frontmatter（`note_id / category / score / collected / source`）+ 五个必需段：

| 段 | 要求 | 可读性权重 |
|---|---|---|
| `# 标题` | 中文、有信息量 | — |
| `## 一句话核心` | ≥15 字，真正概括核心价值 | 0.25 |
| `## 核心要点` | **3-8 条**编号列表，通读后提炼（非摘抄） | 0.35 |
| `## 行动项` | ≥1 条具体可执行 | 0.20 |
| `## 原文与溯源` | 含原链接、note_id、raw 路径 | 0.20 |

推荐附加段：`## 相关资源`（外部链接）。

### 检索输出（kb / ai_context.py）

- `kb 关键词`：序号/标题/分类/相关度/命中片段/卡片路径，供人用
- `ai_context.py "关键词" -n 5`：带出处的完整正文块（Markdown），供 AI 用
- 两者共享同一 BM25 索引，结果可互相验证

## 四、验收（每次采集后）

```bash
kb check    # 一致性：A 索引 / B 分类 / C 新鲜度 / D 资源 / E 备份 —— 必须全绿
kb score    # 效果：四维评分 + 等级 —— 目标 ≥ B；C/D 级笔记要补 ASR 或改卡片
```
