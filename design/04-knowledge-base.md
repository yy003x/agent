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
│    items 表：标量 + 向量 + jieba 分词列          │
│    concepts + graph_edges：doc↔concept 二部图    │
│  检索：向量 ∥ jieba-FTS ∥ 图召回（三路）         │
│        → RRF 融合 → reranker 精排                │
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
├── lance/                 ← LanceDB 数据目录（items / concepts / graph_edges 表）
├── graph.jsonl            ← document-concept 二部图（规则引擎输出，确定性可重建的事实源）
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
| `origin_dir` | string | 原始素材父目录名（`category` concept 来源；媒体复制进 media-store 前记录） |
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

### document↔concept 二部图（替代旧 item-item edges）

图谱是 **document 与 concept 两类节点的二部图**，边只跨类（`document --rel--> concept`），
靠文档元数据由规则引擎**确定性生成**，作为检索的第三路召回通道（见 §7）。**不是独立图数据库。**

**concepts 表**（concept 节点，带向量以支持 query 近义匹配）

| 字段 | 类型 | 说明 |
|---|---|---|
| `concept_id` | string | 主键：`<ctype>:<value>`（类型前缀归一，如 `book:数学之美`） |
| `ctype` | string | `book` / `topic` / `category` |
| `value` | string | 概念原文（书名 / 标签 / 分类目录名） |
| `vector` | vector(512) | `value` 的 bge-small-zh-v1.5 向量（query 语义匹配用） |
| `doc_count` | int | 关联 document 数 |

**graph_edges 表**（二部边）

| 字段 | 类型 | 说明 |
|---|---|---|
| `doc_id` | string | document（items.id） |
| `concept_id` | string | concept（concepts.concept_id） |
| `ctype` | string | 冗余存类型，便于按类过滤 |
| `rel` | string | `is_book` / `has_topic` / `in_category` |
| `weight` | float | 同一 (doc, concept) 重复度 |

### 图谱构建（规则引擎 + JSONL 事实源）

确定性规则（同输入同输出，可随时重建；保守集，不抽 caption 关键词以控噪声）：

| 规则 | 来源元数据 | 边 |
|---|---|---|
| R1 | `title`（书名） | `document --is_book--> book:<title>` |
| R2 | 每个 `tag`（≠ title、≠ origin_dir） | `document --has_topic--> topic:<tag>` |
| R3 | `origin_dir`（原始父目录） | `document --in_category--> category:<dir>` |

- **事实源**：规则引擎遍历 items，输出 `workspace/kb/graph.jsonl`（每行一条边，纯文本、可 diff、可审计、可版本控制）：
  ```json
  {"doc_id":"a1b2","concept_id":"topic:数学思维","ctype":"topic","value":"数学思维","rel":"has_topic","weight":1}
  ```
- **查询载体**：再从 graph.jsonl 灌入 LanceDB `concepts`（concept 顺带 embed）+ `graph_edges` 表，支持双向查询（doc→concepts、concept→docs）。
- **重建**：`kb index --rebuild graph` 一键确定性重建（ingest 末尾自动触发）。
- **用途**：① 检索第三路召回（§7）；② content-generate skill 关联素材扩散（doc→concept→兄弟 doc）。

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

并记录 `origin_dir`（原始父目录，category concept 来源）。图谱不在逐条 ingest 时增量写，而在
ingest 末尾由规则引擎统一重建（见「图谱构建」）：遍历 items → 重写 `graph.jsonl` → 重灌 `concepts`/`graph_edges`。

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

    # ② jieba-FTS 召回（items.text_seg）
    fts_hits = (tbl.search(jieba_seg(query), query_type="fts")
                   .where(flt).limit(FTS_TOPN).to_list())     # FTS_TOPN=30

    # ③ 图召回（第三路）：query → 匹配 concept → 经二部图取关联 doc
    cids = match_concepts(query)                              # jieba 精确 + concepts.vector 近义
    graph_hits = docs_by_concepts(cids, flt, GRAPH_TOPN)      # graph_edges 聚合，按命中 concept 数×weight 排序

    # ④ RRF 融合三路（按 doc id 去重，rank 从 1 起）
    fused = rrf_merge(vec_hits, fts_hits, graph_hits, k=RRF_K)[:FUSE_TOPM]   # RRF_K=60, FUSE_TOPM=20

    # ⑤ cross-encoder 精排
    scores = reranker.predict([(query, f"{d['title']} {d['caption']}") for d in fused])  # bge-reranker-base
    ranked = [d for _, d in sorted(zip(scores, fused), key=lambda x: float(x[0]), reverse=True)]
    return ranked[:topk]                          # rerank_topk=10
```

```python
def rrf_merge(*lists, k=60):
    score, by_id = {}, {}
    for lst in lists:                       # 三路：向量 / FTS / 图，rank 从 1 起
        for rank, d in enumerate(lst, start=1):
            score[d["id"]] = score.get(d["id"], 0) + 1 / (k + rank)
            by_id.setdefault(d["id"], d)
    return [by_id[i] for i, _ in sorted(score.items(), key=lambda kv: kv[1], reverse=True)]


def match_concepts(query):
    """jieba 分词精确匹配 + concepts.vector 近义匹配，返回命中 concept_id 集合。"""
    seg = {w for w in jieba.cut(query) if w.strip()}
    exact = [c for c in concepts.to_pandas().to_dict("records") if c["value"] in seg or c["value"] in query]
    semantic = concepts.search(embed_query(query)).metric("cosine").limit(CONCEPT_TOPN).to_list()
    return {c["concept_id"] for c in exact + semantic}


