# cc-bench

A before/after benchmark harness for Claude Code tool ablation: for a given
(target repo, tool) pair - a skill, CLI, MCP server, or repo artifacts like
OpenSpec - it produces a repeatable ADR-quality number on whether the tool
actually helps.

## Two tiers

- **Tier P (probes).** Tools that change *how* the agent gathers context
  (e.g. rtk, headroom, graphify, gh-axi, ast-grep). Output code doesn't
  change, cost does. Measured on small read-only tasks with frozen answer
  keys - fully automated.
- **Tier F (real tasks).** Tools that change *what* gets produced (e.g.
  OpenSpec, ponytail, sdd-kit as a whole). Needs a real ticket, gates, a
  diff, and blind judging - semi-automated; judging is a manual step.

## Quickstart

```bash
uv sync
uv run bench arms list
uv run bench arms validate
```

## Repo layout

```
arms/                  arm YAML specs (one tool per arm, no bundles)
probes/templates/      tier-P probe templates, instantiated per target repo
src/cc_bench/          the CLI
results/               per-run output (gitignored)
docs/                  design docs, ADRs, protocol
```

## Design docs

Full rationale, ADRs, and protocol now live in this repo under `docs/`
(`docs/PLAN-tools.md`, `docs/adr/`). They originated in the `sdd-kit`
project (`/home/octrow/cybernet/sdd-kit`) before moving here.

## Protocol rules worth knowing

- One tool per arm, no bundles.
- A fired-check is mandatory: if an arm doesn't prove it actually activated
  in a smoke run, it's fixed or dropped - dead arms don't get measured runs.
- Active time (not wall clock) is the timing metric that counts.
- The final report must include a tool-usage breakdown and a section of
  concrete improvement recommendations for the measured tool.

## Status

Phase 1 - arms/prepare/gates/run/extract/report are functional; `probes gen|freeze` and `smoke` land in Phase 2. Extractor verified against real m1/m2 sessions (cost and active time reproduce published numbers).
