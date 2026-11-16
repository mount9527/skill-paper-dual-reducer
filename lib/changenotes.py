"""把「改了什么」写成可验证的量，而不是「优化了表达」。

SOP §5.6 的硬要求：变更说明要写「删除了 2 处套话、拆分了 1 个长句」，不能写
「优化了表达」——前者可复核，后者是空话。所以这里的每个数都是确定性计数，
不含任何模型判断，报告里的每一句都能被人工数一遍对上。
"""

from __future__ import annotations

from . import features


def _sentences(text: str) -> list[str]:
    return [s["text"] for s in features.split_sentences(text)]


def _avg_len(sents: list[str], fallback: str) -> float:
    lens = [len(s) for s in sents] or [len(fallback)]
    return sum(lens) / len(lens)


def _template_hits(text: str) -> int:
    return sum(len(rx.findall(text)) for rx in features._TEMPLATE_RX)


def _connective_hits(text: str) -> int:
    return sum(text.count(w) for w in features.CONNECTIVES)


def _filler_chars(text: str) -> int:
    return sum(text.count(w) * len(w) for w in features.FILLERS)


def describe(before: str, after: str) -> dict:
    """返回 {"metrics": {...}, "notes": [...]}；notes 为可验证的中文变更项。"""
    sb, sa = _sentences(before), _sentences(after)
    mb, ma = _avg_len(sb, before), _avg_len(sa, after)
    tb, ta = _template_hits(before), _template_hits(after)
    cb, ca = _connective_hits(before), _connective_hits(after)
    fb, fa = _filler_chars(before), _filler_chars(after)
    chars_b, chars_a = len(before.strip()), len(after.strip())

    notes: list[str] = []
    if len(sb) != len(sa):
        notes.append("句数 %d→%d" % (len(sb), len(sa)))
    if round(mb) != round(ma):
        notes.append("平均句长 %d→%d 字" % (round(mb), round(ma)))
    if tb != ta:
        notes.append("套语 %d→%d 处" % (tb, ta))
    if cb != ca:
        notes.append("连接词 %d→%d 处" % (cb, ca))
    if fb != fa:
        notes.append("抽象词 %d→%d 字" % (fb, fa))
    if chars_b != chars_a:
        notes.append("字数 %d→%d" % (chars_b, chars_a))
    if not notes:
        notes.append("表述调整，计数未变")

    return {
        "metrics": {
            "sentences": [len(sb), len(sa)],
            "avg_sentence_len": [round(mb, 1), round(ma, 1)],
            "template_hits": [tb, ta],
            "connective_hits": [cb, ca],
            "filler_chars": [fb, fa],
            "chars": [chars_b, chars_a],
        },
        "notes": notes,
    }
