"""tmux task 的 prompt 加工:done_file 指令 + submission 引导文(见 design/03 §3.4)。"""
from __future__ import annotations

from pathlib import Path


def append_done_instruction(prompt_text: str, result_file: Path, done_file: Path) -> str:
    return f"""\
{prompt_text.rstrip()}

## 最终完成标记

完成 result_file 写入并重新读取校验后,最后一步创建空完成标记:
- result_file:{result_file}
- 完成标记:`touch {done_file}`(只能是空文件,且必须在 result_file 写入校验后最后创建)
"""


def submission_text(prompt_file: Path, result_file: Path, done_file: Path) -> str:
    return f"""\
# 调度入口

## 任务文件

请按照以下任务文件完成本次任务:
{prompt_file}

## 完成信号

最终结果必须写入:
{result_file}

完成 result_file 写入和校验后,最后一步创建空完成标记文件:
{done_file}

终端输出只作为过程日志,不能替代 result_file。

## 调度约定

- 只按任务文件中的任务输入、上下文和输出结构执行。
- 所有路径都按绝对路径处理,不要依赖当前 shell 的相对目录猜测。
- 无论成功、失败还是部分完成,都要写入 result_file。

## 自检与有限重试

- 写 result_file 前先自检输出结构、JSON 格式、路径引用和明显截断;发现问题先修正再写。
- 对瞬时 I/O、网络超时、限流或工具临时异常,可以在同一任务边界内短暂重试。
- 权限不足、认证缺失、配置缺失、命令不存在、输入缺失或需要人工判断的问题,不要反复重试;按任务文件的输出结构写明失败或限制。

## 写入要求

- 写 result_file 前先写临时文件,再原子 rename 到 result_file。
- 如果任务文件要求 JSON,result_file 必须是单个合法 JSON 值,不要包 Markdown 代码块。
- 写入后重新读取 result_file,确认路径正确、内容可解析、结构符合任务文件要求。
- 完成标记只能是空文件;必须在 result_file 写入和校验完成后最后创建,可使用 `touch {done_file}`。
"""
