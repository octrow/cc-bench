"""Headless measured runs of a single arm against a single task.

IMPORTANT - interleaving lives one layer up. cmd_run executes N sequential
runs of ONE arm. The protocol's honesty rule (PLAN-tools.md §5.3:
"T0,Tx,Tx,T0,..." interleaved order) is an orchestration concern for
whatever drives `bench run` in a loop (the conductor, or a future
`bench campaign` command) - this module never reaches across arms and
never reorders itself. Each `bench run` invocation is one arm, one task,
N runs, strictly sequential, with a 5s pause between runs to avoid any
resource-contention skew.
"""

from __future__ import annotations

import argparse
import os
import subprocess
import time
from pathlib import Path

from cc_bench import gates

DEFAULT_RUNS = 3
DEFAULT_MODEL = "opus"
SLEEP_BETWEEN_RUNS_S = 5
OTEL_ENDPOINT = "http://localhost:4317"


def _resolve_workdir(args: argparse.Namespace) -> Path:
    if getattr(args, "workdir", None):
        return Path(args.workdir).resolve()
    return (Path.home() / "bench" / args.repo_name / args.arm).resolve()


def _run_env(arm: str, task: str, run_n: int, workdir: Path) -> dict:
    env = os.environ.copy()
    env.update(
        {
            "CLAUDE_CONFIG_DIR": str(workdir / f"cfg-{arm}"),
            "CLAUDE_CODE_ENABLE_TELEMETRY": "1",
            "OTEL_METRICS_EXPORTER": "otlp",
            "OTEL_LOGS_EXPORTER": "otlp",
            "OTEL_EXPORTER_OTLP_PROTOCOL": "grpc",
            "OTEL_EXPORTER_OTLP_ENDPOINT": OTEL_ENDPOINT,
            "OTEL_METRIC_EXPORT_INTERVAL": "10000",
            "OTEL_EXPORTER_OTLP_METRICS_TEMPORALITY_PREFERENCE": "cumulative",
            "OTEL_RESOURCE_ATTRIBUTES": f"arm={arm},task={task},run={run_n}",
        }
    )
    return env


def _reset_clone(clone: Path) -> None:
    subprocess.run(["git", "checkout", "bench-base"], cwd=clone, capture_output=True, check=False)
    subprocess.run(["git", "clean", "-fdq"], cwd=clone, capture_output=True, check=False)


def _branch_for(task: str, arm: str, run_n: int) -> str:
    return f"bench/{task}-{arm}-{run_n}"


def _checkout_fresh_branch(clone: Path, branch: str) -> None:
    subprocess.run(["git", "branch", "-D", branch], cwd=clone, capture_output=True, check=False)
    subprocess.run(["git", "checkout", "-b", branch], cwd=clone, capture_output=True, check=False)


def _invoke_claude(
    clone: Path, prompt_text: str, model: str, arm: str, task: str, run_n: int, workdir: Path
) -> tuple[int, float, str, str]:
    """Run one headless claude session. Returns (exit_code, wall_seconds, stdout, stderr)."""
    cmd = ["claude", "-p", prompt_text, "--model", model, "--permission-mode", "acceptEdits"]
    if arm == "baseline":
        cmd.append("--strict-mcp-config")

    env = _run_env(arm, task, run_n, workdir)
    start = time.monotonic()
    result = subprocess.run(cmd, cwd=clone, env=env, capture_output=True, text=True, check=False)
    wall = time.monotonic() - start
    return result.returncode, wall, result.stdout, result.stderr


def _write_log(log_dir: Path, task: str, arm: str, run_n: int, stdout: str, stderr: str) -> Path:
    log_dir.mkdir(parents=True, exist_ok=True)
    out_path = log_dir / f"{task}-{arm}-{run_n}.out"
    out_path.write_text(stdout + ("\n--- stderr ---\n" + stderr if stderr else ""))
    return out_path


def _run_once(
    clone: Path, prompt_text: str, model: str, arm: str, task: str, run_n: int, workdir: Path, log_dir: Path
) -> dict:
    """Execute a single attempt. Retries once on nonzero exit; second failure
    is recorded as success=0 with a marker line in the log."""
    for attempt in (1, 2):
        code, wall, stdout, stderr = _invoke_claude(clone, prompt_text, model, arm, task, run_n, workdir)
        log_path = _write_log(log_dir, task, arm, run_n, stdout, stderr)
        if code == 0:
            return {"exit_code": code, "wall_s": wall, "success": 1, "log": str(log_path), "attempts": attempt}
        if attempt == 1:
            print(f"  run {run_n}: attempt 1 failed (exit {code}), retrying once")
            continue
        with log_path.open("a") as f:
            f.write("\n--- MARKER: second attempt failed, success=0 ---\n")
        return {"exit_code": code, "wall_s": wall, "success": 0, "log": str(log_path), "attempts": attempt}
    raise AssertionError("unreachable")


def cmd_run(args: argparse.Namespace) -> int:
    workdir = _resolve_workdir(args)
    clone = workdir
    prompt_text = Path(args.prompt_file).read_text()
    model = args.model or DEFAULT_MODEL
    runs = args.runs or DEFAULT_RUNS

    results_dir = Path("results")
    log_dir = results_dir / "logs"
    gates_dir = results_dir / "gates"

    for n in range(1, runs + 1):
        _reset_clone(clone)
        branch = _branch_for(args.task, args.arm, n)
        _checkout_fresh_branch(clone, branch)

        outcome = _run_once(clone, prompt_text, model, args.arm, args.task, n, workdir, log_dir)

        gate_prefix = gates_dir / f"{args.task}-{args.arm}-{n}"
        diff_summary = gates.capture_diff(clone, gate_prefix)

        _reset_clone(clone)

        status = "OK" if outcome["success"] else "FAILED (both attempts)"
        print(
            f"run {n}/{runs} [{args.arm}/{args.task}]: {status}, "
            f"exit={outcome['exit_code']}, wall={outcome['wall_s']:.1f}s, "
            f"diff_files={len(diff_summary['changed_files'])}, log={outcome['log']}"
        )

        if n < runs:
            time.sleep(SLEEP_BETWEEN_RUNS_S)

    return 0


