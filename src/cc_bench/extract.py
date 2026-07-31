"""Metrics extraction for one benchmark run: OTEL export + Claude Code transcript.

Two sources, two jobs (PLAN-tools.md 4 and 8):

* OTEL (the file exporter of the `bench-otel` collector) is AUTHORITATIVE for
  money and tokens. `claude_code.token.usage` / `claude_code.cost.usage` count
  every request of every subagent, which the main transcript does not
  (transcript output tokens undercount by roughly 2x).
* The transcript is authoritative for STRUCTURE: turns, real user prompts,
  tool calls, files touched, compactions, and wall/active time.

Time model (benchmark/comparison-runs.md): active time is the transcript span
minus every "end of assistant response -> next REAL user input" gap. Those gaps
are the agent waiting for a human. `tool_result` user-records are NOT user
input; slash commands typed by the human are.

stdlib only (+pyyaml elsewhere in the package). No pandas.
"""

from __future__ import annotations

import argparse
import csv
import json
import sys
from collections.abc import Iterable, Iterator
from datetime import datetime
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_CSV = REPO_ROOT / "results" / "results.csv"

# claude_code.token.usage `type` attribute values -> row field names.
TOKEN_TYPES = {
    "input": "input",
    "output": "output",
    "cacheRead": "cache_read",
    "cacheCreation": "cache_creation",
}
READ_TOOLS = ("Read", "Grep", "Glob", "NotebookRead")
EDIT_TOOLS = ("Edit", "Write", "NotebookEdit", "MultiEdit")
PATH_KEYS = ("file_path", "notebook_path", "path")

CSV_FIELDS = [
    "arm",
    "task",
    "run",
    "session_id",
    "model_list",
    "tokens_source",
    "tokens_input",
    "tokens_output",
    "tokens_cache_creation",
    "tokens_cache_read",
    "tokens_total",
    "cache_hit_rate",
    "cost_usd",
    "models_json",
    "turns",
    "user_prompts",
    "tool_calls",
    "tools_json",
    "files_read",
    "files_edited",
    "read_to_edit_ratio",
    "compactions",
    "api_errors",
    "skill_activated",
    "wall_clock_s",
    "user_wait_s",
    "active_time_s",
    "largest_pause_s",
    "fired_check_raw",
    "transcript",
    "otel",
]


def _warn(msg: str) -> None:
    print(f"warn: {msg}", file=sys.stderr)


# --------------------------------------------------------------------------
# generic helpers
# --------------------------------------------------------------------------


def _iter_json_lines(path: Path) -> Iterator[dict]:
    """Yield parsed JSON objects, skipping (and reporting) unparsable lines."""
    bad = 0
    try:
        handle = path.open(encoding="utf-8", errors="replace")
    except OSError as exc:
        _warn(f"cannot read {path}: {exc}")
        return
    with handle:
        for line in handle:
            line = line.strip()
            if not line:
                continue
            try:
                obj = json.loads(line)
            except json.JSONDecodeError:
                bad += 1
                continue
            if isinstance(obj, dict):
                yield obj
    if bad:
        _warn(f"{path.name}: skipped {bad} unparsable line(s)")


def _attrs(obj: dict | None) -> dict:
    """OTLP-JSON attribute list -> plain dict (values unwrapped)."""
    out: dict[str, Any] = {}
    for item in (obj or {}).get("attributes", []) or []:
        if not isinstance(item, dict) or "key" not in item:
            continue
        value = item.get("value") or {}
        if not isinstance(value, dict) or not value:
            out[item["key"]] = None
            continue
        out[item["key"]] = next(iter(value.values()))
    return out


def _ts(value: str | None) -> float | None:
    """ISO-8601 (with Z) -> epoch seconds."""
    if not value:
        return None
    try:
        return datetime.fromisoformat(value).timestamp()
    except (TypeError, ValueError):
        return None


def _nano(value: Any) -> float | None:
    try:
        return int(value) / 1e9
    except (TypeError, ValueError):
        return None


def _matches(resource_attrs: dict, arm: str | None, task: str | None, run: str | None) -> bool:
    for key, want in (("arm", arm), ("task", task), ("run", run)):
        if want is None:
            continue
        if str(resource_attrs.get(key, "")) != str(want):
            return False
    return True


