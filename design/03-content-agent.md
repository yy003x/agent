# 内容生成 Agent 应用层设计

本文描述在通用框架（01-framework.md）之上的具体应用：**学而思教育类图书内容生成 Agent**。
涵盖：路由各分类的完整行为、知识库层、content-runtime、内容组装、平台规格。

---

## 路由分类完整行为

`rules/core-routing.md` 定义了 5 个分类。下表描述每类的**完整执行行为**（AI 构建时按此实现）：

### 分类 1：闲聊 / 寒暄
**触发词**：问候、感谢、随便聊、今天怎么样、你好等
**执行行为**：直接回答，不写文件，不触发 finalize，不调 KB
**示例**：「你好」「谢谢」「今天学了什么？」

### 分类 2：一次性问答
**触发词**：单一问题、概念解释、状态查询（「XX 是什么」「现在是 P 几阶段」）
**执行行为**：
1. 优先从 memory/summary.md 和已加载 rules 中回答
2. 需要查 KB 时：`content_runtime.py kb search --query "<问题关键词>" --modality doc --topk 3`，回读命中文档后回答
3. 不写文件，不触发 finalize
**区分说明**：「搜索」分类是「帮我找 X」（用户要素材/资料），一次性问答是「解释 X」

### 分类 3：搜索 / 调研
**触发词**：「帮我找」「查一下」「有哪些」「调研」「搜一下」
**执行行为**：
1. 解析搜索意图：主题、modality 偏好（文档/图片/视频）、数量要求
2. `content_runtime.py kb search --query "<主题>" --modality <auto> --topk 10 --json`
3. 回读前 3-5 条命中的 source_path 原文件
4. 整理成列表回答，附 source_path 和相关度说明
5. 结论落 `outputs/YYYY-MM-DD/research/` 下（.md 格式，仅在用户要求「保存结果」时写入）
6. 不触发 finalize（除非写了文件）

### 分类 4：设计 / 方案 / 规划
**触发词**：「怎么设计」「架构」「方案」「计划」「PRD」「规划」
**执行行为**：
1. 先讨论澄清关键决策点（目标/约束/方案对比）
2. 用户确认方向后，产出设计文档到 `outputs/YYYY-MM-DD/design/<title>.md`
3. 写文件前展示拟写内容，等用户确认
4. 触发 finalize

### 分类 5：内容生成（触发 content-generate skill）
**触发词**：「出一篇」「生成内容」「做个图文」「写小红书」「朋友圈文案」「书单」「读书笔记」「知识卡片」「读后感」「书评」「推荐语」「配图」「短视频」「视频」
**执行行为**：触发 `content-generate` skill（详见下文 Skill 设计和 `templates/skills/content-generate/SKILL.md`）

**分类 6（兜底）：执行 / 代码 / 任务**
**触发词**：「实现」「写代码」「修 bug」「写脚本」「提交」「改配置」
**执行行为**：
1. 分析任务，制定执行计划
2. 执行代码/文件变更
3. 验证（测试/启动确认）
4. `git add <files> && git commit -m "<message>"`（用户确认后）
5. 触发 finalize

---

## 知识库层（KB Layer）

> **KB 已抽为独立模块，完整设计见 [04-knowledge-base.md](04-knowledge-base.md)。**
> 技术栈：**LanceDB**（向量 + 标量 + FTS 同库）+ **BAAI/bge-small-zh-v1.5** 向量
> + **jieba** 中文分词 FTS + **RRF hybrid** 融合 + **BAAI/bge-reranker-base** 精排。
> 本节仅保留与应用层对接的要点；表结构与检索管线详见 04。

### 存储结构

```
workspace/kb/
├── lance/            ← LanceDB 数据目录（items / concepts / graph_edges 表）
├── graph.jsonl       ← document-concept 二部图（规则引擎输出，确定性可重建事实源）
├── search-log.jsonl  ← 检索日志（自我进化事实源之一）
└── caption-cache.json← 图片/帧 caption 缓存（按 file_hash，避免换模型重 ingest 时重复计费）
```

