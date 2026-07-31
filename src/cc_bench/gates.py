"""Post-run gates: diff capture, ruff new-violations-only, pytest, scope creep.

Ported from sdd-kit/benchmark/finish-tails.sh (proven m1/m2 gate logic).
Gates never fail the CLI: cmd_gates always exits 0. Gate outcomes are data
for the report, not a CI signal - a FAIL line in <out>.gates.txt is the
product, not a stop condition.

Ruff gate: only *.py files that changed are checked. "New violation" means
a (file, rule_code) pair whose count increased vs. the same file's content
at the bench-base ref. CPY001 (copyright header) is always excluded - it is
noise inherited from repo templates, not something a benchmarked tool
could plausibly introduce or fix.
"""

from __future__ import annotations

import argparse
import re
import shutil
import subprocess
import tempfile
from collections import Counter
from pathlib import Path

RUFF_LINE_RE = re.compile(r"^([^ :]+):\d+:\d+: ([A-Z]+\d+)")
EXCLUDED_RULES = {"CPY001"}
RUFF_CONFIG_NAMES = ("pyproject.toml", "ruff.toml", ".ruff.toml")


def _run(cmd: list[str], cwd: Path | None = None) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, cwd=cwd, capture_output=True, text=True, check=False)


def capture_diff(clone: Path, out_prefix: Path) -> dict:
    """Stage everything, diff vs bench-base, reset the working tree.

    Writes <out_prefix>.diff and <out_prefix>.files.txt. Returns a summary
    dict with added/removed line counts and changed file count. The clone's
    working tree is left exactly as it was found (git reset -q at the end).
    """
    _run(["git", "add", "-A"], cwd=clone)
    diff = _run(["git", "diff", "--cached", "bench-base"], cwd=clone)
    diff_text = diff.stdout
    out_prefix.parent.mkdir(parents=True, exist_ok=True)
    (out_prefix.with_suffix(out_prefix.suffix + ".diff")).write_text(diff_text)

    status = _run(["git", "status", "--short"], cwd=clone)
    (out_prefix.with_suffix(out_prefix.suffix + ".files.txt")).write_text(status.stdout)

    _run(["git", "reset", "-q"], cwd=clone)

    added = sum(1 for line in diff_text.splitlines() if line.startswith("+") and not line.startswith("+++"))
    removed = sum(1 for line in diff_text.splitlines() if line.startswith("-") and not line.startswith("---"))
    changed_files = sorted(
        line.removeprefix("+++ b/")
        for line in diff_text.splitlines()
        if line.startswith("+++ b/")
    )
    return {
        "added": added,
        "removed": removed,
        "changed_files": changed_files,
        "diff_path": str(out_prefix) + ".diff",
    }


def _changed_py_files(diff_summary: dict) -> list[str]:
    return [f for f in diff_summary["changed_files"] if f.endswith(".py")]


def _aggregate_ruff(output: str) -> Counter:
    """Parse `ruff check --output-format concise` text into {(file, code): count}."""
    counts: Counter = Counter()
    for line in output.splitlines():
        m = RUFF_LINE_RE.match(line)
        if not m:
            continue
        file_, code = m.group(1), m.group(2)
        if code in EXCLUDED_RULES:
            continue
        counts[(file_, code)] += 1
    return counts


def _find_ruff_config(clone: Path, py_file: str) -> Path | None:
    """Walk up from the changed file's directory looking for a ruff config."""
    current = (clone / py_file).parent
    while True:
        for name in RUFF_CONFIG_NAMES:
            candidate = current / name
            if candidate.is_file():
                return candidate
        if current == clone or current == current.parent:
            return None
        current = current.parent


def _ruff_check(clone: Path, files: list[str]) -> str:
    if not files:
        return ""
    result = _run(["uvx", "ruff", "check", "--output-format", "concise", *files], cwd=clone)
    return result.stdout


def _materialize_bench_base(clone: Path, files: list[str], workdir: Path) -> None:
    """Write bench-base's version of each changed .py file into workdir, plus
    whichever ruff config governs it, so `ruff check` there mirrors the repo's
    real config."""
    seen_configs: set[Path] = set()
    for f in files:
        show = _run(["git", "show", f"bench-base:{f}"], cwd=clone)
        if show.returncode != 0:
            continue  # file did not exist at bench-base (newly added)
        dest = workdir / f
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_text(show.stdout)

        config = _find_ruff_config(clone, f)
        if config is None or config in seen_configs:
            continue
        seen_configs.add(config)
        rel = config.relative_to(clone)
        config_dest = workdir / rel
        config_dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy(config, config_dest)


