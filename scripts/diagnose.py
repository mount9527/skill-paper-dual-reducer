#!/usr/bin/env python3
"""diagnose.py —— doc.json → profile.json

只做诊断，不改一个字。产出三样东西：
1. 每段的六维特征、风险分、风险等级
2. 每段的成因归因（哪一维超标 → 用哪个算子）
3. 篇内重复：13 字指纹找出自己抄自己的片段

用法
----
    python scripts/diagnose.py --doc out/doc.json --out out/profile.json --top 15

风险分的口径：0.85 × 六维加权 + 0.15 × 篇内重复占比，只用于**同一篇内部排序**。
它不是 AIGC 概率，不能对外报数，原因见 references/thresholds.md。
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from statistics import mean

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from lib.features import (TH, WEIGHTS, attribute, paragraph_features,
                          risk_level, risk_score)
from lib.fingerprint import FingerprintIndex
from lib.judges import CFG


def _neighbor_effect(rows: list[dict], i: int, lens: list[int]) -> float:
    """邻段长度相似度：与左右邻居字数越接近，段落层面越缺起伏。"""
    near = [lens[j] for j in (i - 1, i + 1) if 0 <= j < len(lens)]
    if not near or not lens[i]:
        return 0.0
    m = mean(near)
    if m <= 0:
        return 0.0
    return max(0.0, 1.0 - abs(lens[i] - m) / max(20.0, m))


def diagnose(doc: dict, top: int = 15) -> dict:
    rows = doc["paragraphs"]
    targets = [r for r in rows if not r["locked"]]

    index = FingerprintIndex()
    for r in targets:
        index.add(r["pid"], r["text"])

    lens = [r["chars"] for r in rows]
    pos = {r["pid"]: i for i, r in enumerate(rows)}

    paras = []
    for r in targets:
        feats = paragraph_features(r["text"])
        dims = dict(feats["dims"])
        nb = _neighbor_effect(rows, pos[r["pid"]], lens)
        dims["a6"] = round(min(1.0, dims["a6"] * 0.6 + nb * 0.4), 4)

        dup = index.dup_ratio(r["pid"])
        risk = round(0.85 * risk_score(dims) + 0.15 * min(1.0, dup / CFG["repeat_threshold"]), 4)
        causes = attribute(dims, dup_ratio=dup)
        paras.append({
            "pid": r["pid"],
            "chars": r["chars"],
            "risk": risk,
            "level": risk_level(risk),
            "dims": dims,
            "raw": feats["raw"],
            "dup_ratio": dup,
            "causes": causes,
            "suggest_op": causes[0]["op"] if causes else None,
        })

    paras.sort(key=lambda p: -p["risk"])
    high = sum(1 for p in paras if p["level"] == "high")
    mid = sum(1 for p in paras if p["level"] == "mid")

    fp = index.stats()
    dups = index.duplicate_spans()
    rewritable_chars = sum(p["chars"] for p in paras)

    return {
        "schema": "paper-dual-reduce/profile@1",
        "source": doc["source"],
        "thresholds": {
            "dims": TH,
            "weights": WEIGHTS,
            "aigc_threshold": CFG["aigc_threshold"],
            "repeat_threshold": CFG["repeat_threshold"],
            "fidelity": {k: CFG[k] for k in ("min_overlap", "len_ratio",
                                             "max_neg_delta", "max_quant_delta")},
        },
        "doc": {
            "paragraphs": len(paras),
            "chars": rewritable_chars,
            "risk_mean": round(mean([p["risk"] for p in paras]), 4) if paras else 0.0,
            "risk_max": max([p["risk"] for p in paras], default=0.0),
            "level_share": {
                "high": round(high / len(paras), 3) if paras else 0.0,
                "mid": round(mid / len(paras), 3) if paras else 0.0,
            },
            "dim_means": {
                k: round(mean([p["dims"][k] for p in paras]), 4) if paras else 0.0
                for k in WEIGHTS
            },
        },
        "duplicates": {"stats": fp, "spans": [m.json() for m in dups[:top]]},
        "summary": {"high": high, "mid": mid, "low": len(paras) - high - mid},
        "ranking": [p["pid"] for p in paras],
        "top": paras[:top],
        "paragraphs": paras,
    }


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="六维特征诊断：每段风险、归因、篇内重复")
    ap.add_argument("--doc", required=True, help="inspect_docx.py 产出的 doc.json")
    ap.add_argument("--out", required=True, help="输出 profile.json")
    ap.add_argument("--top", type=int, default=15, help="报告里保留的高风险段数量（默认 15）")
    ap.add_argument("--quiet", action="store_true")
    args = ap.parse_args(argv)

    doc = json.loads(Path(args.doc).read_text(encoding="utf-8"))
    profile = diagnose(doc, args.top)

    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(profile, ensure_ascii=False, indent=1), encoding="utf-8")

    if not args.quiet:
        d = profile["doc"]
        print("[diagnose] 可改写段 %d ｜ 均风险 %.3f ｜ 高风险 %d ｜ 中风险 %d"
              % (d["paragraphs"], d["risk_mean"], profile["summary"]["high"], profile["summary"]["mid"]))
        print("[diagnose] 维度均值：" + "  ".join("%s=%.2f" % kv for kv in d["dim_means"].items()))
        print("[diagnose] 篇内重复率 %.1f%%（%d 个片段）"
              % (profile["duplicates"]["stats"]["repeat_rate"] * 100, len(profile["duplicates"]["spans"])))
        for p in profile["top"][:5]:
            top_cause = p["causes"][0]["name"] if p["causes"] else "—"
            print("  %s risk=%.2f chars=%d 主因=%s 建议=%s"
                  % (p["pid"], p["risk"], p["chars"], top_cause, p["suggest_op"]))
    print("[diagnose] 已写出 %s" % out)
    return 0


if __name__ == "__main__":
    sys.exit(main())
