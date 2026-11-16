"""六维特征：把「像不像 AI 写的」拆成六个可测量的量，每个归一到 0~1（越大越像 AI）。

维度与依据
----------
a1 句长节奏   检测器看突发性（burstiness）。人写句子长短交错，AI 输出偏向均齐的中长句。
a2 连接词密度  AI 偏爱显式逻辑连接词（首先/其次/此外/因此/综上所述），密度显著高于人类学术写作。
a3 句式模板度  套语框架（随着…的发展、在…方面、对…进行…）是低困惑度片段，最容易被判为生成文本。
a4 信息空泛度  数字、引用、书名、缩写密度低，说明句子在讲「正确但无信息」的话。
a5 词汇平淡度  高频抽象名词堆叠 + 二元组重复率高，词汇意外度低。
a6 结构平行度  句首重复、句数雷同、与邻段长度接近，段落层面缺少起伏。

注意：这些量都是**相对指标**，不是概率。它们只用于同一篇文档内部的排序（哪段更危险），
不能跨文档比较，更不能当成「AIGC 率」。这条限制在 references/thresholds.md 里有说明。
"""

from __future__ import annotations

import re
from statistics import mean, pstdev

# --------------------------------------------------------------------------
# 阈值（与 references/thresholds.md 保持一致）
# --------------------------------------------------------------------------

TH = {
    "cv_full": 0.55,          # 句长变异系数达到此值即认为「足够不规则」
    "cv_floor": 0.20,         # 低于此值强烈均齐
    "mid_len": (20, 35),      # 学术写作的「甜区」句长
    "conn_full": 0.75,        # 每句连接词数达到此值即满分
    "template_full": 1.60,    # 每百字模板命中数达到此值即满分
    "value_full": 2.20,       # 每百字信息性记号数达到此值即「具体」
    "bigram_full": 0.35,      # 二元组重复率达到此值即满分
    "filler_full": 0.22,      # 抽象名词字占比达到此值即满分
    "start_dup_full": 0.50,   # 句首三字重复率达到此值即满分
}

WEIGHTS = {"a1": 0.22, "a2": 0.16, "a3": 0.22, "a4": 0.14, "a5": 0.10, "a6": 0.16}

#: 显式逻辑连接词。密度过高是生成文本的强特征。
CONNECTIVES = [
    "首先", "其次", "再次", "最后", "此外", "另外", "同时", "与此同时", "因此", "因而",
    "所以", "然而", "但是", "而且", "并且", "总之", "综上", "综上所述", "由此可见",
    "值得注意的是", "需要指出的是", "一方面", "另一方面", "换言之", "也就是说",
    "不仅", "从而", "进而", "在此基础上", "具体而言", "总体而言", "简而言之",
]

#: 模板化句式（正则）。命中一次记一次。
TEMPLATES = [
    r"随着[^，。；]{2,14}的(?:发展|进步|普及|推进|深入|变化)",
    r"在[^，。；]{2,14}(?:方面|层面|领域|背景下|视角下)",
    r"对[^，。；]{2,14}进行[^，。；]{2,10}",
    r"具有[^，。；]{2,10}(?:意义|价值|作用|优势)",
    r"为[^，。；]{2,14}提供[^，。；]{2,10}(?:参考|依据|支撑|借鉴)",
    r"(?:不仅|不但)[^，。；]{2,30}(?:而且|还|更)",
    r"(?:是|成为)[^，。；]{2,14}的(?:重要|关键|核心|主要)",
    r"(?:本文|本研究|文章|论文)(?:旨在|试图|尝试|主要)",
    r"(?:通过|采用|运用)[^，。；]{2,14}(?:方法|手段|方式|途径)",
    r"(?:显得|尤为|至关重要|不可或缺|不容忽视|不可忽视)",
    r"(?:有效|显著)(?:提升|改善|促进|推动|增强|提高)",
    r"(?:进一步)(?:分析|探讨|研究|论述|阐释)",
    r"在(?:新时代|新形势|新阶段|新格局)",
    r"(?:既是|也是)[^，。；]{2,20}(?:又是|也是)",
]
_TEMPLATE_RX = [re.compile(p) for p in TEMPLATES]

