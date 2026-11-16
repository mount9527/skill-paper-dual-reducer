#!/usr/bin/env python3
"""build_prompts.py —— tasks.json + doc.json → prompts.jsonl

每个任务出 4 个候选请求（2 松 / 1 中 / 1 紧）。分档而不是调 temperature：
档位对应明确的改动幅度，出问题时能定位到「是宽档编了内容」还是「是紧档没改到位」。

模型只被要求输出一行 JSON：{"pid": "...", "text": "..."}。
它看不到 docx，也不知道 run 的存在；格式保真由 apply_edits 负责。

用法
----
    python scripts/build_prompts.py --tasks out/tasks.json --doc out/doc.json --out out/prompts.jsonl
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

TIERS = ["loose", "loose", "normal", "tight"]

TIER_SPEC = {
    "loose": "可改动约 70% 的句子，允许拆句与合句；保留全部论点与论据顺序。",
    "normal": "可改动约 50% 的句子，优先处理最模板化的表达；句子数量变化不超过 ±1。",
    "tight": "只改 1~2 处最典型的机器味表达，其余原样保留；句子数量不变。",
}

OP_SPEC = {
    "O1": ("句长重分布",
           "打破句长均齐：把连续的中长句拆出一到两个短句（8~15 字），再保留一处长句（40 字以上）形成起伏。"
           "不要为了变短而删信息，也不要把两句合成一句。"),
    "O2": ("连接词抑制",
           "删掉至少一半的显式逻辑连接词（首先/其次/此外/因此/综上所述/值得注意的是…），"
           "改用语义衔接或语序自然过渡。删掉连接词后句子必须仍然读得通。"),
    "O3": ("语序重排",
           "调整从句与状语的顺序：把结论前置或把条件后置，改变句子的信息推进方向，不改变命题内容。"),
    "O4": ("句法重构",
           "拆解模板化句式（随着…的发展、在…方面、对…进行…、具有…意义）：改成具体主谓结构，"
           "去掉空转的名词化表达；长句拆短，被动改主动。"),
    "O9": ("引用转述与合并",
           "把与其他段落重复的表述合并为一处，其余位置改为回指或概括；保留原有引用标记不动。"),
}

SYSTEM = """你是中文学术论文的润色编辑，只做表达层的改写，不做内容创作。

硬性规则（违反任意一条，本次输出作废）：
1. 不得新增任何事实、数据、年份、百分比、人名、机构、文献。原文没有的数字一个都不能出现。
2. 不得删除或改动原文中的引用标记（如 [1]、[2-4]、（张三，2020））、书名（《…》）、
   引号内术语、英文缩写（如 BERT、LSTM）。它们必须原样、按原顺序出现在改写结果里。
3. 不得改变论断方向：原文说「有显著影响」就不能改成「影响有限」；不得增删否定词与程度副词。
4. 只输出一行 JSON，不要解释、不要 Markdown 代码块、不要前后缀：
   {"pid": "段落编号", "text": "改写后的整段文字"}
5. text 必须是一整段可直接替换原文的文字，不含换行符。"""


def build_user(task: dict, text: str) -> str:
    op_name, op_rule = OP_SPEC.get(task["op"], OP_SPEC["O4"])
    c = task["constraints"]
    pid = task["pid"]
    spans = "、".join(c["keep_spans"]) if c["keep_spans"] else "（本段无保护区）"
    return f"""【段落编号】{pid}
【本轮目标】{op_name}（{task["op"]}）
【改动幅度】{TIER_SPEC.get(task.get("_tier", "normal"))}

【原文】
{text}

【约束】
- 字数：{c["min_chars"]}~{c["max_chars"]} 字（原文 {len(text.strip())} 字）
- 句数：原文 {c["sentences"]} 句
- 必须原样保留的片段：{spans}
- 禁止：新增数字/年份/百分比；新增或删除引用；改写书名、术语、英文缩写；改变论断方向

【改写要求】
{op_rule}

【输出】仅一行 JSON：{{"pid": "{pid}", "text": "..."}}"""


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="把任务清单渲染成 prompts.jsonl")
    ap.add_argument("--tasks", required=True)
    ap.add_argument("--doc", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--limit", type=int, default=0, help="只渲染前 N 个任务（0=全部）")
    ap.add_argument("--k", type=int, default=4, help="每个任务的候选数（默认 4）")
    args = ap.parse_args(argv)

    tasks = json.loads(Path(args.tasks).read_text(encoding="utf-8"))
    doc = json.loads(Path(args.doc).read_text(encoding="utf-8"))
    rows = {r["pid"]: r for r in doc["paragraphs"]}

    selected = tasks["tasks"][: args.limit] if args.limit else tasks["tasks"]
    n = 0
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    with out.open("w", encoding="utf-8") as fh:
        for task in selected:
            row = rows.get(task["pid"])
            if row is None:
                continue
            for k in range(args.k):
                tier = TIERS[k % len(TIERS)]
                t = {**task, "_tier": tier}
                rec = {
                    "prompt_id": "%s-c%d" % (task["task_id"], k + 1),
                    "task_id": task["task_id"],
                    "pid": task["pid"],
                    "op": task["op"],
                    "tier": tier,
                    "system": SYSTEM,
                    "user": build_user(t, row["text"]),
                    "expect": {"schema": {"pid": "str", "text": "str"},
                               "max_chars": task["constraints"]["max_chars"]},
                }
                fh.write(json.dumps(rec, ensure_ascii=False) + "\n")
                n += 1

    print("[prompts] 任务 %d ｜ 请求 %d ｜ → %s" % (len(selected), n, out))
    return 0


if __name__ == "__main__":
    sys.exit(main())