- `items` 表：标量元数据 + `vector`(512d, cosine) + jieba 预分词列 `text_seg`（FTS 建于此列）。
- `concepts` + `graph_edges` 表：**document-concept 二部图**（规则引擎确定性生成，事实源 `graph.jsonl`），作为检索第三路召回与关联素材扩散。详见 04。
- 旧栈的 `catalog.db`（SQLite）与 `vector/`（ChromaDB）已废弃，统一到 `lance/`。

### 检索接口（对上层透明）

`kb search` 内部走「向量召回 ∥ jieba-FTS 召回 → RRF 融合 → reranker 精排 → topk」，
返回 `[{id, modality, source_path, title, caption, score}]`。上层只管回读 `source_path` 原文件。

---

## 多模态 Ingestion 流程

> 注：以下伪代码中的 `catalog_db / chroma_collection / write_vector` 为旧栈示意；新栈下统一为
> 「向 LanceDB `items` 表写一条含 `vector`(bge-small-zh-v1.5) + `text_seg`(jieba) 的记录」，
> 落库与索引细节见 [04-knowledge-base.md](04-knowledge-base.md) §6。**解析 / 抽帧 / caption 流程不变。**

### 文档（.md / .txt / .pdf）

```python
def ingest_doc(path, catalog_db, chroma_collection):
    text = parse_text(path)              # pdfplumber / open().read()
    chunks = chunk_text(text, size=800)  # 按 token 切块，相邻块 100 token 重叠
    for i, chunk in enumerate(chunks):
        item_id = make_id(path, i)
        tags = extract_tags(path)        # 从路径推断：书名/目录层级
        caption = chunk[:200]            # 文档 caption = 首 200 字
        write_catalog(item_id, "doc", path, tags=tags, caption=caption, chunk_index=i)
        write_fts(item_id, title=Path(path).stem, tags=tags, caption=caption)
        write_vector(item_id, text=f"{Path(path).stem} {caption}")
```

### 图片（.jpg / .png / .webp）

```python
def ingest_image(path, catalog_db, chroma_collection, claude_client):
    # 1. Claude vision 生成 caption
    with open(path, "rb") as f:
        img_b64 = base64.b64encode(f.read()).decode()
    response = claude_client.messages.create(
        model="claude-opus-4-8",
        max_tokens=300,
        messages=[{
            "role": "user",
            "content": [
                {"type": "image", "source": {"type": "base64", "media_type": "image/jpeg", "data": img_b64}},
                {"type": "text", "text": (
                    "描述这张图片的主题、视觉风格、构图要素，以及与教育/图书内容的关联。"
                    "100字以内。最后另起一行列出5个精确标签，格式：标签：tag1,tag2,tag3,tag4,tag5"
                )}
            ]
        }]
    )
    caption_full = response.content[0].text
    caption, tags_str = parse_caption_and_tags(caption_full)
    tags = tags_str.split(",")

    # 2. 复制原图到 media-store
    dest = f"workspace/media-store/{make_hash(path)}{Path(path).suffix}"
    shutil.copy2(path, dest)

    # 3. 写入四索引
    item_id = make_id(path, 0)
    write_catalog(item_id, "image", dest, tags=tags, caption=caption, width=w, height=h)
    write_fts(item_id, title=Path(path).stem, tags=tags, caption=caption)
    write_vector(item_id, text=f"{Path(path).stem} {caption} {' '.join(tags)}")
    # 图谱不在此增量写；ingest 末尾由规则引擎统一重建 concepts/graph_edges（见 04 §图谱构建）
```

### 视频（.mp4 / .mov）

