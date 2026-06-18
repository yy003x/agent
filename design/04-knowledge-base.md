# 知识库层设计（独立模块）

> 本文把「知识库（KB）」从应用层（03-content-agent.md）中抽出，作为**可独立构建、独立验证**的模块。
> content-runtime 的 `kb` 子命令是 KB 的唯一对外接口；其余层（路由/skill/finalize/自学习）只通过 `kb search` 消费 KB，不关心其内部实现。

读完本文你应能回答：素材如何入库？检索如何做到「向量召回 + 中文分词 FTS + 融合 + 精排」？M1Pro 16G 如何承载？

---

## 1. 定位与边界

```
素材文件夹（用户指定）
      │  kb ingest
      ▼
┌──────────────── KB 模块（本文） ────────────────┐
│  LanceDB 单库                                    │
│    items 表：标量元数据 + 向量列 + 分词列        │
│    edges 表：graph 关系（same_book/same_tag）    │
│  检索管线：向量召回 ∥ jieba-FTS 召回             │
│            → RRF 融合 → reranker 精排            │
└──────────────────────────────────────────────────┘
      │  kb search（返回 topk，附 source_path）
      ▼
content-generate skill / 路由问答（回读原文件）
```

**职责**：素材入库（ingest）、混合检索（search）、索引维护（index）、清理（gc）。
**不负责**：文案生成、媒体组装、发布——那些在 content-generate skill 与 media/publish 域。
**唯一事实源约定**：索引返回的是**候选**，使用方必须回读 `source_path` 原文件作为事实源。

---

## 2. 技术栈选型（替代旧 SQLite+ChromaDB 方案）

| 环节 | 选择 | 说明 |
|---|---|---|
| 向量库 + 标量存储 | **LanceDB** | 嵌入式（无服务进程）、列式（Lance 格式）、Rust 实现、对 Apple Silicon 友好；向量 + 标量 + FTS 同库 |
| 向量模型 | **BAAI/bge-small-zh-v1.5** | 中文专用，512 维，~95MB；M1 上 MPS/CPU 可跑 |
| 中文分词 | **jieba**（精确模式） | 为 FTS 列与 query 做分词；解决「中文无空格」无法直接全文检索的问题 |
| 全文检索 | **LanceDB FTS（Tantivy）** | 建在 jieba 预分词列 `text_seg` 上，等价于「中文分词 FTS」 |
| 融合 | **RRF（Reciprocal Rank Fusion）** | 向量排名与 FTS 排名按 `1/(k+rank)` 融合，无需训练、对分数尺度不敏感 |
| 精排 | **BAAI/bge-reranker-base**（cross-encoder） | ~1.1GB；对融合后的候选做 query-doc 相关性精排 |

**为什么这套更适配 M1Pro 16G**：bge-small-zh-v1.5 比 bge-m3 小一个数量级；LanceDB 无常驻进程、按需 mmap；reranker 仅对 ~20 个候选推理。整体内存峰值可控（见 §9）。

> `text_seg` 方案要点：jieba 不是 LanceDB 内置 tokenizer。做法是 **Python 端用 jieba 把可检索文本切词、空格连接后写入 `text_seg` 列**，对该列建 FTS（默认按空格切分）。检索时对 query 同样 jieba 分词。这样不依赖 LanceDB 的中文 tokenizer 支持，完全可控。

---

## 3. 存储结构

```
workspace/kb/
├── lance/                 ← LanceDB 数据目录（items.lance / edges.lance）
├── search-log.jsonl       ← 检索日志（自我进化事实源，见 02）
└── models/                ← （可选）本地缓存的模型权重
workspace/media-store/     ← ingest 复制进来的媒体原件副本（不进 Git）
```

旧栈的 `catalog.db`（SQLite）与 `vector/`（ChromaDB）**已废弃**，统一到 `lance/`。

---

## 4. LanceDB 表结构

### items 表

| 字段 | 类型 | 说明 |
|---|---|---|
| `id` | string | 主键：`sha256(source_path + mtime + chunk_index)[:16]` |
| `modality` | string | `doc` / `image` / `video` |
| `source_path` | string | 原文件路径（media-store 副本或文档目录） |
| `title` | string | 书名/文件名/标题 |
| `tags` | string | JSON array 字符串：`["数学","思维导图"]` |
| `caption` | string | 图片/视频的 Claude vision 描述；文档为首 200 字 |
| `transcript` | string | 视频帧 caption + 字幕（可选） |
| `text_seg` | string | **jieba 分词后**的检索文本（title+tags+caption+transcript） |
| `vector` | vector(512) | bge-small-zh-v1.5 向量（已归一化） |
| `chunk_index` | int | 文档 chunk 序号（非文档为 0） |
| `duration_s` | float | 视频时长 |
| `width` / `height` | int | 媒体分辨率 |
| `file_hash` | string | 源文件 sha256（增量/断点续跑判重） |
| `ingest_at` / `last_hit_at` / `archived_at` | string | ISO8601 时间戳 |
| `status` | string | `active` / `archived` / `deleted` |

