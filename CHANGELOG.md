# CHANGELOG

## v0.3.0（2026-11-16）

把「降 AI 味」从「句式技巧」扩到「语体」：新增文风档案与同义替换算子。

### 新增

- **文风档案 `--style plain|academic`**（默认 `plain`）。`plain` 把五条语体规则注入每条提示词：
  语序/同义/主被动互换 + 补主语 · 去主观表述 · 助词化 · 标点连贯（少句号多逗号）· 结构松绑。
  总字数约束 ±20%，硬闸仍是 0.55~1.40 倍。
- **O8 同义替换启用**（v0.1 曾关闭）。只换高频抽象词，术语/缩写/引用由实体门禁兜底，
  且明令「不得把强论断换弱」。
- `tasks.json` 记录 `text_style`，`report.md` 概览显示文风档案，便于复核与归因。
- `--style academic` 不注入文风块，等价于旧行为（只做学术润色）。

### 设计取舍

五条规则放在**文风层**而不是拆成五个算子：助词密度、标点密度是全文语体属性，
逐段归因没有意义；算子只保留可归因的动作。只有同义替换有明确适用条件与风险，单独记 O8。

### 验收

- `tests/run_e2e.py`：43 → **48** 项断言全绿（新增文风块注入、`--style` 切换、O8 启用三组）。
- 真实文档（862 段 / 17 表 / 2 节）平实档重跑：12 段真改写，`format_diff`/`entity_diff`/`locked_diff`
  与结构 diff 全空，字数 16145→16259（+0.7%），Word 前后均 **28 页 / 843 段**。

## v0.2.0（2026-11-16）

从「能跑通」到「能被别的 agent 用、且在真实文档上验过」。

### 新增

- `run.py` 单入口 CLI：`doctor / diagnose / rewrite / lint / finish / all`，
  带 `--mode diagnose|rewrite|both` 模式切换（对应 SOP §5.6）。
- `scripts/lint_edits.py`：回填**之前**用同一套闸门 dry-run，逐条告诉模型会被谁拒、怎么改。
  它不是第二套规则——直接复用 `apply_edits.apply(dry_run=True)`，避免两套规则分叉。
- `lib/changenotes.py` + 报告「七、改写对照」：原文 / 改文 / **可验证的变更说明**
  （句数 5→6、套语 3→1 处、字数 170→133），不写「优化了表达」。
- `lib/cli.py`：stdout 错误策略降级为 `replace`，GBK/cp1252 控制台不再抛 UnicodeEncodeError。
- 跨宿主适配层：`AGENTS.md`（Codex/WorkBuddy）、`.cursor/rules/*.mdc`、`PROMPT.md`（对话型宿主）、
  `install/install.ps1`、`install/install.sh`。
- `apply_edits` 结果里记录改后文本（三栏对照的唯一数据源，不依赖 docx 回读）。

### 修复

- **代码块被当成散文改写**：真实文档里 95 段等宽字体段落（目录树/代码清单）此前可被选为改写任务。
  现在按「等宽字体占比 ≥ 60% 或以制表符号开头」判为 `code` 并锁定。这条是拿 862 段真实文档跑出来的，
  样本夹具里加了一行 Consolas 代码作为回归断言。
- **GBK 控制台崩溃**：`run_e2e.py` 的子进程按 cp936 输出、父进程按 UTF-8 解码，
  在第一个汉字上抛 `UnicodeDecodeError`。现在子进程强制 `PYTHONUTF8=1`，父进程 `errors="replace"`。
- 测试脏目录：`out/e2e/run` 残留会让「diagnose 不产改稿」假通过，现在跑前清空。

### 验收

- `tests/run_e2e.py`：32 → **43** 项断言全绿（新增 CLI/模式/lint/代码块/GBK 五组）。
- 真实文档（862 段 / 17 表 / 2 节）：12 段真改写，`format_diff`/`entity_diff`/`locked_diff`/结构 diff 全空，
  Word 打开前后均 **28 页 / 843 段**。

## v0.1.0（2026-11-16）

- 七脚本流水线（解析 → 诊断 → 排期 → 提示词 → 回填 → 复检 → 报告），零第三方依赖。
- 六维特征、13 字指纹、三裁判、保护区锚点与格式签名、篇级回退。
- 32 项端到端断言，含两个篡改反例。
