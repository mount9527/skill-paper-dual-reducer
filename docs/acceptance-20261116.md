# 验收记录 · 2026-11-16

环境：Windows 11 26100，Python 3.13.14，**零第三方依赖**（pip 在本机不可用，装不了包）。

## 一、样本夹具（每次都跑）

```
python tests/run_e2e.py     →  48 / 48 通过
```

**可移植性**：用 `git archive HEAD` 导出一份干净的提交树（等同一份 clone，不含 `out/`），
在其中直接跑 `python tests/run_e2e.py` → **48/48 通过**。仓库自包含，不依赖本机残留产物。

覆盖：解析（段落/锁定/锚点/保护区）· 诊断（归因、13 字指纹、篇内重复率）· 排期（预算、不碰锁定段）·
提示词（4 档候选、红线在系统提示里）· 回填（2 条接受、7 条按预期原因拒绝）· 复检（diff 全空）·
两个篡改反例（格式被偷偷加粗、引用被删）· 单入口 CLI 与三种模式 · lint 预判 · 代码块锁定 · GBK 控制台 ·
文风块注入与 `--style` 切换 · O8 同义替换启用。

## 二、真实文档（862 段）

文档：《论文双降 Skill 复现 SOP 与可行性报告.docx》，28 页、862 段、17 表、2 节、53 个标题，带目录域。

```
python run.py rewrite --in <sop.docx> --outdir out/sop --max-tasks 15     # 出 15 个任务 / 60 条提示词
python run.py lint    --in <sop.docx> --outdir out/sop --edits out/sop/edits.jsonl
python run.py finish  --in <sop.docx> --outdir out/sop --edits out/sop/edits.jsonl
```

| 项目 | 结果 |
|---|---|
| 改写提交 / 通过预检 / 实际回填 | 12 / 12 / 12 |
| 结构 | 862 段、17 表、2 节，前后完全一致，`structure.diff = []` |
| `format_diff` | `[]` |
| `entity_diff` | `added: []`，`removed: []` |
| `locked_diff` | `[]` |
| 字数 | 16145 → 16148（+3） |
| **Word 打开页数** | **28 → 28** |
| **Word 段落数** | **843 → 843** |
| Word 字数 / 字符 | 11925 → 11931 ／ 14967 → 14971 |

结论：12 段真的被改写了，而格式、实体、锁定区、结构、分页全部零漂移。

## 三、v0.3 平实文风档（`--style plain`）

同一篇文档、同一批 12 段，按平实五条（语序/同义/主被动互换 + 补主语 + 去主观 + 助词化 +
标点连贯）重写一遍：

| 项目 | 结果 |
|---|---|
| 改写提交 / 通过预检 / 实际回填 | 12 / 12 / 12 |
| `format_diff` / `entity_diff` / `locked_diff` | `[]` / 空 / `[]` |
| 结构 | 862 段、17 表、2 节，前后一致 |
| 字数 | 16145 → 16259（+0.7%，约束是 ±20%） |
| **Word 页数** | **28 → 28** |
| **Word 段落数** | **843 → 843** |
| Word 字数 / 字符 | 11925 → 12042 ／ 14967 → 15084 |

报告里的变更说明是可数的，例如：

```
p0446 句数 1→2；平均句长 60→37 字；连接词 2→1 处；字数 60→74
p0345 平均句长 27→33 字；抽象词 0→2 字；字数 54→66
p0253 字数 59→61
```

结论：加助词、拆句、少句号这些「变长」的动作没有把字数顶出闸门，也没有破坏格式与实体。

## 四、这一步发现的两个真问题

1. **代码块被当成散文**：该文档有 95 段等宽字体段落（目录树、代码清单）。v0.1 会把它们选进改写池，
   改写即毁排版。现在按「等宽字体占比 ≥ 60% 或以制表符号开头」判为 `code` 锁定，
   样本夹具里加了一行 Consolas 代码作为回归断言。
2. **GBK 控制台崩溃**：测试脚本按 UTF-8 解码子进程输出，而子进程在 cp936 控制台下按 GBK 输出，
   第一个汉字就 `UnicodeDecodeError`。现在子进程强制 `PYTHONUTF8=1`，父进程 `errors="replace"`。

## 五、还没验的（诚实清单）

- **SOP P1 门禁（20 段归因准确率 ≥ 80%）未做**：需要人工抽查，脚本无法自证。抽查表已生成，
  填完才能说这条过。
- **SOP P2 门禁（20 段 Sem ≥ 0.85 比例 ≥ 85%）未做**：语义保真目前是代理指标（字二元组 + 否定/量词守恒），
  不是句向量，指标口径与 SOP 不同。
- **只验了一篇真实文档**。表格、公式、脚注、多级列表的覆盖来自该文档的实际构成，
  不是刻意构造的极端样本。
- **代理检测（F7）没有实现**：风险分只做篇内排序，不做绝对值，也不对接任何官方引擎。

## 五、复现步骤

```bash
python run.py doctor
python run.py diagnose --in <your.docx> --outdir out/real
python run.py rewrite  --in <your.docx> --outdir out/real
#   模型读 out/real/prompts.jsonl，写 out/real/edits.jsonl
python run.py lint   --in <your.docx> --outdir out/real --edits out/real/edits.jsonl
python run.py finish --in <your.docx> --outdir out/real --edits out/real/edits.jsonl
```

判据：`out/real/rewritten.verdict.json` 里 `passed=true`，`format_diff`/`entity_diff`/`locked_diff` 全空。