def otel_files(paths: Iterable[str | Path]) -> list[Path]:
    """Expand file/dir arguments into a list of .jsonl exports."""
    out: list[Path] = []
    for raw in paths:
        path = Path(raw).expanduser()
        if path.is_dir():
            out.extend(sorted(p for p in path.rglob("*.jsonl") if p.is_file()))
        elif path.is_file():
            out.append(path)
        else:
            _warn(f"otel path not found: {path}")
    return out


# --------------------------------------------------------------------------
# OTEL
# --------------------------------------------------------------------------


WANTED_METRICS = ("claude_code.token.usage", "claude_code.cost.usage")


def _point_value(point: dict) -> float | None:
    value = point.get("asDouble")
    if value is None:
        value = point.get("asInt")
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _series_key(name: str, attrs: dict) -> tuple:
    return (name, tuple(sorted((k, str(v)) for k, v in attrs.items())))


def iter_metric_points(
    obj: dict, arm: str | None, task: str | None, run: str | None
) -> Iterator[tuple[str, bool, dict, float, float | None]]:
    """Yield (metric name, is_cumulative, attrs, value, epoch) for wanted metrics."""
    for res_metrics in obj.get("resourceMetrics", []) or []:
        if not _matches(_attrs(res_metrics.get("resource")), arm, task, run):
            continue
        for scope in res_metrics.get("scopeMetrics", []) or []:
            for metric in scope.get("metrics", []) or []:
                name = metric.get("name")
                if name not in WANTED_METRICS:
                    continue
                block = metric.get("sum") or metric.get("gauge") or {}
                cumulative = block.get("aggregationTemporality") == 2
                for point in block.get("dataPoints", []) or []:
                    value = _point_value(point)
                    if value is None:
                        _warn(f"{name}: datapoint without numeric value")
                        continue
                    yield name, cumulative, _attrs(point), value, _nano(
                        point.get("timeUnixNano")
                    )


def iter_log_events(
    obj: dict, arm: str | None, task: str | None, run: str | None
) -> Iterator[tuple[str, dict, float | None]]:
    """Yield (event name, attrs, epoch) for every matching log record."""
    for res_logs in obj.get("resourceLogs", []) or []:
        if not _matches(_attrs(res_logs.get("resource")), arm, task, run):
            continue
        for scope in res_logs.get("scopeLogs", []) or []:
            for record in scope.get("logRecords", []) or []:
                attrs = _attrs(record)
                name = attrs.get("event.name")
                if not name:
                    body = record.get("body") or {}
                    name = str(body.get("stringValue", "")).replace("claude_code.", "")
                stamp = _nano(record.get("timeUnixNano")) or _ts(attrs.get("event.timestamp"))
                yield str(name or "unknown"), attrs, stamp


def collect_otel(
    paths: Iterable[str | Path],
    arm: str | None = None,
    task: str | None = None,
    run: str | None = None,
    session_id: str | None = None,
) -> dict:
    """Aggregate metrics + log events for the runs matching the filter tags.

    Both temporality preferences are handled: DELTA (aggregationTemporality 1)
    datapoints are summed, CUMULATIVE (2) datapoints are max-ed per series, so
    the same code works whether the collector was configured for deltas (the
    original bench-otel setup) or cumulative sums (run.py's env).
    """
    tokens: dict[str, dict[str, float]] = {}
    cost_delta = 0.0
    cumulative_max: dict[tuple, float] = {}
    events: dict[str, int] = {}
    skills: list[str] = []
    sessions: dict[str, int] = {}
    window: list[float] = []
    seen_files = 0

    def _note(sid: str | None, stamp: float | None) -> None:
        if sid:
            sessions[sid] = sessions.get(sid, 0) + 1
        if stamp is not None:
            window.append(stamp)

    for path in otel_files(paths):
        seen_files += 1
        for obj in _iter_json_lines(path):
            for name, cumulative, attrs, value, stamp in iter_metric_points(
                obj, arm, task, run
            ):
                sid = attrs.get("session.id")
                if session_id and sid != session_id:
                    continue
                _note(sid, stamp)
                key = _series_key(name, attrs)
                if cumulative:
                    previous = cumulative_max.get(key, 0.0)
                    if value <= previous:
                        continue
                    cumulative_max[key] = value
                    increment = value - previous
                else:
                    increment = value
                if name == "claude_code.cost.usage":
                    cost_delta += increment
                    continue
                field = TOKEN_TYPES.get(str(attrs.get("type")))
                if field is None:
                    _warn(f"unknown token type {attrs.get('type')!r}")
                    continue
                bucket = tokens.setdefault(str(attrs.get("model") or "unknown"), {})
                bucket[field] = bucket.get(field, 0.0) + increment

            for name, attrs, stamp in iter_log_events(obj, arm, task, run):
                sid = attrs.get("session.id")
                if session_id and sid != session_id:
                    continue
                _note(sid, stamp)
                events[name] = events.get(name, 0) + 1
                if name == "skill_activated":
                    skill = attrs.get("skill.name") or attrs.get("skill_name")
                    if skill:
                        skills.append(str(skill))

    return {
        "files": seen_files,
        "tokens": {m: {k: int(v) for k, v in d.items()} for m, d in tokens.items()},
        "cost_usd": round(cost_delta, 6),
        "events": events,
        "skills": skills,
        "sessions": sessions,
        "window": (min(window), max(window)) if window else (None, None),
    }


