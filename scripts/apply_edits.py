#!/usr/bin/env python3
"""apply_edits.py —— doc.json + edits.jsonl → 新的 .docx

这是「模型不碰 docx」这条硬约束的落地处：模型只产出 JSONL，文本回填、格式保真、
失败回滚全部由这里完成。

edits.jsonl 每行一个编辑：
    {"pid": "p0007", "text": "改写后的整段文字", "expected_sha1": "…", "op": "O1"}

三道闸，逐段执行，任何一道不过只废掉这一段：
1. 实体门禁    新增或缺失数字/年份/引用 → entity_added / entity_lost
1.5 主观表述  新增「我认为」这类主观表述 → subjective_added（平实档只许减不许多）
2. 长度闸      改写前后字数比超出 [0.55, 1.40] → length_out_of_range
3. 格式闸      段落格式或字符格式签名变化 → format_drift（并回滚该段）

全部写完后默认做一次篇级 verify；不通过则整篇回退为原件，退出码 3。

用法
----
    python scripts/apply_edits.py --doc out/doc.json --before in.docx \\
        --edits out/edits.jsonl --out out/rewritten.docx
"""

from __future__ import annotations

import argparse
import json
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from lib.anchor import entity_gate, rewrite_paragraph
from lib.diff import compare, paragraph_signatures
from lib.features import subjective_count
from lib.ooxml import Docx, DocxError

LEN_RATIO = (0.55, 1.40)


def load_jsonl(path: str) -> list[dict]:
    items = []
    with open(path, encoding="utf-8") as fh:
        for lineno, line in enumerate(fh, 1):
            line = line.strip()
            if not line or line.startswith("//"):
                continue
            try:
                items.append(json.loads(line))
            except json.JSONDecodeError as exc:
                raise SystemExit("edits.jsonl 第 %d 行不是合法 JSON：%s" % (lineno, exc))
    return items


