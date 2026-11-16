#!/usr/bin/env python3
"""lint_edits.py —— 把闸门前移：改写落盘之前，先预判「这条会被哪道门拒、怎么改」。

apply_edits 是事后判决（写完 docx 才知道结果）。模型在长文档上改一轮动辄十几段，
改完发现全被拒，返工成本很高。lint_edits 用**同一套闸门**做 dry-run，并对每种拒绝
给出可执行的修法，让模型一轮收敛。

它不是第二套规则：直接复用 apply_edits.apply(dry_run=True)，所以判决结果与真正
回填时逐条一致——两套规则分叉是这类工具最常见的事故源。

用法
----
    python scripts/lint_edits.py --doc out/doc.json --before paper.docx \\
        --edits out/edits.jsonl --out out/lint.json

退出码
------
    0 全部可通过 ｜ 1 有编辑会被拒 ｜ 2 输入有误
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
for p in (str(ROOT), str(ROOT / "scripts")):
    if p not in sys.path:
        sys.path.insert(0, p)

import apply_edits  # noqa: E402  （同目录脚本，复用同一套闸门）
from lib.cli import fail, safe_io, write_json  # noqa: E402
from lib.ooxml import DocxError  # noqa: E402

#: 拒绝原因 → 可执行的修法。刻意写成「下一步做什么」，不是「哪里错了」。
HINTS = {
    "entity_added": "删掉新加的数字/年份/百分比/引用/书名，改写不得新增原文没有的事实",
    "entity_lost": "把被删的数字/引用补回原文写法——删限定词比编造更隐蔽，同样违规",
    "length_out_of_range": "把字数压回原文的 0.55~1.40 倍；改写是换说法，不是缩写或扩写",
    "format_anchor_lost": "该段保护区内含加粗/斜体小标题，改掉它无法还原格式；换一种不动小标题的改法",
    "format_drift": "只替换文字、不要动段落与字符属性；把段落格式改花等于破坏原文",
    "empty_text": "补上改写结果，空文本无法回填",
    "stale_sha1": "重新跑 inspect_docx.py，并在编辑里带上最新的 expected_sha1",
    "unknown_pid": "段落编号不存在：只改 doc.json 里 rewritable=true 的段落",
    "duplicate_edit": "同一段提交了多条编辑，只保留一条",
}


def hint_for(reason: str) -> str:
    head = reason.split(":")[0]
    if head == "locked":
        return "该段是锁定区（%s），不参与改写，跳过它" % reason.split(":", 1)[-1]
    return HINTS.get(head, "该编辑被闸门拒绝，按 reason 字段修正后重试")


def lint(doc_path: str, before: str, edits_path: str) -> dict:
    # dry_run=True：跑完全部闸门，但不写 docx
    raw = apply_edits.apply(doc_path, before, edits_path,
                            str(Path(edits_path).with_suffix(".lint-noop.docx")),
                            dry_run=True, do_verify=False)

    items = []
    for item in raw["items"]:
        ok = item["status"] == "applied"
        row = {
            "pid": item["pid"],
            "op": item.get("op"),
            "status": "would_apply" if ok else "would_reject",
        }
        if ok:
            row["chars"] = item.get("chars")
            row["ratio"] = item.get("ratio")
        else:
            row["reason"] = item["reason"]
            row["hint"] = hint_for(item["reason"])
        items.append(row)

    would_apply = sum(1 for i in items if i["status"] == "would_apply")
    return {
        "schema": "paper-dual-reduce/lint@1",
        "source": {"doc_json": doc_path, "before": before, "edits": edits_path},
        "counts": {
            "edits": len(items),
            "would_apply": would_apply,
            "would_reject": len(items) - would_apply,
        },
        "items": items,
    }


def main(argv: list[str] | None = None) -> int:
    safe_io()
    ap = argparse.ArgumentParser(description="dry-run 预检 edits.jsonl：每条会被哪道门拒、怎么改")
    ap.add_argument("--doc", required=True, help="inspect_docx.py 产出的 doc.json")
    ap.add_argument("--before", required=True, help="原始 .docx")
    ap.add_argument("--edits", required=True, help="edits.jsonl")
    ap.add_argument("--out", required=True, help="输出 lint.json")
    args = ap.parse_args(argv)

    doc_path = Path(args.doc)
    if not doc_path.exists():
        return fail("找不到 %s；先跑 inspect_docx.py 或 run.py diagnose" % doc_path)
    if not Path(args.before).exists():
        return fail("找不到原始文档 %s" % args.before)
    if not Path(args.edits).exists():
        return fail("找不到编辑清单 %s" % args.edits)

    try:
        result = lint(str(doc_path), args.before, args.edits)
    except DocxError as exc:
        return fail("docx 解析失败：%s" % exc)
    except SystemExit as exc:  # apply_edits 对非法 JSONL 用 SystemExit 报错
        return fail(str(exc))

    write_json(args.out, result)
    c = result["counts"]
    print("[lint] 编辑 %d ｜ 可通过 %d ｜ 会被拒 %d"
          % (c["edits"], c["would_apply"], c["would_reject"]))
    for item in result["items"]:
        if item["status"] == "would_reject":
            print("  ✗ %s %s  %s" % (item["pid"], item["reason"], item["hint"]))
    print("[lint] 结果 → %s" % args.out)
    return 0 if c["would_reject"] == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
