#!/usr/bin/env python3
"""pacing.py —— 跨段编排：给「句数雷同」的相邻段落分配不同的目标句数。

为什么需要它：diag 的「结构对称」是**跨段属性**——单独改某一段，相邻段还是同样的
句数分布，run 依然成立。必须一次规划一串相邻段落，让它们的句数互不相同。

产出 pacing.json：
    {"schema": "...", "runs": [{"pids": [...], "lens": [...], "median": 3}],
     "targets": {"p0012": {"from": 3, "to": 5}}}

plan.py 读到 targets 后，把目标句数写进对应任务的约束；build_prompts 再把
「目标句数 N 句（原文 M 句，必须变化）」写进提示词。
"""

from __future__ import annotations

import argparse
import json
import statistics
import sys
from pathlib import Path

MIN_CHARS = 40      # 太短的段落没法改句数
RUN_MIN = 3         # 连续 >=3 段句数相近，才算「对称」
NEAR = 1            # 句数相差 <=1 视为相近
OFFSETS = (-2, 2, -1, 3, -2, 2, -1, 3)


def sent_count(row: dict) -> int:
    s = row.get("sentences")
    return len(s) if isinstance(s, list) else int(s or 0)


def plan(doc: dict) -> dict:
    rows = doc.get("paragraphs", [])
    cand = []
    for r in rows:
        text = (r.get("text") or "").strip()
        if r.get("locked") or len(text) < MIN_CHARS:
            cand.append(None)
            continue
        cand.append({"pid": r["pid"], "n": sent_count(r), "chars": len(text)})

    runs = []
    spans = []
    i = 0
    while i < len(cand):
        if cand[i] is None:
            i += 1
            continue
        j = i + 1
        while (
            j < len(cand)
            and cand[j] is not None
            and abs(cand[j]["n"] - cand[j - 1]["n"]) <= NEAR
        ):
            j += 1
        if j - i >= RUN_MIN:
            runs.append(cand[i:j])
            spans.append((i, j))
        i = j

    targets: dict[str, dict] = {}
    units = []
    for (start, end), run in zip(spans, runs):
        med = int(statistics.median([x["n"] for x in run]))
        low, high = max(2, med - 2), med + 4
        prev = None
        for k, x in enumerate(run):
            to = low if k % 2 == 0 else high
            # 相邻目标必须至少差 2：串的判定是「相差 <=1」，差 1 仍算同一条串。
            # 目标等于原值时也要挪开，否则这一段改了等于没改。
            if to == x["n"] or (prev is not None and abs(to - prev) < 2):
                cands = [v for v in (to + 2, to - 2, to + 3, to - 3, high, low) if v >= 2]
                to = next((v for v in cands if v != x["n"] and (prev is None or abs(v - prev) >= 2)), to)
            targets[x["pid"]] = {"from": x["n"], "to": to}
            prev = to

        # 边界各扩 1 段：串的判定看相邻两句数，只改串内不碰边界，
        # 边界那一段仍可能和串首/串尾相差 <=1，串就拆不掉。
        # 放在最后一个成员之后处理，避免同一串重复生成 unit。
        if k != len(run) - 1:
            continue
        unit_pids = [x["pid"] for x in run]
        for bidx in (start - 1, end):
            if bidx < 0 or bidx >= len(cand) or cand[bidx] is None:
                continue
            b = cand[bidx]
            adj = targets.get(run[0]["pid"] if bidx < start else run[-1]["pid"], {}).get("to")
            if adj is None:
                continue
            to = adj + 2 if adj + 2 <= 10 else max(2, adj - 2)
            if to != b["n"]:
                targets[b["pid"]] = {"from": b["n"], "to": to}
            unit_pids = ([b["pid"]] + unit_pids) if bidx < start else (unit_pids + [b["pid"]])
        units.append({"pids": unit_pids, "run": [x["pid"] for x in run]})

    return {
        "schema": "paper-dual-reduce/pacing@1",
        "runs": [
            {
                "pids": [x["pid"] for x in run],
                "lens": [x["n"] for x in run],
                "median": int(statistics.median([x["n"] for x in run])),
            }
            for run in runs
        ],
        "targets": targets,
        "units": units,
    }


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="跨段节奏编排（破结构对称）")
    ap.add_argument("--doc", required=True)
    ap.add_argument("--out", required=True)
    args = ap.parse_args(argv)

    doc = json.loads(Path(args.doc).read_text(encoding="utf-8"))
    data = plan(doc)
    Path(args.out).write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")

    print("[pacing] 对称段串 %d 条 ｜ 目标句数 %d 段" % (len(data["runs"]), len(data["targets"])))
    for r in data["runs"][:5]:
        print("  %s %s" % (" ".join(r["pids"][:6]), r["lens"][:6]))
    print("[pacing] 已写出 %s" % args.out)
    return 0


if __name__ == "__main__":
    sys.exit(main())
