#!/usr/bin/env python3
"""render_report.py —— doc.json + profile.json (+ verdict / result) → report.md

报告只陈述可复核的事实：段落编号、字数、风险分、成因、闸门拒绝原因、逐段变更量。
不出现「AIGC 率 12%」这类数字——代理分没有概率含义，写进报告就是误导。

三种模式（对应 SOP §5.6 的模式切换：只诊断 / 只改写 / 诊断加改写）
------------------------------------------------------------------
    --mode diagnose   只诊断：概览 + 锁定区 + 高风险段 + 维度均值 + 篇内重复
    --mode rewrite    只改写：概览 + 改写执行 + 三栏对照 + 复检结论
    --mode both       诊断 + 改写（默认）

「三栏对照」是 SOP §5.6 的硬要求：原文 / 改文 / 变更说明，逐段展开；变更说明写
「句数 6→4、套语 3→1 处」这类可数的事实，不写「优化了表达」。

用法
----
    python scripts/render_report.py --doc out/doc.json --profile out/profile.json \\
        --verdict out/rewritten.verdict.json --result out/rewritten.result.json \\
        --out out/report.md --mode both
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from lib.changenotes import describe  # noqa: E402
from lib.cli import read_json, safe_io  # noqa: E402

LEVEL_LABEL = {"high": "高", "mid": "中", "low": "低"}
CN_NUM = ["一", "二", "三", "四", "五", "六", "七", "八", "九", "十"]


def _table(headers: list[str], rows: list[list]) -> str:
    out = ["| " + " | ".join(headers) + " |",
           "|" + "|".join(["---"] * len(headers)) + "|"]
    for r in rows:
        out.append("| " + " | ".join(str(x) for x in r) + " |")
    return "\n".join(out)


def _clip(text: str, limit: int = 220) -> str:
    t = (text or "").replace("\n", " ").replace("|", "\\|").strip()
    return t if len(t) <= limit else t[:limit] + "…"


class Report:
    """按出现顺序自动编号，避免加了章节之后编号与引用对不上。"""

    def __init__(self) -> None:
        self.parts: list[str] = []
        self.n = 0

    def section(self, title: str) -> None:
        num = CN_NUM[self.n] if self.n < len(CN_NUM) else str(self.n + 1)
        self.n += 1
        self.parts.append("## %s、%s\n" % (num, title))

    def add(self, text: str) -> None:
        self.parts.append(text)

    def text(self) -> str:
        return "\n".join(self.parts)


def _overview(r: Report, doc: dict, profile: dict, text_style: str = "plain") -> None:
    s = doc["stats"]
    r.section("文档概览")
    r.add(_table(["指标", "数值"], [
        ["段落总数", s["paragraphs"]],
        ["标题 / 表格 / 分节", "%d / %d / %d" % (s["headings"], s["tables"], s["sections"])],
        ["可改写段落", s["rewritable"]],
        ["可改写字数", "%d / %d（%.0f%%）" % (s["rewritable_chars"], s["chars"],
                                           s["rewritable_share"] * 100)],
        ["风险分布（高/中/低）", "%d / %d / %d" % (profile["summary"]["high"],
                                                profile["summary"]["mid"],
                                                profile["summary"]["low"])],
        ["篇内重复率", "%.1f%%" % (profile["duplicates"]["stats"]["repeat_rate"] * 100)],
        ["文风档案", "平实五条（plain）" if text_style == "plain" else "学术润色（academic）"],
    ]))
    r.add("")


def _diagnosis(r: Report, doc: dict, profile: dict) -> None:
    d = profile["doc"]

    r.section("锁定区构成")
    r.add("以下内容不参与改写，任何改写请求都会被闸门拒绝：\n")
    r.add(_table(["原因", "段落数"],
                 [[k, v] for k, v in doc["lock_reasons"].items()] or [["—", 0]]))
    r.add("")

    r.section("高风险段落（按风险分排序）")
    rows = []
    for item in profile["top"][:12]:
        cause = item["causes"][0]["name"] if item["causes"] else "—"
        rows.append([item["pid"], item["chars"], "%.2f" % item["risk"],
                     LEVEL_LABEL[item["level"]], cause, item["suggest_op"] or "—",
                     "%.2f" % item["dup_ratio"]])
    r.add(_table(["段落", "字数", "风险分", "等级", "主因", "建议算子", "重复占比"], rows))
    r.add("")

    r.section("维度均值")
    r.add(_table(["维度", "均值", "含义"], [
        ["a1 句长节奏", "%.2f" % d["dim_means"]["a1"], "越高越均齐"],
        ["a2 连接词密度", "%.2f" % d["dim_means"]["a2"], "越高越依赖显式连接"],
        ["a3 句式模板度", "%.2f" % d["dim_means"]["a3"], "越高越套语"],
        ["a4 信息空泛度", "%.2f" % d["dim_means"]["a4"], "越高越缺具体信息"],
        ["a5 词汇平淡度", "%.2f" % d["dim_means"]["a5"], "越高越堆抽象名词"],
        ["a6 结构平行度", "%.2f" % d["dim_means"]["a6"], "越高越缺起伏"],
    ]))
    r.add("")

    r.section("篇内重复片段")
    dups = profile["duplicates"]["spans"]
    if dups:
        r.add("13 字指纹在本文内部重复出现的片段（不含外部语料比对）：\n")
        r.add(_table(["段落", "长度", "片段（前 40 字）"],
                     [[m["pid"], m["chars"], m["sample"]] for m in dups[:10]]))
    else:
        r.add("13 字指纹未发现篇内重复片段。")
    r.add("")


def _execution(r: Report, result: dict | None) -> None:
    r.section("改写执行")
    if not result:
        r.add("本次运行没有提交改写。")
        r.add("")
        return
    c = result["counts"]
    r.add(_table(["指标", "数值"], [
        ["提交编辑", c["edits"]],
        ["接受", c["applied"]],
        ["拒绝", c["rejected"]],
        ["执行状态", result["status"]],
    ]))
    rejected = [i for i in result["items"] if i["status"] == "rejected"]
    if rejected:
        r.add("\n**拒绝明细**（这是闸门在起作用，不是失败）：\n")
        r.add(_table(["段落", "算子", "原因"],
                     [[i["pid"], i.get("op") or "—", i["reason"]] for i in rejected[:20]]))
    r.add("")


def _comparison(r: Report, doc: dict, result: dict | None) -> None:
    """三栏对照：原文 / 改文 / 变更说明（SOP §5.6）。"""
    r.section("改写对照（原文 / 改文 / 变更说明）")
    if not result:
        r.add("本次运行没有改写结果，无对照可列。")
        r.add("")
        return

    index = {row["pid"]: row for row in doc["paragraphs"]}
    applied = [i for i in result["items"] if i["status"] == "applied"]
    if not applied:
        r.add("没有编辑通过闸门，原文保持不动。")
        r.add("")
        return

    rows = []
    for item in applied:
        pid = item["pid"]
        before = index.get(pid, {}).get("text", "")
        after = item.get("text") or "（该次运行未记录改后文本）"
        notes = describe(before, after)["notes"] if item.get("text") else ["—"]
        rows.append([pid, _clip(before), _clip(after), "；".join(notes)])
    r.add(_table(["段落", "原文", "改文", "变更说明"], rows))
    r.add("")


def _verdict(r: Report, verdict: dict | None) -> None:
    r.section("复检结论")
    if not verdict:
        r.add("本次运行没有做篇级复检。")
        r.add("")
        return
    sm = verdict["summary"]
    r.add(_table(["检查项", "结果"], [
        ["格式漂移（段落/字符）", sm["format_issues"]],
        ["锁定段改动", sm["locked_changes"]],
        ["实体新增", sm["entity_added"]],
        ["实体缺失", sm["entity_removed"]],
        ["结构变化", sm["structure_issues"]],
        ["结论", "通过" if verdict["passed"] else "不通过（应整篇回退）"],
    ]))
    r.add("")
    r.add("字数 %d → %d，改写段 %d。\n" % (
        verdict["text"]["before_chars"], verdict["text"]["after_chars"],
        verdict["text"]["changed_paragraphs"]))


def render(doc: dict, profile: dict, verdict: dict | None, result: dict | None,
           mode: str = "both", text_style: str = "plain") -> str:
    if mode not in ("diagnose", "rewrite", "both"):
        raise ValueError("mode 只能是 diagnose / rewrite / both，收到 %r" % mode)

    r = Report()
    r.add("# 论文双降 · 诊断与改写报告\n")
    r.add("> 本报告由 paper-dual-reduce 自动生成，仅用于投稿前自查。"
          "文中的「风险分」是篇内相对指标，不是 AIGC 概率，也不能预测任何平台的检测结果。\n")

    _overview(r, doc, profile, text_style)
    if mode in ("diagnose", "both"):
        _diagnosis(r, doc, profile)
    if mode in ("rewrite", "both"):
        _execution(r, result)
        _comparison(r, doc, result)
        _verdict(r, verdict)

    r.add("## 附：口径说明\n")
    r.add("- 风险分 = 0.85 × 六维加权 + 0.15 × 篇内重复占比，取值 0~1，只用于同篇排序。")
    r.add("- 六维特征全部基于可解释的统计量，不含模型推断，因此不随基座模型换代漂移。")
    r.add("- 「变更说明」里的每个数字都是脚本数出来的，可逐条人工复核。")
    r.add("- 「通过」只代表格式与实体未被破坏，不代表任何平台检测结果会下降。")
    r.add("- 本工具不比对任何外部语料库，篇内重复不等于查重率。")
    return r.text()


def main(argv: list[str] | None = None) -> int:
    safe_io()
    ap = argparse.ArgumentParser(description="生成 Markdown 诊断 / 改写报告")
    ap.add_argument("--doc", required=True)
    ap.add_argument("--profile", required=True)
    ap.add_argument("--verdict", help="verify.py 产出的 verdict.json")
    ap.add_argument("--result", help="apply_edits.py 产出的 result.json")
    ap.add_argument("--out", required=True)
    ap.add_argument("--mode", choices=("diagnose", "rewrite", "both"), default="both",
                    help="只诊断 / 只改写 / 诊断加改写（默认 both）")
    ap.add_argument("--style", choices=("plain", "academic"), default="plain",
                    help="文风档案（写进报告概览，便于复核与归因）")
    args = ap.parse_args(argv)

    doc = read_json(args.doc)
    profile = read_json(args.profile)
    verdict = read_json(args.verdict) if args.verdict else None
    result = read_json(args.result) if args.result else None

    md = render(doc, profile, verdict, result, mode=args.mode, text_style=args.style)
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(md, encoding="utf-8")
    print("[report] 已写出 %s（%d 字，模式 %s）" % (out, len(md), args.mode))
    return 0


if __name__ == "__main__":
    sys.exit(main())
