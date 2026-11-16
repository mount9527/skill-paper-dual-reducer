"""锚点与保护区：把「不能动的字符」从段落里圈出来，并据此做格式保真的 run 重建。

三个概念
--------
- 保护区（protected span）：数字、年份、百分比、引用标记、书名、引号内术语、英文缩写。
  改写文本必须按原顺序、原样保留它们，否则视为编造或篡改事实，直接否决。
- 锚点（anchor）：段落的头 12 字、尾 12 字、全文 sha1。用于跨进程核对「改的是不是同一段」，
  防止并发编辑或上游重排版导致的错位改写。
- 格式保真重建：改写后重新划分 run 边界时，普通文字用「主导格式」，保护区用它原先所属
  run 的格式，从而让粗体术语、斜体英文、上标引用等局部格式不丢。

任何一步断言失败都整段回滚——宁可少改一段，不可改坏格式。
"""

from __future__ import annotations

import hashlib
import re
import unicodedata
from collections import Counter
from copy import deepcopy
from dataclasses import dataclass

import xml.etree.ElementTree as ET

from .ooxml import (
    W_RPR,
    W_T,
    XML_SPACE,
    Paragraph,
    Run,
)

# --------------------------------------------------------------------------
# 保护区识别
# --------------------------------------------------------------------------

#: (kind, pattern)。顺序无关，重叠按「起点早、长度长」保留。
SPAN_PATTERNS: list[tuple[str, str]] = [
    ("citation", r"\[\s*\d+(?:\s*[-–—,，、]\s*\d+)*\s*\]"),          # [1] [1-3] [1,2]
    ("citation", r"［\s*\d+(?:\s*[-–—,，、]\s*\d+)*\s*］"),           # 全角方括号
    ("citation", r"（[^（）]{0,30}?(?:19|20)\d{2}[a-z]?[^（）]{0,12}?）"),  # （张三，2020）
    ("citation", r"\([A-Za-z][A-Za-z.\s]{1,20},\s*(?:19|20)\d{2}[a-z]?\)"),
    ("book", r"《[^》]{1,40}》"),
    ("quote", r"「[^」]{1,30}」|“[^”]{1,30}”|『[^』]{1,30}』"),
    ("acronym", r"(?<![A-Za-z])[A-Z]{2,8}(?![A-Za-z])"),             # BERT LSTM CNN
    ("percent", r"\d+(?:\.\d+)?\s*[%％]"),
    ("date", r"(?:19|20)\d{2}\s*年(?:\s*\d{1,2}\s*月)?"),
    ("number", r"\d+(?:[.,，]\d+)*"),
]

_COMPILED = [(k, re.compile(p)) for k, p in SPAN_PATTERNS]

#: 全角转半角，仅用于比较，不改写原文
_FW = str.maketrans("０１２３４５６７８９％．，", "0123456789%.,")


def _norm_entity(kind: str, text: str) -> str:
    s = unicodedata.normalize("NFKC", text).translate(_FW)
    s = re.sub(r"\s+", "", s)
    if kind in ("number", "percent", "date"):
        s = s.replace("，", ",").replace("．", ".")
    return s


@dataclass(frozen=True)
class Span:
    start: int
    end: int
    kind: str
    text: str

    def json(self) -> dict:
        return {"start": self.start, "end": self.end, "kind": self.kind, "text": self.text}


def protected_spans(text: str) -> list[Span]:
    """返回互不重叠的保护区，按起点排序。"""
    cands: list[Span] = []
    for kind, rx in _COMPILED:
        for m in rx.finditer(text):
            if m.end() > m.start():
                cands.append(Span(m.start(), m.end(), kind, m.group(0)))
    cands.sort(key=lambda s: (s.start, -(s.end - s.start)))
    out: list[Span] = []
    last_end = -1
    for sp in cands:
        if sp.start >= last_end:
            out.append(sp)
            last_end = sp.end
    return out


def spans_json(text: str) -> list[dict]:
    return [s.json() for s in protected_spans(text)]


