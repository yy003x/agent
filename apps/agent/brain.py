#!/usr/bin/env python3
"""brain —— 纯 Python 编排器的「认知层」。

把需要智能判断的窄任务收敛为本机 Codex CLI 调用：输入分类、需求抽取、
文案润色、对话 / 问答 / 讨论。Python 主循环仍负责流程编排、确认门与写入门禁。

设计依据：design/01-framework.md（路由→skill 桥梁）、rules/core-routing.md（分类表）、
AGENTS.md（运营合规红线）。

降级策略：未找到可执行的 `codex` CLI 或 CLI 调用失败时，分类降级为关键词规则，
文案润色降级为模板原样返回、对话/问答返回离线提示，保证纯文档链路（P1）可离线跑通。
"""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import tempfile
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
CODEX_CMD = os.environ.get("AGENT_CODEX_CMD", "codex")
CODEX_MODEL = os.environ.get("AGENT_CODEX_MODEL", "")
CODEX_PROFILE = os.environ.get("AGENT_CODEX_PROFILE", "")
CODEX_TIMEOUT_S = int(os.environ.get("AGENT_CODEX_TIMEOUT_S", "180"))

CATEGORIES = ["chitchat", "qa", "search", "design", "content", "exec"]
PLATFORMS = ["xiaohongshu", "moments", "wechat_group"]

# 运营合规红线（注入润色 system prompt，与 AGENTS.md 保持一致）
COMPLIANCE = (
    "你是学而思图书运营，受众是 K12 学生家长，以懂教育的老师视角真诚专业、软引导。"
    "严守红线：不承诺提分/升学/保过/效果；不用极限词（最/第一/唯一/绝对等）；"
    "不碰双减敏感表述、不制造教育焦虑；不贬低其他机构；不伪造家长好评；"
    "不写内部价格策略与学员个人信息。只能基于给定素材事实，不得编造书名/数据/引用。"
)


def has_codex_cli() -> bool:
    return bool(shutil.which(CODEX_CMD))


