# cc-bench

Does that Claude Code tool actually help? cc-bench measures it.

You give it a target repo and one tool (a skill, a CLI, an MCP server, or repo
artifacts like OpenSpec). It runs the same frozen tasks with and without the
tool, in a headless Claude Code session with telemetry on, and gives you a
number you can put in an ADR: cost, tokens, active time, tool calls, correct
or not.

Rule of thumb: one tool per arm, never a bundle. If you can't tell which tool
moved the number, the number is worthless.

## Two tiers

**Tier P - probes.** For tools that change *how* the agent gathers context
(rtk, graphify, gh-axi, ast-grep, chrome-devtools-axi). The output code
doesn't change - the cost does. Small read-only tasks with a frozen answer
key, fully automated. Done: see [status](#status).

**Tier F - real tasks.** For tools that change *what gets produced* (OpenSpec,
ponytail, sdd-kit). Needs a real ticket, quality gates, a git diff and blind
human judging. Semi-automated - judging is manual. Not run yet.

## Quickstart

```bash
uv sync
uv run bench arms list        # what can be measured
uv run bench arms validate    # check arms/*.yaml
```

## Simple mode: one live session

The protocol below exists to compare arms. For the simpler question - "I worked
in my own repo by hand, give me the numbers for that session" - one command does
it all.

One-time setup: `uv tool install --editable ~/cybernet/cc-bench` (puts `bench` on
PATH), telemetry in the `env` block of `~/.claude/settings.json`
(`CLAUDE_CODE_ENABLE_TELEMETRY=1` plus the `OTEL_*` vars pointing at
`localhost:4317`) and a running collector (`docker start bench-otel`). `bench`
preflights the last two and refuses to burn a session it cannot measure.

```bash
cd ~/cybernet/conversation_flow
bench
```

It asks for a task id (Enter takes the branch name), launches `claude` in the
cwd, waits for you to exit and for the OTEL flush, finds that session's
transcript and appends a `results.csv` row. Bare `bench` means `bench session`;
the other subcommands behave as before.

Defaults: repo is the cwd, task is the current git branch, `--arm live`, `--run`
is the next `rN` for that arm+task. Forgot to wrap the session?
`bench --no-launch` picks up the newest one for the repo. Anything after `--` is
passed through to `claude`.

## How one measurement works

1. **Freeze the task.** A probe (tier P) or a ticket (tier F) plus its answer
   key, written down and never edited afterwards. Changing an answer key
   creates a *new* probe, it does not amend the old one.
2. **Prepare the arms.** `bench prepare --repo <path> --arm <name>` clones the
   target repo into `~/bench/`, marks a `bench-base` commit, and installs the
   tool into an isolated `CLAUDE_CONFIG_DIR` so arms can't contaminate each
   other.
3. **Smoke test - the fired-check.** One cheap prompt per arm (`smoke/*.md`)
   that must prove the tool actually activated. No proof of firing, no
   measured run: dead arms get fixed or dropped, never measured.
4. **Measure.** `bench run --arm ... --tier p --task ... --runs 3` executes
   headless runs with OTEL telemetry. Baseline and tool arm are interleaved
   run by run so drift hits both equally.
5. **Score and report.** `bench gates` (diff + ruff/pytest/scope, tier F only),
   `bench extract` turns OTEL + transcript into one CSV row, `bench report`
   builds a median + IQR markdown comparison.

Measured runs need a local OTEL collector on `localhost:4317`. Timing uses
**active time**, not wall clock - wall clock mostly measures the API queue.

## Commands

| Command | What it does |
|---|---|
| `bench session` | measure one normal interactive session end to end |
| `bench arms list` / `validate` | list / schema-check arm definitions |
| `bench prepare` | clone the target repo and install one arm |
| `bench run` | headless measured run(s) for one arm |
| `bench gates` | diff + ruff/pytest/scope checks vs `bench-base` |
| `bench extract` | OTEL + transcript -> one `results.csv` row |
| `bench report` | median + IQR markdown report |

`probes gen|freeze` and `smoke` have no subcommands - those steps are driven
by the conductor (a Claude session) using `probes/templates/` and
`smoke/run-*.sh`.

## Repo layout

```
arms/                  arm specs, one YAML per tool (hypothesis, metric, install, fired-check)
probes/templates/      generic tier-P probe templates with placeholders
probes/<repo>/         frozen probe instances for one target repo - never edited after freeze
smoke/                 one fired-check prompt per arm + the run scripts
src/cc_bench/          the CLI
docs/                  plan, ADRs, glossary
results/               run output, reports, logs (gitignored - local only)
```

## An arm in full

```yaml
name: rtk
tier: p
hypothesis: rtk's token-optimized bash output proxy cuts input tokens per probe versus raw bash output.
primary_metric: input_tokens_per_probe
install:
  - kind: path_binary
    check: command -v rtk
  - kind: hook
    text: Install the rtk PreToolUse Bash rewrite hook into the arm's CLAUDE_CONFIG_DIR settings.json.
  - kind: claude_md_hint
    text: RTK is available - a token-optimized CLI proxy that cuts up to 90% of bash output.
fired_check:
  kind: command_delta
  command: rtk gain
```

Every arm needs a falsifiable hypothesis, one primary metric, and a
fired-check. No fired-check, no arm.

## Status

**Tier P: complete (2026-07-30).** 5 frozen probes x baseline + one tool arm,
6 rounds, 66 runs. Rounds 1-3 with the tool installed but not advertised,
rounds 4-6 with a CLAUDE.md hint added. Headline: the hint made 3 of 6 tools
fire (graphify, gh-axi, chrome-devtools-axi) and did nothing for the other 3
(ast-grep, rtk, headroom) - prose in a memory file does not route the model
away from a built-in that already works. Full write-up in
`results/tier-p-report.md` (local, gitignored).

**headroom was dropped as an arm on 2026-07-31**: 0-2% compression, breaks the
prompt-cache prefix, +45..62% on the bill. Rationale in sdd-kit
`docs/ADR/ADR-0014-drop-headroom.md`. That is why `arms/` has 10 arms and the
tier-P report has 11.

**Tier F: not started.** 5 arms defined (openspec, ponytail, caveman,
grill-with-docs, sdd-kit); needs a real ticket, gates and blind judging.
Two blockers carried over from smoke: **caveman and ponytail never fire** -
the skill is installed but never activates - so by the protocol they get fixed
or dropped, not measured. The `sdd-kit` arm was **re-frozen on 2026-08-01**
against the current kit (`bench-base` = `917f950b`, previous freeze kept at
branch `bench-base-20260730`); gates pass, details and one new sdd-kit defect
are in `results/sdd-kit-static-check.md`.

CLI: `arms`, `prepare`, `gates`, `run`, `extract`, `report` are functional and
were used for the tier-P phase. The extractor is verified against real m1/m2
sessions - cost and active time reproduce the published numbers.

## Docs

- `docs/PLAN-tools.md` - the full plan and protocol (Russian)
- `docs/adr/` - decision records, plus a glossary
- `README_RU.md` - this file in Russian

Design docs originated in the `sdd-kit` project before moving here.