def entities(text: str) -> Counter:
    """实体多重集合。用于「不新增事实」硬门禁。

    只统计保护区里的内容——数字、年份、引用、书名、术语、缩写。
    改写可以调整语序、拆分句子、替换同义词，但不得增减这些。
    """
    c: Counter = Counter()
    for sp in protected_spans(text):
        c[(_norm_entity(sp.kind, sp.text), sp.kind)] += 1
    return c


def entity_gate(before: str, after: str) -> dict:
    """返回 {"added": [...], "removed": [...]}，均为规范化后的字符串列表。"""
    b, a = entities(before), entities(after)
    added = sorted((a - b).elements())
    removed = sorted((b - a).elements())
    return {
        "added": ["%s:%s" % (kind, txt) for txt, kind in added],
        "removed": ["%s:%s" % (kind, txt) for txt, kind in removed],
    }


def anchors(text: str, head: int = 12, tail: int = 12) -> dict:
    t = text.strip()
    return {
        "head": t[:head],
        "tail": t[-tail:] if len(t) > tail else t,
        "sha1": hashlib.sha1(t.encode("utf-8")).hexdigest(),
        "chars": len(t),
    }


def spans_preserved(before: str, after: str) -> tuple[bool, str, list[tuple[int, int]]]:
    """检查 before 的保护区是否按原顺序出现在 after 中。

    返回 (ok, reason, 在 after 中的位置列表)。重复的保护区按出现次数逐个匹配，
    所以两处 "2023" 不会被一处 "2023" 满足。
    """
    pos = 0
    located: list[tuple[int, int]] = []
    for sp in protected_spans(before):
        j = after.find(sp.text, pos)
        if j < 0:
            return False, "entity_lost:%s:%s" % (sp.kind, sp.text), []
        located.append((j, j + len(sp.text)))
        pos = j + len(sp.text)
    return True, "", located


def is_subsequence(needle: list[str], haystack: list[str]) -> bool:
    it = iter(haystack)
    return all(any(x == y for y in it) for x in needle)


# --------------------------------------------------------------------------
# run 重建
# --------------------------------------------------------------------------

def _make_run(template: ET.Element, text: str) -> ET.Element:
    """复制模板 run 的 rPr，插入单一文本节点。"""
    r = deepcopy(template)
    for child in list(r):
        if child.tag != W_RPR:
            r.remove(child)
    t = ET.SubElement(r, W_T)
    t.text = text
    if text != text.strip() or "  " in text:
        t.set(XML_SPACE, "preserve")
    return r


def _run_offsets(runs: list[Run]) -> list[tuple[int, int]]:
    out, acc = [], 0
    for r in runs:
        out.append((acc, acc + len(r.text)))
        acc += len(r.text)
    return out


def rebuild_paragraph(par: Paragraph, new_text: str) -> tuple[bool, str, dict]:
    """把 par 的文本替换为 new_text，尽最大可能保留原有字符格式。

    三种情形：
    A 单 run            —— 直接改文本，保真度 100%
    B 多 run 但格式一致  —— 首个 run 承载全文，其余清空，视觉无变化
    C 多 run 且格式不同  —— 保护区作锚点重建：普通文字用主导格式，保护区用原属 run 的格式

    返回 (ok, reason, detail)。失败时调用方应回滚整段。
    """
    runs = par.runs()
    if not runs:
        return False, "no_runs", {}
    old = par.text
    detail: dict = {"runs_before": len(runs)}

    if any(r.el not in list(par.el) for r in runs):
        # run 被 w:hyperlink / w:ins 等包裹时，删除会破坏关系节点，直接放弃
        return False, "nested_run", detail

    ok, reason, located = spans_preserved(old, new_text)
    if not ok:
        return False, reason, detail

    sigs = [r.signature for r in runs]
    try:
        if len(runs) == 1:
            runs[0].set_text(new_text)
            detail["mode"] = "single"
        elif len(set(sigs)) == 1:
            runs[0].set_text(new_text)
            for r in runs[1:]:
                r.set_text("")
            detail["mode"] = "uniform"
        else:
            detail.update(_rebuild_anchored(par, runs, old, new_text, located))
            detail["mode"] = "anchored"
    except _AnchorLost as exc:
        return False, exc.reason, detail
    except Exception as exc:  # 结构性异常一律回滚
        return False, "rebuild_error:%s" % exc, detail

    detail["runs_after"] = len(par.runs())
    if par.text != new_text:
        return False, "text_mismatch", detail
    ok2, reason2, _ = spans_preserved(old, par.text)
    if not ok2:
        return False, "post_check:" + reason2, detail
    return True, "", detail


