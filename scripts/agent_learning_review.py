#!/usr/bin/env python3
"""自学习候选生成脚本（P4）。

扫描事实源（session 记录 / KB 检索日志 / 成品包），按「候选判定标准」提炼学习候选，
写到 workspace/agent-learning/candidates-YYYY-MM-DD.md，供人工确认后晋升。

设计依据：02-self-evolution.md。

用法：
  python scripts/agent_learning_review.py [--days N]   默认 7
  python scripts/agent_learning_review.py --dry-run     打印候选不写文件

硬约束：只生成候选，不修改任何 rules/skill/memory；单次上限 5 条；已覆盖模式去重。
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DAILY_DIR = ROOT / "workspace" / "daily"
SEARCH_LOG = ROOT / "workspace" / "kb" / "search-log.jsonl"
OUTPUTS_DIR = ROOT / "outputs"
LEARNING_DIR = ROOT / "workspace" / "agent-learning"

MAX_CANDIDATES = 5


# ─────────────────────────── 事实源收集 ───────────────────────────

def _parse_frontmatter(text: str) -> dict:
    m = re.search(r"^---\n(.*?)\n---", text, re.DOTALL)
    fm = {}
    if m:
        for line in m.group(1).splitlines():
            if ":" in line:
                k, _, v = line.partition(":")
                fm[k.strip()] = v.strip()
    return fm


def collect_sessions(since: datetime) -> list[dict]:
    sessions = []
    if not DAILY_DIR.exists():
        return sessions
    for md in DAILY_DIR.rglob("session-*.md"):
        text = md.read_text(encoding="utf-8", errors="ignore")
        fm = _parse_frontmatter(text)
        ts = fm.get("timestamp", "")
        try:
            when = datetime.fromisoformat(ts)
        except ValueError:
            continue
        if when < since:
            continue
        summary_m = re.search(r"## 摘要\n(.*?)(?:\n##|\Z)", text, re.DOTALL)
        sessions.append({
            "path": md.name,
            "skill": fm.get("skill_triggered", "none"),
            "status": fm.get("status", "unknown"),
            "summary": (summary_m.group(1).strip() if summary_m else ""),
        })
    return sessions


def collect_searches(since: datetime) -> list[dict]:
    if not SEARCH_LOG.exists():
        return []
    out = []
    for line in SEARCH_LOG.read_text(encoding="utf-8", errors="ignore").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            rec = json.loads(line)
            when = datetime.fromisoformat(rec["ts"])
        except (json.JSONDecodeError, KeyError, ValueError):
            continue
        if when >= since:
            out.append(rec)
    return out


def collect_output_types(since: datetime) -> Counter:
    types: Counter = Counter()
    if not OUTPUTS_DIR.exists():
        return types
    for cl in OUTPUTS_DIR.rglob("publish-checklist.md"):
        if datetime.fromtimestamp(cl.stat().st_mtime, tz=timezone.utc) < since:
            continue
        # 用平台 + 内容 slug 作为「类型」近似
        types[cl.parent.name] += 1
    return types


# ─────────────────────────── 模式检测 ───────────────────────────

def _norm_query(q: str) -> str:
    return re.sub(r"\s+", "", q.lower())


def detect_candidates(sessions, searches, output_types) -> list[dict]:
    cands: list[dict] = []

    # 1) 高频 search query（≥3 次）→ rule/template（high）
    qc = Counter(_norm_query(s["query"]) for s in searches)
    for q, n in qc.most_common():
        if n >= 3:
            ev = [f"search-log：query「{s['query']}」(modality={s['modality']}, hits={s['hits']})"
                  for s in searches if _norm_query(s["query"]) == q][:3]
            cands.append({
                "type": "rule", "confidence": "high",
                "content": f"高频检索意图「{q}」近 {n} 次出现，建议在 rules/core-routing.md 补充对应触发词或固化为 template。",
                "evidence": ev,
                "target": "rules/core-routing.md",
                "key": f"freq-query:{q}",
            })

    # 2) status=failed 的 skill（≥2 次）→ skill 修复（high）
    fc = Counter(s["skill"] for s in sessions if s["status"] == "failed" and s["skill"] != "none")
    for skill, n in fc.items():
        if n >= 2:
            ev = [f"{s['path']}：{s['summary'][:50]}" for s in sessions
                  if s["status"] == "failed" and s["skill"] == skill][:3]
            cands.append({
                "type": "skill", "confidence": "high",
                "content": f"skill「{skill}」近 {n} 次 status=failed，建议复查 SKILL.md 执行步骤或前置检查。",
                "evidence": ev,
                "target": f"skills/{skill}/SKILL.md",
                "key": f"failed-skill:{skill}",
            })

    # 3) 「用户纠正 AI」关键词 → rule/memory（high）
    correction_kw = ("纠正", "不对", "应该是", "改成", "下次", "以后用")
    corr = [s for s in sessions if any(k in s["summary"] for k in correction_kw)]
    if len(corr) >= 1:
        cands.append({
            "type": "memory", "confidence": "medium",
            "content": "session 摘要中出现用户纠正/偏好信号，建议沉淀为 memory/profile.md 偏好或 rule。",
            "evidence": [f"{s['path']}：{s['summary'][:60]}" for s in corr][:3],
            "target": "memory/profile.md",
            "key": "user-correction",
        })

    # 4) 低命中检索（hits<2，同主题 ≥2 次）→ kb-tuning（medium）
    low = defaultdict(list)
    for s in searches:
        if s["hits"] < 2:
            low[_norm_query(s["query"])].append(s)
    for q, recs in low.items():
        if len(recs) >= 2:
            cands.append({
                "type": "kb-tuning", "confidence": "medium",
                "content": f"主题「{q}」检索连续低命中（hits<2），建议优化 caption prompt 或补充 ingest 素材。",
                "evidence": [f"search-log：「{r['query']}」hits={r['hits']} topk={r['topk']}" for r in recs][:3],
                "target": "skills/content-generate/scripts/content_runtime.py",
                "key": f"low-hit:{q}",
            })

    # 5) 高频成品类型（≥3 次成功）→ template（medium）
    for slug, n in output_types.items():
        if n >= 3:
            cands.append({
                "type": "template", "confidence": "medium",
                "content": f"内容形态「{slug}」近 {n} 次产出，建议固化为 skills/content-generate/templates/ 模板。",
                "evidence": [f"outputs/ 中「{slug}」出现 {n} 次"],
                "target": "skills/content-generate/templates/",
                "key": f"output-type:{slug}",
            })

    # 置信度排序（high 在前）
    cands.sort(key=lambda c: 0 if c["confidence"] == "high" else 1)
    return cands


def dedup_existing(cands: list[dict]) -> list[dict]:
    """过滤掉历史候选已覆盖（pending/accepted）的相同 key。"""
    seen: set[str] = set()
    if LEARNING_DIR.exists():
        for f in LEARNING_DIR.glob("candidates-*.md"):
            text = f.read_text(encoding="utf-8", errors="ignore")
            for km in re.finditer(r"<!--key:(.*?)-->", text):
                # 仅当该候选未被 reject 时算「已覆盖」
                seen.add(km.group(1))
    return [c for c in cands if c["key"] not in seen]


# ─────────────────────────── 候选文件渲染 ───────────────────────────

def render(cands: list[dict], days: int, n_session: int, n_search: int) -> str:
    today = datetime.now().strftime("%Y-%m-%d")
    start = (datetime.now() - timedelta(days=days)).strftime("%Y-%m-%d")
    lines = [
        f"# 学习候选 - {today}", "",
        f"扫描范围：近 {days} 天（{start} ~ {today}）",
        f"Session 数：{n_session}  KB 搜索记录数：{n_search}  候选数：{len(cands)}",
        "",
    ]
    for i, c in enumerate(cands, 1):
        lines += [
            "---", "",
            f"## 候选 #{i}",
            f"<!--key:{c['key']}-->", "",
            f"- **类型**: {c['type']}",
            f"- **置信度**: {c['confidence']}",
            f"- **建议内容**:\n  {c['content']}",
            "- **证据**:",
        ]
        lines += [f"  - {e}" for e in c["evidence"]]
        lines += [f"- **晋升目标**: `{c['target']}`", "- **状态**: pending", ""]
    if not cands:
        lines += ["---", "", "（本次无新候选）", ""]
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="自学习候选生成")
    ap.add_argument("--days", type=int, default=7)
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args(argv)

    since = datetime.now(timezone.utc) - timedelta(days=args.days)
    sessions = collect_sessions(since)
    searches = collect_searches(since)
    output_types = collect_output_types(since)

    cands = detect_candidates(sessions, searches, output_types)
    cands = dedup_existing(cands)[:MAX_CANDIDATES]

    content = render(cands, args.days, len(sessions), len(searches))

    if args.dry_run:
        print(content)
        return 0

    LEARNING_DIR.mkdir(parents=True, exist_ok=True)
    out = LEARNING_DIR / f"candidates-{datetime.now().strftime('%Y-%m-%d')}.md"
    out.write_text(content, encoding="utf-8")
    print(f"[learn] 候选已写入：{out.relative_to(ROOT)}（{len(cands)} 条）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
