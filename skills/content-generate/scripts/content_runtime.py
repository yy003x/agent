#!/usr/bin/env python3
"""content-runtime —— 本地知识库与媒体组装统一 CLI（P1–P3）。

设计依据：03-content-agent.md / content-agent-architecture.md L5-L6。

用法：
  python content_runtime.py init                                   建库（catalog.db + ChromaDB）
  python content_runtime.py kb ingest  --src <folder> [--modality auto|doc|image|video]
                                        [--limit N] [--resume] --allow-write
  python content_runtime.py kb search  --query "<text>" [--modality doc|image|video|all]
                                        [--topk N] [--json]
  python content_runtime.py kb index   --rebuild [fts|vector|graph|all] --allow-write
  python content_runtime.py kb gc      --older-than 180d [--dry-run] --allow-write
  python content_runtime.py media probe    <file>
  python content_runtime.py media assemble --spec <plan.json> --out <dir> --allow-write
  python content_runtime.py publish package --platform xiaohongshu|moments --in <dir> --allow-write

写门禁：ingest / index / gc / assemble / package 必须带 --allow-write，否则仅 dry-run。
重依赖（chromadb / sentence-transformers / anthropic / PIL / pdfplumber / ffmpeg）按需延迟导入。
"""

from __future__ import annotations

import argparse
import base64
import hashlib
import json
import os
import shutil
import sqlite3
import subprocess
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

# ─────────────────────────── 全局常量 ───────────────────────────

ROOT = Path(__file__).resolve().parents[3]   # skills/content-generate/scripts/ → 项目根
KB_DIR = ROOT / "workspace" / "kb"
CATALOG_DB = KB_DIR / "catalog.db"
VECTOR_DIR = KB_DIR / "vector"
MEDIA_STORE = ROOT / "workspace" / "media-store"
SEARCH_LOG = KB_DIR / "search-log.jsonl"
RUNS_DIR = ROOT / "runs" / "content-runtime"

COLLECTION_NAME = "content_kb"
EMBED_MODEL = os.environ.get("EMBED_MODEL", "BAAI/bge-m3")
VISION_MODEL = os.environ.get("VISION_MODEL", "claude-haiku-4-5-20251001")  # caption 高频，用 haiku 省成本

DOC_EXT = {".md", ".txt", ".pdf"}
IMAGE_EXT = {".jpg", ".jpeg", ".png", ".webp"}
VIDEO_EXT = {".mp4", ".mov", ".m4v", ".webm"}