def _codex_exec(system: str, user: str, max_tokens: int = 1024,
                image_paths: list[Path] | None = None) -> str:
    """调用本机 Codex CLI，并返回最后一条 assistant 消息。"""
    if not has_codex_cli():
        raise RuntimeError("未找到 codex CLI")

    prompt = (
        f"{system}\n\n"
        "你正在作为学而思图书运营本地 Agent 的一个窄任务执行器运行。\n"
        "只完成本次请求，不修改文件，不执行发布动作。\n"
        f"输出长度上限参考：{max_tokens} tokens。\n\n"
        f"用户输入：\n{user}"
    )
    with tempfile.TemporaryDirectory(prefix="agent-codex-") as tmp:
        output_path = Path(tmp) / "last-message.txt"
        cmd = [
            CODEX_CMD, "exec",
            "--skip-git-repo-check",
            "--sandbox", "read-only",
            "--color", "never",
            "--output-last-message", str(output_path),
            "-C", str(PROJECT_ROOT),
        ]
        if CODEX_MODEL:
            cmd.extend(["--model", CODEX_MODEL])
        if CODEX_PROFILE:
            cmd.extend(["--profile", CODEX_PROFILE])
        for image_path in image_paths or []:
            cmd.extend(["--image", str(image_path)])
        cmd.append("-")

        proc = subprocess.run(
            cmd,
            input=prompt,
            text=True,
            capture_output=True,
            timeout=CODEX_TIMEOUT_S,
            cwd=PROJECT_ROOT,
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
            raise RuntimeError("codex exec 未返回内容")
        return text


def _extract_json(text: str):
    """从模型输出中抽第一段 JSON（容忍 ```json 包裹与前后说明文字）。"""
    text = text.strip()
    m = re.search(r"\{.*\}|\[.*\]", text, re.S)
    if not m:
        return None
    try:
        return json.loads(m.group(0))
    except Exception:
        return None


# ─────────────────────────── 输入分类（路由器）───────────────────────────

# 关键词降级表，与 rules/core-routing.md 分类表对齐
_KW = {
    "content": ["出一篇", "生成内容", "做个图文", "做一张", "写小红书", "朋友圈文案",
                "家长群", "微信群话术", "社群话术", "书单", "读书笔记", "知识卡片",
                "读后感", "书评", "推荐语", "配图", "短视频", "视频脚本", "文案", "图文"],
    "search": ["帮我找", "查一下", "搜一下", "搜索", "有哪些", "调研", "找一下", "找找"],
    "design": ["怎么设计", "架构", "方案", "规划", "prd", "计划怎么", "选题方向"],
    "exec": ["实现", "写代码", "修 bug", "修bug", "脚本", "提交", "改配置", "重构", "批量改"],
    "chitchat": ["你好", "您好", "在吗", "谢谢", "多谢", "辛苦", "早上好", "晚安", "哈哈"],
}


def _classify_fallback(text: str) -> str:
    low = text.lower()
    # 内容生成优先于搜索（core-routing.md 优先级规则 1）
    for cat in ("content", "design", "exec", "search", "chitchat"):
        if any(kw.lower() in low for kw in _KW[cat]):
            return cat
    return "qa"


def classify(text: str) -> str:
    """语义分类，返回 CATEGORIES 之一。无 Codex CLI 时降级关键词匹配。"""
    if not has_codex_cli():
        return _classify_fallback(text)
    system = (
        "你是学而思图书运营 Agent 的输入路由器。把用户输入分类为以下之一，只输出 JSON：\n"
        '{"category": "<chitchat|qa|search|design|content|exec>"}\n'
        "定义：chitchat=闲聊寒暄；qa=一次性问答/概念解释/状态查询；search=帮我找/查/有哪些/调研；"
        "design=怎么设计/方案/规划/PRD；content=生成成品内容（图文/小红书/朋友圈/书单/读书笔记/"
        "知识卡片/读后感/书评/推荐语/配图/短视频/文案）；exec=写代码/改配置/修bug/提交。\n"
        "优先级：内容生成优先于搜索；「解释X」归 qa，「帮我找X」归 search。"
    )
    try:
        data = _extract_json(_codex_exec(system, text, max_tokens=64))
        cat = (data or {}).get("category")
        return cat if cat in CATEGORIES else _classify_fallback(text)
    except Exception:
        return _classify_fallback(text)


# ─────────────────────────── 需求抽取（content-generate 步骤 1）───────────────────────────

def extract_requirements(text: str) -> dict:
    """从内容生成请求中抽取主题/平台/形态/风格/数量/约束。无 Codex CLI 时给保守默认。"""
    default = {"topic": text.strip(), "platform": "xiaohongshu", "form": "图文",
               "style": "知识科普", "count": 1, "constraints": ""}
    if not has_codex_cli():
        return default
    system = (
        "从用户的内容生成请求中抽取字段，只输出 JSON：\n"
        '{"topic":"内容主题","platform":"xiaohongshu|moments|wechat_group",'
        '"form":"图文|短视频|组合","style":"知识科普|情感共鸣|书单推荐|读书笔记",'
        '"count":1,"constraints":"特殊约束，无则空串"}\n'
        "platform 默认 xiaohongshu；提到「朋友圈」→moments；「家长群/微信群/社群」→wechat_group。"
    )
    try:
        data = _extract_json(_codex_exec(system, text, max_tokens=256)) or {}
    except Exception:
        return default
    out = {**default, **{k: v for k, v in data.items() if v}}
    if out["platform"] not in PLATFORMS:
        out["platform"] = "xiaohongshu"
    try:
        out["count"] = max(1, int(out["count"]))
    except Exception:
        out["count"] = 1
    return out


# ─────────────────────────── 文案润色（content-generate 步骤 5）───────────────────────────

def polish_copy(draft: dict, requirements: dict, source_facts: list[str],
                instruction: str = "") -> dict:
    """基于模板草稿与素材事实，产出更高质量文案。返回 {title, body_text, tags}。

    无 Codex CLI 时原样返回模板草稿（不编造）。
    """
    base = {"title": draft.get("title", ""), "body_text": draft.get("body_text", ""),
            "tags": draft.get("tags", [])}
    if not has_codex_cli():
        return base

    platform = requirements.get("platform", draft.get("platform", "xiaohongshu"))
    limits = {
        "xiaohongshu": "标题≤20字且有钩子；正文分段、可适度 emoji；结尾自然 CTA；标签 3-5 个。",
        "moments": "正文≤180字，口语、真诚，不带话题标签。",
        "wechat_group": "群话术可直接复制粘贴，亲切、不硬广、不冒充家长口吻。",
    }.get(platform, "")
    facts_block = "\n".join(f"- {f}" for f in source_facts) if source_facts else "（无额外素材，只能基于 brief 写，不得编造具体书名/数据）"
    user = (
        f"平台：{platform}\n风格：{requirements.get('style', '知识科普')}\n"
        f"需求：{requirements.get('topic') or draft.get('brief', '')}\n"
        f"平台格式要求：{limits}\n"
        f"可用素材事实（只能用这些，不得编造）：\n{facts_block}\n"
        f"模板初稿正文：\n{draft.get('body_text', '')}\n"
        + (f"\n额外修改要求：{instruction}\n" if instruction else "")
        + '\n请改写润色，只输出 JSON：{"title":"...","body_text":"...","tags":["..."]}'
        + ("（moments/wechat_group 的 tags 给空数组）" if platform != "xiaohongshu" else "")
    )
    try:
        data = _extract_json(_codex_exec(COMPLIANCE, user, max_tokens=1200)) or {}
    except Exception:
        return base
    return {
        "title": data.get("title") or base["title"],
        "body_text": data.get("body_text") or base["body_text"],
        "tags": data.get("tags") if isinstance(data.get("tags"), list) else base["tags"],
    }


# ─────────────────────────── 对话 / 问答 / 讨论 ───────────────────────────

def _chat_like(system: str, text: str, context: str = "") -> str:
    if not has_codex_cli():
        return "（当前未找到可执行的 codex CLI，无法调用对话能力；内容生成/搜索的本地链路仍可用。）"
    user = (f"参考资料：\n{context}\n\n问题：{text}" if context else text)
    try:
        return _codex_exec(system, user, max_tokens=1024).strip()
    except Exception as e:  # noqa: BLE001
        return f"（Codex CLI 调用失败：{e}）"


def chat(text: str) -> str:
    return _chat_like("你是学而思图书运营的本地助手，简短自然地回应闲聊。", text)


def answer(text: str, context: str = "") -> str:
    sys = ("你是学而思图书运营助手。基于参考资料简洁作答；资料不足就如实说，不要编造。"
           "默认中文，先结论后原因。")
    return _chat_like(sys, text, context)


def discuss(text: str, context: str = "") -> str:
    sys = ("你是学而思图书运营的方案/选题讨论搭档。先给结论与推荐，再列要点；"
           "涉及落地按顺序给计划，标出待确认疑点。严守运营合规红线。")
    return _chat_like(sys, text, context)
