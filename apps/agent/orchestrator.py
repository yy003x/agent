#!/usr/bin/env python3
"""orchestrator —— 纯 Python 本地 Agent 运行时（Codex CLI 智能后端）。

实现 design/01-framework.md 的核心循环，让 Python 主循环负责流程编排：
  输入 → rules/core-routing.md 语义分类
       → 命中 content-generate：按 SKILL.md 10 步状态机执行（确认门 + --allow-write 门禁）
       → 其余分类走 core-routing.md「默认行为」
       → 实质性任务结束 → scripts/finalize.py record 收尾

用法：
  python apps/agent/orchestrator.py                  进入交互式 REPL
  python apps/agent/orchestrator.py "出一篇数学思维书单"   单轮执行后退出

底层确定性工具：skills/content-generate/scripts/content_runtime.py
（**同进程导入**，向量/精排模型常驻，不走 subprocess 反复重载）。
认知层：apps/agent/brain.py（分类/抽取/润色/对话，无 Codex CLI 自动降级）。
"""
from __future__ import annotations

import io
import json
import re
import subprocess
import sys
from contextlib import redirect_stdout
from datetime import date
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[2]
AGENT_DIR = ROOT / "apps" / "agent"
RUNTIME_DIR = ROOT / "skills" / "content-generate" / "scripts"
OUTPUTS = ROOT / "outputs"

# 同进程导入底层工具与认知层
sys.path.insert(0, str(RUNTIME_DIR))
sys.path.insert(0, str(AGENT_DIR))
import content_runtime as cr  # noqa: E402
import brain  # noqa: E402


# ─────────────────────────── 交互原语 ───────────────────────────

def say(msg: str = "") -> None:
    print(msg)


def ask(prompt: str) -> str:
    try:
        return input(f"{prompt} ").strip()
    except (EOFError, KeyboardInterrupt):
        return ""


def confirm(prompt: str) -> bool:
    return ask(f"{prompt} [y/N]").lower() in ("y", "yes", "是", "确认", "好", "ok")


def today() -> str:
    return date.today().isoformat()


def slugify(text: str) -> str:
    s = re.sub(r"[^\w一-鿿]+", "-", text.strip()).strip("-")
    return (s[:24] or "content")


# ─────────────────────────── content_runtime 同进程封装 ───────────────────────────

def _call(func, **kwargs) -> str:
    """调用 content_runtime 的 cmd_*，捕获其 stdout 返回（供 --json 解析）。"""
    buf = io.StringIO()
    with redirect_stdout(buf):
        func(SimpleNamespace(**kwargs))
    return buf.getvalue().strip()


_DEPS_HINT = ("缺少知识库依赖，请先：pip install -r requirements.txt"
              "（lancedb / sentence-transformers / jieba）")


def kb_init() -> None:
    try:
        say(_call(cr.cmd_init))
    except ModuleNotFoundError:
        say(_DEPS_HINT)


def kb_search(query: str, modality: str = "all", topk: int = 10,
              no_log: bool = False, no_touch: bool = False) -> list[dict]:
    try:
        out = _call(cr.cmd_search, query=query, modality=modality, topk=topk,
                    json=True, no_log=no_log, no_touch=no_touch)
    except ModuleNotFoundError:
        say(_DEPS_HINT)
        return []
    try:
        return json.loads(out)
    except Exception:
        if out:
            say(out)  # 如「知识库为空，请先 kb ingest」
        return []


def text_draft(brief, platform, style, sources_path, out_path) -> dict:
    _call(cr.cmd_text_draft, brief=brief, platform=platform, style=style,
          sources=str(sources_path), out=str(out_path), allow_write=True)
    return json.loads(Path(out_path).read_text(encoding="utf-8"))


def plan_build(draft_path, out_path) -> dict:
    _call(cr.cmd_plan_build, draft=str(draft_path), sources=None, platform=None,
          out=str(out_path), allow_write=True)
    return json.loads(Path(out_path).read_text(encoding="utf-8"))


