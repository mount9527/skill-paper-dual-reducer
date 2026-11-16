#!/usr/bin/env python3
"""run_e2e.py —— 一键验收：把 SOP 里的承诺逐条变成断言

跑法
----
    python tests/run_e2e.py

它会用真实的 CLI（subprocess，而非直接调库）跑完整条流水线：
inspect → diagnose → plan → build_prompts → apply_edits → verify → render_report

断言覆盖：
1. 解析结果与样本结构一致（段落数、锁定数、可改写数）
2. 每段都有锚点、保护区互不重叠
3. 诊断能区分「AI 味重」与「数字密集」，能发现篇内重复
4. 排期不越预算、不碰锁定段
5. 提示词每任务 4 档、含段落编号与原文
6. 回填的 2 条被接受、7 条被各自对应的闸门拒绝（拒绝原因逐一核对）
7. 篇级校验通过；篡改样本能被抓出（格式 1 处、实体 1 处）
8. 逐段核对：run 格式签名集合、段落格式签名、锁定段文本全部不变
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import zipfile
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
for p in (str(ROOT), str(ROOT / "scripts")):
    if p not in sys.path:
        sys.path.insert(0, p)

from lib.diff import paragraph_signatures
from lib.ooxml import Docx

OUT = ROOT / "out" / "e2e"
FIX = ROOT / "tests" / "fixtures"

#: 子进程一律按 UTF-8 出文本。宿主控制台若是 GBK，不强制时子进程按 cp936 输出，
#: 父进程按 UTF-8 解码会在第一个汉字上抛 UnicodeDecodeError。
CHILD_ENV = {**os.environ, "PYTHONUTF8": "1", "PYTHONIOENCODING": "utf-8"}

results: list[tuple[str, bool, str]] = []


def check(name: str, ok: bool, detail: str = "") -> bool:
    results.append((name, ok, detail))
    print("  %s %s%s" % ("✓" if ok else "✗", name, ("  " + detail) if detail and not ok else ""))
    return ok


def run(args: list[str], expect_rc: int | None = 0) -> subprocess.CompletedProcess:
    cp = subprocess.run([sys.executable] + args, cwd=str(ROOT), env=CHILD_ENV,
                        capture_output=True, text=True, encoding="utf-8", errors="replace")
    if expect_rc is not None and cp.returncode != expect_rc:
        print(cp.stdout)
        print(cp.stderr, file=sys.stderr)
        raise SystemExit("命令失败：%s（rc=%d，期望 %d）" % (" ".join(args), cp.returncode, expect_rc))
    return cp


def main() -> int:
    OUT.mkdir(parents=True, exist_ok=True)
    doc_json = OUT / "doc.json"
    profile_json = OUT / "profile.json"
    tasks_json = OUT / "tasks.json"
    prompts = OUT / "prompts.jsonl"
    rewritten = OUT / "rewritten.docx"

    print("[1/11] 解析")
    run(["tests/make_fixture.py"])
    run(["scripts/inspect_docx.py", "--in", str(FIX / "sample.docx"),
         "--out", str(doc_json), "--summary"])
    doc = json.loads(doc_json.read_text(encoding="utf-8"))
    check("段落数 = 28", doc["stats"]["paragraphs"] == 28, str(doc["stats"]["paragraphs"]))
    check("锁定 20 / 可改写 8",
          doc["stats"]["locked"] == 20 and doc["stats"]["rewritable"] == 8,
          "%d/%d" % (doc["stats"]["locked"], doc["stats"]["rewritable"]))
    check("锁定原因齐全（table/heading/reference/too_short/code）",
          set(doc["lock_reasons"]) == {"table", "heading", "reference", "too_short", "code"},
          str(doc["lock_reasons"]))

    bad_anchor = [r["pid"] for r in doc["paragraphs"] if len(r["anchor"]["sha1"]) != 40]
    check("每段都有 40 位锚点 sha1", not bad_anchor, str(bad_anchor[:3]))

    overlap = []
    for r in doc["paragraphs"]:
        last = -1
        for s in r["spans"]:
            if s["start"] < last:
                overlap.append(r["pid"])
                break
            last = s["end"]
    check("保护区互不重叠", not overlap, str(overlap[:3]))

    print("[2/11] 诊断")
    run(["scripts/diagnose.py", "--doc", str(doc_json), "--out", str(profile_json), "--top", "8"])
    prof = json.loads(profile_json.read_text(encoding="utf-8"))
    by = {p["pid"]: p for p in prof["paragraphs"]}
    abstract = next(p for p in prof["paragraphs"] if p["pid"] == "p0004")
    design = next(p for p in prof["paragraphs"] if p["pid"] == "p0015")
    check("摘要（连接词密集）风险高于研究设计（数字密集）",
          abstract["risk"] > design["risk"] + 0.15,
          "%.2f vs %.2f" % (abstract["risk"], design["risk"]))
    check("摘要主因是连接词过密",
          abstract["causes"][0]["code"] == "A2", str(abstract["causes"][:2]))
    pair = [p for p in prof["paragraphs"] if p["dup_ratio"] > 0.9]
    check("13 字指纹抓到篇内重复段（≥2 段）", len(pair) >= 2, str([p["pid"] for p in pair]))
    check("篇内重复率 > 15%", prof["duplicates"]["stats"]["repeat_rate"] > 0.15,
          "%.3f" % prof["duplicates"]["stats"]["repeat_rate"])

    print("[3/11] 排期")
    run(["scripts/plan.py", "--profile", str(profile_json), "--doc", str(doc_json),
         "--out", str(tasks_json), "--max-tasks", "6"])
    tasks = json.loads(tasks_json.read_text(encoding="utf-8"))
    locked_pids = {r["pid"] for r in doc["paragraphs"] if r["locked"]}
    task_pids = [t["pid"] for t in tasks["tasks"]]
    check("有任务产出", len(task_pids) > 0, str(task_pids))
    check("不越预算", tasks["budget"]["used_chars"] <= tasks["budget"]["budget_chars"],
          "%d/%d" % (tasks["budget"]["used_chars"], tasks["budget"]["budget_chars"]))
    check("不碰锁定段", not (set(task_pids) & locked_pids), str(set(task_pids) & locked_pids))
    check("高风险段进入任务池", "p0004" in task_pids, str(task_pids))

    print("[4/11] 提示词")
    run(["scripts/build_prompts.py", "--tasks", str(tasks_json), "--doc", str(doc_json),
         "--out", str(prompts)])
    lines = [json.loads(x) for x in prompts.read_text(encoding="utf-8").splitlines() if x.strip()]
    check("每任务 4 档候选", len(lines) == 4 * len(task_pids), "%d 行" % len(lines))
    check("档位分布 2:1:1", Counter(x["tier"] for x in lines[:4]) ==
          Counter({"loose": 2, "normal": 1, "tight": 1}), str([x["tier"] for x in lines[:4]]))
    check("提示词含段落编号与原文",
          all(x["pid"] in x["user"] and len(x["user"]) > 200 for x in lines))
    check("系统提示含红线（不得新增事实）", "不得新增任何事实" in lines[0]["system"])

    print("[5/11] 回填")
    cp = run(["scripts/apply_edits.py", "--doc", str(doc_json), "--before",
              str(FIX / "sample.docx"), "--edits", str(FIX / "edits.jsonl"),
              "--out", str(rewritten)], expect_rc=0)
    res = json.loads(Path(str(rewritten).replace(".docx", ".result.json")).read_text(encoding="utf-8"))
    check("接受 2 条", res["counts"]["applied"] == 2, str(res["counts"]))
    got = Counter(i["reason"].split(":")[0] for i in res["items"] if i["status"] == "rejected")
    want = Counter(json.loads(x)["expect"] for x in
                   (FIX / "edits.jsonl").read_text(encoding="utf-8").splitlines()
                   if x.strip() and json.loads(x)["expect"] != "applied")
    check("拒绝原因与预期完全一致", got == want, "实际 %s / 预期 %s" % (dict(got), dict(want)))
    check("执行状态 ok（未整篇回退）", res["status"] == "ok", res["status"])

    print("[6/11] 复检")
    vpath = Path(str(rewritten).replace(".docx", ".verdict.json"))
    verdict = json.loads(vpath.read_text(encoding="utf-8"))
    check("篇级校验通过", verdict["passed"], json.dumps(verdict["summary"], ensure_ascii=False))
    check("格式 diff 为空", verdict["format_diff"] == [], str(verdict["format_diff"][:1]))
    check("实体 diff 为空", not verdict["entity_diff"]["added"] and not verdict["entity_diff"]["removed"])
    check("锁定段未变", verdict["locked_diff"] == [])

    b, a = Docx(str(FIX / "sample.docx")), Docx(str(rewritten))
    bp, ap = b.paragraphs(), a.paragraphs()
    drift = [x.pid for x, y in zip(bp, ap) if paragraph_signatures(x) != paragraph_signatures(y)]
    check("逐段格式签名完全一致", not drift, str(drift))
    locked_changed = [x.pid for x, y in zip(bp, ap) if x.lock_reason() and x.text != y.text]
    check("锁定段文本逐字不变", not locked_changed, str(locked_changed))

    print("[7/11] 反例：篡改能否被抓出")
    cp = run(["scripts/verify.py", "--before", str(FIX / "sample.docx"), "--after",
              str(FIX / "tampered_format.docx"), "--out", str(OUT / "v_format.json")],
             expect_rc=1)
    v1 = json.loads((OUT / "v_format.json").read_text(encoding="utf-8"))
    check("抓出格式篡改（文本未变、格式被改）",
          not v1["passed"] and v1["summary"]["format_issues"] >= 1 and v1["text"]["changed_paragraphs"] == 0,
          str(v1["summary"]))
    run(["scripts/verify.py", "--before", str(FIX / "sample.docx"), "--after",
         str(FIX / "tampered_entity.docx"), "--out", str(OUT / "v_entity.json")], expect_rc=1)
    v2 = json.loads((OUT / "v_entity.json").read_text(encoding="utf-8"))
    check("抓出实体丢失（[1] 被删）",
          not v2["passed"] and v2["summary"]["entity_removed"] >= 1,
          str(v2["entity_diff"]["removed"]))

    print("[8/11] 产物与报告")
    with zipfile.ZipFile(rewritten) as z:
        names = z.namelist()
        ok_zip = "word/document.xml" in names and z.testzip() is None
        head = z.read("word/document.xml")[:200].decode("utf-8", "ignore")
    check("docx 包结构完整", ok_zip, str(names[:4]))
    check("命名空间与 standalone 保留",
          "standalone=\"yes\"" in head and "xmlns:w=" in head, head[:80])

    run(["scripts/render_report.py", "--doc", str(doc_json), "--profile", str(profile_json),
         "--verdict", str(vpath), "--result", str(Path(str(rewritten).replace(".docx", ".result.json"))),
         "--out", str(OUT / "report.md")])
    md = (OUT / "report.md").read_text(encoding="utf-8")
    check("报告含复检结论且标注通过", "## 八、复检结论" in md and "| 结论 | 通过 |" in md)
    check("报告不出现「AIGC 率」式数字", "AIGC 率" not in md or "不是 AIGC 概率" in md)

    print("[9/11] 单入口 CLI 与模式开关")
    rdir = OUT / "run"
    shutil.rmtree(rdir, ignore_errors=True)   # 干净目录，「不产改稿」才是真断言
    run(["run.py", "doctor"])
    run(["run.py", "diagnose", "--in", str(FIX / "sample.docx"), "--outdir", str(rdir)])
    md_diag = (rdir / "report.md").read_text(encoding="utf-8")
    check("diagnose 模式出诊断章节", "## 三、高风险段落" in md_diag)
    check("diagnose 模式不出改写对照", "改写对照" not in md_diag)
    check("diagnose 模式不产改稿", not (rdir / "rewritten.docx").exists())
    lint_stub = rdir / "lint.json"
    check("diagnose 模式不产 lint", not lint_stub.exists())

    print("[10/11] lint 预检 + 三栏对照")
    run(["run.py", "rewrite", "--in", str(FIX / "sample.docx"), "--outdir", str(rdir),
         "--max-tasks", "6"])
    check("rewrite 模式出 prompts.jsonl", (rdir / "prompts.jsonl").exists())

    # 文风档案：plain 是默认，academic 可关掉文风块
    tk = json.loads((rdir / "tasks.json").read_text(encoding="utf-8"))
    check("默认文风为 plain", tk.get("text_style") == "plain", str(tk.get("text_style")))
    p_plain = [json.loads(x) for x in (rdir / "prompts.jsonl").read_text(encoding="utf-8").splitlines() if x.strip()]
    check("提示词含五条文风要求", "【文风要求" in p_plain[0]["user"])
    need = ("助词", "把字句", "句号", "我觉得", "同义词")
    check("文风要求覆盖五条（助词/把字句/标点/主观/同义）",
          all(k in p_plain[0]["user"] for k in need),
          str([k for k in need if k not in p_plain[0]["user"]]))
    run(["run.py", "rewrite", "--in", str(FIX / "sample.docx"), "--outdir", str(rdir / "acad"),
         "--max-tasks", "3", "--style", "academic"])
    p_acad = [json.loads(x) for x in (rdir / "acad" / "prompts.jsonl").read_text(encoding="utf-8").splitlines() if x.strip()]
    check("academic 档不带文风块", "【文风要求" not in p_acad[0]["user"])

    from lib.features import attribute as _attr
    got = _attr({"a1": 0.0, "a2": 0.0, "a3": 0.0, "a4": 0.0, "a5": 0.9, "a6": 0.0}, 0.0)
    check("同义替换算子（O8）已启用", any(c["op"] == "O8" and c["available"] for c in got),
          str([(c["op"], c["available"]) for c in got]))

    run(["run.py", "lint", "--in", str(FIX / "sample.docx"), "--outdir", str(rdir),
         "--edits", str(FIX / "edits.jsonl")], expect_rc=1)
    lint = json.loads(lint_stub.read_text(encoding="utf-8"))
    check("lint 预判放行 2 条", lint["counts"]["would_apply"] == 2, str(lint["counts"]))
    hinted = [i for i in lint["items"] if i["status"] == "would_reject"]
    check("每条被拒都给出原因与修法", all(i.get("hint") for i in hinted),
          str([i.get("pid") for i in hinted if not i.get("hint")]))
    run(["run.py", "finish", "--in", str(FIX / "sample.docx"), "--outdir", str(rdir),
         "--edits", str(FIX / "edits.jsonl")])
    md_full = (rdir / "report.md").read_text(encoding="utf-8")
    check("报告含三栏对照表头", "| 段落 | 原文 | 改文 | 变更说明 |" in md_full)
    check("变更说明是可验证量（句数/套语）", "句数" in md_full and "套语" in md_full)
    v_run = json.loads((rdir / "rewritten.verdict.json").read_text(encoding="utf-8"))
    check("finish 后篇级校验通过", v_run["passed"])

    print("[11/11] GBK 控制台不崩")
    gbk_env = {**os.environ, "PYTHONIOENCODING": "gbk"}
    gbk_env.pop("PYTHONUTF8", None)
    cp = subprocess.run([sys.executable, "scripts/inspect_docx.py", "--in",
                         str(FIX / "sample.docx"), "--out", str(OUT / "gbk.json"), "--summary"],
                        cwd=str(ROOT), env=gbk_env, capture_output=True)
    check("GBK 控制台下脚本正常退出", cp.returncode == 0,
          cp.stderr.decode("gbk", "replace")[:160])

    ok = sum(1 for _, k, _ in results if k)
    total = len(results)
    print("\n通过 %d / %d" % (ok, total))
    if ok != total:
        for name, k, detail in results:
            if not k:
                print("  失败：%s %s" % (name, detail))
        return 1
    print("产物目录：%s" % OUT)
    return 0


if __name__ == "__main__":
    sys.exit(main())
