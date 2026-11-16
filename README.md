# paper-dual-reduce

中文论文 `.docx` 的**诊断 + 句法改写 + 无损回填**工具。零第三方依赖，只跑 Python 标准库。

给论文降 AI 味、降篇内重复，同时保证 Word 格式**一字节不漂**：
表格、公式、脚注、目录域、引用标记、加粗小标题全部原样保留。

```
诊断 → 排期 → 提示词 →（模型改写）→ 预检 → 回填 → 复检 → 报告
 脚本    脚本    脚本      模型       脚本    脚本    脚本    脚本
```

模型只在「改写」这一格出现，它**看不到 docx**，只读 `prompts.jsonl`、只写 `edits.jsonl`。
其余每一步都是确定性脚本。这个切分决定了它能不能调试、能不能回滚、能不能被验收。

## 快速开始

```bash
git clone https://github.com/mount9527/skill-paper-dual-reducer.git
cd skill-paper-dual-reducer
python run.py doctor                                   # 环境自检
python run.py diagnose --in paper.docx --outdir out     # 只诊断：out/report.md
```

要真改：

```bash
python run.py rewrite --in paper.docx --outdir out      # 生成 out/prompts.jsonl
#   ← 让模型（或你自己）按 out/prompts.jsonl 写出 out/edits.jsonl
python run.py lint   --in paper.docx --outdir out --edits out/edits.jsonl
python run.py finish --in paper.docx --outdir out --edits out/edits.jsonl
```

`edits.jsonl` 一行一条：

```json
{"pid": "p0350", "text": "改写后的整段文字", "op": "O1"}
```

## 文风档案：`plain`（默认） / `academic`

`plain` 把「平实、像人写的」落成五条可执行约束，注入每一条提示词：

1. 只换说法不换内容：调语序、换同义词、变主谓宾结构；无主语句补主语（依据上下文，不引入新主体）。
2. 平实文笔：删掉「我觉得／我认为」这类主观表述与夸张修饰。
3. 助词化：适当加「的、了、到、过、会、有、能、把」，句子不那么紧致；但不写成口水话。
4. 标点连贯：少用句号、多用逗号分号；长句拆短；高频词换同义词；专业词通俗化（术语与缩写不动）。
5. 结构松绑：打乱句子结构、把字句与被动句互换、调语序；**核心表意与论断强度不得改变**。

总字数控制在原文 ±20% 以内。只要学术润色、不要语体变随意：`--style academic`。

配套地把 O8 同义替换算子从「关闭」改为「启用」（术语与缩写由实体门禁兜底）。

## 它凭什么敢说「格式不漂」

| 门禁 | 判据 | 失败后果 |
|---|---|---|
| 实体门禁 | 数字/年份/引用/书名/缩写集合必须完全一致 | 该段拒绝 |
| 主观表述 | 「我觉得／我认为」计数不得增加 | 该段拒绝 |
| 长度闸 | 改写后字数 ∈ 原文 × [0.55, 1.40] | 该段拒绝 |
| 格式签名 | 段落属性 + 字符属性集合必须不变 | 该段回滚 |
| 篇级复检 | 全部段落签名字节比对 | 整篇回退为原件 |
| 锁定区 | 标题/表格/代码块/公式/脚注/参考文献 | 一律不改 |

失败是**显式**的：`lint` 告诉你这条会被哪道门拒、怎么改；`finish` 拒绝时给出逐段原因，
篇级失败直接整篇回退，不会给你一个「看起来改好了但打不开」的文件。

## 实测证据

- `python tests/run_e2e.py` → **50/50** 断言通过（含两个篡改反例：格式被偷偷加粗、引用被删；
  文风块注入、`--style` 切换、O8 启用、主观表述门禁、抽查表生成）。
- 真实文档（862 段 / 17 表 / 2 节 / 53 标题 / 含目录域）：
  **12 段真改写**，`format_diff`、`entity_diff`、`locked_diff`、结构 diff **全空**；
  Word 打开前后均 **28 页 / 843 段**。
- 代码块识别：同一文档 95 段等宽字体段落全部判为 `code`，不参与改写。

## 它做不到什么（请先读完再决定信几分）

- **不预测任何平台的检测结果**。报告里的「风险分」是篇内相对指标，只用于排序，
  不是 AIGC 概率，跨文档不可比。任何「降到 12%」「包过知网」的承诺都不要信——包括这个工具说的。
- **不做全库查重**。只做篇内重复（同一文档自己抄自己），不比对知网/维普。
- **不新增事实**。它只会换说法；编数字、补数据、编文献这类请求会被门禁拒绝，也应当被拒绝。
- 语义保真是**代理指标**（字二元组 + 否定/量词守恒），不是句向量。深层语义反转可能漏检。
- 只处理正文 `word/document.xml`；页眉页脚里的文字不参与改写。

## 安装到各类 agent

| 宿主 | 指令文件 | 安装 |
|---|---|---|
| DeepSeek Harness | `SKILL.md` | `pwsh install/install.ps1`（或 `bash install/install.sh`） |
| Claude Code | `SKILL.md` | 同上，装到 `~/.claude/skills/paper-dual-reduce` |
| Codex / WorkBuddy | `AGENTS.md` | 仓库根已在位；把仓库路径加入工作区即可 |
| Cursor | `.cursor/rules/paper-dual-reduce.mdc` | 复制到目标项目 `.cursor/rules/` |
| 豆包等对话型 | `PROMPT.md` | 把文件内容作为系统提示 / 首条消息贴进去 |

装好后，任何能执行 shell 的 agent 都可以只用一条命令跑完：
`python run.py finish --in paper.docx --outdir out --edits out/edits.jsonl`。

## 目录结构

```
run.py                 单入口 CLI
scripts/               七步流水线 + lint 预检
lib/                   OOXML 读写、锚点、六维特征、指纹、裁判、差异、变更量
references/            算子规格、阈值与校准、合规红线
tests/                 run_e2e.py（43 项断言）+ 夹具生成器
```

## 合规边界

写作辅助工具，不是检测规避工具。对外表述请用「改善句式单一、降低表述冗余、减少与公开文献的字面重合」，
不要用「降低 AI 率」「通过检测」。文档不出本机（除你自己选择云端改写外），处理完不留存，不纳入任何训练集。
改写结果必须由作者复核——报告里的每一项都是给人复核用的。

## License

MIT