```python
def ingest_video(path, catalog_db, chroma_collection, claude_client):
    # 1. ffmpeg 抽关键帧（每 30 秒一帧，最多 10 帧）
    frames = extract_frames(path, interval=30, max_count=10)  # -> list[tmp_image_path]

    # 2. 每帧 Claude vision caption，拼接为 transcript
    frame_captions = [caption_image(frame, claude_client) for frame in frames]
    transcript = "\n".join(f"[{i*30}s] {cap}" for i, cap in enumerate(frame_captions))

    # 3. 探测时长/分辨率
    duration, width, height = probe_video(path)   # ffprobe

    # 4. 复制原片到 media-store
    dest = f"workspace/media-store/{make_hash(path)}{Path(path).suffix}"
    shutil.copy2(path, dest)

    # 5. 写入 items（标量 + 向量 + text_seg；transcript = 帧 caption 拼接）
    item_id = make_id(path, 0)
    tags = extract_tags(path)
    caption = frame_captions[0] if frame_captions else ""
    write_catalog(item_id, "video", dest, tags=tags, caption=caption,
                  transcript=transcript, duration_s=duration, width=width, height=height)
    write_fts(item_id, title=Path(path).stem, tags=tags, caption=caption, transcript=transcript)
    write_vector(item_id, text=f"{Path(path).stem} {caption} {transcript[:500]}")
    # 图谱由规则引擎在 ingest 末尾统一重建（见 04 §图谱构建）
```

### Ingestion 通用约定
- 新增/变更检测：`file_hash` 与 catalog 比对，已处理跳过（断点续跑支持）
- 处理顺序：当前串行处理（图片并发为后续优化）
- Claude vision 调用限流：每分钟最多 10 次（避免 API rate limit）
- `--limit N` 参数：单次最多处理 N 个文件（scheduler 用，避免长时间阻塞）

---

## content-runtime CLI 完整接口

实现文件：`skills/content-generate/scripts/content_runtime.py`

```
用法：python skills/content-generate/scripts/content_runtime.py <domain> <action> [options]

KB 域：
  kb ingest   --src <folder> [--modality auto|doc|image|video] [--limit N] [--resume] --allow-write
  kb search   --query "<text>" [--modality doc|image|video|all] [--topk N] [--json] [--no-log] [--no-touch]
  kb index    --rebuild [fts|vector|graph|all] --allow-write
  kb gc       --older-than 180d [--dry-run] --allow-write
  kb legacy   [--json] [--allow-write]          # 检查/清理空旧栈残留 catalog.db / vector/
  kb related  --id <doc_id> [--topk N] [--json]      # 二部图扩散：取同 concept 的兄弟 doc

文案域：
  text draft  --brief "<需求摘要>" --platform xiaohongshu|moments|wechat_group
              [--style <风格>] [--sources <sources.json>] [--out <draft.json>] [--allow-write]

计划域：
  plan build  --draft <draft.json> [--sources <sources.json>] [--platform xiaohongshu|moments|wechat_group]
              --out <plan.json> [--allow-write]

媒体域：
  media probe  <file>
  media assemble --spec <plan.json> --out <dir> --allow-write

发布域：
  publish package --platform xiaohongshu|moments|wechat_group --in <dir> --allow-write

初始化：
  init        初始化 LanceDB 库（items + concepts + graph_edges 表）（首次使用时运行）
```

**写门禁**：`kb ingest`、`kb index`、`kb gc`、`kb legacy`、`text draft --out`、`plan build`、`media assemble`、`publish package` 必须带 `--allow-write` 参数，否则只做 dry-run / stdout 预览并提示。
`kb search` 默认会写 `search-log.jsonl` 和 `last_hit_at`，用于自学习与清理；严格只读时加 `--no-log --no-touch`。
写操作会标记 `workspace/.finalize-activity.json`，供 Stop hook 兜底记录 ignored 运行产物。
`kb ingest` 若任一文件失败返回非 0；需要容忍部分失败时由调用方显式处理。
`kb legacy --allow-write` 只删除空的 `catalog.db` / 空 `vector/`；非空旧栈残留只报告，需另走迁移脚本。