def docs_by_concepts(concept_ids, flt, n):
    """经 graph_edges 聚合关联 doc（命中 concept 的 weight 求和），再按 modality/status 过滤。"""
    edges = graph_edges.search().where(f"concept_id IN ({sql_list(concept_ids)})").to_list()
    score = Counter()
    for e in edges:
        score[e["doc_id"]] += e["weight"]
    top_ids = [d for d, _ in score.most_common(n)]
    return items.search().where(f"{flt} AND id IN ({sql_list(top_ids)})").to_list()
```

### 参数表（默认值，可按效果调）

| 参数 | 默认 | 含义 |
|---|---|---|
| `VEC_TOPN` | 30 | 向量召回数 |
| `FTS_TOPN` | 30 | FTS 召回数 |
| `GRAPH_TOPN` | 30 | 图召回数（经 concept 扩散的 doc 数） |
| `CONCEPT_TOPN` | 10 | query 语义匹配的 concept 数 |
| `RRF_K` | 60 | RRF 平滑常数（三路融合） |
| `FUSE_TOPM` | 20 | 融合后送 rerank 的候选数 |
| `rerank_topk` | 10 | 精排后最终返回数 |

### 降级策略

- FTS index 未建 / jieba 缺失 → 跳过 ②。
- 图谱为空（concepts/graph_edges 无数据）→ 跳过 ③。
- reranker 未安装 → 跳过 ⑤，返回 RRF 融合结果（记日志告警）。
- 三路全空 / 向量库为空 → 返回空并提示先 `kb ingest`。

### 检索日志（写 search-log.jsonl）

```json
{"ts":"2026-06-18T14:30:00+08:00","query":"数学思维","modality":"all",
 "topk":10,"hits":8,"hit_ids":["abc123","def456"],
 "vec_n":30,"fts_n":27,"graph_n":12,"reranked":true}
```

---

## 8. 索引维护与清理

### `kb index --rebuild [fts|vector|graph|all]`
- `fts`：`tbl.create_fts_index("text_seg", replace=True)`
- `vector`：重算 `vector`/`text_seg` 列并 `merge_insert`（换模型/补字段时用）
- `graph`：规则引擎遍历 items → 重写 `graph.jsonl` → 重灌 `concepts`（含向量）+ `graph_edges` 表（确定性）
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
content-runtime kb related  --id <doc_id> [--topk N] [--json]   # 二部图扩散：同 concept 的兄弟 doc
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
| item-item edges（same_book/same_tag） | document-concept 二部图（`concepts`+`graph_edges`+`graph.jsonl`） | `kb index --rebuild graph` 规则引擎确定性重建 |

千级素材直接重跑 ingest 最简单（图片 caption 已是 Claude 调用，重跑成本主要在 vision API；如已 ingest 过，可缓存 caption 避免重复调用——见 content_runtime 实现）。

---

## 12. 实现注意事项（易错点清单）

实现 content_runtime KB 层时按此清单逐条落实，避免试错：

**LanceDB**
- 版本：`lancedb>=0.13`，FTS 用**原生引擎**（Rust，无需 `pip install tantivy`）；`tbl.create_fts_index("text_seg", replace=True)`，检索用 `tbl.search(q_seg, query_type="fts")`。若环境是旧版本报错，再退回 `create_fts_index(..., use_tantivy=True)` 并装 `tantivy`。
- 建表用显式 pyarrow schema（向量列 `pa.list_(pa.float32(), 512)`，维度必须等于 EMBED_DIM）；空表可 `db.create_table(name, schema=...)`。
- upsert 用 `tbl.merge_insert("id").when_matched_update_all().when_not_matched_insert_all().execute(rows)`。
- 向量检索：`tbl.search(qv).metric("cosine").where(flt, prefilter=True).limit(n)`——`prefilter=True` 保证先过滤再取够数。
- `IN` 查询：`where(f"id IN ({...})")`，字符串值须转义单引号（`v.replace("'", "''")`）。
- 千级规模不建 ANN 索引（暴力 cosine 足够）；万级以上再 `create_index`（IVF_PQ）。
- 更新/删除：`tbl.update(where=..., values={...})`、`tbl.delete(where=...)`。

**模型（sentence-transformers）**
- 向量：`SentenceTransformer("BAAI/bge-small-zh-v1.5", device=dev)`，`encode(text, normalize_embeddings=True)`（必须归一化，配 cosine）。
- **query 侧加检索指令** `Q_INSTRUCTION + query`，doc 侧不加（见 §5）。
- 精排：`CrossEncoder("BAAI/bge-reranker-base", device=dev)`，`predict([(q, doc), ...])` 返回分数；**加载失败要 try/except 降级**（跳过精排，记日志）。
- `dev = "mps" if torch.backends.mps.is_available() else "cpu"`，可由 `EMBED_DEVICE` 覆盖。
- 模型**全局单例懒加载**，勿每次 ingest/search 重载。
- 首次运行自动从 HuggingFace 下载（bge-small ~95MB、reranker-base ~1.1GB）；可设 `HF_ENDPOINT=https://hf-mirror.com` 加速。

**jieba**
- 精确模式 `jieba.cut(text)`；`jieba.setLogLevel(20)` 静音；query 与 `text_seg` **必须用同一分词**，否则 FTS 召回偏差。

**图谱**
- 重建是**全量确定性**：规则引擎遍历 active items → 重写 `graph.jsonl` → `drop_table` 后重建 `concepts`/`graph_edges`；在 `kb ingest` 与 `kb gc` 末尾自动触发。
- concept 量级小（百~千），`_match_concepts` 可全表扫描精确匹配 + 向量近义匹配并取并集。

**降级**：任一路（向量/FTS/图/reranker）依赖缺失都应 try/except 跳过并记 warn，不让整次 search 失败（见 §7 降级策略）。

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
