#!/usr/bin/env python3
"""inspect_docx.py —— .docx → doc.json

把一篇 Word 论文拆成「可改写的段落清单 + 每段的锚点/保护区/格式」，后续所有脚本只认这份 JSON。
模型永远拿不到 docx，只能拿到这里产出的文本与约束。

用法
----
    python scripts/inspect_docx.py --in paper.docx --out out/doc.json
    python scripts/inspect_docx.py --in paper.docx --out out/doc.json --min-chars 50 --summary

输出（doc.json）
---------------
    schema / source / config / stats / lock_reasons / paragraphs[]

每个段落含：pid、kind、text、locked、lock_reason、style、runs、sentences、spans、anchor。
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from lib.anchor import anchors, spans_json
from lib.features import split_sentences
from lib.ooxml import Docx, DocxError

HEADING_TEXT_RX = re.compile(
    r"^\s*(?:第[一二三四五六七八九十百]+[章节篇]"
    r"|[一二三四五六七八九十]+[、．.]"
    r"|\d+(?:\.\d+){0,3}[、．.]"
    r"|\d+\s+\S"
    r"|摘要|关键词|Abstract|Key\s*words|参考文献|References|致谢|附录)",
    re.I,
)
REF_HEADING_RX = re.compile(r"^\s*(?:参考文献|引用文献|References|Bibliography)\s*$", re.I)

#: 等宽字体 = 代码块。论文里的目录树、代码清单、伪代码段一律按代码处理：
#: 它们不是散文，改写会毁掉缩进与符号对齐，而这类段落往往正好过得了 min_chars。
MONO_FONTS = {
    "consolas", "courier new", "courier", "menlo", "monaco", "lucida console",
    "source code pro", "dejavu sans mono", "cascadia mono", "cascadia code",
    "fira code", "jetbrains mono", "sf mono", "ubuntu mono", "inconsolata",
}
#: 目录树的制表符号：首字符是它，基本可以断定是代码块而不是正文。
BOX_CHARS = "│├└┌┐┘┤┬┴┼╎╏━┃┆┊"


def looks_like_code(p) -> bool:
    """等宽字体为主的段落，或以制表符号开头的代码块行。

    判据刻意保守：只有「样式缺失时」才轮到它，且必须字体或符号这类硬信号命中，
    不靠「像不像代码」的猜测——误锁一段正文的代价比漏锁一段代码小，
    但两者都不该由语感决定。
    """
    text = p.text.strip()
    if not text:
        return False
    runs = p.runs()
    if runs:
        mono = sum(1 for r in runs
                   if (r.fmt.get("font_ascii") or "").strip().lower() in MONO_FONTS)
        if mono / len(runs) >= 0.6:
            return True
    return text[0] in BOX_CHARS


def looks_like_heading(text: str) -> bool:
    """样式缺失时的兜底判据。宁可少判标题，不可把正文判成标题。"""
    t = text.strip()
    if not t or len(t) > 40 or "。" in t:
        return False
    return bool(HEADING_TEXT_RX.match(t))


def build(path: str, min_chars: int = 40) -> dict:
    docx = Docx(path)
    paragraphs = docx.paragraphs()
    has_style_headings = any(docx.styles.is_heading(p.style_id) for p in paragraphs)

    rows: list[dict] = []
    in_refs = False
    reasons: dict[str, int] = {}
    rewritable_chars = 0

    for p in paragraphs:
        text = p.text
        stripped = text.strip()

        if docx.styles.is_heading(p.style_id):
            is_heading = True
        elif not has_style_headings:
            is_heading = looks_like_heading(stripped)
        else:
            is_heading = False

        if is_heading:
            kind = "heading"
        elif not stripped:
            kind = "empty"
        elif p.in_table:
            kind = "table"
        else:
            kind = "body"

        # 锁定原因（顺序即优先级）
        lock = None
        if kind == "table":
            lock = "table"
        elif kind == "heading":
            lock = "heading"
        elif kind == "empty":
            lock = "empty"
        elif in_refs:
            lock = "reference"
        elif looks_like_code(p):
            lock = "code"
        else:
            lock = p.lock_reason()
            if lock is None and len(stripped) < min_chars:
                lock = "too_short"

        if is_heading and REF_HEADING_RX.match(stripped):
            in_refs = True
        elif is_heading and in_refs:
            in_refs = False

        locked = lock is not None
        if not locked:
            rewritable_chars += len(stripped)

        runs = []
        for k, r in enumerate(p.runs(), 1):
            j = r.json()
            j["rid"] = "%sr%02d" % (p.pid, k)
            runs.append(j)

        fmt = p.fmt
        rows.append({
            "pid": p.pid,
            "index": p.index,
            "kind": kind,
            "text": text,
            "chars": len(stripped),
            "locked": locked,
            "lock_reason": lock,
            "style": {
                "id": fmt["style_id"],
                "name": docx.styles.name(fmt["style_id"]),
                "align": fmt["align"],
                "first_line_indent_pt": fmt["first_line_indent_pt"],
                "space_before_pt": fmt["space_before_pt"],
                "space_after_pt": fmt["space_after_pt"],
                "line_spacing": fmt["line_spacing"],
                "outline_level": fmt["outline_level"],
            },
            "runs": runs,
            "sentences": split_sentences(text),
            "spans": spans_json(text),
            "anchor": anchors(text),
        })
        if locked:
            reasons[lock] = reasons.get(lock, 0) + 1

    total_chars = sum(r["chars"] for r in rows)
    return {
        "schema": "paper-dual-reduce/doc@1",
        "source": {
            "path": str(path),
            "file_bytes": Path(path).stat().st_size,
            "document_xml_sha256": docx.sha256(),
            "generated_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
        },
        "config": {"min_chars": min_chars, "ngram": 13},
        "stats": {
            "paragraphs": len(rows),
            "headings": sum(1 for r in rows if r["kind"] == "heading"),
            "tables": docx.table_count(),
            "sections": docx.section_count(),
            "locked": sum(1 for r in rows if r["locked"]),
            "rewritable": sum(1 for r in rows if not r["locked"]),
            "chars": total_chars,
            "rewritable_chars": rewritable_chars,
            "rewritable_share": round(rewritable_chars / total_chars, 4) if total_chars else 0.0,
            "has_style_headings": has_style_headings,
        },
        "lock_reasons": dict(sorted(reasons.items(), key=lambda kv: -kv[1])),
        "paragraphs": rows,
    }


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="把 .docx 解析为 doc.json（段落、锚点、保护区、锁定标记）")
    ap.add_argument("--in", dest="src", required=True, help="输入 .docx")
    ap.add_argument("--out", dest="dst", required=True, help="输出 doc.json")
    ap.add_argument("--min-chars", type=int, default=40, help="低于此字数的段落不参与改写（默认 40）")
    ap.add_argument("--summary", action="store_true", help="在终端打印摘要")
    args = ap.parse_args(argv)

    try:
        data = build(args.src, args.min_chars)
    except DocxError as exc:
        print("[inspect] 失败：%s" % exc, file=sys.stderr)
        return 2

    out = Path(args.dst)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")

    s = data["stats"]
    if args.summary:
        print("[inspect] 段落 %d（标题 %d / 锁定 %d / 可改写 %d）"
              % (s["paragraphs"], s["headings"], s["locked"], s["rewritable"]))
        print("[inspect] 可改写字数 %d / 总字数 %d（%.0f%%）"
              % (s["rewritable_chars"], s["chars"], s["rewritable_share"] * 100))
        if data["lock_reasons"]:
            print("[inspect] 锁定构成：" + "，".join("%s=%d" % kv for kv in data["lock_reasons"].items()))
    print("[inspect] 已写出 %s" % out)
    return 0


if __name__ == "__main__":
    sys.exit(main())
