---
name: paper-dual-reduce
description: Diagnose and rewrite Chinese academic papers in .docx to reduce AI-style statistical fingerprints and in-document repetition, keeping Word formatting byte-identical. Use when a user asks to lower AIGC detection risk, reduce repetition, or polish thesis writing while keeping tables, formulas and citations intact.
description_zh: 诊断并改写中文论文 .docx，降低 AI 写作的统计特征与篇内重复，同时保证 Word 格式零漂移。适用于降 AIGC 味、降重、润色论文表达并保留原格式/表格/公式/引用。
description_en: Diagnose and rewrite Chinese academic .docx files to reduce AI-style fingerprints and in-document repetition, with byte-level formatting fidelity.
version: "0.4.0"
---

# 论文双降（降 AIGC 味 / 降篇内重复）

读一篇中文论文 `.docx`，诊断哪些段落「机器味重」或「自己抄自己」，按段落给出改写建议，
并把改写结果**无损回填**到原文档。改写只动表达层，不动事实、数据、引用、格式。

**一条硬边界**：模型永远不接触 docx。它只读 `prompts.jsonl`、只写 `edits.jsonl`；
解析、调度、回填、回滚、格式校验全在脚本里。这条边界决定了这个 skill 能不能调试、能不能回滚。

## 什么时候用

- 「降 AIGC 率」「去 AI 味」「让这段读起来像人写的」
- 「降重」「降重复率」「这段和前面重复了」
- 润色论文表达，但明确要求**保留原格式**
- 手里有一篇 `.docx`，问「哪里像 AI 写的」

## 什么时候不用

- **不要承诺检测结果。** 输出是篇内相对风险分，不是任何平台的检测概率。
  用户问「能降到多少」「能过知网吗」，只能回答：无法预测，本工具只做表达层优化。
- 不接「代写」「补数据」「编文献」。改写必须逐句可追溯回原文。
- 不做全文查重（无外部语料授权），只做篇内重复。
- 不支持 PDF、扫描件、图片；`.doc` 请先另存为 `.docx`。
- 公式段、表格、文本框、脚注、超链接、修订痕迹、**代码块/目录树**一律不改——这是设计，不是缺陷。

## 30 秒上手（单入口）

```bash
python run.py doctor                                  # 环境自检，先跑这条
python run.py diagnose --in paper.docx --outdir out    # 只诊断 → out/report.md
python run.py spotcheck --in paper.docx --outdir out   # P1 门禁：20 段归因人工抽查表
python run.py rewrite  --in paper.docx --outdir out    # 出 out/prompts.jsonl，等模型改写
#   ← 模型读 prompts.jsonl，写 out/edits.jsonl（见下节契约）
python run.py lint     --in paper.docx --outdir out --edits out/edits.jsonl
python run.py finish   --in paper.docx --outdir out --edits out/edits.jsonl
```

默认文风是 **`plain`（平实五条）**；只要学术润色就加 `--style academic`（见「文风档案」）。

`finish` 一条命令完成：预检 → 回填 → 篇级复检 → 报告。
**预检不通过就不要回填**——`lint` 会逐条告诉你会被哪道闸门拒、怎么改。

也可以单步跑（每一步都是 JSON 进 JSON 出，可单独重跑）：

```bash
python scripts/inspect_docx.py --in paper.docx --out out/doc.json --summary
python scripts/diagnose.py     --doc out/doc.json --out out/profile.json
python scripts/plan.py         --profile out/profile.json --doc out/doc.json --out out/tasks.json
python scripts/build_prompts.py --tasks out/tasks.json --doc out/doc.json --out out/prompts.jsonl
python scripts/lint_edits.py   --doc out/doc.json --before paper.docx --edits out/edits.jsonl --out out/lint.json
python scripts/apply_edits.py  --doc out/doc.json --before paper.docx --edits out/edits.jsonl --out out/rewritten.docx
python scripts/verify.py       --before paper.docx --after out/rewritten.docx --out out/verdict.json
python scripts/render_report.py --doc out/doc.json --profile out/profile.json \
        --verdict out/rewritten.verdict.json --result out/rewritten.result.json \
        --out out/report.md --mode both
```

