"""`bench prepare` implementation.

Generalizes the proven manual harness in
sdd-kit/benchmark/prepare-m2.sh: fresh local clone of a working repo
pinned to a base SHA, .env copy-over, arm install steps applied and
committed to a `bench-base` branch, and a snapshot file with an
export block ready to paste into a benchmark session.

Entry point: cmd_prepare(args) where args is an argparse.Namespace
with at least .repo and .arm; .base_ref, .workdir, .force are
optional (read via getattr so this works even before cli.py grows
those flags).
"""

from __future__ import annotations

import shutil
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path

import yaml

ARMS_DIR = Path(__file__).resolve().parents[2] / "arms"
DEFAULT_BASE_REF = "origin/dev"
DEFAULT_WORKDIR = Path.home() / "bench"
PROTECTED_FILES = (".claude", "AGENTS.md", "CLAUDE.md", ".mcp.json")


class PrepareError(Exception):
    """Raised for any fatal prepare failure; message is printed as FAIL: ..."""


def note(msg: str, log: list[str]) -> None:
    print(f"== {msg}")
    log.append(msg)


def run(cmd, cwd=None, check=True, shell=False):
    result = subprocess.run(cmd, cwd=cwd, shell=shell, capture_output=True, text=True, check=False)
    if check and result.returncode != 0:
        cmd_str = cmd if shell else " ".join(str(c) for c in cmd)
        raise PrepareError(f"command failed: {cmd_str}\n{result.stderr.strip()}")
    return result


def load_arm(arm: str) -> dict:
    path = ARMS_DIR / f"{arm}.yaml"
    if not path.is_file():
        raise PrepareError(f"arm '{arm}' not found: {path} does not exist")
    doc = yaml.safe_load(path.read_text())
    if not isinstance(doc, dict):
        raise PrepareError(f"{path}: top-level content must be a mapping")
    return doc


def resolve_base_sha(repo: str, base_ref: str, log: list[str]) -> str:
    fetch = run(["git", "-C", repo, "fetch", "origin"], check=False)
    if fetch.returncode != 0:
        note(f"warn: git fetch failed ({fetch.stderr.strip()}), using local refs", log)

    rev = run(["git", "-C", repo, "rev-parse", base_ref], check=False)
    if rev.returncode != 0:
        local_branch = base_ref.split("/", 1)[-1]
        note(f"warn: {base_ref} not found, falling back to local {local_branch}", log)
        rev = run(["git", "-C", repo, "rev-parse", local_branch])
    sha = rev.stdout.strip()
    if not sha:
        raise PrepareError(f"could not resolve base SHA from {base_ref}")
    note(f"base SHA: {sha}", log)
    return sha


def check_dest_safe(dest: Path, force: bool) -> None:
    if not dest.exists():
        return
    status = run(["git", "status", "--porcelain"], cwd=dest, check=False)
    if status.returncode != 0:
        return  # not a usable git checkout (e.g. half-finished clone); safe to wipe
    dirty = status.stdout.strip()
    if dirty and not force:
        raise PrepareError(
            f"refusing to delete {dest}: uncommitted changes present "
            f"(pass --force to override):\n{dirty}"
        )


def make_clone(src: str, dest: Path, sha: str, force: bool, log: list[str]) -> None:
    check_dest_safe(dest, force)
    if dest.exists():
        shutil.rmtree(dest)
    dest.parent.mkdir(parents=True, exist_ok=True)

    run(["git", "clone", "--quiet", "--local", src, str(dest)])
    run(["git", "-C", str(dest), "checkout", "-q", "-B", "bench-base", sha])
    run(["git", "-C", str(dest), "remote", "remove", "origin"])
    (dest / ".claude" / "settings.local.json").unlink(missing_ok=True)
    note(f"clone recreated at {dest} on {sha[:12]}, push disabled", log)


def copy_env_files(repo: str, dest: Path, log: list[str]) -> int:
    found = run(
        ["find", repo, "-maxdepth", "2", "-name", ".env", "-not", "-path", "*/.git/*"]
    )
    files = [f for f in found.stdout.splitlines() if f]
    for f in files:
        rel = Path(f).relative_to(repo)
        target = dest / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(f, target)
    note(f".env copied: {len(files)} file(s)", log)
    return len(files)


