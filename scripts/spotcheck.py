#!/usr/bin/env python3
"""spotcheck.py —— 生成 P1 门禁的人工抽查表：20 段归因核对。

SOP §5.3 的门禁是「抽 20 段，人工判断『这段为什么像机器写的』，与系统归因比对，
80% 一致即过」。这条**脚本无法自证**——归因对不对只能由人判断。所以这里不假装能自动评分，
只把「要核对什么」整理成一张能直接勾的表，并给出计分口径。

表里的每个数都来自 profile.json，不重新计算，避免表与报告口径分叉。

用法
----
    python scripts/spotcheck.py --profile out/profile.json --doc out/doc.json \\
        --out out/spotcheck.md --n 20
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from lib.cli import fail, read_json, safe_io  # noqa: E402

CRITERIA = "句长太齐 / 连接词密 / 套话多 / 没信息 / 用词平淡 / 结构平行"


def build(profile: dict, doc: dict, n: int = 20) -> str:
    index = {r["pid"]: r for r in doc["paragraphs"]}
    rows = [p for p in profile["paragraphs"] if not index.get(p["pid"], {}).get("locked")]
    rows.sort(key=lambda p: -p["risk"])
    rows = rows[:n]

    out = [
        "# P1 门禁抽样 · 归因人工核对",
        "",
        "> 用法：读「原文摘要」，自己判断这一段为什么像机器写的（可多选：%s）。" % CRITERIA,
        "> 与「系统归因」比对，一致填 是，不一致填 否。",
        "> 一致率 ≥ 0.80（%d 段中 ≥%d 段）即过 SOP P1 门禁；不通过的段落逐条记进规则表修正素材。"
        % (len(rows), -(-len(rows) * 4 // 5)),
        "",
        "| # | 段落 | 风险 | 系统归因（主因 / 次因） | 原文摘要 | 人工判断 | 一致? |",
        "|---|---|---|---|---|---|---|",
    ]
    for i, p in enumerate(rows, 1):
        causes = p.get("causes") or []
        cs = " / ".join(c["name"] for c in causes[:2]) or "—"
        text = index.get(p["pid"], {}).get("text", "").replace("|", "\\|")
        clip = text if len(text) <= 90 else text[:90] + "…"
        out.append("| %d | %s | %.2f | %s | %s |  |  |" % (i, p["pid"], p["risk"], cs, clip))

    out += [
        "",
        "## 计分",
        "",
        "数「一致」列里「是」的个数，除以 %d。≥ 0.80 通过。" % len(rows),
        "不通过的段落要能说出「系统写了什么、你判断是什么」，否则不算分歧，算没看懂。",
        "",
    ]
    return "\n".join(out)


def main(argv: list[str] | None = None) -> int:
    safe_io()
    ap = argparse.ArgumentParser(description="生成 P1 门禁的归因人工抽查表（Markdown）")
    ap.add_argument("--profile", required=True, help="diagnose.py 产出的 profile.json")
    ap.add_argument("--doc", required=True, help="inspect_docx.py 产出的 doc.json")
    ap.add_argument("--out", required=True, help="输出 Markdown")
    ap.add_argument("--n", type=int, default=20, help="抽样段数（默认 20）")
    args = ap.parse_args(argv)

    for path in (args.profile, args.doc):
        if not Path(path).exists():
            return fail("找不到 %s；先跑 run.py diagnose" % path)

    md = build(read_json(args.profile), read_json(args.doc), args.n)
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(md, encoding="utf-8")
    rows = md.count("\n| ") - 1
    print("[spotcheck] %d 段 → %s（一致率 ≥ 0.80 即过 P1 门禁）" % (max(0, rows), out))
    return 0


if __name__ == "__main__":
    sys.exit(main())
