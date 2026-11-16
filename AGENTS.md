# AGENTS.md

> 给在本仓库里干活的 coding agent，也给只做操作的运行型 agent。
> 人看的说明在 `README.md`；面向 skill 宿主的指令层在 `SKILL.md`。

## 这个仓库是什么

`paper-dual-reduce` —— 读一篇中文论文 `.docx`，诊断「机器味重 / 自己抄自己」的段落，
按段落改写，并把改写结果**无损回填**到原文档。零第三方依赖，只用 Python 标准库。

设计依据（原始 SOP，不在本仓库内）：
- 《论文双降 Skill 复现 SOP 与可行性报告》—— 执行口径：范围、阶段、脚本契约、验收、Go/No-Go
- 《双降联合优化算法方案》—— 六维特征、九算子、DJR 循环、三裁判
- 《笔灵论文 AI 率检测功能与降 AIGC/降重底层逻辑研究报告》—— 背景与边界

## 五条不可协商的约束

1. **模型不接触 docx。** 模型只读 `prompts.jsonl`、只写 `edits.jsonl`；解析/调度/回填/校验全在脚本里。
2. **所有脚本 JSON 进 JSON 出**，无共享状态，可单独重跑。新增能力也必须是这个形状。
3. **实体门禁是开关式的。** 改写后新增或删除了数字/年份/引用/书名/缩写 → 整段拒绝，不参与打分。
4. **属性 diff 非空则整篇回退**，并输出差异明细（哪个属性、哪一段）。
5. **锁定区不动**：标题、表格、**代码块/目录树**、公式、文本框、脚注、超链接、修订、参考文献、<40 字段。

## 常用命令

```bash
python run.py doctor                                  # 环境自检
python run.py diagnose --in paper.docx --outdir out    # 只诊断
python run.py rewrite  --in paper.docx --outdir out    # 出提示词
python run.py lint     --in paper.docx --outdir out --edits out/edits.jsonl
python run.py finish   --in paper.docx --outdir out --edits out/edits.jsonl
python tests/run_e2e.py                               # 验收：43 项断言
```

- 本机宿主控制台可能是 GBK。脚本已把 stdout 错误策略改成 `replace`；**测试子进程时**必须
  设 `PYTHONUTF8=1` 并按 UTF-8 解码，否则会在第一个汉字上崩。
- 改代码时**先加断言再实现**（TDD）。断言进 `tests/run_e2e.py`：它是唯一的验收入口。
- 不要引入第三方依赖。本机 pip 装不了包；零依赖也是「格式零漂移」的前提。

## 已验收的事实（2026-11-16）

- `tests/run_e2e.py`：43/43 通过（解析/诊断/排期/提示词/回填/复检/两个篡改反例/CLI/模式/lint/代码块/GBK）。
- 真实文档《论文双降 Skill 复现 SOP 与可行性报告.docx》（862 段 / 17 表 / 2 节 / 53 标题）：
  12 段真改写，`format_diff`、`entity_diff`、`locked_diff`、结构 diff 全空；
  Word 打开前后均 **28 页 / 843 段**。
- 代码块锁定：该文档有 95 段等宽字体段落，全部判为 `code` 不参与改写（否则会毁掉目录树）。
- 风险阈值：该文档无段落达到 0.45，退化为相对排序取前 N 段（报告标注 `below_threshold`）。

## 改代码时容易踩的三个坑

1. **祖先/子节点混淆**：`lib/ooxml.py` 直连 XML，改文本只替换 `<w:t>`，任何重建 `rPr` 的写法都会造成格式漂移。
2. **保护区跨 run**：保护区（数字/引用/书名）可能跨 run 边界；当前按 run 最大重叠归属，跨边界还原是 v0.3。
3. **Windows PowerShell 5.1**：读无 BOM 的 UTF-8 文本按 ANSI 处理，会把中文写花。
   做批量文本变换请用 Python，或在 PS 里显式 `-Encoding utf8`。

## 非目标

- 不做全库查重（无语料授权）；不输出绝对 AIGC 百分比或通过率承诺；不做 PDF/图片输入；
  不做代写、补数据、编造文献。