#: 抽象名词 / 套话词。占比高说明句子在讲「正确但无信息」的话。
FILLERS = [
    "显著", "有效", "重要", "关键", "核心", "进一步", "相关", "方面", "问题", "影响",
    "作用", "机制", "路径", "维度", "层面", "体系", "模式", "策略", "意义", "价值",
    "发展", "提升", "优化", "构建", "推动", "促进", "实现", "体现", "反映", "表明",
]

NEGATIONS = ["不", "未", "无", "非", "没有", "难以", "避免", "缺乏", "拒绝"]
QUANTIFIERS = ["全部", "均", "所有", "多数", "部分", "仅", "至少", "最多", "显著", "略微", "略", "大幅"]

_SENT_RX = re.compile(r"[^。！？!?；;\n]+[。！？!?；;]?")


def clamp(x: float) -> float:
    return max(0.0, min(1.0, x))


def split_sentences(text: str) -> list[dict]:
    """按中文标点切句，保留字符偏移。长度不足 3 的片段丢弃。"""
    out = []
    for m in _SENT_RX.finditer(text):
        s = m.group(0).strip()
        if len(s) < 3:
            continue
        out.append({"text": s, "start": m.start(), "end": m.start() + len(m.group(0))})
    return out


def _ratio(n: float, d: float) -> float:
    return n / d if d else 0.0


def _bigram_repetition(text: str) -> float:
    chars = [c for c in text if not c.isspace()]
    if len(chars) < 6:
        return 0.0
    grams = [chars[i] + chars[i + 1] for i in range(len(chars) - 1)]
    return 1.0 - len(set(grams)) / len(grams)


def paragraph_features(text: str) -> dict:
    """单段六维特征。返回原始量 + 归一分。"""
    body = text.strip()
    sents = split_sentences(body)
    lens = [len(s["text"]) for s in sents] or [len(body)]
    n = len(lens)
    mu = mean(lens)
    cv = (pstdev(lens) / mu) if mu else 0.0

    # a1 句长节奏
    uniformity = 1.0 - clamp((cv - TH["cv_floor"]) / max(1e-6, TH["cv_full"] - TH["cv_floor"]))
    lo, hi = TH["mid_len"]
    mid_share = _ratio(sum(1 for x in lens if lo <= x <= hi), n)
    a1 = clamp(0.65 * uniformity + 0.35 * mid_share)

    # a2 连接词密度
    conn_hits = sum(body.count(w) for w in CONNECTIVES)
    conn_density = _ratio(conn_hits, n)
    a2 = clamp(conn_density / TH["conn_full"])

    # a3 句式模板度
    tpl_hits = sum(len(rx.findall(body)) for rx in _TEMPLATE_RX)
    tpl_rate = tpl_hits / max(1.0, len(body) / 100.0)
    a3 = clamp(tpl_rate / TH["template_full"])

    # a4 信息空泛度
    from .anchor import protected_spans

    spans = protected_spans(body)
    value = sum(1 for s in spans if s.kind in ("number", "percent", "date", "citation", "book", "acronym"))
    value_rate = value / max(1.0, len(body) / 100.0)
    a4 = clamp(1.0 - value_rate / TH["value_full"])

    # a5 词汇平淡度
    rep = _bigram_repetition(body)
    filler_chars = sum(body.count(w) * len(w) for w in FILLERS)
    filler_ratio = _ratio(filler_chars, len(body))
    a5 = clamp(0.55 * (rep / TH["bigram_full"]) + 0.45 * (filler_ratio / TH["filler_full"]))

    # a6 结构平行度（段内部分；与邻段的比较在 diagnose 里补）
    starts = [s["text"][:3] for s in sents if len(s["text"]) >= 3]
    start_dup = 1.0 - _ratio(len(set(starts)), len(starts)) if starts else 0.0
    a6 = clamp(start_dup / TH["start_dup_full"])

    dims = {"a1": round(a1, 4), "a2": round(a2, 4), "a3": round(a3, 4),
            "a4": round(a4, 4), "a5": round(a5, 4), "a6": round(a6, 4)}
    return {
        "dims": dims,
        "raw": {
            "sentences": n,
            "avg_len": round(mu, 2),
            "cv": round(cv, 3),
            "min_len": min(lens),
            "max_len": max(lens),
            "connective_hits": conn_hits,
            "template_hits": tpl_hits,
            "value_marks": value,
            "bigram_repeat": round(rep, 3),
            "filler_ratio": round(filler_ratio, 3),
            "start_dup": round(start_dup, 3),
        },
    }


