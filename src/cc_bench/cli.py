"""cc-bench CLI entry point.

Phase 1: arms/prepare/gates/run/extract/report are real.
`probes gen|freeze` and `smoke` land in Phase 2 (probe generation and
fired-check smoke are conductor-driven; see PLAN-tools phases).
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from cc_bench import arms as arms_mod
from cc_bench import extract as extract_mod
from cc_bench import gates as gates_mod
from cc_bench import prepare as prepare_mod
from cc_bench import report as report_mod
from cc_bench import run as run_mod

REPO_ROOT = Path(__file__).resolve().parents[2]
ARMS_DIR = REPO_ROOT / "arms"

NOT_IMPLEMENTED = "not implemented until Phase 2: {what}"


def _stub(what: str) -> int:
    print(NOT_IMPLEMENTED.format(what=what))
    return 2


def cmd_arms_list(_args: argparse.Namespace) -> int:
    return arms_mod.cmd_list(ARMS_DIR)


def cmd_arms_validate(_args: argparse.Namespace) -> int:
    return arms_mod.cmd_validate(ARMS_DIR)


def cmd_probes_gen(args: argparse.Namespace) -> int:
    return _stub(f"LLM instantiates probe templates against {args.repo} for human freeze")


def cmd_probes_freeze(_args: argparse.Namespace) -> int:
    return _stub("lock instantiated probes + answer keys so they cannot change before runs")


def cmd_smoke(args: argparse.Namespace) -> int:
    return _stub(f"run fired-check smoke prompt for arm '{args.arm}' against {args.repo}")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="bench", description="Claude Code before/after tool benchmark harness")
    subparsers = parser.add_subparsers(dest="command", required=True)

    arms_parser = subparsers.add_parser("arms", help="manage arm definitions")
    arms_sub = arms_parser.add_subparsers(dest="arms_command", required=True)
    arms_sub.add_parser("list", help="list arms and their tier/metric").set_defaults(func=cmd_arms_list)
    arms_sub.add_parser("validate", help="validate arms/*.yaml schema").set_defaults(func=cmd_arms_validate)

    prepare_parser = subparsers.add_parser("prepare", help="clone repo and install an arm")
    prepare_parser.add_argument("--repo", required=True, help="path to target working repo")
    prepare_parser.add_argument("--arm", required=True, help="arm name")
    prepare_parser.add_argument("--base-ref", default="origin/dev", help="ref for bench-base SHA")
    prepare_parser.add_argument("--workdir", default=None, help="bench workspace (default ~/bench)")
    prepare_parser.add_argument("--force", action="store_true", help="delete a dirty existing clone")
    prepare_parser.set_defaults(func=prepare_mod.cmd_prepare)

    probes_parser = subparsers.add_parser("probes", help="generate/freeze tier-P probes")
    probes_sub = probes_parser.add_subparsers(dest="probes_command", required=True)
    probes_gen = probes_sub.add_parser("gen", help="instantiate probe templates for a repo")
    probes_gen.add_argument("--repo", required=True, help="path to target repo")
    probes_gen.set_defaults(func=cmd_probes_gen)
    probes_sub.add_parser("freeze", help="freeze instantiated probes + answer keys").set_defaults(
        func=cmd_probes_freeze
    )

    run_parser = subparsers.add_parser("run", help="headless measured run(s) for one arm")
    run_parser.add_argument("--arm", required=True, help="arm name")
    run_parser.add_argument("--tier", required=True, choices=["p", "f"])
    run_parser.add_argument("--repo-name", required=True, help="repo dir name under the workdir")
    run_parser.add_argument("--task", required=True, help="task/probe id")
    run_parser.add_argument("--runs", type=int, default=3)
    run_parser.add_argument("--prompt-file", required=True, help="frozen prompt/probe text")
    run_parser.add_argument("--model", default=None, help="main model (default opus)")
    run_parser.add_argument("--workdir", default=None, help="bench workspace override")
    run_parser.add_argument("--budget-usd", type=float, default=None)
    run_parser.add_argument("--answer-key", default=None, help="tier P frozen answer key file")
    run_parser.set_defaults(func=run_mod.cmd_run)

    gates_parser = subparsers.add_parser("gates", help="diff + ruff/pytest/scope gates vs bench-base")
    gates_parser.add_argument("--clone", required=True, help="path to arm clone")
    gates_parser.add_argument("--out", required=True, help="output prefix for .diff/.gates.txt")
    gates_parser.add_argument("--pytest", default=None, help="pytest command to run as a gate")
    gates_parser.add_argument("--pytest-baseline", default=None, help="baseline failures file")
    gates_parser.add_argument("--blast-radius", default=None, help="frozen expected-files list")
    gates_parser.set_defaults(func=gates_mod.cmd_gates)

    extract_parser = subparsers.add_parser("extract", help="OTEL + transcript -> one results.csv row")
    extract_mod.add_arguments(extract_parser)
    extract_parser.set_defaults(func=extract_mod.cmd_extract)

    smoke_parser = subparsers.add_parser("smoke", help="run a fired-check smoke test for an arm")
    smoke_parser.add_argument("--arm", required=True, help="arm name")
    smoke_parser.add_argument("--repo", required=True, help="path to target repo")
    smoke_parser.set_defaults(func=cmd_smoke)

    report_parser = subparsers.add_parser("report", help="build the median+IQR markdown report")
    report_parser.add_argument("--csv", default=str(REPO_ROOT / "results" / "results.csv"))
    report_parser.add_argument("--out", default=str(REPO_ROOT / "results" / "report.md"))
    report_parser.add_argument("--arms-dir", default=str(ARMS_DIR))
    report_parser.set_defaults(func=report_mod.cmd_report)

    return parser


def main(argv: list[str] | None = None) -> None:
    parser = build_parser()
    args = parser.parse_args(argv)
    sys.exit(args.func(args))


if __name__ == "__main__":
    main()