def media_assemble(plan_path, out_dir) -> str:
    return _call(cr.cmd_assemble, spec=str(plan_path), out=str(out_dir), allow_write=True)


def publish_package(platform, in_dir) -> str:
    return _call(cr.cmd_package, platform=platform, in_dir=str(in_dir), allow_write=True)


# ─────────────────────────── 收尾 ───────────────────────────

def finalize(skill: str, status: str, summary: str, handoff: bool = False) -> None:
    cmd = [sys.executable, str(ROOT / "scripts" / "finalize.py"), "record",
           "--skill", skill, "--status", status, "--summary", summary]
    if handoff:
        cmd.append("--handoff")
    subprocess.run(cmd, cwd=ROOT)


# ─────────────────────────── content-generate 状态机（SKILL.md 步骤 1-10）───────────────────────────

def run_content_generate(user_input: str) -> None:
    say("正在使用 content-generate skill，原因：分类为「内容生成」。")

    # 前置检查：LanceDB 是否已初始化
    if not (ROOT / "workspace" / "kb" / "lance").exists():
        if confirm("知识库未初始化（workspace/kb/lance/ 不存在），现在 init？"):
            kb_init()
        else:
            say("已取消：内容生成需要先初始化并 ingest 知识库。")
            return

    # 步骤 1：解析需求
    req = brain.extract_requirements(user_input)
    say(f"\n[步骤1] 需求解析：主题={req['topic']} 平台={req['platform']} "
        f"形态={req['form']} 风格={req['style']} 数量={req['count']}"
        + (f" 约束={req['constraints']}" if req.get("constraints") else ""))
    edit = ask("确认需求？回车采用，或直接输入修正后的主题：")
    if edit:
        req["topic"] = edit

    # 步骤 2：检索素材（不足时拆词重试）
    rows = kb_search(req["topic"], modality="all", topk=10)
    if len(rows) < 3:
        for kw in re.split(r"[\s，,/]+", req["topic"]):
            if len(kw) >= 2:
                rows = _merge_rows(rows, kb_search(kw, modality="all", topk=10))
            if len(rows) >= 3:
                break
    if len(rows) < 3:
        say(f"[步骤2] KB 命中仅 {len(rows)} 条，素材可能不足。")
        if not confirm("是否仍用现有素材继续？（否则请先 ingest 素材或换主题）"):
            say("已停止。可运行：python skills/content-generate/scripts/content_runtime.py "
                "kb ingest --src <素材目录> --allow-write")
            return

    # 步骤 3：展示候选，等用户筛选
    say(f"\n[步骤3] 候选素材（共 {len(rows)} 条）：")
    say(" #  | 类型   | 标题                     | 描述                         | ID")
    for i, r in enumerate(rows, 1):
        cap = re.sub(r"\s+", " ", (r.get("caption") or "")).strip()[:28]
        say(f"{i:>2}  | {(r.get('modality') or '-'):<5} | {(r.get('title') or '')[:24]:<24} | {cap:<28} | {r.get('id')}")
    sel = ask("选择使用哪些素材（编号如 1,3,5；或「全部」/回车=全部）：")
    selected = rows if (not sel or sel in ("全部", "all")) else _pick(rows, sel)
    if not selected:
        say("未选择任何素材，已停止。")
        return

    # 步骤 4：回读选中素材（doc 取正文摘要补进 caption，作为文案事实）
    facts: list[str] = []
    for r in selected:
        sp = r.get("source_path")
        if r.get("modality") == "doc" and sp and Path(sp).exists() and not r.get("caption"):
            try:
                r["caption"] = Path(sp).read_text(encoding="utf-8", errors="ignore")[:500]
            except Exception:
                pass
        title = r.get("title") or (Path(sp).stem if sp else "素材")
        cap = re.sub(r"\s+", " ", r.get("caption") or "").strip()[:120]
        facts.append(f"{title}：{cap}")

    # 输出目录
    out_dir = OUTPUTS / today() / "content" / slugify(req["topic"])
    out_dir.mkdir(parents=True, exist_ok=True)
    sources_path = out_dir / "sources.json"
    sources_path.write_text(json.dumps(selected, ensure_ascii=False, indent=2), encoding="utf-8")

    # 步骤 5：生成文案草稿 + 认知层润色（带修订循环）
    draft_path = out_dir / "draft.json"
    draft = text_draft(req["topic"], req["platform"], req["style"], sources_path, draft_path)
    for _ in range(3):
        polished = brain.polish_copy(draft, req, facts)
        draft.update(polished)
        draft_path.write_text(json.dumps(draft, ensure_ascii=False, indent=2), encoding="utf-8")
        say(f"\n[步骤5] 文案草稿（{req['platform']}）：")
        say(f"标题：{draft.get('title')}")
        say(f"正文：\n{draft.get('body_text')}")
        if draft.get("tags"):
            say(f"标签：{draft.get('tags')}")
        instr = ask("文案是否满意？回车=满意；或输入修改要求（风格/长度/角度）：")
        if not instr:
            break
        polished = brain.polish_copy(draft, req, facts, instruction=instr)
        draft.update(polished)
        draft_path.write_text(json.dumps(draft, ensure_ascii=False, indent=2), encoding="utf-8")
        say(f"\n[步骤5*] 已按要求修订：\n标题：{draft.get('title')}\n正文：\n{draft.get('body_text')}")
        if not confirm("继续微调？"):
            break

    # 步骤 6：生成 plan.json
    plan_path = out_dir / "plan.json"
    plan = plan_build(draft_path, plan_path)
    say(f"\n[步骤6] 组装计划：封面={'有' if plan.get('cover') else '无'} "
        f"配图 {len(plan.get('images', []))} 张 视频 {len(plan.get('clips', []))} 段 "
        f"跳过素材 {len(plan.get('skipped_assets', []))} 个")
    if not confirm("确认按此计划组装？"):
        say(f"已停在计划阶段。草稿与计划在：{_rel(out_dir)}")
        finalize("content-generate", "partial", f"内容生成中止于组装前：{req['topic']}", handoff=True)
        return

    # 步骤 7-8：组装 + 打包
    say("\n[步骤7] 组装中…")
    media_assemble(plan_path, out_dir)
    say("[步骤8] 打包中…")
    publish_package(req["platform"], out_dir)

    # 步骤 9：预览确认（不自动发布）
    say(f"\n[步骤9] 成品包：{_rel(out_dir)}")
    for p in sorted(out_dir.rglob("*")):
        if p.is_file():
            say(f"  {p.relative_to(out_dir)}")
    say(f"\n标题：{draft.get('title')}\n正文：\n{draft.get('body_text')}")
    if draft.get("tags"):
        say(f"标签：{draft.get('tags')}")
    say("\n成品包已就绪，请检查后**手动发布**（本 Agent 不自动发帖）。")

    # 步骤 10：收尾
    finalize("content-generate", "success",
             f"生成{req['platform']}内容：{req['topic']}，用素材 {len(selected)} 条，产出 {_rel(out_dir)}")
    say(f"\n[收尾] 已记录 session。成品位置：{_rel(out_dir)}")


