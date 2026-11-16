"""三个裁判 + 仲裁。

裁判的职责不是「打分好不好看」，而是各自守一条线：
- 保真裁判（fidelity）：意思有没有变、事实有没有动。**有否决权**。
- AIGC 裁判（aigc）：改写后还剩多少 AI 风格。只用于排序，不输出对外数字。
- 重复裁判（repeat）：改写有没有引入新的篇内重复。

仲裁顺序是固定的，不允许调换：
    硬门禁（实体/长度） → 保真否决 → 双指标联合取优
把三条线揉成一个加权和是最常见的错误——它会用「重复降了很多」去抵掉「事实被改了」。
"""

from __future__ import annotations

from .features import fidelity_signals, paragraph_features, risk_score
from .anchor import entity_gate

# ---- 阈值（与 references/thresholds.md 同步） ----
CFG = {
    "min_overlap": 0.55,        # 字二元组 Dice 下限
    "len_ratio": (0.60, 1.25),  # 改写后长度比允许区间
    "max_neg_delta": 0,         # 否定词数量不得变化
    "max_quant_delta": 1,       # 量词允许 ±1（合并句子时的自然漂移）
    "aigc_threshold": 0.62,     # risk 达到此值视为「高」
    "repeat_threshold": 0.35,   # 13 字片段外部重复比例上限
    "regression_cap": 0.15,     # 单指标允许的有界回退
}


def fidelity(before: str, after: str) -> dict:
    """保真裁判。返回 pass + 具体原因，任何一个原因都足以否决。"""
    sig = fidelity_signals(before, after)
    reasons = []
    if sig["overlap"] < CFG["min_overlap"]:
        reasons.append("overlap_low:%.3f" % sig["overlap"])
    lo, hi = CFG["len_ratio"]
    if not (lo <= sig["len_ratio"] <= hi):
        reasons.append("length_ratio:%.3f" % sig["len_ratio"])
    if abs(sig["neg_delta"]) > CFG["max_neg_delta"]:
        reasons.append("negation_shift:%+d" % sig["neg_delta"])
    if abs(sig["quant_delta"]) > CFG["max_quant_delta"]:
        reasons.append("quantifier_shift:%+d" % sig["quant_delta"])
    return {
        "pass": not reasons,
        "reasons": reasons,
        "signals": sig,
    }


def aigc(text: str) -> dict:
    """AIGC 代理裁判。返回 0~1 的 risk 与相对阈值的比值 r。"""
    feats = paragraph_features(text)
    risk = risk_score(feats["dims"])
    return {
        "risk": risk,
        "r": round(risk / CFG["aigc_threshold"], 4),
        "dims": feats["dims"],
        "raw": feats["raw"],
    }


def repeat(text: str, index=None, pid: str | None = None) -> dict:
    """重复裁判。有指纹索引时用索引（跨段重复），否则退化为段内自重复。"""
    if index is not None and pid is not None and pid in index.grams:
        ratio = index.dup_ratio(pid)
    else:
        from collections import Counter

        from .fingerprint import hashes

        hs = hashes(text)
        if hs:
            counter = Counter(hs)
            ratio = round(sum(v for v in counter.values() if v > 1) / len(hs), 4)
        else:
            ratio = 0.0
    return {
        "ratio": ratio,
        "r": round(ratio / CFG["repeat_threshold"], 4),
        "threshold": CFG["repeat_threshold"],
    }


def entity_hard_gate(before: str, after: str) -> dict:
    """实体门禁：开关式，不参与打分。"""
    diff = entity_gate(before, after)
    return {"pass": not diff["added"] and not diff["removed"], **diff}


def arbitrate(candidates: list[dict], baseline: dict | None = None) -> dict:
    """从候选中选一个。candidates 每项需含 id / text / fidelity / aigc / repeat。

    返回 {"chosen": id|None, "reason": str, "detail": {...}}。
    chosen 为 None 表示「都不如原文」，调用方应保留原段。
    """
    if not candidates:
        return {"chosen": None, "reason": "no_candidates", "detail": {}}

    # 1) 硬门禁
    survivors = [c for c in candidates if c["fidelity"]["pass"]]
    gate_failed = [c["id"] for c in candidates if not c["fidelity"]["pass"]]
    if not survivors:
        return {
            "chosen": None,
            "reason": "all_failed_fidelity",
            "detail": {"failed": gate_failed},
        }

    # 2) minimax：先保证「最坏的那个指标」尽可能低
    def worst(c):
        return max(c["aigc"]["r"], c["repeat"]["r"])

    survivors.sort(key=lambda c: (worst(c), c["aigc"]["r"] + c["repeat"]["r"]))
    best = survivors[0]

    if baseline is not None:
        b_worst = max(baseline["aigc"]["r"], baseline["repeat"]["r"])
        # 累计回退封顶：任何单一指标不得比原文差出 regression_cap
        cap = CFG["regression_cap"]
        if (best["aigc"]["r"] - baseline["aigc"]["r"] > cap
                or best["repeat"]["r"] - baseline["repeat"]["r"] > cap):
            return {"chosen": None, "reason": "regression_cap_exceeded", "detail": {}}
        if worst(best) >= b_worst - 1e-9:
            return {"chosen": None, "reason": "no_improvement", "detail": {"baseline_worst": round(b_worst, 4)}}

    return {
        "chosen": best["id"],
        "reason": "minimax",
        "detail": {
            "r_aigc": best["aigc"]["r"],
            "r_repeat": best["repeat"]["r"],
            "worst": round(worst(best), 4),
            "rejected": gate_failed,
        },
    }
