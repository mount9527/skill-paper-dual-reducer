#!/usr/bin/env python3
"""run.py —— 单入口：一条命令跑完整条流水线，供任意 agent 调用。

为什么需要
----------
七个脚本各自 JSON 进 JSON 出，好处是每一步都能单独重跑；坏处是 Codex / Cursor /
豆包这类宿主只会执行一两条命令，串不起来。run.py 就是那一条命令：按顺序调用脚本，
产物固定落在 --outdir，失败时原样抛出子进程输出，不吞错、不猜状态。

用法
----
    python run.py doctor
    python run.py diagnose --in paper.docx --outdir out            # 只诊断
    python run.py rewrite  --in paper.docx --outdir out            # 出提示词，等模型改写
    python run.py lint     --in paper.docx --outdir out --edits edits.jsonl
    python run.py finish   --in paper.docx --outdir out --edits edits.jsonl
    python run.py all      --in paper.docx --outdir out --edits edits.jsonl --mode both

退出码
------
    0 成功 ｜ 1 lint 有会被拒的编辑 ｜ 2 输入/环境有误 ｜ 3 篇级校验失败已整篇回退
"""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

from lib.cli import safe_io  # noqa: E402

#: 子进程强制 UTF-8 出文本：宿主控制台是 GBK 时，不强制会让父进程按 UTF-8 解码失败。
CHILD_ENV = {**os.environ, "PYTHONUTF8": "1", "PYTHONIOENCODING": "utf-8"}

SCRIPTS = ("inspect_docx.py", "diagnose.py", "plan.py", "build_prompts.py",
           "lint_edits.py", "apply_edits.py", "verify.py", "render_report.py",
           "spotcheck.py")

MODES = ("diagnose", "rewrite", "both")


class StepFailed(SystemExit):
    def __init__(self, step: str, rc: int):
        super().__init__("[%s] 失败（rc=%d）" % (step, rc))
        self.rc = rc


def _run(step: str, argv: list[str], allow: tuple[int, ...] = (0,)) -> int:
    print("\n== %s ==" % step)
    cp = subprocess.run([sys.executable] + argv, cwd=str(ROOT), env=CHILD_ENV,
                        capture_output=True, text=True, encoding="utf-8", errors="replace")
    sys.stdout.write(cp.stdout)
    if cp.stderr.strip():
        sys.stderr.write(cp.stderr)
    if cp.returncode not in allow:
        raise StepFailed(step, cp.returncode)
    return cp.returncode


def paths(outdir: Path) -> dict[str, Path]:
    return {
        "doc": outdir / "doc.json",
        "profile": outdir / "profile.json",
        "tasks": outdir / "tasks.json",
        "prompts": outdir / "prompts.jsonl",
        "lint": outdir / "lint.json",
        "out": outdir / "rewritten.docx",
        "result": outdir / "rewritten.result.json",
        "verdict": outdir / "rewritten.verdict.json",
        "report": outdir / "report.md",
        "spotcheck": outdir / "spotcheck.md",
    }


def do_inspect(src: str, outdir: Path, p: dict, min_chars: int) -> None:
    _run("解析", ["scripts/inspect_docx.py", "--in", src, "--out", str(p["doc"]),
                  "--min-chars", str(min_chars), "--summary"])


def do_diagnose(p: dict, top: int) -> None:
    _run("诊断", ["scripts/diagnose.py", "--doc", str(p["doc"]), "--out", str(p["profile"]),
                  "--top", str(top)])


def do_plan(p: dict, max_tasks: int, style: str) -> None:
    _run("排期", ["scripts/plan.py", "--profile", str(p["profile"]), "--doc", str(p["doc"]),
                  "--out", str(p["tasks"]), "--max-tasks", str(max_tasks), "--style", style])


def do_prompts(p: dict) -> None:
    _run("提示词", ["scripts/build_prompts.py", "--tasks", str(p["tasks"]),
                    "--doc", str(p["doc"]), "--out", str(p["prompts"])])


