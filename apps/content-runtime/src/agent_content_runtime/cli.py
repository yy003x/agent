#!/usr/bin/env python3
"""content-runtime —— 本地知识库与媒体组装统一 CLI。

知识库层：
  LanceDB（向量 + 标量 + FTS 同库） + BAAI/bge-small-zh-v1.5 向量
  + jieba 分词中文 FTS + RRF hybrid 融合 + BAAI/bge-reranker-base 精排。

用法：
  python apps/content-runtime/bin/content-runtime init
  python apps/content-runtime/bin/content-runtime kb ingest --src <folder> --allow-write
  python apps/content-runtime/bin/content-runtime kb search --query "<text>" --json
  python apps/content-runtime/bin/content-runtime kb index --rebuild all --allow-write
  python apps/content-runtime/bin/content-runtime kb gc --older-than 180d --dry-run --allow-write
  python apps/content-runtime/bin/content-runtime text draft --brief <需求> --platform xiaohongshu --allow-write
  python apps/content-runtime/bin/content-runtime plan build --draft <draft.json> --out <plan.json> --allow-write
  python apps/content-runtime/bin/content-runtime media probe <file>
  python apps/content-runtime/bin/content-runtime media assemble --spec <plan.json> --out <dir> --allow-write
  python apps/content-runtime/bin/content-runtime publish package --platform xiaohongshu --in <dir> --allow-write

写门禁：ingest / index / gc / text draft / plan build / assemble / package 写入时必须带
--allow-write，否则仅 dry-run 或打印到 stdout。
重依赖（lancedb / sentence-transformers / jieba / PIL / pdfplumber / ffmpeg）按需延迟导入。
图片/视频 caption 由本地 CLI 按需生成，并写入 caption cache。
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

# ─────────────────────────── 全局常量 ───────────────────────────

ROOT = Path(__file__).resolve().parents[4]   # apps/content-runtime/src/agent_content_runtime/ → 项目根
KB_DIR = Path(os.environ.get("CONTENT_RUNTIME_KB_DIR", ROOT / "workspace" / "kb")).expanduser()
LANCE_DIR = Path(os.environ.get("CONTENT_RUNTIME_LANCE_DIR", KB_DIR / "lance")).expanduser()
MEDIA_STORE = Path(os.environ.get("CONTENT_RUNTIME_MEDIA_STORE", ROOT / "workspace" / "media-store")).expanduser()
SEARCH_LOG = KB_DIR / "search-log.jsonl"
CAPTION_CACHE = KB_DIR / "caption-cache.json"
RUNS_DIR = Path(os.environ.get("CONTENT_RUNTIME_RUNS_DIR", ROOT / "runs" / "content-runtime")).expanduser()
ACTIVITY_FILE = ROOT / "workspace" / ".finalize-activity.json"

# 模型
EMBED_MODEL = os.environ.get("EMBED_MODEL", "BAAI/bge-small-zh-v1.5")
EMBED_DIM = 512
RERANK_MODEL = os.environ.get("RERANK_MODEL", "BAAI/bge-reranker-base")
EMBED_DEVICE = os.environ.get("EMBED_DEVICE", "")   # 空 = 自动（mps→cpu）
CODEX_CMD = os.environ.get("AGENT_CODEX_CMD", "codex")
CODEX_MODEL = os.environ.get("AGENT_CODEX_MODEL", "")
CODEX_PROFILE = os.environ.get("AGENT_CODEX_PROFILE", "")
CODEX_TIMEOUT_S = int(os.environ.get("AGENT_CODEX_TIMEOUT_S", "180"))
Q_INSTRUCTION = "为这个句子生成表示以用于检索相关文章："
TEST_HASH_EMBEDDING = os.environ.get("CONTENT_RUNTIME_TEST_EMBEDDING", "").lower() in {"1", "true", "yes", "hash"}

# 检索管线参数
VEC_TOPN = 30
FTS_TOPN = 30
GRAPH_TOPN = 30        # 图召回：经 concept 扩散的 doc 数
CONCEPT_TOPN = 10      # query 语义匹配的 concept 数
RRF_K = 60
FUSE_TOPM = 20
GRAPH_JSONL = KB_DIR / "graph.jsonl"   # 二部图事实源（确定性可重建）

DOC_EXT = {".md", ".txt", ".pdf"}
IMAGE_EXT = {".jpg", ".jpeg", ".png", ".webp"}
VIDEO_EXT = {".mp4", ".mov", ".m4v", ".webm"}

PLATFORM_SPEC = {
    "xiaohongshu": {"cover": (1080, 1080), "image": (1080, 1440), "max_images": 9},
    "moments": {"cover": (1080, 1080), "image": (1080, 1080), "max_images": 9},
    "wechat_group": {"cover": (1080, 1080), "image": (1080, 1080), "max_images": 9},
}
CAPTION_PROMPT = (
    "描述这张图片的主题、视觉风格、构图要素，以及与教育/图书内容的关联。100字以内。"
    "最后另起一行列出5个精确标签，格式：标签：tag1,tag2,tag3,tag4,tag5"
)


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def log_run(msg: str) -> None:
    RUNS_DIR.mkdir(parents=True, exist_ok=True)
    (RUNS_DIR / f"{datetime.now().strftime('%Y-%m-%d')}.log").open("a", encoding="utf-8").write(
        f"{now_iso()} {msg}\n"
    )


def mark_runtime_activity(summary: str, status: str = "success") -> None:
    """标记运行态写操作，供 finalize hook 在 ignored 目录变更时兜底记录。"""
    ACTIVITY_FILE.parent.mkdir(parents=True, exist_ok=True)
    ACTIVITY_FILE.write_text(
        json.dumps({
            "timestamp": now_iso(),
            "skill": "content-generate",
            "status": status,
            "summary": summary,
            "source": "content-runtime",
        }, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def display_path(path: Path) -> str:
    try:
        return str(path.relative_to(ROOT))
    except ValueError:
        return str(path)


def _read_json(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))


def _write_json(path: Path, data) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def _normalize_sources(data) -> list[dict]:
    if not data:
        return []
    if isinstance(data, dict):
        for key in ("sources", "items", "hits", "results"):
            if isinstance(data.get(key), list):
                return [x for x in data[key] if isinstance(x, dict)]
        return [data]
    if isinstance(data, list):
        return [x for x in data if isinstance(x, dict)]
    return []


def _load_sources(path: str | None) -> list[dict]:
    if not path:
        return []
    return _normalize_sources(_read_json(Path(path)))


def _source_line(src: dict) -> str:
    title = (src.get("title") or Path(src.get("source_path") or "").stem or "素材").strip()
    caption = re.sub(r"\s+", " ", src.get("caption") or "").strip()
    return f"{title}：{caption[:80]}" if caption else title


def _derive_tags(brief: str, sources: list[dict]) -> list[str]:
    tags: list[str] = []
    seed = f"{brief} " + " ".join(str(s.get("title") or "") for s in sources)
    for word in ("数学思维", "亲子阅读", "图书推荐", "小学学习", "思维训练", "阅读启蒙"):
        if word in seed:
            tags.append(word)
    tags += ["图书推荐", "亲子阅读"]
    return list(dict.fromkeys(tags))[:5]


def _draft_title(brief: str, platform: str) -> str:
    topic = re.sub(r"[，。！？\s]+", "", brief)[:10] or "这本书"
    if platform in ("moments", "wechat_group"):
        return topic
    return f"{topic}，适合和孩子一起读"[:20]


def _guess_modality_from_source(src: dict) -> str | None:
    if src.get("modality"):
        return src["modality"]
    sp = src.get("source_path")
    return detect_modality(Path(sp)) if sp else None


# ─────────────────────────── 标识 / 哈希 ───────────────────────────

def file_hash(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def make_id(source_path: str, chunk_index: int = 0) -> str:
    try:
        mtime = os.path.getmtime(source_path)
    except OSError:
        mtime = 0
    return hashlib.sha256(f"{source_path}:{mtime}:{chunk_index}".encode()).hexdigest()[:16]


def extract_tags(path: Path) -> list[str]:
    tags = []
    if path.parent.name:
        tags.append(path.parent.name)
    tags.append(path.stem)
    return [t for t in tags if t]


# ─────────────────────────── 模型单例（懒加载） ───────────────────────────

_embedder = None
_reranker = None
_jieba_ready = False


def _pick_device() -> str:
    if EMBED_DEVICE:
        return EMBED_DEVICE
    try:
        import torch
        if torch.backends.mps.is_available():
            return "mps"
    except Exception:
        pass
    return "cpu"


def get_embedder():
    global _embedder
    if _embedder is None:
        from sentence_transformers import SentenceTransformer
        _embedder = SentenceTransformer(EMBED_MODEL, device=_pick_device())
    return _embedder


def get_reranker():
    """返回 CrossEncoder，加载失败返回 None（检索降级为不精排）。"""
    global _reranker
    if _reranker is None:
        try:
            from sentence_transformers import CrossEncoder
            _reranker = CrossEncoder(RERANK_MODEL, device=_pick_device())
        except Exception as e:  # noqa: BLE001
            print(f"[warn] reranker 加载失败（{e}），跳过精排", file=sys.stderr)
            _reranker = False
    return _reranker or None


def _hash_embedding(text: str, salt: str) -> list[float]:
    """Deterministic lightweight embedding for isolated smoke tests."""
    values: list[float] = []
    seed = f"{salt}:{text}".encode("utf-8")
    counter = 0
    while len(values) < EMBED_DIM:
        digest = hashlib.sha256(seed + str(counter).encode("ascii")).digest()
        for byte in digest:
            values.append((byte / 127.5) - 1.0)
            if len(values) >= EMBED_DIM:
                break
        counter += 1
    norm = sum(v * v for v in values) ** 0.5 or 1.0
    return [float(v / norm) for v in values]


def _ensure_jieba():
    global _jieba_ready
    if not _jieba_ready:
        import jieba
        jieba.setLogLevel(20)
        _jieba_ready = True


def jieba_seg(text: str) -> str:
    _ensure_jieba()
    import jieba
    return " ".join(w for w in jieba.cut(text or "") if w.strip())


def embed_doc(text: str) -> list[float]:
    if TEST_HASH_EMBEDDING:
        return _hash_embedding(text[:2000], "doc")
    vec = get_embedder().encode(text[:2000], normalize_embeddings=True)
    return [float(x) for x in vec]


def embed_query(query: str) -> list[float]:
    if TEST_HASH_EMBEDDING:
        return _hash_embedding(Q_INSTRUCTION + query, "query")
    vec = get_embedder().encode(Q_INSTRUCTION + query, normalize_embeddings=True)
    return [float(x) for x in vec]


# ─────────────────────────── LanceDB ───────────────────────────

_db = None


def get_db():
    global _db
    if _db is None:
        import lancedb
        LANCE_DIR.mkdir(parents=True, exist_ok=True)
        _db = lancedb.connect(str(LANCE_DIR))
    return _db


def _items_schema():
    import pyarrow as pa
    return pa.schema([
        ("id", pa.string()), ("modality", pa.string()), ("source_path", pa.string()),
        ("origin_dir", pa.string()),
        ("title", pa.string()), ("tags", pa.string()), ("caption", pa.string()),
        ("transcript", pa.string()), ("text_seg", pa.string()),
        ("vector", pa.list_(pa.float32(), EMBED_DIM)),
        ("chunk_index", pa.int64()), ("duration_s", pa.float64()),
        ("width", pa.int64()), ("height", pa.int64()), ("file_hash", pa.string()),
        ("ingest_at", pa.string()), ("last_hit_at", pa.string()),
        ("archived_at", pa.string()), ("status", pa.string()),
    ])


def _concepts_schema():
    import pyarrow as pa
    return pa.schema([
        ("concept_id", pa.string()), ("ctype", pa.string()), ("value", pa.string()),
        ("vector", pa.list_(pa.float32(), EMBED_DIM)), ("doc_count", pa.int64()),
    ])


def _graph_edges_schema():
    import pyarrow as pa
    return pa.schema([
        ("doc_id", pa.string()), ("concept_id", pa.string()),
        ("ctype", pa.string()), ("rel", pa.string()), ("weight", pa.float64()),
    ])


def get_items_table(create: bool = True):
    db = get_db()
    if "items" in db.table_names():
        return db.open_table("items")
    if not create:
        return None
    return db.create_table("items", schema=_items_schema())


def get_concepts_table(create: bool = True):
    db = get_db()
    if "concepts" in db.table_names():
        return db.open_table("concepts")
    if not create:
        return None
    return db.create_table("concepts", schema=_concepts_schema())


def get_graph_edges_table(create: bool = True):
    db = get_db()
    if "graph_edges" in db.table_names():
        return db.open_table("graph_edges")
    if not create:
        return None
    return db.create_table("graph_edges", schema=_graph_edges_schema())


def init_db() -> None:
    get_items_table(create=True)
    get_concepts_table(create=True)
    get_graph_edges_table(create=True)


def _blank_item() -> dict:
    return dict(id="", modality="", source_path="", origin_dir="", title="", tags="[]", caption="",
                transcript="", text_seg="", vector=[0.0] * EMBED_DIM, chunk_index=0,
                duration_s=0.0, width=0, height=0, file_hash="", ingest_at="",
                last_hit_at="", archived_at="", status="active")


def upsert_items(tbl, rows: list[dict]) -> None:
    """先按 source_path 删除已有行，再 upsert。

    防孤儿 chunk：doc 的 id 含 mtime，文件更新后所有 chunk 得到新 id，仅靠
    merge_insert(id) 会留下原 mtime 的原 chunk。先删同 source_path 的已有行可避免。
    """
    if not rows:
        return
    for sp in {r["source_path"] for r in rows if r.get("source_path")}:
        try:
            tbl.delete("source_path = '%s'" % sp.replace("'", "''"))
        except Exception:
            pass
    (tbl.merge_insert("id")
        .when_matched_update_all()
        .when_not_matched_insert_all()
        .execute(rows))


# ─────────────────────────── Codex image caption（含缓存） ───────────────────────────


def has_codex_cli() -> bool:
    return bool(shutil.which(CODEX_CMD))


def _load_caption_cache() -> dict:
    if CAPTION_CACHE.exists():
        try:
            return json.loads(CAPTION_CACHE.read_text(encoding="utf-8"))
        except Exception:
            return {}
    return {}


def _save_caption_cache(cache: dict) -> None:
    KB_DIR.mkdir(parents=True, exist_ok=True)
    CAPTION_CACHE.write_text(json.dumps(cache, ensure_ascii=False, indent=2), encoding="utf-8")


def _codex_caption(path: Path) -> str:
    if not has_codex_cli():
        raise RuntimeError("未找到 codex CLI")
    prompt = (
        "你是学而思图书运营本地 Agent 的图片理解器。"
        "请只基于图片内容生成可用于本地知识库检索的中文 caption。"
        f"\n{CAPTION_PROMPT}"
    )
    with tempfile.TemporaryDirectory(prefix="content-caption-") as tmp:
        output_path = Path(tmp) / "last-message.txt"
        cmd = [
            CODEX_CMD, "exec",
            "--skip-git-repo-check",
            "--sandbox", "read-only",
            "--color", "never",
            "--output-last-message", str(output_path),
            "-C", str(ROOT),
        ]
        if CODEX_MODEL:
            cmd.extend(["--model", CODEX_MODEL])
        if CODEX_PROFILE:
            cmd.extend(["--profile", CODEX_PROFILE])
        cmd.extend(["--image", str(path), "-"])
        proc = subprocess.run(
            cmd,
            input=prompt,
            text=True,
            capture_output=True,
            timeout=CODEX_TIMEOUT_S,
            cwd=ROOT,
            check=False,
        )
        if output_path.exists():
            text = output_path.read_text(encoding="utf-8", errors="ignore").strip()
        else:
            text = proc.stdout.strip()
        if proc.returncode != 0:
            detail = (proc.stderr or proc.stdout or "").strip().splitlines()[-1:]
            raise RuntimeError(detail[0] if detail else f"codex exec 退出码 {proc.returncode}")
        if not text:
            raise RuntimeError("codex exec 未返回 caption")
        return text


def caption_image_file(path: Path, fhash: str | None = None) -> tuple[str, list[str]]:
    """调 Codex CLI 生成图片描述（按 file_hash 缓存，避免重复 caption）。"""
    cache = _load_caption_cache()
    key = fhash or file_hash(path)
    if key in cache:
        c = cache[key]
        return c["caption"], c["tags"]
    try:
        text = _codex_caption(path)
    except Exception as exc:  # noqa: BLE001
        log_run(f"caption unavailable: {display_path(path)} reason={exc}")
        text = f"{path.stem}。标签：{path.stem}"
    caption, tags = parse_caption_and_tags(text)
    cache[key] = {"caption": caption, "tags": tags}
    _save_caption_cache(cache)
    return caption, tags


def parse_caption_and_tags(text: str) -> tuple[str, list[str]]:
    caption, tags = text.strip(), []
    for marker in ("标签：", "标签:", "Tags:", "tags:"):
        if marker in text:
            caption, _, tag_str = text.partition(marker)
            tags = [t.strip() for t in tag_str.replace("，", ",").split(",") if t.strip()]
            break
    return caption.strip(), tags[:5]


# ─────────────────────────── 文本切块 ───────────────────────────

def parse_text(path: Path) -> str:
    if path.suffix.lower() == ".pdf":
        import pdfplumber
        with pdfplumber.open(path) as pdf:
            return "\n".join((p.extract_text() or "") for p in pdf.pages)
    return path.read_text(encoding="utf-8", errors="ignore")


def chunk_text(text: str, size: int = 800, overlap: int = 100) -> list[str]:
    text = text.strip()
    if not text:
        return []
    chunks, start = [], 0
    while start < len(text):
        chunks.append(text[start:start + size])
        start += size - overlap
    return chunks


def build_text_seg(title: str, tags: list[str], caption: str, transcript: str) -> str:
    return jieba_seg(f"{title} {' '.join(tags)} {caption} {transcript}")


# ─────────────────────────── ingest ───────────────────────────

def detect_modality(path: Path) -> str | None:
    ext = path.suffix.lower()
    if ext in DOC_EXT:
        return "doc"
    if ext in IMAGE_EXT:
        return "image"
    if ext in VIDEO_EXT:
        return "video"
    return None


def already_ingested(tbl, fhash: str) -> bool:
    try:
        rows = tbl.search().where(f"file_hash = '{fhash}'").limit(1).to_list()
        return len(rows) > 0
    except Exception:
        return False


def copy_to_store(path: Path) -> Path:
    MEDIA_STORE.mkdir(parents=True, exist_ok=True)
    dest = MEDIA_STORE / f"{file_hash(path)[:16]}{path.suffix.lower()}"
    if not dest.exists():
        shutil.copy2(path, dest)
    return dest


def build_doc_rows(path: Path) -> list[dict]:
    text = parse_text(path)
    chunks = chunk_text(text)
    tags = extract_tags(path)
    fhash = file_hash(path)
    rows = []
    for i, chunk in enumerate(chunks):
        caption = chunk[:200]
        row = _blank_item()
        row.update(
            id=make_id(str(path), i), modality="doc", source_path=str(path), origin_dir=path.parent.name, title=path.stem,
            tags=json.dumps(tags, ensure_ascii=False), caption=caption,
            text_seg=build_text_seg(path.stem, tags, caption, ""),
            vector=embed_doc(f"{path.stem} {caption}"),
            chunk_index=i, file_hash=fhash, ingest_at=now_iso(),
        )
        rows.append(row)
    return rows


def build_image_rows(path: Path) -> list[dict]:
    fhash = file_hash(path)
    caption, ai_tags = caption_image_file(path, fhash)
    dest = copy_to_store(path)
    tags = list(dict.fromkeys(extract_tags(path) + ai_tags))
    width = height = 0
    try:
        from PIL import Image
        with Image.open(path) as im:
            width, height = im.size
    except Exception:
        pass
    row = _blank_item()
    row.update(
        id=make_id(str(dest), 0), modality="image", source_path=str(dest), origin_dir=path.parent.name, title=path.stem,
        tags=json.dumps(tags, ensure_ascii=False), caption=caption,
        text_seg=build_text_seg(path.stem, tags, caption, ""),
        vector=embed_doc(f"{path.stem} {caption} {' '.join(tags)}"),
        width=width, height=height, file_hash=fhash, ingest_at=now_iso(),
    )
    return [row]


def extract_frames(path: Path, interval: int = 30, max_count: int = 10) -> list[Path]:
    tmp = RUNS_DIR / "frames" / file_hash(path)[:16]
    tmp.mkdir(parents=True, exist_ok=True)
    subprocess.run(
        ["ffmpeg", "-y", "-i", str(path), "-vf", f"fps=1/{interval}",
         "-frames:v", str(max_count), str(tmp / "frame_%03d.jpg")],
        check=True, capture_output=True,
    )
    return sorted(tmp.glob("frame_*.jpg"))


def probe_video(path: Path) -> tuple[float, int, int]:
    out = subprocess.run(
        ["ffprobe", "-v", "error", "-select_streams", "v:0",
         "-show_entries", "stream=width,height:format=duration", "-of", "json", str(path)],
        check=True, capture_output=True, text=True,
    )
    data = json.loads(out.stdout)
    stream = (data.get("streams") or [{}])[0]
    duration = float(data.get("format", {}).get("duration", 0) or 0)
    return duration, int(stream.get("width", 0) or 0), int(stream.get("height", 0) or 0)


def build_video_rows(path: Path) -> list[dict]:
    frames = extract_frames(path)
    frame_caps = [caption_image_file(fr)[0] for fr in frames]
    transcript = "\n".join(f"[{i*30}s] {c}" for i, c in enumerate(frame_caps))
    duration, width, height = probe_video(path)
    dest = copy_to_store(path)
    tags = extract_tags(path)
    caption = frame_caps[0] if frame_caps else ""
    row = _blank_item()
    row.update(
        id=make_id(str(dest), 0), modality="video", source_path=str(dest), origin_dir=path.parent.name, title=path.stem,
        tags=json.dumps(tags, ensure_ascii=False), caption=caption, transcript=transcript,
        text_seg=build_text_seg(path.stem, tags, caption, transcript),
        vector=embed_doc(f"{path.stem} {caption} {transcript[:500]}"),
        duration_s=duration, width=width, height=height, file_hash=file_hash(path), ingest_at=now_iso(),
    )
    return [row]


def cmd_ingest(args) -> int:
    src = Path(args.src)
    if not src.exists():
        print(f"[ingest] 源目录不存在：{src}")
        return 1
    files = [p for p in sorted(src.rglob("*")) if p.is_file() and detect_modality(p)]
    if args.modality != "auto":
        files = [p for p in files if detect_modality(p) == args.modality]
    if args.limit:
        files = files[: args.limit]

    if not args.allow_write:
        print(f"[dry-run] 将 ingest {len(files)} 个文件（加 --allow-write 实际写入）：")
        for p in files[:20]:
            print(f"  - [{detect_modality(p)}] {p}")
        if len(files) > 20:
            print(f"  ... 其余 {len(files) - 20} 个")
        return 0

    init_db()
    tbl = get_items_table()
    done = skipped = failed = 0
    for p in files:
        modality = detect_modality(p)
        if args.resume and already_ingested(tbl, file_hash(p)):
            skipped += 1
            continue
        try:
            if modality == "doc":
                rows = build_doc_rows(p)
            elif modality == "image":
                rows = build_image_rows(p)
            else:
                rows = build_video_rows(p)
            upsert_items(tbl, rows)
            done += 1
            log_run(f"ingest ok [{modality}] {p} (+{len(rows)})")
            print(f"  ✓ [{modality}] {p.name} (+{len(rows)})")
        except Exception as e:  # noqa: BLE001
            failed += 1
            log_run(f"ingest FAIL {p}: {e}")
            print(f"  ✗ [{modality}] {p.name}: {e}")
    # 重建 FTS index + graph edges
    _rebuild_fts(tbl)
    _rebuild_graph(tbl)
    summary = f"KB ingest：{done} 成功 / {skipped} 跳过 / {failed} 失败 / 共 {len(files)}（FTS+graph 已重建）"
    status = "failed" if failed and not done else ("partial" if failed else "success")
    mark_runtime_activity(summary, status=status)
    print(f"[ingest] 完成：{done} 成功 / {skipped} 跳过 / {failed} 失败 / 共 {len(files)}（FTS+graph 已重建）")
    return 1 if failed else 0


# ─────────────────────────── search（hybrid + rerank） ───────────────────────────

def _rrf_merge(*lists: list[dict], k: int = RRF_K) -> list[dict]:
    """RRF 融合任意多路召回（向量 / FTS / 图），按 doc id 去重。"""
    score: dict[str, float] = {}
    by_id: dict[str, dict] = {}
    for lst in lists:
        for rank, d in enumerate(lst, start=1):
            _id = d["id"]
            score[_id] = score.get(_id, 0.0) + 1.0 / (k + rank)
            by_id.setdefault(_id, d)
    ordered = sorted(score.items(), key=lambda kv: kv[1], reverse=True)
    out = []
    for _id, s in ordered:
        d = by_id[_id]
        d["_rrf"] = s
        out.append(d)
    return out


def _build_filter(modality: str) -> str:
    base = "status = 'active'"
    if modality != "all":
        base = f"modality = '{modality}' AND {base}"
    return base


def _vector_search(tbl, query: str, flt: str, n: int) -> list[dict]:
    try:
        qv = embed_query(query)
        return tbl.search(qv).metric("cosine").where(flt, prefilter=True).limit(n).to_list()
    except Exception as e:  # noqa: BLE001
        print(f"[warn] 向量检索不可用（{e}）", file=sys.stderr)
        return []


def _fts_search(tbl, query: str, flt: str, n: int) -> list[dict]:
    try:
        q_seg = jieba_seg(query)
        return tbl.search(q_seg, query_type="fts").where(flt, prefilter=True).limit(n).to_list()
    except Exception as e:  # noqa: BLE001
        print(f"[warn] FTS 检索不可用（{e}），仅用向量召回", file=sys.stderr)
        return []


def _sql_in(values) -> str:
    """构造 SQL IN 列表（单引号字符串，转义内部单引号）。"""
    return ", ".join("'" + str(v).replace("'", "''") + "'" for v in values) or "''"


def _match_concepts(query: str) -> set[str]:
    """jieba 分词精确匹配 + concepts.vector 近义匹配，返回命中 concept_id。"""
    ctbl = get_concepts_table(create=False)
    if ctbl is None:
        return set()
    hits: set[str] = set()
    try:
        seg = {w for w in jieba_seg(query).split() if w.strip()}
        for c in ctbl.search().limit(100000).to_list():   # 概念量级小（百~千），全表扫描可接受
            if c["value"] in seg or c["value"] in query:
                hits.add(c["concept_id"])
    except Exception:
        pass
    try:
        for c in ctbl.search(embed_query(query)).metric("cosine").limit(CONCEPT_TOPN).to_list():
            hits.add(c["concept_id"])
    except Exception as e:  # noqa: BLE001
        print(f"[warn] concept 语义匹配不可用（{e}）", file=sys.stderr)
    return hits


def _graph_search(query: str, flt: str, n: int) -> list[dict]:
    """第三路：query → 匹配 concept → 经二部图取关联 doc（按命中 concept 的 weight 求和排序）。"""
    cids = _match_concepts(query)
    if not cids:
        return []
    getbl = get_graph_edges_table(create=False)
    itbl = get_items_table(create=False)
    if getbl is None or itbl is None:
        return []
    try:
        from collections import Counter
        edges = getbl.search().where(f"concept_id IN ({_sql_in(cids)})").limit(100000).to_list()
        score: Counter = Counter()
        for e in edges:
            score[e["doc_id"]] += e.get("weight", 1.0)
        top_ids = [d for d, _ in score.most_common(n)]
        if not top_ids:
            return []
        docs = itbl.search().where(f"({flt}) AND id IN ({_sql_in(top_ids)})").limit(n).to_list()
        order = {d: i for i, d in enumerate(top_ids)}
        return sorted(docs, key=lambda d: order.get(d["id"], 1 << 30))
    except Exception as e:  # noqa: BLE001
        print(f"[warn] 图召回不可用（{e}）", file=sys.stderr)
        return []


def _rerank(query: str, cands: list[dict], topk: int) -> tuple[list[dict], bool]:
    reranker = get_reranker()
    if not reranker or not cands:
        return cands[:topk], False
    pairs = [(query, f"{d.get('title','')} {d.get('caption','')}") for d in cands]
    scores = reranker.predict(pairs)
    ranked = [d for _, d in sorted(zip(scores, cands), key=lambda x: float(x[0]), reverse=True)]
    return ranked[:topk], True


def cmd_search(args) -> int:
    tbl = get_items_table(create=False)
    if tbl is None:
        print("（知识库为空，请先 kb ingest）")
        return 0
    flt = _build_filter(args.modality)
    vec_hits = _vector_search(tbl, args.query, flt, VEC_TOPN)
    fts_hits = _fts_search(tbl, args.query, flt, FTS_TOPN)
    graph_hits = _graph_search(args.query, flt, GRAPH_TOPN)
    fused = _rrf_merge(vec_hits, fts_hits, graph_hits)[:FUSE_TOPM]
    ranked, reranked = _rerank(args.query, fused, args.topk)

    if not args.no_touch:
        ts = now_iso()
        for r in ranked:
            try:
                tbl.update(where=f"id = '{r['id']}'", values={"last_hit_at": ts})
            except Exception:
                pass

    rows = [{"id": r["id"], "modality": r.get("modality"), "source_path": r.get("source_path"),
             "title": r.get("title"), "caption": r.get("caption"),
             "score": round(float(r.get("_rrf", 0.0)), 5)} for r in ranked]

    if not args.no_log:
        _write_search_log(args.query, args.modality, args.topk, [r["id"] for r in rows],
                          len(vec_hits), len(fts_hits), len(graph_hits), reranked)

    if args.json:
        print(json.dumps(rows, ensure_ascii=False, indent=2))
    else:
        if not rows:
            print("（无命中。可尝试拆分关键词或先 ingest 素材）")
        for i, r in enumerate(rows, 1):
            cap = (r["caption"] or "").replace("\n", " ")[:60]
            print(f"{i:>2} | {r['modality']:<5} | {(r['title'] or '')[:24]:<24} | {cap} | {r['id']}")
    return 0


def _write_search_log(query, modality, topk, hit_ids, vec_n, fts_n, graph_n, reranked) -> None:
    KB_DIR.mkdir(parents=True, exist_ok=True)
    rec = {"ts": now_iso(), "query": query, "modality": modality, "topk": topk,
           "hits": len(hit_ids), "hit_ids": hit_ids,
           "vec_n": vec_n, "fts_n": fts_n, "graph_n": graph_n, "reranked": reranked}
    with SEARCH_LOG.open("a", encoding="utf-8") as f:
        f.write(json.dumps(rec, ensure_ascii=False) + "\n")


# ─────────────────────────── index / gc ───────────────────────────

def _rebuild_fts(tbl) -> None:
    try:
        tbl.create_fts_index("text_seg", replace=True)
    except Exception as e:  # noqa: BLE001
        print(f"[warn] FTS index 重建失败：{e}", file=sys.stderr)


def _concept_id(ctype: str, value: str) -> str:
    return f"{ctype}:{value.strip()}"


def extract_concepts(item: dict) -> list[dict]:
    """规则引擎：从单条 item 元数据确定性抽取 document→concept 边（保守集：书名/标签/分类）。"""
    title = (item.get("title") or "").strip()
    origin = (item.get("origin_dir") or "").strip()
    out = []
    if title:
        out.append({"ctype": "book", "value": title, "rel": "is_book"})        # R1
    if origin:
        out.append({"ctype": "category", "value": origin, "rel": "in_category"})  # R3
    for tag in json.loads(item.get("tags") or "[]"):                            # R2
        tag = (tag or "").strip()
        if tag and tag != title and tag != origin:
            out.append({"ctype": "topic", "value": tag, "rel": "has_topic"})
    return out


def _rebuild_graph(tbl) -> None:
    """规则引擎遍历 items → 重写 graph.jsonl（事实源）→ 重灌 concepts + graph_edges 表（确定性）。"""
    try:
        rows = tbl.search().where("status = 'active'").limit(1000000).to_list()
    except Exception:
        rows = []

    edges: list[dict] = []
    concept_meta: dict[str, dict] = {}
    for r in rows:
        for c in extract_concepts(r):
            cid = _concept_id(c["ctype"], c["value"])
            edges.append({"doc_id": r["id"], "concept_id": cid,
                          "ctype": c["ctype"], "rel": c["rel"], "weight": 1.0})
            m = concept_meta.setdefault(cid, {"ctype": c["ctype"], "value": c["value"], "doc_count": 0})
            m["doc_count"] += 1

    # 事实源：graph.jsonl（确定性可重建、可 diff、可审计）
    KB_DIR.mkdir(parents=True, exist_ok=True)
    with GRAPH_JSONL.open("w", encoding="utf-8") as f:
        for e in edges:
            f.write(json.dumps({**e, "value": concept_meta[e["concept_id"]]["value"]},
                               ensure_ascii=False) + "\n")

    # 查询载体：从 graph.jsonl 同源重灌 concepts（带向量）+ graph_edges 表
    db = get_db()
    for name in ("concepts", "graph_edges"):
        if name in db.table_names():
            db.drop_table(name)
    ctbl = get_concepts_table(create=True)
    getbl = get_graph_edges_table(create=True)
    if concept_meta:
        ctbl.add([{"concept_id": cid, "ctype": m["ctype"], "value": m["value"],
                   "vector": embed_doc(m["value"]), "doc_count": m["doc_count"]}
                  for cid, m in concept_meta.items()])
    if edges:
        getbl.add(edges)


def cmd_index(args) -> int:
    if not args.allow_write:
        print(f"[dry-run] 将重建索引：{args.rebuild}（加 --allow-write 执行）")
        return 0
    init_db()
    tbl = get_items_table()
    target = args.rebuild
    if target in ("fts", "all"):
        _rebuild_fts(tbl)
        print("  ✓ FTS 重建完成")
    if target in ("graph", "all"):
        _rebuild_graph(tbl)
        print("  ✓ graph 重建完成")
    if target in ("vector", "all"):
        rows = tbl.search().limit(100000).to_list()
        upd = []
        for r in rows:
            txt = f"{r.get('title','')} {r.get('caption','')} {(r.get('transcript') or '')[:500]}"
            nr = {k: r[k] for k in r if not k.startswith("_")}
            nr["vector"] = embed_doc(txt)
            nr["text_seg"] = build_text_seg(r.get("title", ""), json.loads(r.get("tags") or "[]"),
                                            r.get("caption", ""), r.get("transcript", "") or "")
            upd.append(nr)
        upsert_items(tbl, upd)
        _rebuild_fts(tbl)
        print(f"  ✓ vector 重建完成（{len(upd)} 条）")
    mark_runtime_activity(f"KB index：重建 {target} 索引")
    return 0


def _parse_days(s: str) -> int:
    return int(s[:-1]) if s.endswith("d") else int(s)


def _older_than(value: str | None, cutoff: str) -> bool:
    return bool(value) and value < cutoff


def cmd_gc(args) -> int:
    tbl = get_items_table(create=False)
    if tbl is None:
        print("（知识库为空）")
        return 0
    days = _parse_days(args.older_than)
    cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()
    archive_cutoff = (datetime.now(timezone.utc) - timedelta(days=90)).isoformat()

    all_rows = tbl.search().limit(1000000).to_list()
    to_archive = [
        r for r in all_rows
        if r.get("status") == "active"
        and (
            _older_than(r.get("last_hit_at"), cutoff)
            or (not r.get("last_hit_at") and _older_than(r.get("ingest_at"), cutoff))
        )
    ]
    to_delete = [r for r in all_rows if r.get("status") == "archived"
                 and (r.get("archived_at") or "") < archive_cutoff]

    print(f"待归档：{len(to_archive)} 条（last_hit_at 或无命中时 ingest_at < {cutoff[:10]}）")
    print(f"待删除（归档满 90 天）：{len(to_delete)} 条")

    if args.dry_run or not args.allow_write:
        print("[dry-run] 未执行实际清理（去掉 --dry-run 且加 --allow-write 生效）")
        return 0

    ts = now_iso()
    for r in to_archive:
        tbl.update(where=f"id = '{r['id']}'", values={"status": "archived", "archived_at": ts})
    for r in to_delete:
        sp = Path(r.get("source_path") or "")
        if MEDIA_STORE in sp.parents and sp.exists():
            sp.unlink()  # 只删 ingest 复制进来的副本，不动用户原始文件
        tbl.delete(f"id = '{r['id']}'")
    if to_archive or to_delete:
        _rebuild_graph(tbl)   # 图谱依 active items 重建，剔除已归档/删除项
    mark_runtime_activity(f"KB gc：归档 {len(to_archive)} / 删除 {len(to_delete)}")
    print(f"[gc] 已归档 {len(to_archive)} / 删除 {len(to_delete)}")
    return 0


# ─────────────────────────── text / plan ───────────────────────────

def _draft_body(brief: str, platform: str, style: str, sources: list[dict]) -> str:
    facts = [_source_line(s) for s in sources[:3]]
    if platform == "wechat_group":
        lines = [
            f"家长们好，今天想和大家分享一份关于「{brief}」的阅读/学习素材。",
        ]
        if facts:
            lines.append(f"里面有个点很适合和孩子聊：{facts[0]}")
        lines.append("这类内容不建议一次性讲太多，可以先陪孩子读一小段，再让孩子说说自己的理解。")
        lines.append("有需要的家长可以先收藏，后面也可以按孩子年级和兴趣再细看。")
        return "\n".join(lines)
    if platform == "moments":
        base = f"最近在看{brief}，里面有些内容挺适合和孩子一起聊。"
        if facts:
            base += f" 比如{facts[0]}。"
        base += "不急着给孩子灌输结论，更适合当成一次轻松的亲子阅读和讨论。"
        return base[:180]

    lines = [
        f"想给孩子找一份关于「{brief}」的阅读素材，可以先从这几本/这些内容看起。",
        "",
    ]
    if facts:
        lines.append("我会优先看这几个点：")
        for i, item in enumerate(facts, 1):
            lines.append(f"{i}. {item}")
        lines.append("")
    lines += [
        f"整体更适合用「{style}」的方式讲：不硬推、不制造焦虑，先把书里有价值的知识点和孩子真实学习场景连起来。",
        "如果孩子最近正好在做相关主题，可以把它当作一次课外阅读和讨论素材。",
    ]
    return "\n".join(lines)


def cmd_text_draft(args) -> int:
    sources = _load_sources(args.sources)
    draft = {
        "platform": args.platform,
        "brief": args.brief,
        "style": args.style,
        "title": _draft_title(args.brief, args.platform),
        "body_text": _draft_body(args.brief, args.platform, args.style, sources),
        "tags": _derive_tags(args.brief, sources) if args.platform == "xiaohongshu" else [],
        "sources": sources,
        "drafted_at": now_iso(),
    }
    if not args.out:
        print(json.dumps(draft, ensure_ascii=False, indent=2))
        return 0
    out = Path(args.out)
    if not args.allow_write:
        print(f"[dry-run] 将写入文案草稿：{out}（加 --allow-write 执行）")
        print(json.dumps({k: draft[k] for k in ("platform", "title", "tags")}, ensure_ascii=False, indent=2))
        return 0
    _write_json(out, draft)
    mark_runtime_activity(f"文案草稿：生成 {display_path(out)}")
    print(f"[text] 文案草稿已写入：{out}")
    return 0


def cmd_plan_build(args) -> int:
    draft = _read_json(Path(args.draft))
    sources = _load_sources(args.sources) if args.sources else _normalize_sources(draft.get("sources"))
    platform = args.platform or draft.get("platform") or "xiaohongshu"
    plan = {
        "type": platform,
        "title": draft.get("title", Path(args.out).stem),
        "tags": draft.get("tags", []),
        "body_text": draft.get("body_text", ""),
        "images": [],
        "clips": [],
        "skipped_assets": [],
    }

    for src in sources:
        sp = src.get("source_path")
        if not sp:
            continue
        path = Path(sp)
        modality = _guess_modality_from_source(src)
        if modality in ("image", "video") and not path.exists():
            plan["skipped_assets"].append({"source_path": sp, "reason": "not_exists"})
            continue
        if modality == "image":
            item = {"src": sp}
            if "cover" not in plan:
                plan["cover"] = item
            else:
                plan["images"].append(item)
        elif modality == "video":
            plan["clips"].append({"src": sp, "start": 0})

    if not args.allow_write:
        print(f"[dry-run] 将写入组装计划：{args.out}（加 --allow-write 执行）")
        print(json.dumps(plan, ensure_ascii=False, indent=2))
        return 0
    out = Path(args.out)
    _write_json(out, plan)
    mark_runtime_activity(f"组装计划：生成 {display_path(out)}")
    print(f"[plan] 组装计划已写入：{out}")
    return 0


# ─────────────────────────── media ───────────────────────────

def cmd_probe(args) -> int:
    path = Path(args.file)
    modality = detect_modality(path)
    info = {"file": str(path), "modality": modality, "exists": path.exists()}
    if modality == "video" and path.exists():
        try:
            d, w, h = probe_video(path)
            info.update(duration_s=d, width=w, height=h)
        except Exception as e:  # noqa: BLE001
            info["error"] = str(e)
    elif modality == "image" and path.exists():
        try:
            from PIL import Image
            with Image.open(path) as im:
                info.update(width=im.size[0], height=im.size[1], format=im.format)
        except Exception as e:  # noqa: BLE001
            info["error"] = str(e)
    print(json.dumps(info, ensure_ascii=False, indent=2))
    return 0


def _resize_image(src: Path, size: tuple[int, int], crop: list[int] | None, out: Path) -> None:
    from PIL import Image
    im = Image.open(src).convert("RGB")
    if crop:
        im = im.crop(tuple(crop))
    im = im.resize(tuple(size), Image.LANCZOS)
    im.save(out, quality=90)


def cmd_assemble(args) -> int:
    plan = json.loads(Path(args.spec).read_text(encoding="utf-8"))
    out_dir = Path(args.out)
    if not args.allow_write:
        print(f"[dry-run] 将组装到 {out_dir}（加 --allow-write 执行）")
        print(f"  type={plan.get('type')} images={len(plan.get('images', []))} "
              f"clips={len(plan.get('clips', []))}")
        return 0

    out_dir.mkdir(parents=True, exist_ok=True)
    ptype = plan.get("type", "xiaohongshu")
    spec_sz = PLATFORM_SPEC.get(ptype, PLATFORM_SPEC["xiaohongshu"])
    sources = []

    if plan.get("cover"):
        cov = plan["cover"]
        cov_src = cov["src"] if isinstance(cov, dict) else cov
        _resize_image(Path(cov_src), spec_sz["cover"],
                      (cov.get("crop") if isinstance(cov, dict) else None), out_dir / "cover.jpg")
        sources.append(("cover.jpg", cov_src))

    for i, img in enumerate(plan.get("images", [])):
        img_src = img["src"] if isinstance(img, dict) else img
        size = img.get("resize") if isinstance(img, dict) else None
        crop = img.get("crop") if isinstance(img, dict) else None
        _resize_image(Path(img_src), tuple(size) if size else spec_sz["image"], crop,
                      out_dir / f"img_{i:02d}.jpg")
        sources.append((f"img_{i:02d}.jpg", img_src))

    clips_dir = out_dir / "clips"
    for i, clip in enumerate(plan.get("clips", [])):
        clips_dir.mkdir(exist_ok=True)
        _assemble_clip(clip, clips_dir / f"clip_{i:02d}.mp4")
        sources.append((f"clips/clip_{i:02d}.mp4", clip["src"]))

    meta = {"type": ptype, "title": plan.get("title", out_dir.name),
            "tags": plan.get("tags", []), "body_text": plan.get("body_text", ""),
            "sources": [{"out": o, "from": Path(s).name} for o, s in sources]}
    (out_dir / "_meta.json").write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")
    mark_runtime_activity(f"媒体组装：生成 {len(sources)} 个素材到 {display_path(out_dir)}")
    print(f"[assemble] 已生成 {len(sources)} 个素材到 {out_dir}")
    return 0


def _assemble_clip(clip: dict, out_path: Path) -> None:
    cmd = ["ffmpeg", "-y", "-i", clip["src"], "-ss", str(clip.get("start", 0))]
    if "end" in clip:
        cmd += ["-t", str(clip["end"] - clip.get("start", 0))]
    if clip.get("subtitle"):
        srt = out_path.with_suffix(".srt")
        srt.write_text(f"1\n00:00:00,000 --> 00:00:30,000\n{clip['subtitle']}\n", encoding="utf-8")
        cmd += ["-vf", f"subtitles={srt}"]
    cmd += ["-c:v", "libx264", "-crf", "23", str(out_path)]
    subprocess.run(cmd, check=True, capture_output=True)


# ─────────────────────────── publish ───────────────────────────

def cmd_package(args) -> int:
    in_dir = Path(args.in_dir)
    platform = args.platform
    meta_path = in_dir / "_meta.json"
    meta = json.loads(meta_path.read_text(encoding="utf-8")) if meta_path.exists() else {}

    if not args.allow_write:
        print(f"[dry-run] 将打包 {platform} 成品包到 {in_dir}/{platform}/（加 --allow-write 执行）")
        return 0

    out_dir = in_dir / platform
    out_dir.mkdir(parents=True, exist_ok=True)
    spec_sz = PLATFORM_SPEC.get(platform, PLATFORM_SPEC["xiaohongshu"])
    imgs = sorted(in_dir.glob("*.jpg"))
    clips = sorted((in_dir / "clips").glob("*.mp4"))
    order = []
    for p in imgs[: spec_sz["max_images"]]:
        size = spec_sz["cover"] if p.name == "cover.jpg" else spec_sz["image"]
        _resize_image(p, size, None, out_dir / p.name)
        order.append(p.name)
    if clips:
        clips_out = out_dir / "clips"
        clips_out.mkdir(exist_ok=True)
        for p in clips:
            shutil.copy2(p, clips_out / p.name)
            order.append(f"clips/{p.name}")
    (out_dir / "publish-checklist.md").write_text(_build_checklist(platform, meta, order), encoding="utf-8")
    mark_runtime_activity(f"发布打包：{platform} 成品包到 {display_path(out_dir)}（{len(order)} 个素材）")
    print(f"[package] {platform} 成品包就绪：{out_dir}（{len(order)} 个素材 + publish-checklist.md）")
    return 0


def _build_checklist(platform: str, meta: dict, order: list[str]) -> str:
    title = meta.get("title", "")
    body = meta.get("body_text", "")
    tags = meta.get("tags", [])
    sources = meta.get("sources", [])
    pname = {"xiaohongshu": "小红书", "moments": "朋友圈", "wechat_group": "家长群"}.get(platform, platform)
    lines = [f"# 发布清单 - {title}", "", f"## 平台\n{pname}", ""]
    if platform == "xiaohongshu":
        lines += [f"## 标题\n{title}", ""]
    lines += [f"## 正文\n{body}", "", "## 素材顺序"]
    for i, name in enumerate(order, 1):
        lines.append(f"{i}. {name}")
    lines.append("")
    if platform == "xiaohongshu" and tags:
        lines += ["## 话题标签", " ".join(f"#{t}" for t in tags), ""]
    lines += ["## 素材来源（仅文件名，不含绝对路径）"]
    for s in sources:
        lines.append(f"- {s.get('out')} ← {s.get('from')}")
    lines += ["", "## 状态\n待发布", ""]
    return "\n".join(lines)


# ─────────────────────────── init ───────────────────────────

def cmd_init(args) -> int:
    try:
        init_db()
        print(f"  ✓ LanceDB 库就绪：{LANCE_DIR.relative_to(ROOT)}（items + concepts + graph_edges 表）")
    except Exception as e:  # noqa: BLE001
        print(f"  ! LanceDB 初始化失败（{e}）；安装 lancedb 后重试")
        return 1
    try:
        get_embedder()
        print(f"  ✓ 向量模型就绪：{EMBED_MODEL}（{EMBED_DIM}d, device={_pick_device()}）")
    except Exception as e:  # noqa: BLE001
        print(f"  ! 向量模型加载跳过（{e}）；pip install sentence-transformers")
    print(f"  · reranker：{RERANK_MODEL}（检索时懒加载）")
    return 0


# ─────────────────────────── graph 扩散（供 skill 关联素材） ───────────────────────────

def cmd_related(args) -> int:
    """给定 doc_id，经二部图取「同 concept」的兄弟 doc（content-generate 步骤 2 关联素材扩散用）。"""
    itbl = get_items_table(create=False)
    getbl = get_graph_edges_table(create=False)
    if itbl is None or getbl is None:
        print("（图谱为空，请先 kb ingest）")
        return 0
    from collections import Counter
    mine = getbl.search().where(f"doc_id = '{args.id}'").limit(100000).to_list()
    cids = {e["concept_id"] for e in mine}
    if not cids:
        print("（该 doc 无关联 concept）")
        return 0
    sib = getbl.search().where(f"concept_id IN ({_sql_in(cids)})").limit(100000).to_list()
    score: Counter = Counter()
    for e in sib:
        if e["doc_id"] != args.id:
            score[e["doc_id"]] += e.get("weight", 1.0)
    top = [d for d, _ in score.most_common(args.topk)]
    docs = itbl.search().where(f"id IN ({_sql_in(top)})").limit(args.topk).to_list() if top else []
    order = {d: i for i, d in enumerate(top)}
    docs.sort(key=lambda d: order.get(d["id"], 1 << 30))
    out = [{"id": d["id"], "modality": d.get("modality"), "title": d.get("title"),
            "source_path": d.get("source_path"), "shared": round(float(score[d["id"]]), 2)} for d in docs]
    if args.json:
        print(json.dumps(out, ensure_ascii=False, indent=2))
    else:
        for i, d in enumerate(out, 1):
            print(f"{i:>2} | {d['modality']:<5} | {(d['title'] or '')[:24]:<24} | shared={d['shared']} | {d['id']}")
    return 0


# ─────────────────────────── CLI ───────────────────────────

def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="content-runtime", description="本地 KB 与媒体组装 CLI")
    sub = p.add_subparsers(dest="domain", required=True)

    sub.add_parser("init", help="初始化 LanceDB 库与表").set_defaults(func=cmd_init)

    kb = sub.add_parser("kb", help="知识库").add_subparsers(dest="action", required=True)

    ing = kb.add_parser("ingest")
    ing.add_argument("--src", required=True)
    ing.add_argument("--modality", default="auto", choices=["auto", "doc", "image", "video"])
    ing.add_argument("--limit", type=int, default=0)
    ing.add_argument("--resume", action="store_true")
    ing.add_argument("--allow-write", action="store_true")
    ing.set_defaults(func=cmd_ingest)

    sea = kb.add_parser("search")
    sea.add_argument("--query", required=True)
    sea.add_argument("--modality", default="all", choices=["doc", "image", "video", "all"])
    sea.add_argument("--topk", type=int, default=10)
    sea.add_argument("--json", action="store_true")
    sea.add_argument("--no-log", action="store_true", help="不写 workspace/kb/search-log.jsonl")
    sea.add_argument("--no-touch", action="store_true", help="不更新命中条目的 last_hit_at")
    sea.set_defaults(func=cmd_search)

    idx = kb.add_parser("index")
    idx.add_argument("--rebuild", default="all", choices=["fts", "vector", "graph", "all"])
    idx.add_argument("--allow-write", action="store_true")
    idx.set_defaults(func=cmd_index)

    gc = kb.add_parser("gc")
    gc.add_argument("--older-than", default="180d")
    gc.add_argument("--dry-run", action="store_true")
    gc.add_argument("--allow-write", action="store_true")
    gc.set_defaults(func=cmd_gc)

    rel = kb.add_parser("related")
    rel.add_argument("--id", required=True)
    rel.add_argument("--topk", type=int, default=10)
    rel.add_argument("--json", action="store_true")
    rel.set_defaults(func=cmd_related)

    text = sub.add_parser("text", help="文案草稿").add_subparsers(dest="action", required=True)
    draft = text.add_parser("draft")
    draft.add_argument("--brief", required=True)
    draft.add_argument("--platform", required=True, choices=["xiaohongshu", "moments", "wechat_group"])
    draft.add_argument("--style", default="知识科普")
    draft.add_argument("--sources", help="kb search --json 的结果文件")
    draft.add_argument("--out", help="输出 draft.json；缺省时打印到 stdout")
    draft.add_argument("--allow-write", action="store_true")
    draft.set_defaults(func=cmd_text_draft)

    plan = sub.add_parser("plan", help="组装计划").add_subparsers(dest="action", required=True)
    build = plan.add_parser("build")
    build.add_argument("--draft", required=True)
    build.add_argument("--sources", help="覆盖 draft 中 sources 的素材 JSON")
    build.add_argument("--platform", choices=["xiaohongshu", "moments", "wechat_group"])
    build.add_argument("--out", required=True)
    build.add_argument("--allow-write", action="store_true")
    build.set_defaults(func=cmd_plan_build)

    media = sub.add_parser("media", help="媒体").add_subparsers(dest="action", required=True)
    pr = media.add_parser("probe")
    pr.add_argument("file")
    pr.set_defaults(func=cmd_probe)
    asm = media.add_parser("assemble")
    asm.add_argument("--spec", required=True)
    asm.add_argument("--out", required=True)
    asm.add_argument("--allow-write", action="store_true")
    asm.set_defaults(func=cmd_assemble)

    pub = sub.add_parser("publish", help="发布打包").add_subparsers(dest="action", required=True)
    pk = pub.add_parser("package")
    pk.add_argument("--platform", required=True, choices=["xiaohongshu", "moments", "wechat_group"])
    pk.add_argument("--in", dest="in_dir", required=True)
    pk.add_argument("--allow-write", action="store_true")
    pk.set_defaults(func=cmd_package)

    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
