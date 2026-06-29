import type { OutputRef } from "./types";

export function providerLabel(value?: string) {
  if (value === "cli") return "CLI";
  if (value === "api") return "API";
  if (value === "tmux") return "Tmux";
  return "Tmux";
}

export function statusClass(value?: string) {
  if (["done", "ok", "idle", "success"].includes(value ?? "")) return "ok";
  if (["running", "queued", "waiting_result", "warn", "partial"].includes(value ?? "")) return "warn";
  if (["failed", "stopped", "missing", "cancelled"].includes(value ?? "")) return "bad";
  return "";
}

export function titleFromMessage(content: string) {
  const firstLine = content.split(/\r?\n/).map((line) => line.trim()).find(Boolean) ?? "新会话";
  const firstSentence = firstLine.split(/(?<=[。！？!?])/)[0] || firstLine;
  return firstSentence.length > 36 ? `${firstSentence.slice(0, 36)}...` : firstSentence;
}

function outputType(path: string, label = "") {
  const text = `${path} ${label}`.toLowerCase();
  if (text.includes("xiaohongshu") || text.includes("小红书")) return "小红书图文";
  if (text.includes("moments") || text.includes("朋友圈")) return "朋友圈文案";
  if (text.includes("wechat") || text.includes("群话术") || text.includes("家长群")) return "家长群话术";
  if (text.includes("compliance") || text.includes("审核")) return "合规审核报告";
  if (text.includes("campaign") || text.includes("活动")) return "活动计划";
  if (text.includes("knowledge") || text.includes("sync")) return "知识库同步报告";
  return "运营产出";
}

export function normalizeOutput(item: OutputRef): OutputRef {
  const label = item.label || item.name || item.path.split("/").pop() || item.path;
  return {
    ...item,
    label,
    type: item.type || outputType(item.path, label),
    status: item.status || "草稿"
  };
}
