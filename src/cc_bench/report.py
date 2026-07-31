"""Aggregate results.csv into the median+IQR markdown report.

Row shape assumed (flat, one row per arm/task/run - see extract.py, which
owns results.csv's schema): at minimum `task`, `arm`, `run`. Any of the
following numeric columns are aggregated when present, so the report
degrades gracefully if a column hasn't landed yet: cost_usd, active_time_s,
wall_s, input_tokens, output_tokens, cache_creation_tokens,
cache_read_tokens, tool_calls, turns. `tools_json` / `models_json` are
JSON-encoded per-row breakdowns (tool name -> count / model name -> tokens);
`fired_check` and `skill_activated` are truthy strings ("true"/"1"/...) -
an empty `fired_check` means the smoke run did not measure it for that row,
so it is skipped rather than scored as a failure;
`compactions` is an int count; `success` is 0/1.

Design note: wall_s is a reference column only. Per ADR-0005/PLAN-tools.md
§5.6, active_time_s is the metric that ever appears in a verdict - wall
clock is not, because it is contaminated by user-wait time in interactive
sessions (not applicable here since runs are headless, but the rule is
kept uniform with the rest of the toolkit's reports).
"""

from __future__ import annotations

import argparse
import csv
import json
import statistics
from collections import Counter, defaultdict
from datetime import UTC, datetime
from pathlib import Path

from cc_bench import arms as arms_mod

DEFAULT_CSV = "results/results.csv"
DEFAULT_OUT = "results/report.md"
BASELINE_ARM = "baseline"

METRIC_COLUMNS = [
    "cost_usd",
    "active_time_s",
    "wall_s",
    "input_tokens",
    "output_tokens",
    "cache_creation_tokens",
    "cache_read_tokens",
    "tool_calls",
    "turns",
]
TRUTHY = {"true", "1", "yes", "t"}


def _read_rows(csv_path: Path) -> list[dict]:
    if not csv_path.is_file():
        return []
    with csv_path.open(newline="") as f:
        return list(csv.DictReader(f))


def _to_float(value: str | None) -> float | None:
    if value in (None, ""):
        return None
    try:
        return float(value)
    except ValueError:
        return None


def _is_truthy(value: str | None) -> bool:
    return (value or "").strip().lower() in TRUTHY


def _stats(values: list[float]) -> dict:
    """median + IQR when n>=3, raw values with an n<3 flag otherwise."""
    if len(values) >= 3:
        med = statistics.median(values)
        q1, _, q3 = statistics.quantiles(values, n=4)
        return {"n": len(values), "median": med, "iqr_low": q1, "iqr_high": q3, "flagged": False}
    return {"n": len(values), "values": values, "flagged": True}


def _fmt_stat(stat: dict) -> str:
    if stat["flagged"]:
        return f"{', '.join(f'{v:.2f}' for v in stat['values'])} (n={stat['n']}<3)"
    return f"{stat['median']:.2f} (IQR {stat['iqr_low']:.2f}-{stat['iqr_high']:.2f})"


def _delta_pct(value: dict, baseline: dict) -> str:
    if value["flagged"] or baseline["flagged"]:
        return "n/a"
    base_med = baseline["median"]
    if base_med == 0:
        return "n/a"
    pct = (value["median"] - base_med) / base_med * 100
    return f"{pct:+.1f}%"


def _iqr_overlaps(a: dict, b: dict) -> bool:
    if a["flagged"] or b["flagged"]:
        return True  # can't tell -> treat conservatively as "might overlap"
    return a["iqr_low"] <= b["iqr_high"] and b["iqr_low"] <= a["iqr_high"]


def _group(rows: list[dict]) -> dict[tuple[str, str], list[dict]]:
    grouped: dict[tuple[str, str], list[dict]] = defaultdict(list)
    for row in rows:
        grouped[(row.get("task", ""), row.get("arm", ""))].append(row)
    return grouped


def _metric_values(rows: list[dict], column: str) -> list[float]:
    return [v for v in (_to_float(r.get(column)) for r in rows) if v is not None]


def _tool_counts(rows: list[dict]) -> Counter:
    counts: Counter = Counter()
    for row in rows:
        raw = row.get("tools_json")
        if not raw:
            continue
        try:
            parsed = json.loads(raw)
        except (json.JSONDecodeError, TypeError):
            continue
        if isinstance(parsed, dict):
            for tool, n in parsed.items():
                counts[tool] += int(n)
    return counts


def _read_edit_ratio(tool_counts: Counter) -> str:
    reads = sum(n for tool, n in tool_counts.items() if tool.lower() in ("read", "grep", "glob"))
    edits = sum(n for tool, n in tool_counts.items() if tool.lower() in ("edit", "write", "multiedit"))
    if edits == 0:
        return f"{reads}:0 (no edits)"
    return f"{reads / edits:.2f}:1"