def _merge_rows(a: list[dict], b: list[dict]) -> list[dict]:
    seen = {r["id"] for r in a}
    return a + [r for r in b if r.get("id") not in seen]


def _pick(rows: list[dict], sel: str) -> list[dict]:
    idx = []
    for part in re.split(r"[,，\s]+", sel):
        if part.isdigit() and 1 <= int(part) <= len(rows):
            idx.append(int(part) - 1)
    return [rows[i] for i in dict.fromkeys(idx)]


def _rel(p: Path) -> str:
    try:
        return str(p.relative_to(ROOT))
    except ValueError:
        return str(p)


# ─────────────────────────── 其余分类：core-routing.md 默认行为 ───────────────────────────

def run_search(user_input: str) -> None:
    rows = kb_search(user_input, modality="all", topk=10)
    if not rows:
        say("KB 无命中。可换关键词，或先 ingest 素材。")
        return
    say(f"检索命中 {len(rows)} 条：")
    for i, r in enumerate(rows[:5], 1):
        cap = re.sub(r"\s+", " ", (r.get("caption") or "")).strip()[:40]
        say(f"{i}. [{r.get('modality')}] {r.get('title')} — {cap}\n   {r.get('source_path')}")
    if confirm("是否保存检索结果到 outputs/？"):
        out = OUTPUTS / today() / "research" / f"{slugify(user_input)}.md"
        out.parent.mkdir(parents=True, exist_ok=True)
        lines = [f"# 检索：{user_input}\n"]
        for r in rows:
            lines.append(f"- [{r.get('modality')}] {r.get('title')} — {r.get('source_path')}")
        out.write_text("\n".join(lines), encoding="utf-8")
        say(f"已保存：{_rel(out)}")
        finalize("none", "success", f"检索并保存结果：{user_input} → {_rel(out)}")