---

## content-generate Skill 执行流程

（完整 SKILL.md 见 `templates/skills/content-generate/SKILL.md`，以下为设计摘要）

```
Step 1  解析需求
  提取：主题 / 目标平台（小红书|朋友圈|家长群|通用）/ 形态（图文|短视频|话术|组合）
       风格（知识科普|情感共鸣|书单推荐|读书笔记）/ 数量

Step 2  检索素材
  python content_runtime.py kb search \
    --query "<主题>" --modality all --topk 10 --json
  graph 扩散：content_runtime.py kb related --id <命中 doc_id> --json（经二部图取同 concept 的兄弟素材）
  人工筛选确认：展示 topk 候选（title + caption 摘要），询问「使用哪几条？」

Step 3  回读事实源
  对用户确认的 item，读取 source_path 原文件内容
  （索引是候选，原文件是事实源，必须回读）

Step 4  生成文案
  python content_runtime.py text draft \
    --brief "<需求摘要>" --platform xiaohongshu --style "<风格>" \
    --sources outputs/YYYY-MM-DD/content/<slug>/sources.json \
    --out outputs/YYYY-MM-DD/content/<slug>/draft.json \
    --allow-write
  AI 可在 draft.json 基础上 inline 润色，但必须回读素材事实，不编造。

Step 5  生成 plan
  python content_runtime.py plan build \
    --draft outputs/YYYY-MM-DD/content/<slug>/draft.json \
    --out outputs/YYYY-MM-DD/content/<slug>/plan.json \
    --allow-write

Step 6  组装
  python content_runtime.py media assemble \
    --spec plan.json --out outputs/YYYY-MM-DD/content/<slug>/ --allow-write

Step 7  打包
  python content_runtime.py publish package \
    --platform xiaohongshu --in outputs/YYYY-MM-DD/content/<slug>/ --allow-write

Step 8  预览确认
  列出成品包文件树 + 文案全文
  等用户说「发布」后才进行（手动）发布，AI 不自动发帖
  
Step 9  收尾
  python scripts/finalize.py record --skill content-generate --status success
```

---

## 平台文案 Prompt 模板

### 小红书图文
```
角色：你是一个专注于教育类图书推荐的小红书博主，语气活泼、知识扎实。
任务：基于以下素材，生成一篇小红书图文笔记。

素材：
<命中的书名 / caption / 核心内容摘要>

要求：
- 标题：20字以内，有钩子（疑问/数字/情绪），不用「最」「第一」等极限词
- 正文：300-500字，分段，每段 2-4 行，自然穿插 emoji（不过度）
- 末尾：3-5 个话题标签（格式：#XX #YY）
- 风格：<用户指定风格，如「知识科普」「情感共鸣」>
- 内容侧重：<用户指定侧重，如「数学思维方法」>

输出格式：
---标题---
[标题]
---正文---
[正文]
---标签---
[标签]
---配图建议---
[从素材图片中建议使用哪 1-3 张，描述选择理由]
```

### 朋友圈
```
角色：你是一个爱读书、有品位的朋友圈博主，语气真诚、有感染力。
任务：基于以下内容，生成一条朋友圈文案。

素材：
<命中内容摘要>

要求：
- 长度：80-150字（手机屏幕一屏内）
- 不加话题标签
- 以个人感悟切入，有分享欲
- 结尾可以留一个引发互动的问句（可选）
- 配图：建议从素材中选 1-3 张（描述理由）
```

---

## 内容组装规格

### plan.json 格式（media assemble 的输入）