def do_report(p: dict, mode: str, style: str) -> None:
    argv = ["scripts/render_report.py", "--doc", str(p["doc"]), "--profile", str(p["profile"]),
            "--out", str(p["report"]), "--mode", mode, "--style", style]
    if p["verdict"].exists():
        argv += ["--verdict", str(p["verdict"])]
    if p["result"].exists():
        argv += ["--result", str(p["result"])]
    _run("报告", argv)


def do_spotcheck(p: dict, n: int) -> None:
    _run("抽查表", ["scripts/spotcheck.py", "--profile", str(p["profile"]), "--doc", str(p["doc"]),
                    "--out", str(p["spotcheck"]), "--n", str(n)])


def do_lint(before: str, edits: str, p: dict, strict: bool) -> int:
    rc = _run("预检", ["scripts/lint_edits.py", "--doc", str(p["doc"]), "--before", before,
                       "--edits", edits, "--out", str(p["lint"])], allow=(0, 1))
    if rc == 1 and strict:
        raise StepFailed("预检", 1)
    return rc


def do_apply(before: str, edits: str, p: dict) -> None:
    _run("回填", ["scripts/apply_edits.py", "--doc", str(p["doc"]), "--before", before,
                  "--edits", edits, "--out", str(p["out"])], allow=(0, 3))


def do_doctor() -> int:
    """环境自检：跨宿主运行时最先跑这条，能省掉一整轮瞎猜。"""
    checks: list[tuple[str, bool, str]] = []
    py = sys.version_info
    checks.append(("python >= 3.10", py >= (3, 10), "%d.%d.%d" % (py[0], py[1], py[2])))

    missing = [s for s in SCRIPTS if not (ROOT / "scripts" / s).exists()]
    checks.append(("脚本齐全", not missing,
                   "缺失 %s" % missing if missing else "%d/%d" % (len(SCRIPTS), len(SCRIPTS))))

    outdir = ROOT / "out"
    try:
        outdir.mkdir(parents=True, exist_ok=True)
        probe = outdir / ".doctor-probe"
        probe.write_text("ok", encoding="utf-8")
        probe.unlink()
        writable = True
        detail = str(outdir)
    except OSError as exc:
        writable = False
        detail = str(exc)
    checks.append(("输出目录可写", writable, detail))

    enc = (sys.stdout.encoding or "?").lower()
    checks.append(("stdout 能编码中文", "论文".encode(enc, "replace") != b"??",
                   "编码 %s（不可编码字符会降级为 ?，不会崩）" % enc))

    checks.append(("零第三方依赖", True, "只用标准库 zipfile / ElementTree"))

    print("== 环境自检 ==")
    for name, ok, detail in checks:
        print("  %s %s  %s" % ("✓" if ok else "✗", name, detail))
    bad = [n for n, ok, _ in checks if not ok]
    if bad:
        print("\n[y] 不通过：%s —— 先补齐再继续" % "、".join(bad))
        return 1
    print("\n[y] 环境可用。下一步：python run.py diagnose --in <论文.docx> --outdir out")
    return 0