def _primary_metrics(arms_dir: Path) -> dict[str, str]:
    parsed, _errors = arms_mod.load_all(arms_dir)
    return {a.name: a.primary_metric for a in parsed}


def _header(csv_path: Path, rows: list[dict]) -> list[str]:
    tasks = sorted({r.get("task", "") for r in rows if r.get("task")})
    arms_seen = sorted({r.get("arm", "") for r in rows if r.get("arm")})
    date = datetime.now(UTC).strftime("%Y-%m-%d %H:%M UTC")
    return [
        "# cc-bench report",
        "",
        f"Generated: {date}",
        f"Source: `{csv_path}` ({len(rows)} rows)",
        f"Arms covered: {', '.join(arms_seen) or 'none'}",
        f"Tasks covered: {', '.join(tasks) or 'none'}",
        "",
    ]


def _per_task_tables(grouped: dict[tuple[str, str], list[dict]]) -> list[str]:
    lines = ["## Per-task metrics", ""]
    tasks = sorted({task for task, _arm in grouped})
    for task in tasks:
        lines.append(f"### {task}")
        lines.append("")
        arm_names = sorted({arm for (t, arm) in grouped if t == task})
        baseline_rows = grouped.get((task, BASELINE_ARM), [])

        cols = [c for c in METRIC_COLUMNS if any(_metric_values(grouped[(task, arm)], c) for arm in arm_names)]
        if not cols:
            lines.append("_no numeric metric columns present yet_")
            lines.append("")
            continue

        header = "| arm | n | " + " | ".join(cols) + " | Δ% vs baseline (primary col) |"
        sep = "|---|---|" + "---|" * len(cols) + "---|"
        lines.append(header)
        lines.append(sep)

        baseline_stats = {c: _stats(_metric_values(baseline_rows, c)) for c in cols}
        for arm in arm_names:
            arm_rows = grouped[(task, arm)]
            arm_stats = {c: _stats(_metric_values(arm_rows, c)) for c in cols}
            cells = [_fmt_stat(arm_stats[c]) if arm_stats[c].get("n") else "-" for c in cols]
            delta = "-"
            if arm != BASELINE_ARM and cols:
                first_col = cols[0]
                if arm_stats[first_col].get("n") and baseline_stats[first_col].get("n"):
                    delta = _delta_pct(arm_stats[first_col], baseline_stats[first_col])
            lines.append(f"| {arm} | {len(arm_rows)} | " + " | ".join(cells) + f" | {delta} |")
        lines.append("")
    return lines


def _by_arm(grouped: dict[tuple[str, str], list[dict]]) -> dict[str, list[dict]]:
    """Collapse the (task, arm) grouping down to arm -> all its rows."""
    out: dict[str, list[dict]] = defaultdict(list)
    for (_task, arm), rows in grouped.items():
        out[arm].extend(rows)
    return out


def _tool_usage_section(grouped: dict[tuple[str, str], list[dict]]) -> list[str]:
    lines = ["## Tool usage", ""]
    by_arm = _by_arm(grouped)

    for arm in sorted(by_arm):
        rows = by_arm[arm]
        tool_counts = _tool_counts(rows)
        top = tool_counts.most_common(5)
        compactions = sum(int(_to_float(r.get("compactions")) or 0) for r in rows)
        fired_flags = [v for r in rows if (v := r.get("fired_check"))]
        skill_flags = [r.get("skill_activated") for r in rows if "skill_activated" in r]

        lines.append(f"### {arm}")
        lines.append("")
        if top:
            lines.append("Top tools: " + ", ".join(f"{t} ({n})" for t, n in top))
        else:
            lines.append("Top tools: no tools_json data")
        lines.append(f"Read:Edit ratio: {_read_edit_ratio(tool_counts)}")
        lines.append(f"Compactions (total): {compactions}")
        if fired_flags:
            fired_rate = sum(_is_truthy(f) for f in fired_flags) / len(fired_flags)
            lines.append(f"Fired-check: {fired_rate:.0%} of runs ({len(fired_flags)} runs)")
        if skill_flags:
            skill_rate = sum(_is_truthy(f) for f in skill_flags) / len(skill_flags)
            lines.append(f"skill_activated: {skill_rate:.0%} of runs ({len(skill_flags)} runs)")
        lines.append("")
    return lines