def run_qa(user_input: str, _history) -> None:
    # 一次性问答：需要时查 KB doc 作上下文，只读，不写文件、不收尾
    rows = kb_search(user_input, modality="doc", topk=3, no_log=True, no_touch=True)
    ctx = ""
    for r in rows:
        sp = r.get("source_path")
        if sp and Path(sp).exists():
            ctx += f"\n## {r.get('title')}\n{Path(sp).read_text(encoding='utf-8', errors='ignore')[:600]}\n"
    say(brain.answer(user_input, ctx))


def run_design(user_input: str, _history) -> None:
    say(brain.discuss(user_input))
    if confirm("是否把方案落成设计文档到 outputs/？"):
        title = slugify(user_input)
        out = OUTPUTS / today() / "design" / f"{title}.md"
        out.parent.mkdir(parents=True, exist_ok=True)
        content = brain.discuss(f"请把以下需求整理成一份结构化设计文档（Markdown）：{user_input}")
        out.write_text(content, encoding="utf-8")
        say(f"已写入：{_rel(out)}")
        finalize("none", "success", f"产出设计文档：{user_input} → {_rel(out)}")


def run_exec(user_input: str, _history) -> None:
    say("[执行类] 本 Agent 聚焦图书运营内容生成；代码/任务类请在工程环境处理。")
    say(brain.discuss(user_input))


def run_chitchat(user_input: str, _history) -> None:
    say(brain.chat(user_input))


# ─────────────────────────── 主循环 ───────────────────────────

DISPATCH = {
    "content": lambda t, h: run_content_generate(t),
    "search": lambda t, h: run_search(t),
    "qa": run_qa,
    "design": run_design,
    "exec": run_exec,
    "chitchat": run_chitchat,
}

_CAT_CN = {"content": "内容生成", "search": "搜索/调研", "qa": "一次性问答",
           "design": "设计/方案", "exec": "执行/任务", "chitchat": "闲聊"}


def handle(user_input: str, history: list) -> None:
    cat = brain.classify(user_input)
    say(f"[路由] 分类：{_CAT_CN.get(cat, cat)}")
    DISPATCH.get(cat, run_chitchat)(user_input, history)


def repl() -> None:
    say("学而思图书运营 本地 Agent（纯 Python 运行时）。输入内容开始；输入 exit / quit 退出。")
    if not brain.has_codex_cli():
        say("[提示] 未找到可执行的 codex CLI：分类走关键词降级、文案不做 AI 润色、对话不可用；"
            "内容生成与 KB 检索的本地链路仍可用。")
    history: list = []
    while True:
        user_input = ask("\n> ")
        if not user_input:
            continue
        if user_input.lower() in ("exit", "quit", "退出", ":q"):
            say("再见。")
            return
        try:
            handle(user_input, history)
        except KeyboardInterrupt:
            say("\n[已中断本轮]")
        except Exception as e:  # noqa: BLE001
            say(f"[出错] {e}")
        history.append(user_input)


def main(argv: list[str] | None = None) -> int:
    argv = argv if argv is not None else sys.argv[1:]
    if argv:
        handle(" ".join(argv), [])
    else:
        repl()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