def _resolve_src(repo: str, src: str) -> Path | None:
    """Resolve an install-step `src` against the target repo, falling back
    to the cc-bench arms/ parent dir (so arm-local fixtures also work)."""
    candidates = [Path(repo) / src, Path(src).expanduser(), ARMS_DIR.parent / src]
    for c in candidates:
        if c.exists():
            return c
    return None


def _install_cfg_skill(step: dict, cfg_dir: Path, log: list[str]) -> None:
    src = step.get("src", "")
    src_path = Path(src).expanduser()
    if not src or src == "TBD" or not src_path.exists():
        raise PrepareError(
            f"blocked: cfg_skill install has no usable src ({src!r}); "
            "fill in a real path in the arm YAML before running prepare"
        )
    dest = cfg_dir / "skills" / src_path.name
    if dest.exists():
        shutil.rmtree(dest)
    shutil.copytree(src_path, dest)
    note(f"cfg_skill: copied {src_path} -> {dest}", log)


def _install_hook(step: dict, cfg_dir: Path, log: list[str]) -> None:
    text = step.get("text", "install hook manually")
    note(f"hook (manual step, v1 does not automate plugins): {text} [CLAUDE_CONFIG_DIR={cfg_dir}]", log)


def _install_path_binary(step: dict, log: list[str]) -> None:
    check_cmd = step.get("check")
    if not check_cmd:
        return
    result = run(check_cmd, shell=True, check=False)
    if result.returncode != 0:
        raise PrepareError(f"path_binary check failed: {check_cmd}")
    note(f"path_binary: ok ({check_cmd})", log)


def _install_mcp(step: dict, cfg_dir: Path, log: list[str]) -> None:
    server = step.get("server", "?")
    claude_json = cfg_dir / ".claude.json"
    text = claude_json.read_text() if claude_json.is_file() else ""
    if server in text:
        note(f"mcp: {server} present in {claude_json}", log)
    else:
        note(
            f"mcp: {server} NOT found in {claude_json} — add it manually "
            f"({step.get('text', '')})",
            log,
        )


def _install_repo_artifacts(step: dict, repo: str, dest: Path, log: list[str]) -> None:
    src = step.get("src", "")
    resolved = _resolve_src(repo, src) if src else None
    if resolved and resolved.is_dir():
        target = dest / src
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copytree(resolved, target, dirs_exist_ok=True)
        # Some arms (e.g. graphify) want the artifact copied but never
        # committed to bench-base; others (e.g. openspec) explicitly want
        # it committed so every run starts from the same seeded tree.
        # Default to committing (git add -A picks it up normally); only
        # git-exclude when the arm step opts in via `exclude_from_git`.
        if step.get("exclude_from_git", False):
            exclude = dest / ".git" / "info" / "exclude"
            exclude_line = f"{src.rstrip('/')}/"
            existing = exclude.read_text() if exclude.is_file() else ""
            if exclude_line not in existing:
                with exclude.open("a") as f:
                    f.write(exclude_line + "\n")
            note(f"repo_artifacts: copied {resolved} -> {target}, git-excluded", log)
        else:
            note(f"repo_artifacts: copied {resolved} -> {target}, will be committed", log)
    else:
        note(f"repo_artifacts (manual step): {step.get('text', '')}", log)


def apply_install_steps(arm: str, doc: dict, repo: str, dest: Path, cfg_dir: Path, log: list[str]) -> None:
    if arm == "baseline":
        return
    for step in doc.get("install", []):
        kind = step.get("kind")
        if kind == "cfg_skill":
            _install_cfg_skill(step, cfg_dir, log)
        elif kind == "hook":
            _install_hook(step, cfg_dir, log)
        elif kind == "path_binary":
            _install_path_binary(step, log)
        elif kind == "mcp":
            _install_mcp(step, cfg_dir, log)
        elif kind == "repo_artifacts":
            _install_repo_artifacts(step, repo, dest, log)
        else:
            note(f"warn: unknown install kind '{kind}', skipped", log)

    status = run(["git", "status", "--porcelain"], cwd=dest, check=False)
    if not status.stdout.strip():
        note(f"commit: nothing to commit for arm '{arm}'", log)
        return
    run(["git", "-C", str(dest), "add", "-A"])
    run(["git", "-C", str(dest), "commit", "-q", "-m", f"install {arm} for bench-base"])
    short = run(["git", "-C", str(dest), "rev-parse", "--short", "HEAD"]).stdout.strip()
    note(f"install commit for arm '{arm}': {short}", log)