退出码：`lint` 0=全通过 1=有会被拒的编辑；`apply_edits` 0=正常 3=篇级校验失败已整篇回退；
`verify` 0=通过 1=不通过 2=输入有误；`run.py` 同义。

## 模型这一环：edits.jsonl 契约

一行一个编辑，UTF-8：

```json
{"pid": "p0350", "text": "改写后的整段文字", "op": "O1", "expected_sha1": "可选，防串段"}
```

规则（违反任何一条都会被闸门拒，且**只废这一段**，不连坐）：

| 规则 | 判据 | 拒绝原因 |
|---|---|---|
| 不改事实 | 数字/年份/百分比/引用/书名/缩写集合必须完全一致 | `entity_added` / `entity_lost` |
| 不新增主观表述 | 「我觉得／我认为／笔者认为」计数不得增加 | `subjective_added` |
| 不改长度量级 | 改写后字数 ∈ 原文 × [0.55, 1.40] | `length_out_of_range` |
| 不改格式 | 段落/字符属性签名必须不变 | `format_drift` |
| 不动锁定区 | 标题、表格、代码块、公式、脚注、参考文献、<40 字段 | `locked:<原因>` |
| 保留段内加粗小标题 | 保护区内外的加粗片段要原样出现在新文本里 | `format_anchor_lost` |

`lint` 对每种拒绝都给出下一步动作（删掉新加的数字、把字数压回区间、换一种不动小标题的改法…）。

## 硬约束（不可协商）

1. **模型不接触 docx。** 只读 `prompts.jsonl`、只写 `edits.jsonl`。
2. **实体门禁是开关式的。** 多出或少掉任何数字/年份/引用/书名/缩写，整段拒绝，不参与打分。
3. **格式漂移零容忍。** 段级不符只废该段；篇级不符整篇回退为原件。
4. **锁定区不动。** 标题、表格、公式、文本框、脚注、超链接、修订、参考文献、代码块、<40 字段。
5. **不新增事实。** 编造具体数字能让代理分显著下降——所以这条必须靠门禁，不能靠自觉。

## 文风档案（`--style`）

| 档案 | 语义 | 何时用 |
|---|---|---|
| `plain`（默认） | 平实五条：语序/同义/主被动互换 + 补主语 + 去主观 + 加助词 + 标点连贯 | 「降 AI 味」「像人写的」「平实一点」 |
| `academic` | 只做学术润色，不注入文风块 | 只要「润色通顺」，不能接受语体变随意 |

平实五条（写进每条 prompt，模型必须同时满足）：

1. **只换说法不换内容**：调语序、换同义词、变主谓宾结构；无主语句补主语（只能来自上下文，不得引入新主体）。
2. **平实文笔**：删掉「我觉得／我认为／笔者认为」这类主观表述与夸张修饰，平铺直叙。
3. **助词化**：适当加「的、了、到、过、会、有、能、把」，让句子不那么紧致；但不写成口水话。
4. **标点连贯**：少用句号、多用逗号分号；长句拆短；高频词换同义词；专业词在不改术语与缩写的前提下通俗化。
5. **结构松绑**：打乱句子结构、把字句与被动句互换、调语序；核心表意与论断强度不得改变。

底线：总字数与原文相差不超过 ±20%（硬闸仍是 0.55~1.40 倍，超了整段拒）。

配套算子：`plain` 下 O8 同义替换已启用（v0.2）。五条规则属于**文风层**，不是五个算子——
助词密度、标点密度是全文语体属性，逐段归因没有意义；算子只保留可归因的动作（见 `references/operators.md`）。

## 三个模式

| 模式 | 命令 | 产出 |
|---|---|---|
| 只诊断 | `run.py diagnose` | 概览 + 锁定区 + 高风险段 + 维度均值 + 篇内重复 |
| 只改写 | `run.py finish --mode rewrite` | 改写执行 + 三栏对照 + 复检结论 |
| 诊断加改写 | `run.py finish --mode both`（默认） | 全部 |

模式只影响报告内容，不改判据。默认建议**先只诊断**——把风险最低的路径设为默认。