向量距离：**cosine**（向量已 L2 归一化）。

### edges 表（graph，最小化）

| 字段 | 类型 | 说明 |
|---|---|---|
| `src` | string | item id |
| `dst` | string | item id |
| `rel` | string | `same_book` / `same_tag` / `same_source` / `cited_by` |

graph 用于检索后扩散：对命中 item 查 `same_book/same_tag` 取关联素材（content-generate 步骤 2）。

---

## 5. 向量与分词构造规则

```python
# 文档侧（入库）：向量输入不加指令
embed_text = f"{title} {caption} {transcript}"[:2000]
vector = embedder.encode(embed_text, normalize_embeddings=True)   # bge-small-zh-v1.5

# 查询侧（检索）：bge-zh-v1.5 短查询建议加检索指令，提升召回
Q_INSTRUCTION = "为这个句子生成表示以用于检索相关文章："
q_vector = embedder.encode(Q_INSTRUCTION + query, normalize_embeddings=True)

# FTS 列：jieba 精确模式分词后空格连接
text_seg = " ".join(jieba.cut(f"{title} {' '.join(tags)} {caption} {transcript}"))
# 检索时 query 同样分词
q_seg = " ".join(jieba.cut(query))
```

- 模型用 sentence-transformers 加载，`device` 优先 `mps`，回退 `cpu`（见 §9）。
- 全局单例懒加载，避免每次 ingest/search 重载模型。

---

## 6. Ingestion（落库差异）

多模态解析流程（文档分块 / 图片 Claude vision caption / 视频抽帧 caption + 可选 whisper）**与 03-content-agent.md 一致**，本文只描述「落库」差异：

每条 item 入库时写 **3 件事**（同一张 LanceDB 表，一次 `add`/`merge_insert`）：

1. **标量字段** —— modality/title/tags/caption/transcript/元数据
2. **向量列** `vector` —— `embedder.encode(title+caption+transcript)`
3. **分词列** `text_seg` —— `jieba.cut(...)` 结果

并写 **edges 表**：同 `title` → `same_book`；同 `tag` → `same_tag`。

通用约定（不变）：
- 增量/断点续跑：按 `file_hash` 比对，已处理跳过（`--resume`）。
- 并发：图片 ≤3，视频 1（M1 本地友好）。
- Claude vision 限流：每分钟 ≤10 次。
- `--limit N`：单次最多处理 N 个文件（scheduler 后台用）。
- ingest 后需 `create_fts_index("text_seg", replace=True)`（首次或新增后重建 FTS）。

---

## 7. 检索管线（hybrid + rerank）

`kb search --query "<text>" [--modality doc|image|video|all] [--topk N]`

```python
def search(query, modality="all", topk=10):
    flt = None if modality == "all" else f"modality = '{modality}'"
    flt = f"({flt}) AND status = 'active'" if flt else "status = 'active'"

    # ① 向量召回
    qv = embed_query(query)                       # bge-small-zh-v1.5 + 检索指令
    vec_hits = (tbl.search(qv).metric("cosine")
                   .where(flt).limit(VEC_TOPN).to_list())     # VEC_TOPN=30

    # ② jieba-FTS 召回
    q_seg = " ".join(jieba.cut(query))
    fts_hits = (tbl.search(q_seg, query_type="fts")
                   .where(flt).limit(FTS_TOPN).to_list())     # FTS_TOPN=30

    # ③ RRF 融合（按 id 去重，rank 从 1 起）
    fused = rrf_merge(vec_hits, fts_hits, k=RRF_K)[:FUSE_TOPM]  # RRF_K=60, FUSE_TOPM=20

    # ④ cross-encoder 精排
    pairs = [(query, doc_text_for_rerank(d)) for d in fused]    # 用 caption / chunk 原文
    scores = reranker.predict(pairs)              # bge-reranker-base
    ranked = [d for _, d in sorted(zip(scores, fused), key=lambda x: x[0], reverse=True)]
    return ranked[:topk]                          # rerank_topk=10
```

```python
def rrf_merge(list_a, list_b, k=60):
    score = {}
    for lst in (list_a, list_b):
        for rank, d in enumerate(lst, start=1):
            score[d["id"]] = score.get(d["id"], 0) + 1 / (k + rank)
    # 取并集去重，按融合分排序，返回 doc 对象
    ...
```

### 参数表（默认值，可按效果调）

| 参数 | 默认 | 含义 |
|---|---|---|
| `VEC_TOPN` | 30 | 向量召回数 |
| `FTS_TOPN` | 30 | FTS 召回数 |
| `RRF_K` | 60 | RRF 平滑常数 |
| `FUSE_TOPM` | 20 | 融合后送 rerank 的候选数 |
| `rerank_topk` | 10 | 精排后最终返回数 |