# --------------------------------------------------------------------------
# transcript
# --------------------------------------------------------------------------


def is_real_user_input(record: dict) -> bool:
    """True for a human-typed prompt, False for tool results and meta records."""
    if record.get("type") != "user":
        return False
    if record.get("isMeta") or record.get("isSidechain"):
        return False
    content = (record.get("message") or {}).get("content")
    if isinstance(content, str):
        return bool(content.strip())
    if isinstance(content, list):
        blocks = [b for b in content if isinstance(b, dict)]
        if any(b.get("type") == "tool_result" for b in blocks):
            return False
        return any(b.get("type") in ("text", "image") for b in blocks)
    return False


def _is_compaction(record: dict) -> bool:
    if record.get("isCompactSummary") or record.get("compactMetadata"):
        return True
    if record.get("subtype") in ("compact_boundary", "compact"):
        return True
    content = (record.get("message") or {}).get("content")
    return isinstance(content, str) and content.startswith(
        "This session is being continued from a previous conversation"
    )


def timings(records: list[dict]) -> dict:
    """Span, waiting-for-human time, active time (comparison-runs.md model)."""
    stamped = [(t, r) for r in records if (t := _ts(r.get("timestamp"))) is not None]
    stamped.sort(key=lambda pair: pair[0])
    if not stamped:
        return {
            "wall_clock_s": 0.0,
            "user_wait_s": 0.0,
            "active_time_s": 0.0,
            "largest_pause_s": 0.0,
            "pauses": [],
            "start": None,
            "end": None,
        }
    start, end = stamped[0][0], stamped[-1][0]
    span = end - start
    pauses: list[tuple[float, str]] = []
    last_assistant: float | None = None
    for moment, record in stamped:
        if record.get("type") == "assistant":
            last_assistant = moment
        elif is_real_user_input(record) and last_assistant is not None:
            pauses.append((moment - last_assistant, record.get("timestamp") or ""))
    wait = sum(p[0] for p in pauses)
    return {
        "wall_clock_s": round(span, 3),
        "user_wait_s": round(wait, 3),
        "active_time_s": round(span - wait, 3),
        "largest_pause_s": round(max((p[0] for p in pauses), default=0.0), 3),
        "pauses": pauses,
        "start": start,
        "end": end,
    }