def check_baseline_clean(arm: str, dest: Path, log: list[str]) -> None:
    if arm != "baseline":
        return
    for name in PROTECTED_FILES:
        if (dest / name).exists():
            note(f"WARNING: baseline clone has {name} (came from git at this SHA)", log)


def auth_check(cfg_dir: Path, log: list[str]) -> None:
    result = run(["claude", "--version"], check=False)
    version = result.stdout.strip() or result.stderr.strip() or "unknown"
    note(f"claude --version (CLAUDE_CONFIG_DIR={cfg_dir}): {version}", log)
    note(
        f"REMINDER: {cfg_dir} needs a one-time interactive subscription login "
        "(`CLAUDE_CONFIG_DIR=... claude` then /login) — never an API key",
        log,
    )


def start_otel(log: list[str]) -> None:
    result = run(["docker", "start", "bench-otel"], check=False)
    if result.returncode == 0:
        note("otel: bench-otel started", log)
    else:
        note(f"warn: otel bench-otel did not start ({result.stderr.strip()})", log)


def _tool_version(cmd: list[str]) -> str:
    result = run(cmd, check=False)
    return (result.stdout.strip() or result.stderr.strip() or "unknown").splitlines()[0]


def write_snapshot(workdir: Path, arm: str, sha: str, env_count: int, log: list[str]) -> Path:
    short = sha[:12]
    snap_path = workdir / f"prep-{arm}-{short}.txt"
    lines = [
        f"prepare {arm} {datetime.now(UTC).isoformat()}",
        f"base SHA: {sha}",
        f"claude: {_tool_version(['claude', '--version'])}",
        f"python: {_tool_version(['python3', '--version'])}",
        f"ruff: {_tool_version(['uvx', 'ruff', '--version'])}",
        f".env files copied: {env_count}",
        "",
        *[f"== {line}" for line in log],
    ]
    snap_path.write_text("\n".join(lines) + "\n")
    return snap_path


def print_export_block(arm: str, cfg_dir: Path) -> None:
    print()
    print("=== export block ===")
    print(f"export CLAUDE_CONFIG_DIR={cfg_dir}")
    print("export CLAUDE_CODE_ENABLE_TELEMETRY=1 OTEL_METRICS_EXPORTER=otlp OTEL_LOGS_EXPORTER=otlp")
    print("export OTEL_EXPORTER_OTLP_PROTOCOL=grpc OTEL_EXPORTER_OTLP_ENDPOINT=http://localhost:4317")
    print("export OTEL_METRIC_EXPORT_INTERVAL=10000")
    print(f'export OTEL_RESOURCE_ATTRIBUTES="arm={arm},task=TBD,run=TBD"')


def cmd_prepare(args) -> int:
    log: list[str] = []
    arm = args.arm
    repo = str(Path(args.repo).expanduser().resolve())
    base_ref = getattr(args, "base_ref", None) or DEFAULT_BASE_REF
    workdir = Path(getattr(args, "workdir", None) or DEFAULT_WORKDIR).expanduser()
    force = bool(getattr(args, "force", False))

    try:
        doc = load_arm(arm)
        note(f"arm loaded: {ARMS_DIR / (arm + '.yaml')}", log)

        sha = resolve_base_sha(repo, base_ref, log)

        repo_name = Path(repo).name
        dest = workdir / repo_name / arm
        make_clone(repo, dest, sha, force, log)

        env_count = copy_env_files(repo, dest, log)

        cfg_dir = workdir / f"cfg-{arm}"
        cfg_dir.mkdir(parents=True, exist_ok=True)
        note(f"cfg dir: {cfg_dir}", log)

        apply_install_steps(arm, doc, repo, dest, cfg_dir, log)
        check_baseline_clean(arm, dest, log)

        auth_check(cfg_dir, log)
        start_otel(log)

        snap_path = write_snapshot(workdir, arm, sha, env_count, log)
        note(f"snapshot written: {snap_path}", log)

        print_export_block(arm, cfg_dir)
        return 0
    except PrepareError as exc:
        print(f"FAIL: {exc}", file=sys.stderr)
        return 1