def risk_score(dims: dict) -> float:
    return round(sum(WEIGHTS[k] * dims[k] for k in WEIGHTS), 4)


def risk_level(score: float) -> str:
    if score >= 0.62:
        return "high"
    if score >= 0.45:
        return "mid"
    return "low"


#: 成因 → 算子。v0.1 只启用 O1/O2/O3/O4/O9，其余标注 availability。
CAUSES = [
    {"code": "A1", "name": "句长均齐", "dim": "a1", "op": "O1", "op_name": "句长重分布", "v01": True},
    {"code": "A2", "name": "连接词过密", "dim": "a2", "op": "O2", "op_name": "连接词抑制", "v01": True},
    {"code": "A3", "name": "句式模板化", "dim": "a3", "op": "O4", "op_name": "句法重构", "v01": True},
    {"code": "A4", "name": "信息空泛", "dim": "a4", "op": "O5", "op_name": "具体化改写", "v01": False},
    {"code": "A5", "name": "词汇平淡", "dim": "a5", "op": "O8", "op_name": "同义替换", "v01": False},
    {"code": "A6", "name": "结构平行", "dim": "a6", "op": "O6", "op_name": "段落节奏调整", "v01": False},
    {"code": "A7", "name": "内部重复", "dim": None, "op": "O9", "op_name": "引用转述与合并", "v01": True},
]


def attribute(dims: dict, dup_ratio: float = 0.0, floor: float = 0.45) -> list[dict]:
    """按维度超阈值程度归因，返回按权重降序的成因列表。

    floor 是「算得上一个成因」的下限；低于它的维度不写进报告，避免噪声。
    """
    out = []
    for c in CAUSES:
        if c["dim"] is None:
            val = dup_ratio
        else:
            val = dims.get(c["dim"], 0.0)
        if val < floor:
            continue
        out.append({
            "code": c["code"],
            "name": c["name"],
            "op": c["op"],
            "op_name": c["op_name"],
            "value": round(val, 3),
            "available": c["v01"],
        })
    out.sort(key=lambda x: -x["value"])
    return out


def fidelity_signals(before: str, after: str) -> dict:
    """无需模型的语义保真代理：字二元组重合 + 否定词/量词守恒 + 长度比。

    这是 sem 的**替代品**，不是等价物：它抓不住「同义改写但意思反了」的深层错误，
    但对 v0.1 的三类高发问题（删句、改数、掉否定）足够敏感。
    接入句向量模型后可替换，见 references/thresholds.md。
    """
    def grams(s: str) -> list[str]:
        chars = [c for c in s if not c.isspace()]
        return [chars[i] + chars[i + 1] for i in range(len(chars) - 1)]

    ga, gb = grams(before), grams(after)
    if not ga or not gb:
        return {"overlap": 0.0, "len_ratio": 0.0, "neg_delta": 0, "quant_delta": 0}
    from collections import Counter

    ca, cb = Counter(ga), Counter(gb)
    inter = sum(min(ca[g], cb[g]) for g in ca)
    dice = 2.0 * inter / (len(ga) + len(gb))
    return {
        "overlap": round(dice, 4),
        "len_ratio": round(len(after.strip()) / max(1, len(before.strip())), 4),
        "neg_delta": sum(after.count(w) for w in NEGATIONS) - sum(before.count(w) for w in NEGATIONS),
        "quant_delta": sum(after.count(w) for w in QUANTIFIERS) - sum(before.count(w) for w in QUANTIFIERS),
    }