def ruff_gate(clone: Path, diff_summary: dict) -> tuple[bool, str, list[str]]:
    """Run the new-violations-only ruff gate. Returns (passed, detail, detail_lines)."""
    changed_py = _changed_py_files(diff_summary)
    if not changed_py:
        return True, "no changed .py files", []

    after_counts = _aggregate_ruff(_ruff_check(clone, changed_py))

    with tempfile.TemporaryDirectory(prefix="cc-bench-ruff-") as tmp:
        workdir = Path(tmp)
        _materialize_bench_base(clone, changed_py, workdir)
        before_files = [str(p.relative_to(workdir)) for p in workdir.rglob("*.py")]
        before_counts = _aggregate_ruff(_ruff_check(workdir, before_files))

    new = sorted(
        (key, after_counts[key] - before_counts.get(key, 0))
        for key in after_counts
        if after_counts[key] > before_counts.get(key, 0)
    )
    if not new:
        return True, "no new violations", []

    total_new = sum(n for _, n in new)
    lines = [f"  new: {f} {code} +{n}" for (f, code), n in new]
    return False, f"{total_new} new", lines


def pytest_gate(clone: Path, pytest_cmd: str, baseline_path: Path | None) -> tuple[bool, str]:
    """Run pytest_cmd; if a baseline failure list is given, gate only on new
    failures (failures present at baseline don't count against this run)."""
    result = _run(pytest_cmd.split(), cwd=clone)
    after_failures = {
        line.strip() for line in result.stdout.splitlines() if line.strip().startswith("FAILED")
    }
    if baseline_path is None:
        if result.returncode == 0:
            return True, "no failures"
        return False, f"{len(after_failures)} failing (no baseline given)"

    before_failures = set()
    if baseline_path.is_file():
        before_failures = {line.strip() for line in baseline_path.read_text().splitlines() if line.strip()}

    new_failures = after_failures - before_failures
    if not new_failures:
        return True, "no new failures"
    return False, f"{len(new_failures)} new failures"


def scope_creep_gate(diff_summary: dict, blast_radius_path: Path) -> tuple[bool, str]:
    """Changed files outside the frozen blast-radius list = scope creep."""
    import fnmatch

    patterns = [line.strip() for line in blast_radius_path.read_text().splitlines() if line.strip()]
    outside = [
        f for f in diff_summary["changed_files"] if not any(fnmatch.fnmatch(f, p) for p in patterns)
    ]
    if not outside:
        return True, "no files outside blast radius"
    return False, f"{len(outside)} files outside blast radius: {', '.join(outside)}"


def cmd_gates(args: argparse.Namespace) -> int:
    """CLI entry: bench gates --clone PATH --out PREFIX [--pytest CMD]
    [--pytest-baseline FILE] [--blast-radius FILE]

    Always exits 0 - gates produce data (PREFIX.gates.txt) for the report,
    they are not a CI pass/fail signal.
    """
    clone = Path(args.clone).resolve()
    out_prefix = Path(args.out)

    lines: list[str] = []

    diff_summary = capture_diff(clone, out_prefix)
    diff_line_count = diff_summary["added"] + diff_summary["removed"]
    lines.append(f"PASS diff-captured ({diff_line_count} lines, {len(diff_summary['changed_files'])} files)")

    ruff_ok, ruff_detail, ruff_lines = ruff_gate(clone, diff_summary)
    lines.append(f"{'PASS' if ruff_ok else 'FAIL'} ruff ({ruff_detail})")
    lines.extend(ruff_lines)

    if args.pytest:
        baseline_path = Path(args.pytest_baseline) if args.pytest_baseline else None
        pytest_ok, pytest_detail = pytest_gate(clone, args.pytest, baseline_path)
        lines.append(f"{'PASS' if pytest_ok else 'FAIL'} pytest ({pytest_detail})")

    if args.blast_radius:
        scope_ok, scope_detail = scope_creep_gate(diff_summary, Path(args.blast_radius))
        lines.append(f"{'PASS' if scope_ok else 'FAIL'} scope-creep ({scope_detail})")

    gates_path = out_prefix.with_suffix(out_prefix.suffix + ".gates.txt")
    gates_path.write_text("\n".join(lines) + "\n")

    print(f"=== gates: {clone.name} ===")
    print("\n".join(lines))
    return 0