def parse_transcript(path: Path) -> dict:
    """Structure metrics from one main-session transcript file."""
    records: list[dict] = []
    turns = 0
    user_prompts = 0
    compactions = 0
    api_errors = 0
    tools: dict[str, int] = {}
    files_read: set[str] = set()
    files_edited: set[str] = set()
    models: set[str] = set()
    session_id = None
    usage = {"input": 0, "output": 0, "cache_creation": 0, "cache_read": 0}
    usage_models: dict[str, dict[str, int]] = {}
    # One assistant message is split across several records (one per content
    # block), each repeating the same `usage` -- count each message once.
    counted_messages: set[str] = set()

    for record in _iter_json_lines(path):
        records.append(record)
        session_id = session_id or record.get("sessionId") or record.get("session_id")
        if _is_compaction(record):
            compactions += 1
        rtype = record.get("type")
        if rtype == "user":
            if is_real_user_input(record):
                user_prompts += 1
            continue
        if rtype != "assistant":
            continue
        turns += 1
        message = record.get("message") or {}
        model = message.get("model")
        if model:
            models.add(str(model))
        if record.get("isApiErrorMessage"):
            api_errors += 1
        used = message.get("usage") or {}
        message_key = str(message.get("id") or record.get("uuid") or "")
        if used and message_key not in counted_messages:
            counted_messages.add(message_key)
            per_model = usage_models.setdefault(str(model or "unknown"), dict.fromkeys(usage, 0))
            for field, key in (
                ("input", "input_tokens"),
                ("output", "output_tokens"),
                ("cache_creation", "cache_creation_input_tokens"),
                ("cache_read", "cache_read_input_tokens"),
            ):
                try:
                    amount = int(used.get(key) or 0)
                except (TypeError, ValueError):
                    amount = 0
                usage[field] += amount
                per_model[field] += amount
        for block in message.get("content") or []:
            if not isinstance(block, dict) or block.get("type") != "tool_use":
                continue
            name = str(block.get("name") or "unknown")
            tools[name] = tools.get(name, 0) + 1
            payload = block.get("input")
            if not isinstance(payload, dict):
                continue
            paths = [str(payload[k]) for k in PATH_KEYS if payload.get(k)]
            if not paths:
                continue
            if name in READ_TOOLS:
                files_read.update(paths)
            elif name in EDIT_TOOLS:
                files_edited.update(paths)

    time_info = timings(records)
    return {
        "path": path,
        "session_id": session_id,
        "turns": turns,
        "user_prompts": user_prompts,
        "tool_calls": sum(tools.values()),
        "tools": dict(sorted(tools.items(), key=lambda kv: (-kv[1], kv[0]))),
        "files_read": len(files_read),
        "files_edited": len(files_edited),
        "compactions": compactions,
        "api_errors": api_errors,
        "models": sorted(models),
        "usage": usage,
        "usage_models": usage_models,
        **time_info,
    }


def session_transcripts(cfg_dir: Path) -> list[Path]:
    """Main-session transcripts under a CLAUDE_CONFIG_DIR (subagents excluded)."""
    projects = cfg_dir / "projects" if (cfg_dir / "projects").is_dir() else cfg_dir
    return [
        p
        for p in sorted(projects.rglob("*.jsonl"))
        if p.is_file() and "subagents" not in p.parts
    ]


def pick_transcript(
    cfg_dir: str | Path,
    session_ids: Iterable[str] = (),
    window: tuple[float | None, float | None] = (None, None),
) -> Path | None:
    """Pick the session for this run: OTEL session.id first, else time overlap.

    Fallback order:
      1. a transcript whose filename is one of the OTEL `session.id` values
         (largest one wins if several -- the main session, not a resumed stub);
      2. the newest transcript whose span overlaps the OTEL time window;
      3. the newest transcript in the directory.
    """
    cfg_dir = Path(cfg_dir).expanduser()
    candidates = session_transcripts(cfg_dir)
    if not candidates:
        _warn(f"no transcripts under {cfg_dir}")
        return None

    wanted = set(session_ids)
    by_id = [p for p in candidates if p.stem in wanted]
    if by_id:
        return max(by_id, key=lambda p: p.stat().st_size)

    start, end = window
    if start is not None and end is not None:
        overlapping = []
        for path in candidates:
            info = timings(list(_iter_json_lines(path)))
            if info["start"] is None:
                continue
            if info["start"] <= end and info["end"] >= start:
                overlapping.append((info["start"], path))
        if overlapping:
            return max(overlapping)[1]
        _warn("no transcript overlaps the OTEL window; falling back to newest")

    return max(candidates, key=lambda p: p.stat().st_mtime)


# --------------------------------------------------------------------------
# row assembly
# --------------------------------------------------------------------------


def _round(value: float | None, digits: int = 4) -> float | None:
    return None if value is None else round(value, digits)