```json
{
  "type": "xiaohongshu",
  "cover": {
    "src": "workspace/media-store/<hash>.jpg",
    "crop": [0, 0, 1080, 1080]
  },
  "images": [
    {"src": "workspace/media-store/<hash>.jpg", "resize": [1080, 1080]},
    {"src": "workspace/media-store/<hash>.png", "resize": [1080, 1440]}
  ],
  "clips": [
    {"src": "workspace/media-store/<hash>.mp4", "start": 10.5, "end": 35.0, "subtitle": "可选字幕文本"}
  ],
  "body_text": "正文文案（用于写入 publish-checklist.md，不叠加到图片上）"
}
```

### media assemble 实现要点

```python
# 图文组装（Pillow）
def assemble_images(plan, out_dir):
    for i, img_spec in enumerate(plan["images"]):
        img = Image.open(img_spec["src"])
        if "crop" in img_spec:
            img = img.crop(img_spec["crop"])
        img = img.resize(img_spec["resize"], Image.LANCZOS)
        img.save(f"{out_dir}/img_{i:02d}.jpg", quality=90)

# 视频裁剪 + 可选字幕（ffmpeg）；正文文案仍放 publish-checklist.md
def assemble_clip(clip_spec, out_path):
    cmd = [
        "ffmpeg", "-i", clip_spec["src"],
        "-ss", str(clip_spec["start"]),
        "-t", str(clip_spec["end"] - clip_spec["start"]),
    ]
    if clip_spec.get("subtitle"):
        srt_path = write_srt(clip_spec["subtitle"])
        cmd += ["-vf", f"subtitles={srt_path}"]
    cmd += ["-c:v", "libx264", "-crf", "23", out_path]
    subprocess.run(cmd, check=True)
```

### publish package 产出结构

```
outputs/YYYY-MM-DD/content/<slug>/
├── xiaohongshu/
│   ├── cover.jpg             ← 封面（1:1 或 3:4）
│   ├── img_01.jpg            ← 配图 1
│   ├── img_02.jpg            ← 配图 2
│   ├── clips/                ← 短视频素材（如有）
│   └── publish-checklist.md  ← 发布清单（标题+正文+标签+素材顺序）
└── moments/
    ├── img_01.jpg
    └── publish-checklist.md  ← 文案+配图
```

**`publish-checklist.md` 格式**：

```markdown
# 发布清单 - <内容标题>

## 平台
小红书

## 标题
[标题内容]

## 正文
[正文全文]

## 素材顺序
1. cover.jpg — [描述]
2. img_01.jpg — [描述]
3. clips/clip_00.mp4 — [描述]

## 话题标签
#数学思维 #图书推荐 #亲子教育

## 素材来源
- img_01.jpg 来自：workspace/media-store/<hash>.jpg（<书名>配图）

## 状态
待发布
```

---

## 平台规格

### 小红书
- 封面：1:1（1080×1080）或 3:4（1080×1440）
- 正文：不超过 1000 字（建议 300-500）
- 图片：最多 9 张，建议 3-6 张
- 标签：3-5 个（#开头）
- 禁用词：极限词（最/第一/唯一）、绝对化表达

### 朋友圈
- 文案：建议 80-150 字
- 图片：1-9 张，建议 1-3 张
- 无话题标签

---

## KB 清理规格

```bash
# 查看候选（dry-run）
python content_runtime.py kb gc --older-than 180d --dry-run

# 输出示例：
# 待归档：47 条（last_hit_at 或无命中时 ingest_at < 2025-12-17）
# 待删除（已归档 90 天）：12 条
# 预计释放：2.3 GB

# 确认执行
python content_runtime.py kb gc --older-than 180d --allow-write
```

清理策略：
1. `last_hit_at < 180天前`；或从未命中且 `ingest_at < 180天前` → status 改为 `archived`，不删物理文件
2. `status=archived 且 archived_at < 90天前` → 删 media-store 副本（不删用户原始文件夹），LanceDB `items` 表 status 改 `deleted`
3. 从 LanceDB `items` 表 `delete` 该记录（向量随行删除）
4. 不清理 `status=active` 的条目，不动 `workspace/media-inbox/`（用户放置的待 ingest 文件）