## 关键设计

- **只排序，不报绝对分。** 风险分只用于同篇内「哪段更危险」，跨文档不可比。报告里不出现「AIGC 率 12%」。
- **格式保真靠锚点，不靠重建。** 单 run 直接替换文本；多 run 同格式合并；异格式时以「保护区 + 格式锚点」
  重建 run 边界，锚点找不到落点就整段拒绝（`format_anchor_lost`）。
- **变更说明可验证。** 三栏对照里的每句话都是数出来的：「句数 5→6、套语 3→1 处、字数 170→133」，
  不写「优化了表达」。
- **三层裁判。** 保真（有否决权）→ AIGC（只排序）→ 重复；仲裁顺序固定，不做加权和。

## 已知限制

- **零第三方依赖是硬约束**（本机装不了 pip 包）。六维特征是启发式统计量，不是模型推断；
  语义保真用字二元组重合 + 否定/量词守恒做代理，接不住深层语义反转。接句向量后可按
  `references/thresholds.md` 的替换路径升级。
- 加粗小标题类「无文字锚点」格式，改写若动到小标题本身，会以 `format_anchor_lost` 拒绝。
  宁可少改一段，不可抹平格式。
- 只处理 `word/document.xml`；页眉页脚、脚注正文不参与改写。
- 中文分词未接入，英文缩写与数字靠正则识别。
- 全文没有段落达到风险阈值 0.45 时，退化为按相对排序取前 N 段并标注 `below_threshold`。

## 自检

```bash
python tests/run_e2e.py     # 43 项断言：解析/诊断/排期/提示词/回填/复检/反例/CLI/模式/lint/代码块/GBK
```

真实文档验收（可选，需要你自己那篇）：

```bash
python run.py finish --in your.docx --outdir out/real --edits out/real/edits.jsonl
python -c "import json;v=json.load(open('out/real/rewritten.verdict.json',encoding='utf-8'));print(v['passed'],v['format_diff'],v['entity_diff'])"
```

## 文件地图

| 路径 | 作用 |
|---|---|
| `run.py` | 单入口 CLI（doctor / diagnose / rewrite / lint / finish / all） |
| `scripts/*.py` | 七步流水线各一脚本，JSON 进 JSON 出 |
| `scripts/lint_edits.py` | 回填前的门禁预判（复用 apply_edits 的同一套闸门） |
| `scripts/spotcheck.py` | P1 门禁的人工抽查表（归因核对 + 计分口径） |
| `lib/ooxml.py` | OOXML 读写（标准库 zipfile + ElementTree），run/段落格式签名 |
| `lib/anchor.py` | 保护区、锚点、run 重建与回滚 |
| `lib/features.py` | 六维特征、归因、保真信号 |
| `lib/fingerprint.py` | 13 字滚动哈希、倒排索引、篇内重复 |
| `lib/judges.py` | 三裁判与 minimax 仲裁 |
| `lib/diff.py` | 前后比对（格式/实体/锁定段/结构） |
| `lib/changenotes.py` | 可验证的变更量（三栏对照用） |
| `lib/cli.py` | UTF-8 / 退出码 / 失败出口 |
| `references/operators.md` | 九个算子的适用条件与风险 |
| `references/thresholds.md` | 全部阈值、取值理由、校准与升级路径 |
| `references/compliance.md` | 红线与交付话术 |

## 跨宿主使用

同一个仓库被多种 agent 使用，指令层各取所需：

| 宿主 | 读什么 |
|---|---|
| DSH | `SKILL.md`（装到 `~/.dsh/skills/paper-dual-reduce`） |
| Claude Code | `SKILL.md`（装到 `~/.claude/skills/paper-dual-reduce`） |
| Codex / WorkBuddy / Cursor | 仓库根 `AGENTS.md`（Cursor 另有 `.cursor/rules/paper-dual-reduce.mdc`） |
| 豆包等只能对话的宿主 | `PROMPT.md`（把论文粘进去，按里面的规则走；无文件系统时只出建议，不回写） |

一键装：`pwsh install/install.ps1` 或 `bash install/install.sh`。