MEDIA_TYPE = {".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".png": "image/png", ".webp": "image/webp"}

# 平台成品规格
PLATFORM_SPEC = {
    "xiaohongshu": {"cover": (1080, 1080), "image": (1080, 1440), "max_images": 9},
    "moments": {"cover": (1080, 1080), "image": (1080, 1080), "max_images": 9},
}

CAPTION_PROMPT = (
    "描述这张图片的主题、视觉风格、构图要素，以及与教育/图书内容的关联。100字以内。"
    "最后另起一行列出5个精确标签，格式：标签：tag1,tag2,tag3,tag4,tag5"
)


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def log_run(msg: str) -> None:
    RUNS_DIR.mkdir(parents=True, exist_ok=True)
    line = f"{now_iso()} {msg}\n"
    (RUNS_DIR / f"{datetime.now().strftime('%Y-%m-%d')}.log").open("a", encoding="utf-8").write(line)


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
    raw = f"{source_path}:{mtime}:{chunk_index}"
    return hashlib.sha256(raw.encode()).hexdigest()[:16]


def extract_tags(path: Path) -> list[str]:
    """从路径推断标签：父目录名 + 文件名（去扩展名）。"""
    tags = []
    if path.parent.name:
        tags.append(path.parent.name)
    tags.append(path.stem)
    return [t for t in tags if t]


# ─────────────────────────── SQLite ───────────────────────────

SCHEMA = """
CREATE TABLE IF NOT EXISTS items (
    id          TEXT PRIMARY KEY,
    modality    TEXT NOT NULL,
    source_path TEXT NOT NULL,
    title       TEXT,
    tags        TEXT,
    caption     TEXT,
    transcript  TEXT,
    chunk_index INTEGER DEFAULT 0,
    duration_s  REAL,
    width       INTEGER,
    height      INTEGER,
    file_hash   TEXT,
    ingest_at   TEXT,
    last_hit_at TEXT,
    archived_at TEXT,
    status      TEXT DEFAULT 'active'
);

CREATE VIRTUAL TABLE IF NOT EXISTS items_fts USING fts5(
    id UNINDEXED, title, tags, caption, transcript, tokenize = "trigram"
);

CREATE TRIGGER IF NOT EXISTS items_fts_insert AFTER INSERT ON items BEGIN
    INSERT INTO items_fts(id, title, tags, caption, transcript)
    VALUES (new.id, new.title, new.tags, new.caption, new.transcript);
END;

CREATE TRIGGER IF NOT EXISTS items_fts_delete AFTER DELETE ON items BEGIN
    DELETE FROM items_fts WHERE id = old.id;
END;

CREATE TABLE IF NOT EXISTS edges (
    src TEXT NOT NULL, dst TEXT NOT NULL, rel TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS edges_src ON edges(src);
"""


def connect_db() -> sqlite3.Connection:
    KB_DIR.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(CATALOG_DB)
    conn.row_factory = sqlite3.Row
    return conn


def init_db() -> None:
    conn = connect_db()
    conn.executescript(SCHEMA)
    conn.commit()
    conn.close()


# ─────────────────────────── ChromaDB ───────────────────────────

_collection = None


def get_collection():
    """惰性获取 ChromaDB collection（带本地 bge-m3 embedding）。"""
    global _collection
    if _collection is not None:
        return _collection
    import chromadb
    from chromadb.utils import embedding_functions

    ef = embedding_functions.SentenceTransformerEmbeddingFunction(model_name=EMBED_MODEL)
    client = chromadb.PersistentClient(path=str(VECTOR_DIR))
    _collection = client.get_or_create_collection(
        name=COLLECTION_NAME,
        embedding_function=ef,
        metadata={"hnsw:space": "cosine"},
    )
    return _collection


# ─────────────────────────── Claude vision ───────────────────────────

_claude = None


def get_claude():
    global _claude
    if _claude is None:
        import anthropic
        _claude = anthropic.Anthropic()  # 读 ANTHROPIC_API_KEY
    return _claude


def caption_image_file(path: Path) -> tuple[str, list[str]]:
    """调 Claude vision，返回 (caption, tags)。"""
    mt = MEDIA_TYPE.get(path.suffix.lower(), "image/jpeg")
    b64 = base64.b64encode(path.read_bytes()).decode()
    resp = get_claude().messages.create(
        model=VISION_MODEL,
        max_tokens=300,
        messages=[{
            "role": "user",
            "content": [
                {"type": "image", "source": {"type": "base64", "media_type": mt, "data": b64}},
                {"type": "text", "text": CAPTION_PROMPT},
            ],
        }],
    )
    text = resp.content[0].text
    return parse_caption_and_tags(text)


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


def already_ingested(conn: sqlite3.Connection, source_path: str, fhash: str) -> bool:
    row = conn.execute(
        "SELECT 1 FROM items WHERE source_path = ? AND file_hash = ? LIMIT 1",
        (source_path, fhash),
    ).fetchone()
    return row is not None


def upsert_item(conn: sqlite3.Connection, item: dict) -> None:
    conn.execute("DELETE FROM items WHERE id = ?", (item["id"],))
    cols = ("id", "modality", "source_path", "title", "tags", "caption", "transcript",
            "chunk_index", "duration_s", "width", "height", "file_hash", "ingest_at", "status")
    conn.execute(
        f"INSERT INTO items ({','.join(cols)}) VALUES ({','.join('?' * len(cols))})",
        tuple(item.get(c) for c in cols),
    )


def write_graph_edges(conn: sqlite3.Connection, item_id: str, title: str, tags: list[str]) -> None:
    """同书名 same_book、同标签 same_tag 互连。"""
    for tag in tags:
        for (other,) in conn.execute(
            "SELECT id FROM items WHERE id != ? AND tags LIKE ?", (item_id, f'%"{tag}"%')
        ).fetchall():
            conn.execute("INSERT INTO edges (src, dst, rel) VALUES (?, ?, 'same_tag')", (item_id, other))
    if title:
        for (other,) in conn.execute(
            "SELECT id FROM items WHERE id != ? AND title = ?", (item_id, title)
        ).fetchall():
            conn.execute("INSERT INTO edges (src, dst, rel) VALUES (?, ?, 'same_book')", (item_id, other))


def add_vector(item_id: str, text: str, modality: str) -> None:
    col = get_collection()
    col.upsert(ids=[item_id], documents=[text[:2000]], metadatas=[{"modality": modality}])


def ingest_doc(conn: sqlite3.Connection, path: Path) -> int:
    text = parse_text(path)
    chunks = chunk_text(text)
    tags = extract_tags(path)
    for i, chunk in enumerate(chunks):
        item_id = make_id(str(path), i)
        caption = chunk[:200]
        item = dict(id=item_id, modality="doc", source_path=str(path), title=path.stem,
                    tags=json.dumps(tags, ensure_ascii=False), caption=caption, transcript=None,
                    chunk_index=i, duration_s=None, width=None, height=None,
                    file_hash=file_hash(path), ingest_at=now_iso(), status="active")
        upsert_item(conn, item)
        add_vector(item_id, f"{path.stem} {caption}", "doc")
    return max(len(chunks), 1)


def copy_to_store(path: Path) -> Path:
    MEDIA_STORE.mkdir(parents=True, exist_ok=True)
    dest = MEDIA_STORE / f"{file_hash(path)[:16]}{path.suffix.lower()}"
    if not dest.exists():
        shutil.copy2(path, dest)
    return dest


def ingest_image(conn: sqlite3.Connection, path: Path) -> int:
    caption, ai_tags = caption_image_file(path)
    dest = copy_to_store(path)
    tags = list(dict.fromkeys(extract_tags(path) + ai_tags))
    width = height = None
    try:
        from PIL import Image
        with Image.open(path) as im:
            width, height = im.size
    except Exception:
        pass
    item_id = make_id(str(dest), 0)
    item = dict(id=item_id, modality="image", source_path=str(dest), title=path.stem,
                tags=json.dumps(tags, ensure_ascii=False), caption=caption, transcript=None,
                chunk_index=0, duration_s=None, width=width, height=height,
                file_hash=file_hash(path), ingest_at=now_iso(), status="active")
    upsert_item(conn, item)
    add_vector(item_id, f"{path.stem} {caption} {' '.join(tags)}", "image")
    write_graph_edges(conn, item_id, path.stem, tags)
    return 1


def extract_frames(path: Path, interval: int = 30, max_count: int = 10) -> list[Path]:
    tmp = RUNS_DIR / "frames" / file_hash(path)[:16]
    tmp.mkdir(parents=True, exist_ok=True)
    out_pat = str(tmp / "frame_%03d.jpg")
    subprocess.run(
        ["ffmpeg", "-y", "-i", str(path), "-vf", f"fps=1/{interval}",
         "-frames:v", str(max_count), out_pat],
        check=True, capture_output=True,
    )
    return sorted(tmp.glob("frame_*.jpg"))


def probe_video(path: Path) -> tuple[float, int, int]:
    out = subprocess.run(
        ["ffprobe", "-v", "error", "-select_streams", "v:0",
         "-show_entries", "stream=width,height:format=duration",
         "-of", "json", str(path)],
        check=True, capture_output=True, text=True,
    )
    data = json.loads(out.stdout)
    stream = (data.get("streams") or [{}])[0]
    duration = float(data.get("format", {}).get("duration", 0) or 0)
    return duration, int(stream.get("width", 0) or 0), int(stream.get("height", 0) or 0)


def ingest_video(conn: sqlite3.Connection, path: Path) -> int:
    frames = extract_frames(path)
    frame_caps = []
    for fr in frames:
        cap, _ = caption_image_file(fr)
        frame_caps.append(cap)
    transcript = "\n".join(f"[{i*30}s] {c}" for i, c in enumerate(frame_caps))
    try:
        from faster_whisper import WhisperModel  # noqa: F401
        # 字幕默认后置：仅当显式安装 faster-whisper 才尝试，失败忽略
    except Exception:
        pass
    duration, width, height = probe_video(path)
    dest = copy_to_store(path)
    tags = extract_tags(path)
    caption = frame_caps[0] if frame_caps else ""
    item_id = make_id(str(dest), 0)
    item = dict(id=item_id, modality="video", source_path=str(dest), title=path.stem,
                tags=json.dumps(tags, ensure_ascii=False), caption=caption, transcript=transcript,
                chunk_index=0, duration_s=duration, width=width, height=height,
                file_hash=file_hash(path), ingest_at=now_iso(), status="active")
    upsert_item(conn, item)
    add_vector(item_id, f"{path.stem} {caption} {transcript[:500]}", "video")
    write_graph_edges(conn, item_id, path.stem, tags)
    return 1


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
    conn = connect_db()
    done = skipped = 0
    for p in files:
        modality = detect_modality(p)
        if args.resume and already_ingested(conn, str(p), file_hash(p)):
            skipped += 1
            continue
        try:
            if modality == "doc":
                n = ingest_doc(conn, p)
            elif modality == "image":
                n = ingest_image(conn, p)
            else:
                n = ingest_video(conn, p)
            conn.commit()
            done += 1
            log_run(f"ingest ok [{modality}] {p} (+{n})")
            print(f"  ✓ [{modality}] {p.name}")
        except Exception as e:  # noqa: BLE001
            conn.rollback()
            log_run(f"ingest FAIL {p}: {e}")
            print(f"  ✗ [{modality}] {p.name}: {e}")
    conn.close()
    print(f"[ingest] 完成：{done} 成功 / {skipped} 跳过 / 共 {len(files)}")
    return 0


# ─────────────────────────── search ───────────────────────────

def cmd_search(args) -> int:
    init_db()
    conn = connect_db()
    topk = args.topk
    modality = args.modality

    hits: dict[str, dict] = {}

    # 1) 向量检索
    try:
        col = get_collection()
        where = None if modality == "all" else {"modality": modality}
        res = col.query(query_texts=[args.query], n_results=topk * 2, where=where)
        ids = res.get("ids", [[]])[0]
        dists = res.get("distances", [[]])[0] or [None] * len(ids)
        for i, _id in enumerate(ids):
            hits.setdefault(_id, {})["score"] = 1 - dists[i] if dists[i] is not None else None
    except Exception as e:  # noqa: BLE001
        print(f"[warn] 向量检索不可用（{e}），回退 FTS", file=sys.stderr)

    # 2) FTS 检索
    try:
        q = args.query.replace('"', " ")
        sql = "SELECT id FROM items_fts WHERE items_fts MATCH ? LIMIT ?"
        for (fid,) in conn.execute(sql, (q, topk * 2)).fetchall():
            hits.setdefault(fid, {}).setdefault("score", 0.0)
    except Exception as e:  # noqa: BLE001
        print(f"[warn] FTS 检索失败：{e}", file=sys.stderr)

    # 3) 取元数据 + 过滤 modality + 排序
    rows = []
    for _id, meta in hits.items():
        r = conn.execute(
            "SELECT id, modality, source_path, title, caption FROM items WHERE id = ? AND status='active'",
            (_id,),
        ).fetchone()
        if not r:
            continue
        if modality != "all" and r["modality"] != modality:
            continue
        rows.append({**dict(r), "score": meta.get("score") or 0.0})
    rows.sort(key=lambda x: x["score"], reverse=True)
    rows = rows[:topk]

    # 4) 更新 last_hit_at + 写检索日志
    ts = now_iso()
    for r in rows:
        conn.execute("UPDATE items SET last_hit_at = ? WHERE id = ?", (ts, r["id"]))
    conn.commit()
    conn.close()
    _write_search_log(args.query, modality, topk, [r["id"] for r in rows])

    if args.json:
        print(json.dumps(rows, ensure_ascii=False, indent=2))
    else:
        if not rows:
            print("（无命中。可尝试拆分关键词或先 ingest 素材）")
        for i, r in enumerate(rows, 1):
            cap = (r["caption"] or "").replace("\n", " ")[:60]
            print(f"{i:>2} | {r['modality']:<5} | {r['title']:<24} | {cap} | {r['id']}")
    return 0


def _write_search_log(query: str, modality: str, topk: int, hit_ids: list[str]) -> None:
    KB_DIR.mkdir(parents=True, exist_ok=True)
    rec = {"ts": now_iso(), "query": query, "modality": modality,
           "topk": topk, "hits": len(hit_ids), "hit_ids": hit_ids}
    with SEARCH_LOG.open("a", encoding="utf-8") as f:
        f.write(json.dumps(rec, ensure_ascii=False) + "\n")


# ─────────────────────────── index / gc ───────────────────────────

def cmd_index(args) -> int:
    if not args.allow_write:
        print(f"[dry-run] 将重建索引：{args.rebuild}（加 --allow-write 执行）")
        return 0
    init_db()
    conn = connect_db()
    target = args.rebuild
    if target in ("fts", "all"):
        conn.execute("DELETE FROM items_fts")
        conn.execute(
            "INSERT INTO items_fts(id,title,tags,caption,transcript) "
            "SELECT id,title,tags,caption,transcript FROM items"
        )
        print("  ✓ FTS 重建完成")
    if target in ("graph", "all"):
        conn.execute("DELETE FROM edges")
        for r in conn.execute("SELECT id, title, tags FROM items").fetchall():
            tags = json.loads(r["tags"] or "[]")
            write_graph_edges(conn, r["id"], r["title"], tags)
        print("  ✓ graph 重建完成")
    if target in ("vector", "all"):
        for r in conn.execute("SELECT id, modality, title, caption, transcript FROM items").fetchall():
            txt = f"{r['title']} {r['caption'] or ''} {(r['transcript'] or '')[:500]}"
            add_vector(r["id"], txt, r["modality"])
        print("  ✓ vector 重建完成")
    conn.commit()
    conn.close()
    return 0


def _parse_days(s: str) -> int:
    return int(s[:-1]) if s.endswith("d") else int(s)


def cmd_gc(args) -> int:
    init_db()
    conn = connect_db()
    days = _parse_days(args.older_than)
    cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()
    archive_cutoff = (datetime.now(timezone.utc) - timedelta(days=90)).isoformat()

    to_archive = conn.execute(
        "SELECT id FROM items WHERE status='active' AND (last_hit_at IS NULL OR last_hit_at < ?)",
        (cutoff,),
    ).fetchall()
    to_delete = conn.execute(
        "SELECT id, source_path FROM items WHERE status='archived' AND archived_at < ?",
        (archive_cutoff,),
    ).fetchall()

    print(f"待归档：{len(to_archive)} 条（last_hit_at < {cutoff[:10]}）")
    print(f"待删除（归档满 90 天）：{len(to_delete)} 条")

    if args.dry_run or not args.allow_write:
        print("[dry-run] 未执行实际清理（去掉 --dry-run 且加 --allow-write 生效）")
        conn.close()
        return 0

    ts = now_iso()
    for r in to_archive:
        conn.execute("UPDATE items SET status='archived', archived_at=? WHERE id=?", (ts, r["id"]))
    col = None
    try:
        col = get_collection()
    except Exception:
        pass
    for r in to_delete:
        sp = Path(r["source_path"])
        if MEDIA_STORE in sp.parents and sp.exists():
            sp.unlink()  # 只删 ingest 复制进来的副本，不动用户原始文件
        conn.execute("UPDATE items SET status='deleted' WHERE id=?", (r["id"],))
        if col is not None:
            try:
                col.delete(ids=[r["id"]])
            except Exception:
                pass
    conn.commit()
    conn.close()
    print(f"[gc] 已归档 {len(to_archive)} / 删除 {len(to_delete)}")
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
    spec = Path(args.spec)
    plan = json.loads(spec.read_text(encoding="utf-8"))
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

    # 封面
    if plan.get("cover"):
        cov = plan["cover"]
        cov_src = cov["src"] if isinstance(cov, dict) else cov
        _resize_image(Path(cov_src), spec_sz["cover"], (cov.get("crop") if isinstance(cov, dict) else None),
                      out_dir / "cover.jpg")
        sources.append(("cover.jpg", cov_src))

    # 配图
    for i, img in enumerate(plan.get("images", [])):
        img_src = img["src"] if isinstance(img, dict) else img
        size = img.get("resize") if isinstance(img, dict) else None
        crop = img.get("crop") if isinstance(img, dict) else None
        _resize_image(Path(img_src), tuple(size) if size else spec_sz["image"], crop,
                      out_dir / f"img_{i:02d}.jpg")
        sources.append((f"img_{i:02d}.jpg", img_src))

    # 视频片段
    clips_dir = out_dir / "clips"
    for i, clip in enumerate(plan.get("clips", [])):
        clips_dir.mkdir(exist_ok=True)
        _assemble_clip(clip, clips_dir / f"clip_{i:02d}.mp4")
        sources.append((f"clips/clip_{i:02d}.mp4", clip["src"]))

    # 元信息（供 publish package 读取，不写绝对路径到对外文件）
    meta = {
        "type": ptype,
        "title": plan.get("title", out_dir.name),
        "tags": plan.get("tags", []),
        "body_text": plan.get("body_text", ""),
        "sources": [{"out": o, "from": Path(s).name} for o, s in sources],
    }
    (out_dir / "_meta.json").write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")
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

    # 收集已组装图片，复制并按平台规格归一
    imgs = sorted([p for p in in_dir.glob("*.jpg")])
    order = []
    for p in imgs[: spec_sz["max_images"]]:
        size = spec_sz["cover"] if p.name == "cover.jpg" else spec_sz["image"]
        _resize_image(p, size, None, out_dir / p.name)
        order.append(p.name)

    checklist = _build_checklist(platform, meta, order)
    (out_dir / "publish-checklist.md").write_text(checklist, encoding="utf-8")
    print(f"[package] {platform} 成品包就绪：{out_dir}（{len(order)} 图 + publish-checklist.md）")
    return 0


def _build_checklist(platform: str, meta: dict, order: list[str]) -> str:
    title = meta.get("title", "")
    body = meta.get("body_text", "")
    tags = meta.get("tags", [])
    sources = meta.get("sources", [])
    pname = {"xiaohongshu": "小红书", "moments": "朋友圈"}.get(platform, platform)

    # 标题 / 正文：小红书拆分，朋友圈合并（朋友圈无标题）
    title_line, body_text = title, body
    if "---标题---" in body:  # 兼容 prompt 模板输出直接塞进 body 的情况
        body_text = body

    lines = [f"# 发布清单 - {title_line}", "", f"## 平台\n{pname}", ""]
    if platform == "xiaohongshu":
        lines += [f"## 标题\n{title_line}", ""]
    lines += [f"## 正文\n{body_text}", "", "## 配图顺序"]
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
    init_db()
    print(f"  ✓ catalog.db 已建：{CATALOG_DB.relative_to(ROOT)}")
    try:
        get_collection()
        print(f"  ✓ ChromaDB collection '{COLLECTION_NAME}' 就绪（embed={EMBED_MODEL}）")
    except Exception as e:  # noqa: BLE001
        print(f"  ! ChromaDB 初始化跳过（{e}）；安装 chromadb + sentence-transformers 后重试")
    return 0


# ─────────────────────────── CLI ───────────────────────────

def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="content-runtime", description="本地 KB 与媒体组装 CLI")
    sub = p.add_subparsers(dest="domain", required=True)

    sub.add_parser("init", help="初始化 catalog.db 与 ChromaDB").set_defaults(func=cmd_init)

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
    pk.add_argument("--platform", required=True, choices=["xiaohongshu", "moments"])
    pk.add_argument("--in", dest="in_dir", required=True)
    pk.add_argument("--allow-write", action="store_true")
    pk.set_defaults(func=cmd_package)

    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