def apply(doc_json_path: str, before: str, edits_path: str, out_path: str,
          dry_run: bool = False, do_verify: bool = True) -> dict:
    doc = json.loads(Path(doc_json_path).read_text(encoding="utf-8"))
    index = {row["pid"]: row for row in doc["paragraphs"]}

    docx = Docx(before)
    current_sha = docx.sha256()
    stale_source = current_sha != doc["source"]["document_xml_sha256"]
    paragraphs = {p.pid: p for p in docx.paragraphs()}

    edits = load_jsonl(edits_path)
    seen: set[str] = set()
    items: list[dict] = []

    for e in edits:
        pid = e.get("pid", "")
        new_text = (e.get("text") or "").strip()
        row = index.get(pid)
        par = paragraphs.get(pid)

        def reject(reason: str, **detail):
            items.append({"pid": pid, "status": "rejected", "reason": reason,
                          "op": e.get("op"), **detail})

        if row is None or par is None:
            reject("unknown_pid")
            continue
        if pid in seen:
            reject("duplicate_edit")
            continue
        seen.add(pid)
        if not new_text:
            reject("empty_text")
            continue
        if row["locked"]:
            reject("locked:%s" % row["lock_reason"])
            continue
        if e.get("expected_sha1") and e["expected_sha1"] != row["anchor"]["sha1"]:
            reject("stale_sha1", expected=e["expected_sha1"], actual=row["anchor"]["sha1"])
            continue

        old_text = par.text

        # 闸 1：实体
        gate = entity_gate(old_text, new_text)
        if gate["added"]:
            reject("entity_added", added=gate["added"][:6])
            continue
        if gate["removed"]:
            reject("entity_lost", removed=gate["removed"][:6])
            continue

        # 闸 1.5：主观表述不得增加。平实文风档要求把它们删掉，
        # 底线是「不能越改越多」——这条只能靠计数，不能靠提示词自觉。
        sub_before = subjective_count(old_text)
        sub_after = subjective_count(new_text)
        if sub_after > sub_before:
            reject("subjective_added", before=sub_before, after=sub_after)
            continue

        # 闸 1.6：门禁对冲——旧段本来没踩线，改写后不能踩线。
        # 实测教训：把逗号写成「——」，整篇语体门禁会从「通过」翻成「未通过」，
        # 而这种回归在回填后的复检才暴露，太晚。
        dash_before = old_text.count("——")
        dash_after = new_text.count("——")
        if dash_after > dash_before and dash_after > 1:
            reject("gate_regression:dash", before=dash_before, after=dash_after)
            continue

        # 闸 2：长度
        ratio = len(new_text) / max(1, len(old_text.strip()))
        if not (LEN_RATIO[0] <= ratio <= LEN_RATIO[1]):
            reject("length_out_of_range", ratio=round(ratio, 3),
                   limit=list(LEN_RATIO))
            continue

        # 闸 3：格式（先改，再验，不一致就回滚这一段）
        sig_before = paragraph_signatures(par)
        snap = docx.snapshot(par)          # 必须在改写前取快照
        ok, reason, detail = rewrite_paragraph(docx, par, new_text)
        if not ok:
            reject(reason, detail=detail)
            continue
        sig_after = paragraph_signatures(par)
        if sig_before != sig_after:
            Docx.restore(par, snap)
            reject("format_drift", before=sorted(sig_before[1]), after=sorted(sig_after[1]))
            continue

        items.append({"pid": pid, "status": "applied", "op": e.get("op"),
                      "mode": detail.get("mode"), "chars": [len(old_text.strip()), len(new_text)],
                      "ratio": round(ratio, 3),
                      # 存下改后文本：报告的三栏对照要用，且这是唯一不依赖 docx 的留痕
                      "text": new_text})

    applied = sum(1 for i in items if i["status"] == "applied")

    result = {
        "schema": "paper-dual-reduce/apply@1",
        "status": "ok",
        "source": {"doc_json": doc_json_path, "before": before},
        "stale_source": stale_source,
        "counts": {"edits": len(edits), "applied": applied, "rejected": len(items) - applied},
        "items": items,
    }

    if dry_run:
        result["status"] = "dry_run"
        return result

    docx.save(out_path)
    result["output"] = out_path

    if do_verify:
        verdict_path = str(Path(out_path).with_suffix(".verdict.json"))
        verdict = compare(before, out_path)
        Path(verdict_path).write_text(json.dumps(verdict, ensure_ascii=False, indent=1), encoding="utf-8")
        result["verify"] = {"passed": verdict["passed"], "path": verdict_path,
                            "summary": verdict["summary"]}
        if not verdict["passed"]:
            # 硬约束：属性 diff 非空则整篇回退
            shutil.copyfile(before, out_path)
            result["status"] = "rolled_back"
            result["output"] = out_path
            result["note"] = "篇级校验未通过，已整篇回退为原件；明细见 verdict"
    return result


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="把 edits.jsonl 的改写回填到 .docx，并保证格式零漂移")
    ap.add_argument("--doc", required=True, help="inspect_docx.py 产出的 doc.json")
    ap.add_argument("--before", required=True, help="原始 .docx")
    ap.add_argument("--edits", required=True, help="edits.jsonl")
    ap.add_argument("--out", required=True, help="输出 .docx")
    ap.add_argument("--result", help="结果 JSON 路径（默认与输出同名 .result.json）")
    ap.add_argument("--dry-run", action="store_true", help="只跑闸门，不写文件")
    ap.add_argument("--no-verify", action="store_true", help="跳过篇级校验（不推荐）")
    args = ap.parse_args(argv)

    try:
        result = apply(args.doc, args.before, args.edits, args.out,
                       dry_run=args.dry_run, do_verify=not args.no_verify)
    except DocxError as exc:
        print("[apply] 失败：%s" % exc, file=sys.stderr)
        return 2

    out = Path(args.result) if args.result else Path(args.out).with_suffix(".result.json")
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(result, ensure_ascii=False, indent=1), encoding="utf-8")

    c = result["counts"]
    print("[apply] 编辑 %d ｜ 接受 %d ｜ 拒绝 %d ｜ 状态 %s"
          % (c["edits"], c["applied"], c["rejected"], result["status"]))
    reasons: dict[str, int] = {}
    for item in result["items"]:
        if item["status"] == "rejected":
            key = item["reason"].split(":")[0]
            reasons[key] = reasons.get(key, 0) + 1
    if reasons:
        print("[apply] 拒绝原因：" + "，".join("%s=%d" % kv for kv in reasons.items()))
    if result.get("verify"):
        print("[apply] 篇级校验 %s" % ("通过" if result["verify"]["passed"] else "未通过（已整篇回退）"))
    if result["status"] == "rolled_back":
        return 3
    print("[apply] 结果 → %s" % out)
    return 0


if __name__ == "__main__":
    sys.exit(main())
