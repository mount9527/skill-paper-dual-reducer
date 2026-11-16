"""CLI 底座：让脚本在任意宿主控制台上都不因编码崩掉。

为什么需要
----------
中文 Windows 的控制台代码页通常是 936（GBK），而 agent 宿主（Codex / Cursor /
Claude Code / DSH / WorkBuddy）捕获输出时又多是管道。同一个脚本要在两种环境里都不抛
UnicodeEncodeError，唯一稳妥的做法是：**保留控制台自己的编码，只把错误策略改成
replace** —— 编不出来的字符降级成 '?'，而不是让整条命令失败。

数据文件不受影响：所有 JSON / Markdown / JSONL 一律显式 encoding="utf-8" 读写。
"""

from __future__ import annotations

import json
import sys
from pathlib import Path


def safe_io() -> None:
    """把 stdout / stderr 的错误策略改为 replace（不改变编码）。幂等。"""
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(errors="replace")
        except (AttributeError, ValueError, OSError):
            pass


def fail(msg: str, code: int = 2) -> int:
    """统一的失败出口：明确报错而不是 traceback。"""
    print("[error] %s" % msg, file=sys.stderr)
    return code


def read_json(path: str | Path) -> dict:
    return json.loads(Path(path).read_text(encoding="utf-8"))


def write_json(path: str | Path, payload: dict) -> Path:
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(payload, ensure_ascii=False, indent=1), encoding="utf-8")
    return p