### 降级策略

- FTS index 未建 / jieba 缺失 → 跳过 ②，仅向量召回。
- reranker 未安装 → 跳过 ④，返回 RRF 融合结果（记日志告警）。
- 向量库为空 → 返回空并提示先 `kb ingest`。

### 检索日志（写 search-log.jsonl）

```json
{"ts":"2026-06-18T14:30:00+08:00","query":"数学思维","modality":"all",
 "topk":10,"hits":8,"hit_ids":["abc123","def456"],
 "vec_n":30,"fts_n":27,"reranked":true}
```

---

## 8. 索引维护与清理

### `kb index --rebuild [fts|vector|graph|all]`
- `fts`：`tbl.create_fts_index("text_seg", replace=True)`
- `vector`：重算 `vector`/`text_seg` 列并 `merge_insert`（换模型/补字段时用）
- `graph`：清空 edges 表，按 title/tags 重建
- `all`：以上全部

### `kb gc --older-than 180d [--dry-run]`
1. `last_hit_at < cutoff` 且 `status=active` → `status=archived`（LanceDB update）
2. `status=archived` 且 `archived_at < 90 天前` → 删 media-store 副本（不动用户原始文件）+ `status=deleted` + 从表中 `delete`
3. 不动 `media-inbox/`；`--dry-run` 只统计不写。

---

## 9. 性能与内存预算（macOS M1Pro 16G）

| 组件 | 常驻内存 | 备注 |
|---|---|---|
| bge-small-zh-v1.5 | ~0.1 GB | 512 维，编码快 |
| bge-reranker-base | ~1.1 GB | 仅 rerank 时推理，对 ~20 候选打分 |
| LanceDB（千级） | < 0.5 GB | mmap，按需加载 |
| jieba 词典 | ~0.05 GB | 首次加载 |
| **合计峰值** | **~2 GB** | 16G 宽裕，可与 Claude Code 同跑 |

**设备选择**：`device = "mps" if torch.backends.mps.is_available() else "cpu"`。
首次运行会从 HuggingFace 下载权重（bge-small-zh ~95MB、reranker-base ~1.1GB），可设 `HF_ENDPOINT` 镜像加速。

**调优建议**：
- 千级规模可不建 ANN 索引（暴力 cosine 足够快）；万级以上再 `create_index`（IVF_PQ）。
- ingest 批量编码（batch encode）比逐条快数倍。
- reranker 是延迟主因，`FUSE_TOPM` 控制在 20 左右即可平衡质量/速度。

---

## 10. 与 content-runtime 的对接

KB 模块对外**只暴露 `kb` 子命令**，签名不变（写操作仍需 `--allow-write`）：

```
content-runtime init                  # 建 LanceDB 库 + 表 + FTS index
content-runtime kb ingest   --src <folder> [--modality ...] [--limit N] [--resume] --allow-write
content-runtime kb search   --query "<text>" [--modality ...] [--topk N] [--json]
content-runtime kb index    --rebuild [fts|vector|graph|all] --allow-write
content-runtime kb gc       --older-than 180d [--dry-run] --allow-write
```

上层（路由问答、content-generate skill）调用方式不变——**KB 内部从 ChromaDB 换到 LanceDB 对上层透明**。

---

## 11. 从旧栈迁移

| 旧（已废弃） | 新 | 迁移动作 |
|---|---|---|
| SQLite `catalog.db` | LanceDB `items` 表 | 重新 `kb ingest`（推荐）或写一次性导出脚本 |
| ChromaDB `vector/` | LanceDB `vector` 列 | 同上，向量由 bge-small-zh-v1.5 重算 |
| FTS5 trigram | jieba + LanceDB FTS | `text_seg` 列 + `create_fts_index` |
| bge-m3 | bge-small-zh-v1.5 | `EMBED_MODEL` 环境变量切换 |
| 无 reranker | bge-reranker-base | `RERANK_MODEL` 环境变量配置 |

千级素材直接重跑 ingest 最简单（图片 caption 已是 Claude 调用，重跑成本主要在 vision API；如已 ingest 过，可缓存 caption 避免重复调用——见 content_runtime 实现）。

---

## 验证（P1 KB 独立验收）

```bash
python skills/content-generate/scripts/content_runtime.py init
python skills/content-generate/scripts/content_runtime.py kb ingest \
  --src ./test-data --modality doc --limit 5 --allow-write
python skills/content-generate/scripts/content_runtime.py kb index --rebuild fts --allow-write
python skills/content-generate/scripts/content_runtime.py kb search \
  --query "数学思维" --modality all --topk 5 --json
```

通过标准：ingest 写入 LanceDB items 表；search 返回经 RRF+rerank 的 topk，人工回读 source_path 核对相关性；search-log.jsonl 追加记录。
