#!/usr/bin/env python3
"""verify.py —— 前后两份 .docx → verdict.json

这是交付前的最后一道闸。四个检查项，任何一项不为空就判不通过：

1. format_diff  段落格式 / 字符格式是否漂移（显式属性逐项比对）
2. entity_diff  数字、年份、引用、书名、术语、缩写是否被增删
3. locked_diff  锁定段（标题、表格、公式、参考文献…）是否被改动
4. structure    段落数 / 表格数 / 分节数是否变化

用法
----
    python scripts/verify.py --before in.docx --after out.docx --out out/verdict.json
    echo $?        # 0=通过，1=不通过，2=输入有误

不通过时按硬约束应整篇回退到原件，见 references/compliance.md。
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from lib.diff import compare
from lib.ooxml import DocxError


def render(v: dict) -> str:
    s = v["summary"]
    lines = []
    lines.append("段落 %d → %d ｜ 表格 %d → %d ｜ 分节 %d → %d"
                 % (v["structure"]["paragraphs"]["before"], v["structure"]["paragraphs"]["after"],
                    v["structure"]["tables"]["before"], v["structure"]["tables"]["after"],
                    v["structure"]["sections"]["before"], v["structure"]["sections"]["after"]))
    lines.append("字数 %d → %d ｜ 改写段 %d"
                 % (v["text"]["before_chars"], v["text"]["after_chars"], v["text"]["changed_paragraphs"]))
    lines.append("格式问题 %d ｜ 锁定段改动 %d ｜ 实体新增 %d ｜ 实体缺失 %d ｜ 结构问题 %d"
                 % (s["format_issues"], s["locked_changes"], s["entity_added"],
                    s["entity_removed"], s["structure_issues"]))
    for item in v["format_diff"][:8]:
        if item["kind"] == "run":
            lines.append("  ! %s 字符格式 %s" % (item["pid"], item.get("missing") or item.get("added")))
        else:
            lines.append("  ! %s 段落格式 %s" % (item["pid"], item["fields"]))
    for item in v["locked_diff"][:5]:
        lines.append("  ! %s 锁定段被改（%s）" % (item["pid"], item["reason"]))
    for e in v["entity_diff"]["added"][:5]:
        lines.append("  + 新增实体 %s" % e)
    for e in v["entity_diff"]["removed"][:5]:
        lines.append("  - 丢失实体 %s" % e)
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="比对新旧 .docx 的格式、实体、锁定段一致性")
    ap.add_argument("--before", required=True, help="原始 .docx")
    ap.add_argument("--after", required=True, help="改写后 .docx")
    ap.add_argument("--out", required=True, help="输出 verdict.json")
    ap.add_argument("--quiet", action="store_true", help="只输出结论行")
    args = ap.parse_args(argv)

    try:
        verdict = compare(args.before, args.after)
    except DocxError as exc:
        print("[verify] 输入有误：%s" % exc, file=sys.stderr)
        return 2

    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(verdict, ensure_ascii=False, indent=1), encoding="utf-8")

    if not args.quiet:
        print(render(verdict))
    print("[verify] %s → %s" % ("通过" if verdict["passed"] else "不通过", out))
    return 0 if verdict["passed"] else 1


if __name__ == "__main__":
    sys.exit(main())