def extract_run(
    otel: Iterable[str | Path] | str | Path | None = None,
    arm: str | None = None,
    task: str | None = None,
    run: str | None = None,
    transcript: str | Path | None = None,
    cfg_dir: str | Path | None = None,
    session_id: str | None = None,
) -> dict:
    """Build one metrics row from an OTEL export and a session transcript.

    Either `transcript` (explicit file) or `cfg_dir` (CLAUDE_CONFIG_DIR whose
    newest matching session is picked) may be given; both may be omitted, in
    which case only the OTEL half of the row is filled.
    """
    if otel is None:
        otel_paths: list[str | Path] = []
    elif isinstance(otel, (str, Path)):
        otel_paths = [otel]
    else:
        otel_paths = list(otel)

    otel_data = (
        collect_otel(otel_paths, arm=arm, task=task, run=run, session_id=session_id)
        if otel_paths
        else None
    )
    if otel_data is not None and not otel_data["tokens"] and not otel_data["events"]:
        _warn("OTEL export matched no data for these filters")
        otel_data = None

    path = Path(transcript).expanduser() if transcript else None
    if path is None and cfg_dir:
        path = pick_transcript(
            cfg_dir,
            session_ids=(otel_data or {}).get("sessions", {}),
            window=(otel_data or {}).get("window", (None, None)),
        )
    tx = parse_transcript(path) if path and path.is_file() else None
    if path and not tx:
        _warn(f"transcript not readable: {path}")

    models_tokens: dict[str, dict[str, int]] = {}
    tokens_source = "none"
    cost = None
    if otel_data and otel_data["tokens"]:
        models_tokens = otel_data["tokens"]
        tokens_source = "otel"
        cost = otel_data["cost_usd"]
    elif tx:
        models_tokens = {m: dict(v) for m, v in tx["usage_models"].items()}
        tokens_source = "transcript"
        _warn("no OTEL tokens; falling back to transcript usage (output undercounts ~2x)")

    totals = dict.fromkeys(("input", "output", "cache_creation", "cache_read"), 0)
    for bucket in models_tokens.values():
        for field in totals:
            totals[field] += int(bucket.get(field, 0) or 0)
    cache_denominator = totals["input"] + totals["cache_read"]
    cache_hit_rate = totals["cache_read"] / cache_denominator if cache_denominator else None

    models = sorted(set(models_tokens) | set(tx["models"] if tx else []))
    events = (otel_data or {}).get("events", {})
    skills = list(dict.fromkeys((otel_data or {}).get("skills", [])))

    compactions = (tx or {}).get("compactions", 0)
    if "compaction" in events:
        compactions = max(compactions, events["compaction"])
    api_errors = events.get("api_error", (tx or {}).get("api_errors", 0))

    reads = (tx or {}).get("files_read", 0)
    edits = (tx or {}).get("files_edited", 0)

    sessions = (otel_data or {}).get("sessions", {})
    resolved_session = (
        session_id
        or (tx or {}).get("session_id")
        or (max(sessions, key=lambda s: sessions[s]) if sessions else None)
    )

    row = {
        "arm": arm,
        "task": task,
        "run": run,
        "session_id": resolved_session,
        "model_list": ";".join(models),
        "tokens_source": tokens_source,
        "tokens_input": totals["input"],
        "tokens_output": totals["output"],
        "tokens_cache_creation": totals["cache_creation"],
        "tokens_cache_read": totals["cache_read"],
        "tokens_total": sum(totals.values()),
        "cache_hit_rate": _round(cache_hit_rate),
        "cost_usd": _round(cost, 6),
        "models_json": json.dumps(models_tokens, sort_keys=True),
        "turns": (tx or {}).get("turns"),
        "user_prompts": (tx or {}).get("user_prompts"),
        "tool_calls": (tx or {}).get("tool_calls"),
        "tools_json": json.dumps((tx or {}).get("tools", {})),
        "files_read": reads if tx else None,
        "files_edited": edits if tx else None,
        "read_to_edit_ratio": _round(reads / edits, 3) if tx and edits else None,
        "compactions": compactions,
        "api_errors": api_errors,
        "skill_activated": ";".join(skills),
        "wall_clock_s": (tx or {}).get("wall_clock_s"),
        "user_wait_s": (tx or {}).get("user_wait_s"),
        "active_time_s": (tx or {}).get("active_time_s"),
        "largest_pause_s": (tx or {}).get("largest_pause_s"),
        "fired_check_raw": None,  # filled by gates/smoke, not here
        "transcript": str(tx["path"]) if tx else "",
        "otel": ";".join(str(p) for p in otel_paths),
    }
    return row


def append_csv(row: dict, csv_path: str | Path = DEFAULT_CSV) -> Path:
    """Append one row, writing the header if the file is new/empty."""
    csv_path = Path(csv_path).expanduser()
    csv_path.parent.mkdir(parents=True, exist_ok=True)
    is_new = not csv_path.exists() or csv_path.stat().st_size == 0
    with csv_path.open("a", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=CSV_FIELDS, extrasaction="ignore")
        if is_new:
            writer.writeheader()
        writer.writerow({field: row.get(field) for field in CSV_FIELDS})
    return csv_path


