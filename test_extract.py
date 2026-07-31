"""Check the extract/report/prepare paths the audit refactor touched.

Plain asserts, no pytest: `uv run python test_extract.py`.
ponytail: one file covering the whole pipeline; split per-module if it grows.
"""

from __future__ import annotations

import json
import pathlib
import tempfile

from cc_bench import cli, extract, prepare, report

TRANSCRIPT = [
    {"type": "user", "timestamp": "2026-01-01T00:00:00Z", "sessionId": "S", "message": {"content": "go"}},
    {
        "type": "assistant",
        "timestamp": "2026-01-01T00:00:10Z",
        "message": {
            "id": "m1",
            "model": "opus",
            "usage": {"input_tokens": 10, "output_tokens": 5, "cache_read_input_tokens": 7},
            "content": [
                {"type": "tool_use", "name": "Read", "input": {"file_path": "a.py"}},
                {"type": "tool_use", "name": "Edit", "input": {"file_path": "b.py"}},
            ],
        },
    },
    # same message id, second content-block record: usage must NOT be double-counted
    {"type": "assistant", "timestamp": "2026-01-01T00:00:11Z",
     "message": {"id": "m1", "model": "opus", "usage": {"input_tokens": 10}, "content": []}},
    # tool_result is not real user input -> no pause, not a prompt
    {"type": "user", "timestamp": "2026-01-01T00:00:12Z",
     "message": {"content": [{"type": "tool_result", "content": "ok"}]}},
    {"type": "user", "timestamp": "2026-01-01T00:05:12Z", "message": {"content": "again"}},
    {
        "type": "assistant",
        "timestamp": "2026-01-01T00:05:20Z",
        "message": {"id": "m2", "model": "opus", "usage": {"output_tokens": 3},
                    "content": [{"type": "tool_use", "name": "Read", "input": {"file_path": "a.py"}},
                                {"type": "tool_use", "name": "Grep", "input": {"path": "c"}}]},
    },
    "{ broken json",  # unparsable lines are skipped, not fatal
]


def _dp(value: float, **attrs: object) -> dict:
    """One OTLP datapoint; `bare` has an empty value dict on purpose."""
    listed = [{"key": k, "value": {"stringValue": str(v)}} for k, v in attrs.items()]
    return {"asDouble": value, "attributes": listed + [{"key": "bare", "value": {}}]}


OTEL = {
    "resourceMetrics": [{
        "resource": {"attributes": [{"key": "arm", "value": {"stringValue": "rtk"}}]},
        "scopeMetrics": [{"metrics": [
            {"name": "claude_code.cost.usage",
             "sum": {"aggregationTemporality": 1,
                     "dataPoints": [_dp(0.25, **{"session.id": "sess"})]}},
            {"name": "claude_code.token.usage",
             "sum": {"aggregationTemporality": 2, "dataPoints": [
                 _dp(100, **{"session.id": "sess", "type": "input", "model": "opus"}),
                 _dp(140, **{"session.id": "sess", "type": "input", "model": "opus"}),
                 _dp(60, **{"session.id": "sess", "type": "cacheRead", "model": "opus"}),
             ]}},
        ]}],
    }]
}


def main() -> None:
    tmp = pathlib.Path(tempfile.mkdtemp())
    tx = tmp / "sess.jsonl"
    tx.write_text("\n".join(r if isinstance(r, str) else json.dumps(r) for r in TRANSCRIPT))

    t = extract.parse_transcript(tx)
    assert (t["turns"], t["user_prompts"], t["tool_calls"]) == (3, 2, 4), t
    assert t["usage_models"]["opus"] == {"input": 10, "output": 8, "cache_creation": 0, "cache_read": 7}
    assert list(t["tools"].items()) == [("Read", 2), ("Edit", 1), ("Grep", 1)], t["tools"]
    assert (t["files_read"], t["files_edited"]) == (2, 1), t
    # 320s span, 301s waiting for the human between 00:00:11 and 00:05:12
    assert (t["wall_clock_s"], t["user_wait_s"], t["active_time_s"]) == (320.0, 301.0, 19.0), t

    otel = tmp / "o.jsonl"
    otel.write_text(json.dumps(OTEL))
    o = extract.collect_otel([otel], arm="rtk")
    assert (o["cost_usd"], o["sessions"]) == (0.25, {"sess": 4}), o
    assert o["tokens"] == {"opus": {"input": 140, "cache_read": 60}}, o["tokens"]  # cumulative -> max
    assert extract.collect_otel([otel], arm="other")["cost_usd"] == 0.0  # resource-attr filter
    assert extract.pick_transcript(tmp, session_ids=["sess"]) == tx

    row = extract.extract_run(otel=[otel], arm="rtk", cfg_dir=tmp)
    assert set(row) == set(extract.CSV_FIELDS), set(row) ^ set(extract.CSV_FIELDS)
    assert row["tokens_source"] == "otel" and row["tokens_input"] == 140 and row["cost_usd"] == 0.25
    assert row["session_id"] == "S" and row["cache_hit_rate"] == 0.3 and row["active_time_s"] == 19.0
    assert row["fired_check"] == ""  # not measured unless the smoke run says so
    extract.append_csv(row, tmp / "r.csv")

    def _report(csv_name: str, md_name: str) -> str:
        args = cli.build_parser().parse_args(
            ["report", "--csv", str(tmp / csv_name), "--out", str(tmp / md_name), "--arms-dir", "arms"]
        )
        assert report.cmd_report(args) == 0
        return (tmp / md_name).read_text()

    md = _report("r.csv", "rep.md")
    assert "## Verdicts" in md
    # unmeasured fired-check must not be scored as a failure
    assert "Fired-check:" not in md and "fired-check failed" not in md

    # a real verdict does reach the report, and a false one raises the bullet
    for verdict, expect in (("true", "Fired-check: 100% of runs"), ("false", "Fired-check: 0% of runs")):
        csv_name, md_name = f"r-{verdict}.csv", f"rep-{verdict}.md"
        extract.append_csv(
            extract.extract_run(otel=[otel], arm="rtk", cfg_dir=tmp, fired_check=verdict),
            tmp / csv_name,
        )
        md = _report(csv_name, md_name)
        assert expect in md, (verdict, md)
    assert "fired-check failed on 1/1 runs" in md  # md is the "false" report

    repo = tmp / "repo"
    (repo / "sub").mkdir(parents=True)
    (repo / ".git").mkdir()
    (repo / ".env").write_text("A=1")
    (repo / "sub" / ".env").write_text("B=2")
    (repo / ".git" / ".env").write_text("X=9")  # must be skipped
    assert prepare.copy_env_files(str(repo), tmp / "clone", []) == 2
    assert (tmp / "clone" / "sub" / ".env").exists() and not (tmp / "clone" / ".git").exists()

    print("ok")


if __name__ == "__main__":
    main()