def main(argv: list[str] | None = None) -> int:
    safe_io()
    ap = argparse.ArgumentParser(
        prog="run.py",
        description="论文双降 · 单入口 CLI（诊断 / 改写 / 回填 / 复检 一条命令）")
    sub = ap.add_subparsers(dest="cmd", required=True)

    sub.add_parser("doctor", help="环境自检")

    for name, help_text in (("diagnose", "只诊断：出 doc.json / profile.json / report.md"),
                            ("spotcheck", "生成 P1 门禁的归因人工抽查表"),
                            ("rewrite", "出提示词（模型改写前的一步）"),
                            ("lint", "dry-run 预检 edits.jsonl"),
                            ("finish", "预检 → 回填 → 复检 → 报告"),
                            ("all", "全流程（按 --mode 决定做到哪一步）")):
        sp = sub.add_parser(name, help=help_text)
        sp.add_argument("--in", dest="src", required=True, help="输入 .docx")
        sp.add_argument("--outdir", required=True, help="产物目录")
        sp.add_argument("--edits", help="edits.jsonl（lint / finish / all 必填）")
        sp.add_argument("--mode", choices=MODES, default="both", help="模式切换（默认 both）")
        sp.add_argument("--style", choices=("plain", "academic"), default="plain",
                        help="文风档案：plain=平实五条（默认），academic=只做学术润色")
        sp.add_argument("--n", type=int, default=20, help="抽查表段数（默认 20）")
        sp.add_argument("--max-tasks", type=int, default=12, help="改写任务上限（默认 12）")
        sp.add_argument("--top", type=int, default=15, help="报告保留的高风险段数（默认 15）")
        sp.add_argument("--min-chars", type=int, default=40, help="低于此字数不改写（默认 40）")
        sp.add_argument("--strict", action="store_true", help="lint 有拒绝就中止")

    args = ap.parse_args(argv)
    if args.cmd == "doctor":
        return do_doctor()

    src = args.src
    if not Path(src).exists():
        print("[error] 找不到输入文件 %s" % src, file=sys.stderr)
        return 2

    outdir = Path(args.outdir)
    outdir.mkdir(parents=True, exist_ok=True)
    p = paths(outdir)

    try:
        if args.cmd == "diagnose":
            do_inspect(src, outdir, p, args.min_chars)
            do_diagnose(p, args.top)
            do_report(p, "diagnose", args.style)
            print("\n只诊断模式：报告 → %s" % p["report"])
            return 0

        if args.cmd == "spotcheck":
            do_inspect(src, outdir, p, args.min_chars)
            do_diagnose(p, args.top)
            do_spotcheck(p, args.n)
            print("\n抽查表 → %s（填「一致?」列，≥80%% 即过 P1 门禁）" % p["spotcheck"])
            return 0

        if args.cmd == "rewrite":
            do_inspect(src, outdir, p, args.min_chars)
            do_diagnose(p, args.top)
            do_plan(p, args.max_tasks, args.style)
            do_prompts(p)
            print("\n下一步：按 %s 逐行改写，写出 edits.jsonl，再跑" % p["prompts"])
            print("  python run.py finish --in %s --outdir %s --edits edits.jsonl" % (src, outdir))
            return 0

        if not args.edits:
            print("[error] %s 需要 --edits edits.jsonl" % args.cmd, file=sys.stderr)
            return 2
        if not Path(args.edits).exists():
            print("[error] 找不到编辑清单 %s" % args.edits, file=sys.stderr)
            return 2

        if args.cmd == "lint":
            do_inspect(src, outdir, p, args.min_chars)
            rc = do_lint(src, args.edits, p, strict=False)
            return rc

        # finish / all
        if not p["doc"].exists() or not p["profile"].exists():
            do_inspect(src, outdir, p, args.min_chars)
            do_diagnose(p, args.top)
        if args.cmd == "all" and args.mode in ("rewrite", "both"):
            do_plan(p, args.max_tasks, args.style)
            do_prompts(p)

        do_lint(src, args.edits, p, strict=args.strict)
        do_apply(src, args.edits, p)
        do_report(p, args.mode, args.style)

        from lib.cli import read_json
        verdict = read_json(p["verdict"]) if p["verdict"].exists() else {"passed": False}
        print("\n%s：篇级校验 %s ｜ 报告 → %s"
              % ("完成" if verdict.get("passed") else "需回退",
                 "通过" if verdict.get("passed") else "未通过", p["report"]))
        return 0 if verdict.get("passed") else 3

    except StepFailed as exc:
        print("\n%s" % exc, file=sys.stderr)
        return exc.rc if exc.rc in (1, 2, 3) else 2


if __name__ == "__main__":
    sys.exit(main())