def _recommendations_section(grouped: dict[tuple[str, str], list[dict]]) -> list[str]:
    """Mandatory per ADR-0005: a TODO scaffold, never empty, always renders.

    Bullets are data-driven stubs - the conductor interprets them, this
    function does not attempt to draw conclusions on its own.
    """
    lines = ["## Recommendations for the measured tool", "", "_Scaffold for conductor interpretation - not conclusions._", ""]
    bullets: list[str] = []

    for arm, rows in sorted(_by_arm(grouped).items()):
        if arm == BASELINE_ARM:
            continue
        fired_flags = [v for r in rows if (v := r.get("fired_check"))]
        if fired_flags and not all(_is_truthy(f) for f in fired_flags):
            fail_n = sum(not _is_truthy(f) for f in fired_flags)
            bullets.append(f"- **{arm}**: fired-check failed on {fail_n}/{len(fired_flags)} runs - TODO conductor: dead arm or install bug?")

        skill_flags = [r.get("skill_activated") for r in rows if "skill_activated" in r]
        if skill_flags and not any(_is_truthy(f) for f in skill_flags):
            bullets.append(f"- **{arm}**: skill_activated never true across {len(skill_flags)} runs - TODO conductor: is the trigger wired up?")

        cost_values = _metric_values(rows, "cost_usd")
        success_values = _metric_values(rows, "success")
        if cost_values and success_values and statistics.fmean(success_values) < 1.0 and statistics.fmean(cost_values) > 0:
            bullets.append(
                f"- **{arm}**: spending (median cost ${statistics.median(cost_values):.2f}) without full success "
                f"(success rate {statistics.fmean(success_values):.0%}) - TODO conductor: token burn without payoff?"
            )

        for (task, a), task_rows in grouped.items():
            if a != arm:
                continue
            baseline_rows = grouped.get((task, BASELINE_ARM), [])
            active = _stats(_metric_values(task_rows, "active_time_s"))
            base_active = _stats(_metric_values(baseline_rows, "active_time_s"))
            if active.get("n") and base_active.get("n") and _iqr_overlaps(active, base_active):
                bullets.append(f"- **{arm}/{task}**: active_time_s IQR overlaps baseline - TODO conductor: is this a real effect or noise?")

    if not bullets:
        bullets.append("- No automatic flags raised from the current data - TODO conductor: sanity-check this is expected (enough runs? fired-check columns populated?).")

    lines.extend(bullets)
    lines.append("")
    return lines


def _verdicts_section(grouped: dict[tuple[str, str], list[dict]], primary_metrics: dict[str, str]) -> list[str]:
    lines = ["## Verdicts", "", "_Auto-flagged \"no-signal\" when primary-metric IQR overlaps baseline; otherwise TBD conductor._", ""]
    by_arm = _by_arm(grouped)

    for arm in sorted(by_arm):
        if arm == BASELINE_ARM:
            continue
        rows = by_arm[arm]
        metric_col = primary_metrics.get(arm)
        # primary_metric names in arms/*.yaml are descriptive, not literal
        # column names (e.g. "input_tokens_per_probe") -- fall back to the
        # closest matching numeric column we actually have.
        candidate_cols = [c for c in METRIC_COLUMNS if metric_col and c in metric_col] or ["cost_usd"]
        col = candidate_cols[0]

        arm_stat = _stats(_metric_values(rows, col))
        base_stat = _stats(_metric_values(by_arm.get(BASELINE_ARM, []), col))

        if not arm_stat.get("n") or not base_stat.get("n"):
            verdict = "no-signal (insufficient data)"
        elif _iqr_overlaps(arm_stat, base_stat):
            verdict = "no-signal (IQR overlaps baseline)"
        else:
            verdict = "TBD conductor"

        lines.append(f"- **{arm}** (primary metric: `{metric_col or 'unknown'}`, compared via `{col}`): {verdict}")
    lines.append("")
    return lines


def build_report(rows: list[dict], primary_metrics: dict[str, str], csv_path: Path) -> str:
    grouped = _group(rows)
    parts: list[str] = []
    parts.extend(_header(csv_path, rows))
    parts.extend(_per_task_tables(grouped))
    parts.extend(_tool_usage_section(grouped))
    parts.extend(_recommendations_section(grouped))
    parts.extend(_verdicts_section(grouped, primary_metrics))
    return "\n".join(parts)


def cmd_report(args: argparse.Namespace) -> int:
    csv_path = Path(args.csv)
    out_path = Path(args.out)
    arms_dir = Path(getattr(args, "arms_dir", None) or "arms")

    rows = _read_rows(csv_path)
    primary_metrics = _primary_metrics(arms_dir) if arms_dir.is_dir() else {}

    report = build_report(rows, primary_metrics, csv_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(report)

    print(f"wrote {out_path} ({len(rows)} rows from {csv_path})")
    return 0


