"""改写前后的一致性比较：格式属性 diff + 实体 diff + 锁定段不变性。

两层防线
--------
- 段级（apply_edits 调用）：单段重建后立刻比对格式签名，不一致就只回滚这一段，
  拒绝原因记为 format_drift。好处是坏一段不影响其他段。
- 篇级（verify 调用）：整篇比对，任何非空 diff 都意味着交付不可接受，
  按硬约束应整篇回退到原件。这一层防的是「段级检查没想到的情况」。

比较的是**显式属性**：run 上写了什么就比什么。没有 rPr 的 run 统一记为 inherit，
因为它的实际格式继承自样式，改写不会改变样式。
"""

from __future__ import annotations

from .anchor import entity_gate
from .ooxml import Docx, Paragraph, paragraph_format


def _p_fmt(p: Paragraph) -> dict:
    return paragraph_format(p.el)


def paragraph_signatures(p: Paragraph) -> tuple[str, frozenset[str]]:
    """(段落格式签名, run 格式签名集合)。集合而非多重集：合并同格式 run 不算漂移。"""
    return (p.signature, frozenset(r.signature for r in p.runs()))


def _fmt_delta(before: dict, after: dict) -> list[dict]:
    out = []
    for k in before:
        if before[k] != after[k]:
            out.append({"field": k, "before": before[k], "after": after[k]})
    return out


def compare(before_path: str, after_path: str) -> dict:
    b = Docx(before_path)
    a = Docx(after_path)
    bp, ap = b.paragraphs(), a.paragraphs()

    format_diff: list[dict] = []
    locked_diff: list[dict] = []
    changed: list[dict] = []
    aligned = min(len(bp), len(ap))

    for i in range(aligned):
        x, y = bp[i], ap[i]
        if x.text != y.text:
            changed.append({
                "pid": x.pid,
                "before_chars": x.chars,
                "after_chars": y.chars,
                "ratio": round(y.chars / max(1, x.chars), 3),
            })

        # 锁定段必须逐字不变；但格式检查对所有段落都要做——
        # 「锁定」约束的是改写，不是允许格式漂移。
        if x.lock_reason() and x.text != y.text:
            locked_diff.append({
                "pid": x.pid,
                "reason": x.lock_reason(),
                "before": x.text[:60],
                "after": y.text[:60],
            })

        # 段落格式
        dx = _fmt_delta(_p_fmt(x), _p_fmt(y))
        if dx:
            format_diff.append({
                "pid": x.pid,
                "kind": "paragraph",
                "fields": dx[:6],
            })

        # 字符格式：集合比对，允许多 run 合并，不允许格式增删
        _sig_b, runs_b = paragraph_signatures(x)
        _sig_a, runs_a = paragraph_signatures(y)
        missing = runs_b - runs_a
        added = runs_a - runs_b
        if missing or added:
            format_diff.append({
                "pid": x.pid,
                "kind": "run",
                "missing": sorted(missing),
                "added": sorted(added),
            })

    structure_diff = []
    if len(bp) != len(ap):
        structure_diff.append({"field": "paragraphs", "before": len(bp), "after": len(ap)})
    if b.table_count() != a.table_count():
        structure_diff.append({"field": "tables", "before": b.table_count(), "after": a.table_count()})
    if b.section_count() != a.section_count():
        structure_diff.append({"field": "sections", "before": b.section_count(), "after": a.section_count()})

    entity = entity_gate(b.text_all(), a.text_all())

    before_chars = sum(p.chars for p in bp)
    after_chars = sum(p.chars for p in ap)

    passed = (
        not format_diff
        and not locked_diff
        and not structure_diff
        and not entity["added"]
        and not entity["removed"]
    )

    return {
        "schema": "paper-dual-reduce/verdict@1",
        "passed": passed,
        "source": {"before": before_path, "after": after_path},
        "structure": {
            "paragraphs": {"before": len(bp), "after": len(ap)},
            "tables": {"before": b.table_count(), "after": a.table_count()},
            "sections": {"before": b.section_count(), "after": a.section_count()},
            "diff": structure_diff,
        },
        "format_diff": format_diff,
        "locked_diff": locked_diff,
        "entity_diff": entity,
        "text": {
            "before_chars": before_chars,
            "after_chars": after_chars,
            "changed_paragraphs": len(changed),
            "items": changed,
        },
        "summary": {
            "format_issues": len(format_diff),
            "locked_changes": len(locked_diff),
            "entity_added": len(entity["added"]),
            "entity_removed": len(entity["removed"]),
            "structure_issues": len(structure_diff),
        },
    }