def format_summary(row: dict) -> str:
    """Human-readable two-column summary of a row."""

    def minutes(seconds: Any) -> str:
        return "-" if seconds in (None, "") else f"{float(seconds) / 60:.1f} min"

    def number(value: Any) -> str:
        if value is None or value == "":
            return "-"
        return f"{value:,}" if isinstance(value, int) else str(value)

    tools = json.loads(row.get("tools_json") or "{}")
    top_tools = ", ".join(f"{k}={v}" for k, v in list(tools.items())[:6]) or "-"
    rate = row.get("cache_hit_rate")
    cost = row.get("cost_usd")

    def pair(first: str, second: str) -> str:
        return f"{number(row.get(first))} / {number(row.get(second))}"

    lines = [
        ("arm / task / run", f"{row.get('arm')} / {row.get('task')} / {row.get('run')}"),
        ("session", row.get("session_id") or "-"),
        ("models", row.get("model_list") or "-"),
        ("tokens source", row.get("tokens_source")),
        ("tokens in / out", pair("tokens_input", "tokens_output")),
        ("cache create / read", pair("tokens_cache_creation", "tokens_cache_read")),
        ("tokens total", number(row.get("tokens_total"))),
        ("cache hit rate", "-" if rate is None else f"{rate * 100:.2f}%"),
        ("cost", "-" if cost is None else f"${cost:.2f}"),
        ("turns / user prompts", pair("turns", "user_prompts")),
        ("tool calls", number(row.get("tool_calls"))),
        ("top tools", top_tools),
        (
            "files read / edited",
            f"{pair('files_read', 'files_edited')} (r/e {row.get('read_to_edit_ratio')})",
        ),
        ("compactions / api errors", f"{row.get('compactions')} / {row.get('api_errors')}"),
        ("skills", row.get("skill_activated") or "-"),
        ("span (transcript)", minutes(row.get("wall_clock_s"))),
        ("user wait", minutes(row.get("user_wait_s"))),
        ("ACTIVE time", minutes(row.get("active_time_s"))),
        ("largest pause", minutes(row.get("largest_pause_s"))),
    ]
    width = max(len(label) for label, _ in lines)
    body = "\n".join(f"  {label.ljust(width)}  {value}" for label, value in lines)
    return f"cc-bench extract\n{body}"


# --------------------------------------------------------------------------
# CLI
# --------------------------------------------------------------------------


def add_arguments(parser: argparse.ArgumentParser) -> argparse.ArgumentParser:
    """Wire `bench extract` flags onto a parser (called by cli.py)."""
    parser.add_argument("--otel", action="append", default=None, help="OTEL export file or dir")
    parser.add_argument("--arm")
    parser.add_argument("--task")
    parser.add_argument("--run")
    parser.add_argument("--session-id", dest="session_id")
    parser.add_argument("--transcript", help="session .jsonl (skips session picking)")
    parser.add_argument("--cfg", help="CLAUDE_CONFIG_DIR to pick the session from")
    parser.add_argument("--csv", default=str(DEFAULT_CSV), help=f"default: {DEFAULT_CSV}")
    parser.add_argument("--no-csv", action="store_true", help="print only, do not append")
    return parser


def cmd_extract(args: argparse.Namespace) -> int:
    otel = getattr(args, "otel", None)
    if not otel and not getattr(args, "transcript", None) and not getattr(args, "cfg", None):
        print("extract: need at least one of --otel, --transcript, --cfg", file=sys.stderr)
        return 2
    row = extract_run(
        otel=otel,
        arm=getattr(args, "arm", None),
        task=getattr(args, "task", None),
        run=getattr(args, "run", None),
        transcript=getattr(args, "transcript", None),
        cfg_dir=getattr(args, "cfg", None),
        session_id=getattr(args, "session_id", None),
    )
    print(format_summary(row))
    if not getattr(args, "no_csv", False):
        written = append_csv(row, getattr(args, "csv", None) or DEFAULT_CSV)
        print(f"  -> appended to {written}")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = add_arguments(argparse.ArgumentParser(prog="cc-bench extract"))
    return cmd_extract(parser.parse_args(argv))


if __name__ == "__main__":
    raise SystemExit(main())
