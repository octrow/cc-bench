"""One-shot measurement of a normal interactive Claude Code session.

`bench session` is the lazy path: no arms, no probes, no prepare. It launches
`claude` in a repo you already work in, waits for you to finish, then finds
that session's transcript and turns it into a results.csv row via extract.

Telemetry itself is not set up here -- it belongs in the env block of
~/.claude/settings.json so every session is measured, not just the ones you
remembered to wrap. This command only preflights that it is on.
"""

from __future__ import annotations

import argparse
import csv
import json
import re
import socket
import subprocess
import sys
import time
from pathlib import Path

from cc_bench.extract import DEFAULT_CSV, append_csv, extract_run, format_summary

DEFAULT_OTEL_DIR = Path.home() / "bench" / "otel" / "data"
COLLECTOR_HOST, COLLECTOR_PORT = "localhost", 4317
# OTEL_METRIC_EXPORT_INTERVAL is 10s; give the last batch time to land.
FLUSH_WAIT_S = 13


def _slug(repo: Path) -> str:
    """Claude Code's transcript dir name for a cwd: non-alphanumerics -> '-'."""
    return re.sub(r"[^a-zA-Z0-9]", "-", str(repo))


def _next_run(csv_path: Path, arm: str, task: str) -> str:
    """r1, r2, ... counting rows already recorded for this arm+task."""
    if not csv_path.exists():
        return "r1"
    with csv_path.open(newline="", encoding="utf-8") as handle:
        n = sum(1 for row in csv.DictReader(handle) if row["arm"] == arm and row["task"] == task)
    return f"r{n + 1}"


def _default_task(repo: Path) -> str:
    """Current branch as the task id -- the branch usually IS the ticket."""
    result = subprocess.run(
        ["git", "-C", str(repo), "rev-parse", "--abbrev-ref", "HEAD"],
        capture_output=True,
        text=True,
        check=False,
    )
    branch = result.stdout.strip()
    return branch if result.returncode == 0 and branch not in ("", "HEAD") else "adhoc"


def _pick_transcript(cfg_dir: Path, repo: Path, since: float | None) -> Path | None:
    """Newest main-session transcript for `repo`, optionally touched after `since`."""
    project_dir = cfg_dir / "projects" / _slug(repo)
    search_root = project_dir if project_dir.is_dir() else cfg_dir / "projects"
    candidates = [
        p
        for p in search_root.rglob("*.jsonl")
        if "subagents" not in p.parts and (since is None or p.stat().st_mtime >= since)
    ]
    return max(candidates, key=lambda p: p.stat().st_mtime) if candidates else None


def _preflight(cfg_dir: Path) -> list[str]:
    """Reasons this session would not be measurable. Empty list means good."""
    problems = []
    settings = cfg_dir / "settings.json"
    env = {}
    if settings.exists():
        try:
            env = json.loads(settings.read_text()).get("env", {})
        except json.JSONDecodeError:
            problems.append(f"{settings} is not valid JSON")
    if env.get("CLAUDE_CODE_ENABLE_TELEMETRY") != "1":
        problems.append(
            f'telemetry is off -- add "CLAUDE_CODE_ENABLE_TELEMETRY": "1" and the '
            f"OTEL_* vars to the env block of {settings}"
        )
    try:
        with socket.create_connection((COLLECTOR_HOST, COLLECTOR_PORT), timeout=2):
            pass
    except OSError:
        problems.append(
            f"no OTEL collector on {COLLECTOR_HOST}:{COLLECTOR_PORT} "
            "-- start it with: docker start bench-otel"
        )
    return problems


def cmd_session(args: argparse.Namespace) -> int:
    repo = Path(args.repo or Path.cwd()).expanduser().resolve()
    if not repo.is_dir():
        print(f"session: {repo} is not a directory", file=sys.stderr)
        return 2

    cfg_dir = Path(args.cfg).expanduser().resolve()
    csv_path = Path(args.csv).expanduser()
    arm = args.arm
    task = args.task or _default_task(repo)
    run = args.run or _next_run(csv_path, arm, task)

    since = None
    if not args.no_launch:
        problems = _preflight(cfg_dir)
        if problems:
            for problem in problems:
                print(f"session: {problem}", file=sys.stderr)
            if not args.force:
                print("session: refusing to burn a session that cannot be measured", file=sys.stderr)
                return 1
        print(f"session: {arm} / {task} / {run} in {repo}")
        since = time.time()
        code = subprocess.run(["claude", *args.claude_args], cwd=repo, check=False).returncode
        print(f"session: claude exited {code}; waiting {FLUSH_WAIT_S}s for the OTEL flush")
        time.sleep(FLUSH_WAIT_S)

    transcript = _pick_transcript(cfg_dir, repo, since)
    if transcript is None:
        print(f"session: no transcript found for {repo} under {cfg_dir}/projects", file=sys.stderr)
        return 1

    row = extract_run(
        otel=[str(Path(args.otel).expanduser())],
        arm=arm,
        task=task,
        run=run,
        transcript=str(transcript),
        session_id=transcript.stem,
    )
    print(format_summary(row))
    if not args.no_csv:
        print(f"  -> appended to {append_csv(row, csv_path)}")
    return 0


def add_arguments(parser: argparse.ArgumentParser) -> argparse.ArgumentParser:
    parser.add_argument("--repo", default=None, help="repo to work in (default: cwd)")
    parser.add_argument("--task", default=None, help="task id (default: current git branch)")
    parser.add_argument("--arm", default="live", help="label for this setup (default: live)")
    parser.add_argument("--run", default=None, help="run id (default: next rN for this arm+task)")
    parser.add_argument(
        "--no-launch",
        action="store_true",
        help="skip launching claude; measure the newest existing session for the repo",
    )
    parser.add_argument("--otel", default=str(DEFAULT_OTEL_DIR), help=f"default: {DEFAULT_OTEL_DIR}")
    parser.add_argument("--cfg", default=str(Path.home() / ".claude"), help="CLAUDE_CONFIG_DIR")
    parser.add_argument("--csv", default=str(DEFAULT_CSV), help=f"default: {DEFAULT_CSV}")
    parser.add_argument("--no-csv", action="store_true", help="print only, do not append")
    parser.add_argument("--force", action="store_true", help="launch even if preflight fails")
    parser.add_argument(
        "claude_args",
        nargs=argparse.REMAINDER,
        help="args after -- are passed through to claude",
    )
    return parser
