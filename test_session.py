"""Check the bits of `bench session` that can silently pick the wrong thing.

Plain asserts, no pytest: `uv run python test_session.py`.
"""

from __future__ import annotations

import json
import os
import pathlib
import tempfile

from cc_bench.cli import with_default_command
from cc_bench.session import _next_run, _pick_transcript, _preflight, _slug


def test_slug_matches_claude_code_layout() -> None:
    # observed on disk: underscores collapse to '-' just like slashes
    assert _slug(pathlib.Path("/home/octrow/cybernet/conversation_flow")) == (
        "-home-octrow-cybernet-conversation-flow"
    )
    assert _slug(pathlib.Path("/home/octrow/cybernet/cc-bench")) == "-home-octrow-cybernet-cc-bench"


def test_next_run_counts_only_matching_arm_and_task() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        csv_path = pathlib.Path(tmp) / "results.csv"
        assert _next_run(csv_path, "live", "CF-1") == "r1"  # no file yet
        csv_path.write_text("arm,task,run\nlive,CF-1,r1\nlive,CF-2,r1\nbaseline,CF-1,r1\n")
        assert _next_run(csv_path, "live", "CF-1") == "r2"
        assert _next_run(csv_path, "live", "CF-9") == "r1"


def test_pick_transcript_scopes_to_repo_and_launch_time() -> None:
    repo = pathlib.Path("/repo/x")
    with tempfile.TemporaryDirectory() as tmp:
        cfg = pathlib.Path(tmp) / "cfg"
        project = cfg / "projects" / _slug(repo)
        project.mkdir(parents=True)
        (cfg / "projects" / "-other").mkdir()

        old = project / "old.jsonl"
        new = project / "new.jsonl"
        other = cfg / "projects" / "-other" / "z.jsonl"
        for path, mtime in ((old, 1000), (new, 2000), (other, 3000)):
            path.write_text("{}")
            os.utime(path, (mtime, mtime))

        assert _pick_transcript(cfg, repo, None) == new  # newest for this repo, not `other`
        assert _pick_transcript(cfg, repo, 1500) == new
        assert _pick_transcript(cfg, repo, 2500) is None  # nothing since the launch

        sub = project / "subagents" / "s.jsonl"  # subagent runs are not the main session
        sub.parent.mkdir()
        sub.write_text("{}")
        os.utime(sub, (9000, 9000))
        assert _pick_transcript(cfg, repo, None) == new


def test_preflight_flags_telemetry_off() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        cfg = pathlib.Path(tmp)
        (cfg / "settings.json").write_text(json.dumps({"env": {}}))
        assert any("telemetry is off" in p for p in _preflight(cfg))

        (cfg / "settings.json").write_text(json.dumps({"env": {"CLAUDE_CODE_ENABLE_TELEMETRY": "1"}}))
        assert not any("telemetry is off" in p for p in _preflight(cfg))


def test_bare_bench_means_session() -> None:
    assert with_default_command([]) == ["session"]
    assert with_default_command(["--task", "CF-1"]) == ["session", "--task", "CF-1"]
    assert with_default_command(["report"]) == ["report"]  # real subcommands untouched
    assert with_default_command(["--help"]) == ["--help"]  # top-level help, not session's


if __name__ == "__main__":
    for name, fn in sorted(globals().items()):
        if name.startswith("test_"):
            fn()
            print(f"ok  {name}")
    print("test_session: all passed")