class _AnchorLost(Exception):
    """非主导格式的 run 在改写后找不到落点——无法保真，必须整段拒绝。"""

    def __init__(self, reason: str):
        super().__init__(reason)
        self.reason = reason


def _rebuild_anchored(
    par: Paragraph,
    runs: list[Run],
    old_text: str,
    new_text: str,
    located: list[tuple[int, int]],
) -> dict:
    """按「格式锚点 + 保护区」重建 run 边界。

    两类锚点：
    - 保护区：必须原样保留，位置已由 spans_preserved 校验
    - 格式锚点：与主导格式不同的 run，只要它的文本在改写后依然存在，就把该文本段
      恢复成它原来的格式（典型场景：斜体的《书名》、加粗的小标题、上标引用）

    格式锚点找不到落点时必须拒绝整段——否则加粗小标题会被悄悄抹平，
    而这正是「格式零漂移」要防的事。
    """
    spans = protected_spans(old_text)
    offsets = _run_offsets(runs)
    sigs = [r.signature for r in runs]
    dominant = max(range(len(runs)), key=lambda i: len(runs[i].text))
    dom_sig = sigs[dominant]

    def owner(char_idx: int) -> int:
        for i, (a, b) in enumerate(offsets):
            if a <= char_idx < b:
                return i
        return len(runs) - 1

    span_ranges = [
        (j, end, owner(spans[k].start)) for k, (j, end) in enumerate(located)
    ]

    anchors: list[tuple[int, int, int]] = []
    for i, r in enumerate(runs):
        if i == dominant or sigs[i] == dom_sig:
            continue
        t = r.text.strip()
        if len(t) < 2:
            continue
        j = new_text.find(t)
        if j < 0:
            raise _AnchorLost("format_anchor_lost:%s" % t[:12])
        anchors.append((j, j + len(t), i))
    anchors.sort(key=lambda a: (a[0], -(a[1] - a[0])))
    kept: list[tuple[int, int, int]] = []
    last = -1
    for a in anchors:
        if a[0] >= last:
            kept.append(a)
            last = a[1]
    anchors = kept

    cuts = {0, len(new_text)}
    for j, end, _ in span_ranges:
        cuts.update((j, end))
    for s, e, _ in anchors:
        cuts.update((s, e))
    cuts_sorted = sorted(c for c in cuts if 0 <= c <= len(new_text))

    segments: list[list] = []  # [文本, 模板 run 下标]
    for a, b in zip(cuts_sorted, cuts_sorted[1:]):
        if a >= b:
            continue
        tpl = None
        for s, e, idx in anchors:
            if s <= a and b <= e:
                tpl = idx
                break
        if tpl is None:
            for j, end, idx in span_ranges:
                if j <= a and b <= end:
                    tpl = idx
                    break
        if tpl is None:
            tpl = dominant
        # 相邻片段格式一致就合并，避免保护区边界把同一格式切碎（视觉无差别，但 run 数会膨胀）
        if segments and sigs[segments[-1][1]] == sigs[tpl]:
            segments[-1][0] += new_text[a:b]
        else:
            segments.append([new_text[a:b], tpl])

    anchor_el = runs[0].el
    idx0 = list(par.el).index(anchor_el)
    new_els = [_make_run(runs[tpl].el, txt) for txt, tpl in segments if txt]
    for k, el in enumerate(new_els):
        par.el.insert(idx0 + k, el)
    for r in runs:
        try:
            par.el.remove(r.el)
        except ValueError:
            pass
    return {
        "segments": len(new_els),
        "dominant_run": dominant,
        "format_anchors": len(anchors),
        "spans": len(spans),
    }


def rewrite_paragraph(docx, par: Paragraph, new_text: str) -> tuple[bool, str, dict]:
    """带快照回滚的改写入口。"""
    snapshot = docx.snapshot(par)
    ok, reason, detail = rebuild_paragraph(par, new_text)
    if not ok:
        docx.restore(par, snapshot)
    return ok, reason, detail
