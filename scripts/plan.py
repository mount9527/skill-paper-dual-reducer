#!/usr/bin/env python3
"""plan.py —— profile.json + doc.json → tasks.json

调度不是「按分数从高到低改到预算用完」，那样会把预算全砸在开头几段。这里用
风险加权背包：性价比 ρ = 预期降幅 ÷ 成本（按百字计），并加四条硬规则——

1. 单段单轮只派一个任务（多算子叠加会让归因失效，也没法验收）
2. 单段成本不超过本轮预算的 40%（防止一段吃掉全部额度）
3. 全篇改写字数不超过可改写总量的 40%（留出复检与二次迭代的余地）
4. 锁定段永不进池

用法
----
    python scripts/plan.py --profile out/profile.json --doc out/doc.json --out out/tasks.json
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

FLOOR_RISK = 0.30   # 低于此风险视为「不值得改」
DEFAULT_MIN = 0.45  # 只有中风险以上才进候选池
GUARANTEED_RISK = 0.70  # 达到此分数的段落不受「单段预算上限」限制


def plan(profile: dict, doc: dict, max_tasks: int = 12,
         budget_share: float = 0.40, min_risk: float = DEFAULT_MIN,
         style: str = "plain") -> dict:
    rows = {r["pid"]: r for r in doc["paragraphs"]}
    paras = [p for p in profile["paragraphs"] if p["risk"] >= min_risk]

    # 阈值是绝对线，但工具不能因为「整篇都不够危险」就什么都不做：
    # 用户明确要求改写时，退化为按相对排序取前几段，并在结果里标注 below_threshold。
    fallback = False
    if not paras and profile["paragraphs"]:
        paras = sorted(profile["paragraphs"], key=lambda p: -p["risk"])[:max_tasks]
        fallback = True

    total = sum(p["chars"] for p in profile["paragraphs"])
    budget = int(total * budget_share)

    scored = []
    for p in paras:
        gain = max(0.0, p["risk"] - FLOOR_RISK)
        cost = max(1, p["chars"])
        rho = gain / (cost / 100.0)
        scored.append({**p, "gain": round(gain, 4), "rho": round(rho, 4), "cost": cost})
    scored.sort(key=lambda x: -x["rho"])
    # 高风险段先保底：宁可挤掉两段中等风险的短段，也不能漏掉最危险的长段
    guaranteed = [p for p in scored if p["risk"] >= GUARANTEED_RISK]
    queue = sorted(guaranteed, key=lambda x: -x["risk"]) + [p for p in scored if p not in guaranteed]

    single_cap = int(budget * 0.40)
    used = 0
    tasks = []
    skipped_cap = 0
    for p in queue:
        if len(tasks) >= max_tasks or used >= budget:
            break
        over_cap = p["cost"] > single_cap
        if over_cap and p["risk"] < GUARANTEED_RISK:
            skipped_cap += 1
            continue
        if used + p["cost"] > budget:
            continue

        # 选算子：优先「可用的最高成因」，都不可用时退回 O4（句法重构，v0.1 通用算子）
        usable = [c for c in p["causes"] if c["available"]]
        if usable:
            op, op_name, cause = usable[0]["op"], usable[0]["op_name"], usable[0]["code"]
        else:
            op, op_name, cause = "O4", "句法重构", (p["causes"][0]["code"] if p["causes"] else "A3")

        row = rows[p["pid"]]
        tasks.append({
            "task_id": "t%03d" % (len(tasks) + 1),
            "pid": p["pid"],
            "op": op,
            "op_name": op_name,
            "cause": cause,
            "risk": p["risk"],
            "gain": p["gain"],
            "rho": p["rho"],
            "cost_chars": p["chars"],
            "dup_ratio": p["dup_ratio"],
            "constraints": {
                "min_chars": int(p["chars"] * 0.60),
                "max_chars": int(p["chars"] * 1.25),
                "keep_spans": [s["text"] for s in row["spans"]],
                "span_kinds": sorted({s["kind"] for s in row["spans"]}),
                "sentences": len(row["sentences"]),
                "style": row["style"],
                "forbidden": ["新增数字/年份/百分比", "新增或删除引用标记",
                              "改写书名、术语、英文缩写", "改变论断方向或结论"],
            },
            "dims": p["dims"],
        })
        used += p["chars"]

    return {
        "schema": "paper-dual-reduce/tasks@1",
        "round": 1,
        # 文风档案由这里决定，build_prompts 据此决定是否注入五条文风规则
        "text_style": style,
        "budget": {
            "total_chars": total,
            "share": budget_share,
            "budget_chars": budget,
            "used_chars": used,
            "used_share": round(used / total, 4) if total else 0.0,
            "single_cap": single_cap,
        },
        "counts": {
            "candidates": len(paras),
            "tasks": len(tasks),
            "skipped_by_cap": skipped_cap,
            "below_threshold": fallback,
            "min_risk": min_risk,
        },
        "tasks": tasks,
    }


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="按风险加权背包把诊断结果排成改写任务")
    ap.add_argument("--profile", required=True)
    ap.add_argument("--doc", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--max-tasks", type=int, default=12)
    ap.add_argument("--budget-share", type=float, default=0.40)
    ap.add_argument("--min-risk", type=float, default=DEFAULT_MIN)
    ap.add_argument("--style", choices=("plain", "academic"), default="plain",
                    help="文风档案：plain=平实五条（默认），academic=只做学术润色")
    args = ap.parse_args(argv)

    profile = json.loads(Path(args.profile).read_text(encoding="utf-8"))
    doc = json.loads(Path(args.doc).read_text(encoding="utf-8"))
    data = plan(profile, doc, args.max_tasks, args.budget_share, args.min_risk, args.style)

    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")

    b = data["budget"]
    if data["counts"]["below_threshold"]:
        print("[plan] 提示：全文没有段落达到风险阈值 %.2f，已退化为按相对排序取前 %d 段"
              % (data["counts"]["min_risk"], data["counts"]["tasks"]))
    print("[plan] 候选 %d ｜ 出任务 %d ｜ 文风 %s ｜ 用预算 %d/%d 字（%.0f%%）"
          % (data["counts"]["candidates"], data["counts"]["tasks"], data["text_style"],
             b["used_chars"], b["budget_chars"], b["used_share"] * 100))
    for t in data["tasks"][:6]:
        print("  %s %s risk=%.2f %s chars=%d"
              % (t["task_id"], t["pid"], t["risk"], t["op"], t["cost_chars"]))
    print("[plan] 已写出 %s" % out)
    return 0


if __name__ == "__main__":
    sys.exit(main())
