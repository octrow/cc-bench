# Free/Open-Source Telemetry, Cost, Eval & Code-Quality Tooling for Benchmarking AI Coding Agents

No single free tool covers all 30 metrics. The best "best" answer is a four-layer stack: (1) an OTel-native
observability backend for token/cost/tool telemetry - Langfuse (MIT) or a Grafana+Prometheus+Loki stack fed by Claude
Code's native OpenTelemetry export (the claude-code-otel project wires this up in one docker compose); (2) a small glue
collector (Python/DuckDB over the JSONL session transcripts) for the agent-internal metrics no backend captures
automatically; (3) a code-quality/diff layer (Semgrep + radon/lizard + jscpd + diff-cover + coverage.py, gated in CI);
and (4) a blind-review annotation UI (Label Studio's pairwise template, or the tiny open-model-arena for ELO).
Claude Code is the only agent with first-class, documented telemetry: it natively emits OpenTelemetry metrics including
claude_code.token.usage (typed input/output/cacheRead/cacheCreation, per model), claude_code.cost.usage,
claude_code.code_edit_tool.decision (accept/reject), claude_code.active_time.total, and claude_code.lines_of_code.count.
Cursor, Aider, Cline, Roo, OpenHands, Codex CLI and Gemini CLI do not emit comparable native OTel; for those you must
parse local JSONL/SQLite session logs (tools like agenttrace and TokenTelemetry already do this for 13-28 agents).
Metrics 6-13 (tokens/cache/turns/tool-calls per model) are automatable; metrics 20-26 (build/lint/types/tests, diff
size, scope creep, coverage delta, complexity/duplication delta) come from CI + static analysis; metrics 1, 2, 4,
27-29 (acceptance grading, human interventions, "would I merge", convention fit, blind review) are inherently manual
rubric work with no automated tool. Metric 30 (median + IQR over 3 runs) requires your own aggregation - Inspect AI's
--epochs and its evals_df/samples_df dataframes are the closest OSS support.

### Key Findings

The candidate landscape splits into five families

1. LLM-observability platforms (traces + per-model token/cost + some evals + UI): Langfuse, Arize Phoenix, Comet Opik,
   OpenLIT, Helicone, Lunary, Laminar, LangWatch, SigNoz.
2. Instrumentation/gateways (emit or route the telemetry the platforms consume): OpenLLMetry/Traceloop, LiteLLM proxy,
   Portkey Gateway, OpenTelemetry GenAI semantic conventions.
3. Coding-agent-specific usage analytics (parse the logs the agents already write): ccusage, claude-code-otel,
   Claude-Code-Usage-Monitor, sniffly, agenttrace, TokenTelemetry, vibe-log, cchistory/claude-trace, claude-code-log.
4. Eval / benchmark harnesses: SWE-bench (+ Epoch's swebench-docker registry), OpenHands eval harness, terminal-bench,
   Inspect AI, promptfoo, DeepEval, Ragas, OpenAI Evals, lm-eval-harness.
5. Code-quality / DORA / annotation: SonarQube Community, Semgrep, radon, lizard, jscpd, diff-cover,
   coverage.py/pytest-cov, reviewdog, PR-Agent, Apache DevLake, GrimoireLab; Label Studio, Argilla, Doccano,
   open-model-arena/FastChat.

### Claude Code native OpenTelemetry - exact env vars, metrics and attributes

Enable with CLAUDE_CODE_ENABLE_TELEMETRY=1, then choose exporters: OTEL_METRICS_EXPORTER=otlp|prometheus|console,
OTEL_LOGS_EXPORTER=otlp|console, endpoint via OTEL_EXPORTER_OTLP_PROTOCOL=grpc and
OTEL_EXPORTER_OTLP_ENDPOINT=http://localhost:4317, auth via OTEL_EXPORTER_OTLP_HEADERS. Critical gotcha: Claude Code
defaults to Delta temporality; set OTEL_EXPORTER_OTLP_METRICS_TEMPORALITY_PREFERENCE=cumulative or short sessions vanish
before Prometheus scrapes them. Per the Claude Code Docs (code.claude.com/docs/en/monitoring-usage), "The default export
intervals are 60 seconds for metrics and 5 seconds for logs" - shorten OTEL_METRIC_EXPORT_INTERVAL while debugging. When
prometheus is the only exporter, Claude Code omits units so the scrape stays valid Prometheus text.

It exports eight metric counters:

- claude_code.session.count - sessions started (use to verify setup).
  claude
- claude_code.token.usage - tokens; per SigNoz's Claude Code monitoring docs, "Claude Code emits a
  claude_code.token.usage metric after every API request, broken down by type (input, output, cacheRead, cacheCreation),
  model, and query_source." This one metric yields metrics 6-10. Cache hit rate (metric 10) = cacheRead / (input +
  cacheRead).
- claude_code.cost.usage - estimated USD per request, attributes model, agent.name, skill.name, query_source.
- claude_code.lines_of_code.count - lines added/removed by type.
- claude_code.code_edit_tool.decision - accept/reject per edit, attributes tool (Edit/Write/NotebookEdit), decision (
  accept|reject), language, source. This yields metric 3 (rejected edits).
- claude_code.active_time.total - active (non-idle) time; a cleaner proxy than wall-clock for metric 19.
- claude_code.commit.count and claude_code.pull_request.count - commits/PRs created.

It also emits five log/event types (via OTEL_LOGS_EXPORTER), all sharing a prompt.id UUID that lets you reconstruct one
user interaction: user_prompt, api_request (includes latency, token counts, model -> metric 18/19), tool_decision,
tool_result, and api_error (-> metric 18, API errors/retries). Traces are in beta.

The code.claude.com/docs/en/monitoring-usage page is the authoritative spec; ColeMurray/claude-code-otel (MIT)
implements the full Collector->Prometheus+Loki->Grafana stack, and Grafana dashboard #25255 ("Claude Code Metrics (
Prometheus)") gives per-model/user/org breakdowns out of the box. Note: Claude Code's aggregated team analytics
console (code.claude.com/docs/en/analytics) shows accepted lines, suggestion accept rate, DAU/sessions/spend, but it is
a hosted summary - the OTel export is the free, self-hostable path.
GitHub

The JSONL session transcripts - what yields metrics 11-17

Claude Code writes one JSON object per line to ~/.claude/projects/<encoded-path>/<session-id>.jsonl. Records are typed
and linked by parentUuid. Assistant records carry message.content (an array of text, thinking, and tool_use blocks) and
message.usage with input_tokens, output_tokens, cache_creation_input_tokens, cache_read_input_tokens. Each tool_use has
an id, a name (Read/Grep/Glob/Edit/Bash/Task/...), and an input; the matching tool_result arrives in a later type:"user"
entry with the same id.

From this you compute:

Metric 11 (agent turns) = count of assistant records (or api_request events).
Metric 12 (tool-call count) = count of tool_use blocks.
Metric 13 (tool-call breakdown Read/Grep/Glob/Edit/Bash) = group tool_use by name.
Metric 14 (files read : files edited) = distinct file paths in Read vs Edit/Write inputs.
Metric 15 (compactions + pre/post tokens) = compaction/summary entries (isCompactSummary/summary records) with
surrounding usage snapshots.
Metric 16 (peak context-window fill) = max of input_tokens + cache_read_input_tokens across turns.
Metric 17 (dead-end branches / backtracking) = heuristic over parentUuid branch points and revert/rejected-edit
patterns - no tool does this cleanly; it needs custom scripting.

Data-quality caveat: Claude Code issue #27361 reports the JSONL never logs the final message_stop: "output_tokens in the
usage field is a mid-stream snapshot, not the final count... stop_reason is always null... Output tokens are undercounted
by ~2x compared to actual content" (a related ccusage issue reports far larger undercounts on Opus). Prefer the OTel
claude_code.token.usage metric for authoritative output tokens; use JSONL for structural metrics (turns, tool calls,
files). daaain/claude-code-log, chiphuyen/sniffly, and the Rust claude_code_transcripts crate are ready-made parsers.

Other agents' telemetry
Codex CLI: writes rollout-<session-id>.jsonl under ~/.codex/sessions/YYYY/MM/DD/ with prompts, responses, tool calls,
results, approvals and token-usage counters. Also emits OTel metrics in recent versions; AWS CloudWatch "Coding Agent
Insights" now ingests Claude Code, Codex and Copilot via OTel resource attributes.
AWS
Cursor: session/"plan" data in local stores; no documented OTel - parse via agenttrace/TokenTelemetry.
Cline / Roo / Kilo: Cline keeps a CLI SQLite store + VS Code extension JSON task history (TokenTelemetry de-dupes both).
Aider, Gemini CLI, OpenHands, Copilot, OpenCode: local logs of varying shape; agenttrace claims to read Claude Code,
Codex, Gemini CLI, Qwen, Cline, Aider, Cursor, OpenCode, Copilot-style logs and generic JSONL; TokenTelemetry covers 13
agents; claude-code-history-viewer covers 28. None of these non-Claude agents expose the accept/reject or active-time
metrics Claude Code does - you get tokens/tool-calls/cost by parsing, but edit-decision telemetry is Claude-Code-only.
OpenTelemetry GenAI semantic conventions map to metrics 6-12

The gen_ai.* conventions define gen_ai.request.model, gen_ai.usage.input_tokens, gen_ai.usage.output_tokens,
gen_ai.operation.name (chat/invoke_agent/execute_tool), gen_ai.response.finish_reasons, and agent attributes
gen_ai.agent.name/id. Maturity caveat: per John Hodge's "The state of the OpenTelemetry GenAI semantic conventions (July
2026)," the main semantic-conventions repo's v1.42.0 release (June 12, 2026) moved all gen_ai.* content to the dedicated
open-telemetry/semantic-conventions-genai repo, and "As of July 17, 2026, no GenAI-specific span, event, metric, or
attribute in the dedicated repository is marked Stable; the GenAI conventions remain Development." Pin the version and
isolate the attribute strings behind a mapping layer.

Cache tokens: the GenAI spec now defines gen_ai.usage.cache_creation.input_tokens and
gen_ai.usage.cache_read.input_tokens (the Bedrock semconv notes "gen_ai.usage.cache_read.input_tokens: The value SHOULD
be included in gen_ai.usage.input_tokens"), but both remain Development-stage, not Stable - so in practice only
Anthropic-aware tooling (Claude Code's own claude_code.token.usage, or LiteLLM/Langfuse cost logic) reliably tracks
metrics 8-9 today. If you route agents through a LiteLLM proxy (LITELLM_OTEL_V2=true), you get one GenAI-conventions
trace per request with per-model cost/token attributes across 100+ providers - the single best way to get a uniform
per-model token/cost layer across heterogeneous arms.

## Details

### Matrix 1 - Candidate tools × capabilities

Legend: OK yes · ⚠️ partial/needs config · ❌ no. Stars/licenses per GitHub as observed July 30, 2026 (fluctuate).

| Tool (repo)                                         | Self-host free    | UI                                          | Per-model cost/token  | Cache tokens        | Tool-call breakdown    | Agent turn counts | Context/compaction | OTel-native        | CC/Cursor/Aider/Codex ingest                | Eval/dataset/exp.            | Human annot. + blind review | Stars                                         | License                                              | Activity                                     |
|-----------------------------------------------------|-------------------|---------------------------------------------|-----------------------|---------------------|------------------------|-------------------|--------------------|--------------------|---------------------------------------------|------------------------------|-----------------------------|-----------------------------------------------|------------------------------------------------------|----------------------------------------------|
| **Langfuse** (langfuse/langfuse)                    | OK                 | OK                                           | OK                     | ⚠️ (usage detail)   | ⚠️ spans               | ⚠️ spans          | ❌                  | OK                  | ⚠️ via LiteLLM/SDK                          | OK                            | ⚠️ annotation queues        | ~27,200                                       | MIT (except `ee/`)                                   | active 2026                                  |
| **Arize Phoenix** (Arize-ai/phoenix)                | OK                 | OK                                           | OK                     | ⚠️                  | ⚠️                     | ⚠️                | ❌                  | OK (OpenInference)  | ⚠️ via OTel                                 | OK built-in evaluators        | ⚠️                          | ~10,000                                       | **Elastic License 2.0 (not OSI)**                    | active 2026                                  |
| **Comet Opik** (comet-ml/opik)                      | OK full platform   | OK                                           | OK                     | ⚠️                  | ⚠️                     | ⚠️                | ❌                  | OK                  | ⚠️ via OTel                                 | OK LLM-judge, optimizer       | ⚠️                          | ~20,000                                       | Apache-2.0 (no gating)                               | active 2026                                  |
| **OpenLIT** (openlit/openlit)                       | OK                 | OK                                           | OK                     | ⚠️                  | ⚠️                     | ⚠️                | ❌                  | OK                  | ⚠️ via OTel                                 | OK                            | ❌                           | ~2,300                                        | Apache-2.0                                           | active 2026                                  |
| **Helicone** (Helicone/helicone)                    | OK                 | OK                                           | OK                     | ⚠️ Anthropic-aware  | ⚠️                     | ⚠️                | ❌                  | ⚠️                 | ⚠️ proxy                                    | OK                            | ⚠️                          | ~5,700                                        | Apache-2.0                                           | active 2026 (some sources: maintenance mode) |
| **Lunary** (lunary-ai/lunary)                       | OK                 | OK                                           | OK                     | ❌                   | ⚠️                     | ⚠️                | ❌                  | ⚠️                 | ⚠️                                          | OK                            | ⚠️                          | ~1,400                                        | Apache-2.0                                           | active 2026                                  |
| **Laminar** (lmnr-ai/lmnr)                          | OK                 | OK                                           | OK                     | ⚠️                  | ⚠️                     | ⚠️                | ❌                  | OK                  | ⚠️ via OTel                                 | OK                            | OK annotations               | ~2,500                                        | Apache-2.0                                           | active 2026                                  |
| **LangWatch** (langwatch/langwatch)                 | OK core            | OK                                           | OK                     | ⚠️                  | ⚠️                     | ⚠️                | ❌                  | OK                  | ⚠️                                          | OK                            | ⚠️                          | ~3,000                                        | Apache-2.0 core (`ee/` commercial)                   | active 2026                                  |
| **SigNoz** (SigNoz/signoz)                          | OK                 | OK                                           | OK (CC dashboard)      | OK (CC `type` label) | OK (CC events)          | OK (CC events)     | ⚠️                 | OK                  | OK CC; Codex/Copilot                         | ❌                            | ❌                           | ~26,900                                       | MIT (except `ee/`)                                   | active 2026                                  |
| **LiteLLM proxy** (BerriAI/litellm)                 | OK                 | OK admin UI                                  | OK per-key/model spend | OK Anthropic-aware   | ❌                      | ❌                 | ❌                  | OK (v2 GenAI conv.) | OK routes all agents                         | ❌                            | ❌                           | ~54,900                                       | MIT (except `enterprise/`)                           | active 2026                                  |
| **Portkey Gateway** (Portkey-AI/gateway)            | OK gateway+console | ⚠️ basic console OSS; rich dashboard hosted | OK                     | OK                   | ❌                      | ❌                 | ❌                  | ⚠️                 | OK routes agents                             | ❌                            | ❌                           | ~12,000                                       | MIT (analytics UI hosted/paywalled)                  | active 2026                                  |
| **OpenLLMetry** (traceloop/openllmetry)             | OK lib only        | ❌ needs backend                             | OK attributes          | ⚠️                  | ⚠️                     | ⚠️                | ❌                  | OK                  | ⚠️                                          | ❌                            | ❌                           | ~6,100                                        | Apache-2.0                                           | active 2026                                  |
| **Grafana+Prometheus+Loki** (via claude-code-otel)  | OK                 | OK                                           | OK                     | OK CC metric         | OK Loki events          | OK                 | ⚠️                 | OK                  | OK CC native; others via OTel                | ❌                            | ❌                           | 300-420 (glue repo)                           | MIT glue; Grafana AGPL/Prom Apache                   | active 2025                                  |
| **ccusage** (ryoppippi/ccusage)                     | OK                 | ⚠️ TUI/CLI                                  | OK per-model, offline  | OK from JSONL        | ⚠️                     | ⚠️                | ❌                  | ❌                  | CC + Codex                                  | ❌                            | ❌                           | ~17,200                                       | MIT                                                  | active 2026                                  |
| **Claude-Code-Usage-Monitor** (Maciek-roboblog)     | OK                 | ⚠️ Rich TUI                                 | OK                     | OK                   | ❌                      | ❌                 | ❌                  | ❌                  | CC                                          | ❌                            | ❌                           | ~8,200                                        | MIT                                                  | active 2026                                  |
| **sniffly** (chiphuyen/sniffly)                     | OK                 | OK web :8081                                 | OK                     | OK                   | OK error/tool breakdown | OK                 | ⚠️                 | ❌                  | CC                                          | ❌                            | ❌                           | ~1,200                                        | MIT                                                  | active 2025                                  |
| **agenttrace** (luoyuctl/agenttrace)                | OK                 | ⚠️ TUI + reports + CI gates                 | OK                     | OK                   | OK                      | OK                 | ⚠️                 | ❌                  | CC/Codex/Gemini/Cline/Aider/Cursor +        | ❌                            | ❌                           | newer/low                                     | see repo                                             | active 2026                                  |
| **TokenTelemetry** (VasiHemanth/tokentelemetry)     | OK                 | OK dashboard                                 | OK                     | OK                   | OK                      | ⚠️                | ❌                  | ❌                  | 13 agents                                   | ❌                            | ❌                           | newer/low                                     | MIT                                                  | active 2026                                  |
| **Inspect AI** (UKGovernmentBEIS/inspect_ai)        | OK                 | OK Inspect View                              | OK per-model in logs   | ⚠️                  | OK tool events          | OK                 | ⚠️                 | ⚠️                 | OK drives CC/Codex/Gemini as external agents | OK 200+ evals, epochs         | ⚠️ transcript review        | ~2,300                                        | MIT                                                  | active 2026                                  |
| **promptfoo** (promptfoo/promptfoo)                 | OK                 | OK web matrix                                | OK                     | ⚠️                  | ⚠️                     | ⚠️                | ❌                  | ❌                  | ⚠️ providers                                | OK YAML side-by-side          | ⚠️                          | ~23,000                                       | MIT (OpenAI-owned)                                   | active 2026                                  |
| **DeepEval** (confident-ai/deepeval)                | OK                 | ⚠️ Confident cloud for UI                   | ⚠️                    | ❌                   | ❌                      | ❌                 | ❌                  | ❌                  | ❌                                           | OK pytest metrics             | ❌                           | ~16,000                                       | Apache-2.0                                           | active 2026                                  |
| **SWE-bench** (princeton-nlp/SWE-bench)             | OK                 | ❌ harness                                   | ❌                     | ❌                   | ❌                      | ❌                 | ❌                  | ❌                  | ⚠️ agent-agnostic patches                   | OK pass/fail oracle           | ❌                           | ~3,200                                        | MIT                                                  | active 2026                                  |
| **Label Studio** (HumanSignal/label-studio)         | OK Community       | OK                                           | ❌                     | ❌                   | ❌                      | ❌                 | ❌                  | ❌                  | ❌                                           | OK **pairwise/RLHF template** | ~27,700                     | Apache-2.0 (agreement metrics = Enterprise $) | active 2026                                          |
| **Argilla** (argilla-io/argilla)                    | OK                 | OK                                           | ❌                     | ❌                   | ❌                      | ❌                 | ❌                  | ❌                  | ❌                                           | OK feedback/DPO               | ~4,700                      | Apache-2.0 (maintenance slowed)               | slowed                                               |
| **Apache DevLake** (apache/incubator-devlake)       | OK                 | OK Grafana                                   | ❌                     | ❌                   | ❌                      | ❌                 | ❌                  | ❌                  | ❌ (DORA from Git/CI)                        | ⚠️ review rounds via GitHub  | ❌                           | ~3,000                                        | Apache-2.0                                           | active 2026                                  |
| **reviewdog** (reviewdog/reviewdog)                 | OK                 | ⚠️ PR comments                              | ❌                     | ❌                   | ❌                      | ❌                 | ❌                  | ❌                  | ❌                                           | n/a CI gate                  | n/a                         | ~9,400                                        | MIT                                                  | active 2026                                  |
| **PR-Agent** (qodo-ai/pr-agent)                     | OK self-host       | ⚠️ PR comments                              | ❌                     | ❌                   | ❌                      | ❌                 | ❌                  | ❌                  | ❌                                           | n/a                          | n/a                         | ~12,000                                       | Apache->AGPL->Apache (transitional); Qodo Merge = paid | active 2026                                  |
| **open-model-arena** (pete-builds/open-model-arena) | OK                 | OK web arena                                 | ❌                     | ❌                   | ❌                      | ❌                 | ❌                  | ❌                  | any OpenAI-compat endpoint                  | ⚠️                           | OK **blind pairwise + ELO**  | newer/low                                     | see repo                                             | active 2026                                  |

**Open-core / paywall flags to watch:**

- **Arize Phoenix** is **Elastic License 2.0** - free to self-host but you cannot offer it as a competing hosted
  service; managed Arize AX is the paid SaaS.
- **Langfuse, SigNoz, LiteLLM, LangWatch** are core-open with an `ee/`/`enterprise/` directory holding SSO, RBAC, audit
  logs behind a license - the tracing/cost core you need is fully free.
- **Label Studio Community** ships full labeling incl. a pairwise-comparison template, but **inter-annotator agreement
  metrics and the quality dashboard start at ~$99/user/mo (Enterprise)** - a real gap for metric 29 quality control.
- **Portkey**: gateway + local console + cost DB are MIT; the rich multi-user observability dashboard is
  hosted/Enterprise.
- **PR-Agent**: self-host free but license is transitional; **Qodo Merge** is the paid hosted tier.
- **promptfoo** remains MIT after the 2026 OpenAI acquisition, but roadmap is now less vendor-neutral.
- **Helicone** is reported by at least one source to have entered maintenance mode - verify before adopting.

### Matrix 2 - The 30 metrics × best tool × acquisition method

"какая модель" (which model) - every token/cost/turn/tool metric below is broken down by `model` via the `model`
attribute (OTel) or the `model` field in JSONL/ccusage.

| #  | Metric                                                     | Best tool(s)                                                                | How obtained                                                         | Automatable?                                   |
|----|------------------------------------------------------------|-----------------------------------------------------------------------------|----------------------------------------------------------------------|------------------------------------------------|
| 1  | Task success (acceptance criteria; 0/0.5/1)                | SWE-bench (if hidden tests) / Inspect AI scorer / **manual rubric**         | Built-in pass/fail oracle if tests exist; else manual grade          | ⚠️ Auto only with test oracle; else **manual** |
| 2  | Human interventions (redirections)                         | **none**                                                                    | Manual tally, or custom hook counting user-interrupt events in JSONL | ❌ **Manual** (best proxy of real value)        |
| 3  | Rejected edits                                             | **Claude Code OTel** `claude_code.code_edit_tool.decision{decision=reject}` | Built-in metric (CC only); parse JSONL for others                    | OK Auto (CC); ⚠️ others                         |
| 4  | Time-to-mergeable                                          | **none direct**                                                             | Manual timestamp (first run -> PR you'd merge); or GitHub API on PR   | ❌ Mostly **manual**                            |
| 5  | Review rounds to approve                                   | **Apache DevLake** / GitHub API                                             | GitHub API: count review submissions per PR                          | OK Auto (GitHub API)                            |
| 6  | token input (uncached) / model                             | **CC OTel** `claude_code.token.usage{type=input}`; ccusage; LiteLLM         | OTel attribute / JSONL / proxy                                       | OK Auto                                         |
| 7  | token output / model                                       | same `{type=output}`                                                        | OTel / JSONL (prefer OTel - JSONL undercounts)                       | OK Auto                                         |
| 8  | token cacheCreation / model                                | same `{type=cacheCreation}`                                                 | OTel / JSONL `cache_creation_input_tokens`                           | OK Auto (Anthropic-aware only)                  |
| 9  | token cacheRead / model                                    | same `{type=cacheRead}`                                                     | OTel / JSONL `cache_read_input_tokens`                               | OK Auto (Anthropic-aware only)                  |
| 10 | Cache hit rate = cacheRead/(input+cacheRead)               | PromQL over `claude_code.token.usage`                                       | Derived metric in Grafana/backend                                    | OK Auto (derived)                               |
| 11 | Agent turns / model                                        | JSONL parse / Inspect AI                                                    | Count assistant records / `api_request` events                       | ⚠️ Glue code (or Inspect)                      |
| 12 | Tool-call count / model                                    | JSONL parse / sniffly / agenttrace                                          | Count `tool_use` blocks                                              | ⚠️ Glue (tools exist)                          |
| 13 | Tool breakdown Read/Grep/Glob/Edit/Bash                    | sniffly / agenttrace / JSONL                                                | Group `tool_use` by `name`                                           | ⚠️ Glue (tools exist)                          |
| 14 | Files read : files edited                                  | **none**                                                                    | Custom: distinct paths in Read vs Edit/Write inputs                  | ❌ Glue code                                    |
| 15 | Compactions + pre/post tokens                              | **none**                                                                    | Custom: detect compaction/summary entries + surrounding usage        | ❌ Glue code                                    |
| 16 | Peak context-window fill                                   | **none**                                                                    | Custom: max(input+cacheRead) per turn from JSONL                     | ❌ Glue code                                    |
| 17 | Dead-end branches / backtracks                             | **none**                                                                    | Custom heuristic over `parentUuid` + reverts                         | ❌ Glue code (hardest)                          |
| 18 | API errors / retries                                       | **CC OTel** `api_error` event; LiteLLM                                      | Log event / proxy error counts                                       | OK Auto                                         |
| 19 | Wall-clock (reference only)                                | CC `api_request` latency / `active_time`; any backend                       | OTel / timestamps                                                    | OK Auto (keep as reference)                     |
| 20 | Build / lint / types / tests green (binary gates)          | **CI + reviewdog**; tsc/mypy/ruff/eslint; pytest                            | CI job exit codes                                                    | OK Auto                                         |
| 21 | Diff size (+/− lines, files)                               | `git diff --numstat`; DevLake                                               | Git/CI                                                               | OK Auto                                         |
| 22 | Scope creep (files outside blast radius)                   | custom vs pre-registered file list                                          | Git diff ∩ expected-files set                                        | ⚠️ Glue (pin expected files first)             |
| 23 | New deps / new abstractions                                | custom (dependency-diff); jscpd for dup                                     | Parse manifests; static analysis                                     | ⚠️ Glue                                        |
| 24 | Hallucinated API (undefined symbols)                       | **compiler/linter** (tsc, mypy, pyflakes, import checks)                    | CI job - count import/compile errors                                 | OK Auto                                         |
| 25 | Test-coverage delta on changed lines                       | **diff-cover** + coverage.py/pytest-cov (or pytest_nlcov)                   | CI: coverage.xml vs git diff                                         | OK Auto                                         |
| 26 | Cyclomatic-complexity + duplication delta                  | **radon** (Python CC), **lizard** (multi-lang CC), **jscpd** (dup)          | Static analysis, before/after                                        | OK Auto                                         |
| 27 | Repo-convention fit                                        | Semgrep/ESLint custom rules (partial) + **rubric**                          | Linter for mechanizable rules; rest manual                           | ⚠️ Partial auto + **manual**                   |
| 28 | Rubric (correctness/readability/tests/"would I merge" 1-5) | **Label Studio** / Argilla (collection UI)                                  | Manual scoring in annotation UI                                      | ❌ **Manual**                                   |
| 29 | Blind review (strip arm markers, shuffle diffs)            | **Label Studio pairwise** / **open-model-arena** / FastChat                 | Manual, in blind UI                                                  | ❌ **Manual** (tooling helps blind/shuffle)     |
| 30 | Median + IQR over 3 runs                                   | **Inspect AI `--epochs`** + `evals_df`; or DuckDB/pandas                    | Aggregation over ≥3 runs                                             | ⚠️ Glue/Inspect (not mean-of-one)              |

### Gaps that NO free tool captures (need custom glue)

Metrics **2, 4, 14, 15, 16, 17, 22, 23** have no off-the-shelf automated tool. Recommended minimal glue: a ~200-line *
*Python collector** that (a) reads each run's `~/.claude/projects/.../*.jsonl` (and `~/.codex/sessions/...` for
Codex), (b) computes turns, tool-call histogram, files-read:edited, compaction count, peak context, and a backtracking
heuristic, (c) joins the authoritative token/cost from the OTel metrics (or ccusage `--json`), (d) shells out to
`git diff --numstat`, `radon cc`, `lizard`, `jscpd`, `diff-cover coverage.xml`, and your build/lint/type/test exit
codes, (e) diffs touched files against a **pre-registered expected-file list** for scope creep, and (f) writes one row
per (arm, task, run) to **Postgres or DuckDB/ClickHouse**. Point **Grafana** or **Metabase** at that table for
dashboards. A pure `jq | duckdb` pipeline over the JSONL is the lightest variant. Manual metrics (1, 2, 4, 27-29) are
entered through the annotation UI and joined on `(task, run)`.

### Statistical / experiment-design tooling (metrics 30 & 29)

- **Metric 30 (median + IQR over 3 runs):** run each (arm × task) ≥3× and aggregate - never report a single-run mean. *
  *Inspect AI** natively supports repeated runs via `--epochs`, statistical bootstrap, and exposes `evals_df`/
  `samples_df`/`events_df` for pandas-style median/IQR; its **Inspect View** web UI and **Inspect Viz** dashboards
  visualize per-sample transcripts. Alternatively compute median/IQR in DuckDB/pandas over your collector table.
  SWE-bench itself reports resolved-rate but you wrap it with your own repetition loop.
- **Metric 29 (blinded, shuffled diff review):** the ELO/arena lineage is the model. **LMSYS Chatbot Arena / FastChat**
  publishes its code (Gradio + FastChat serving) - heavyweight. **`pete-builds/open-model-arena`** is a lighter
  self-hosted Docker/YAML blind pairwise arena with an ELO leaderboard against any OpenAI-compatible endpoint (K=32 ELO,
  ratings from 1000). For diffs specifically, **Label Studio's pairwise-comparison template** lets you present two
  anonymized diffs side-by-side and record a preference; you strip arm markers and randomize order in your export step.
  Standard ELO update: expected `p_i = 1/(1+10^((R_j−R_i)/400))`, `R'_i = R_i + K(S_i − p_i)`.

### Eval harnesses for metric 1 (task success)

If your tickets have executable acceptance tests, **SWE-bench**'s Docker harness gives a deterministic pass/fail oracle.
**SWE-bench Verified** is, per OpenAI's "Introducing SWE-bench Verified," *"a subset of the original test set from
SWE-bench, consisting of 500 samples verified to be non-problematic by our human annotators"* (drawn from 12 Python
repositories; the 'easy' subset is 196 sub-15-minute tasks, the 'hard' subset 45 over-1-hour tasks). Use **Epoch AI's
swebench-docker registry** instead of building from source - per Epoch's "How to run SWE-bench Verified in one hour on
one machine," the registry was reduced *"to 30 GiB for 500 SWE-bench Verified images (6x reduction). This allows us to
run SWE-bench Verified in 62 minutes on a single GitHub actions VM with 32 cores and 128GB of RAM."* **OpenHands** ships
a SWE-bench adapter (`evaluation/benchmarks/swe_bench`). **Inspect AI** can drive Claude Code, Codex CLI and Gemini CLI
as external agents via its Agent Bridge, giving you one harness with scoring + transcript logging across arms. *
*terminal-bench** covers shell/CLI tasks. For prompt-level A/B, **promptfoo** gives a side-by-side matrix; **DeepEval**
gives pytest-style CI gating.

## Recommendations

**Stage 0 - turn on what's free immediately.** Set `CLAUDE_CODE_ENABLE_TELEMETRY=1` with `OTEL_METRICS_EXPORTER=otlp`,
`OTEL_LOGS_EXPORTER=otlp`, and `OTEL_EXPORTER_OTLP_METRICS_TEMPORALITY_PREFERENCE=cumulative` before any benchmark run -
the data is emitted whether or not you collect it. Run `ccusage` for a zero-setup per-model token/cost sanity check.

**Minimal stack (fastest, ~half a day):**

- `ColeMurray/claude-code-otel` (Docker Compose: OTel Collector + Prometheus + Loki + Grafana) -> metrics 3, 6-10, 18, 19
  per model, plus Grafana dashboard #25255.
- `ccusage` + `sniffly` for per-model cost and tool/error breakdown (metrics 12, 13).
- CI job (reviewdog + ruff/eslint/tsc/mypy + pytest) -> metrics 20, 24.
- A spreadsheet or Label Studio for manual metrics 1, 2, 28, 29.
- **Tradeoff:** covers Claude Code only; metrics 11, 14-17, 22, 23, 25, 26, 30 not yet covered. Infra: Docker only.

**Balanced stack (recommended - ~2-3 days):**

- **LiteLLM proxy** (`LITELLM_OTEL_V2=true`) in front of *all* arms -> uniform per-model token/cost/error telemetry (
  metrics 6-10, 18) even for Cursor/Aider/Codex/Gemini, via OpenAI-compatible base-URL swap.
- **Langfuse** (self-host, Postgres + ClickHouse) as the trace/cost backend + UI, or keep the Grafana stack if you
  prefer PromQL.
- **Python/DuckDB glue collector** over JSONL + git + static analysis -> metrics 11-17, 21-26, and scope creep (22) vs a
  pre-registered file list.
- **diff-cover + coverage.py + radon + lizard + jscpd** in CI -> metrics 25, 26.
- **Label Studio** (pairwise template) for blind review (28, 29); **DuckDB/pandas** for median+IQR (30).
- **Tradeoff:** best coverage-to-effort ratio; ~all 30 metrics. Infra: Docker, Postgres, ClickHouse (Langfuse), one
  small Python service.

**Maximal stack (rigorous, ~1-2 weeks):**

- **Inspect AI** as the master harness - drive each arm (Claude Code/Codex/Gemini as external agents via Agent Bridge),
  get `--epochs`≥3, scorers for metric 1, tool-event logs for 11-13, Inspect View for transcripts, `evals_df` for
  median/IQR (30).
- **SWE-bench Verified** (Epoch swebench-docker registry) for objective task-success where test oracles exist.
- LiteLLM + Langfuse (or SigNoz) for the uniform telemetry plane; **Apache DevLake** for review-round/DORA metrics (5)
  from the GitHub API.
- Full static-analysis CI (SonarQube Community for complexity/duplication/coverage dashboards + Semgrep for convention
  rules) -> metrics 20, 24, 26, 27-partial.
- **open-model-arena** or Label Studio pairwise for blind ELO review (29).
- **Tradeoff:** highest fidelity and reproducibility; heaviest infra (Docker, Postgres, ClickHouse, Elasticsearch for
  SonarQube, image registry disk).

**Benchmarks that would change the recommendation:** if all arms can be routed through an OpenAI-compatible proxy,
prefer the LiteLLM-centered balanced stack (uniform cross-arm telemetry). If your tickets lack executable tests, drop
SWE-bench and lean on Inspect AI scorers + manual rubric. If you only ever benchmark Claude Code, the minimal Grafana
stack is sufficient and you can skip LiteLLM.

## Caveats

- **JSONL output-token undercount (Claude Code issue #27361):** always take output/cache tokens from the OTel
  `claude_code.token.usage` metric, not from JSONL `usage`, which is a mid-stream snapshot undercounted ~2× (larger on
  Opus per the related ccusage issue). Use JSONL only for structural metrics.
- **Cache metrics are Anthropic-specific in practice.** The generic OTel
  `gen_ai.usage.cache_read/cache_creation.input_tokens` fields exist but remain Development-stage (no Stable GenAI
  attributes as of July 17, 2026), so reliable cacheCreation/cacheRead tracking today only comes from
  Claude-Code-aware / Anthropic-aware tools; cross-arm cache comparison only works for Anthropic-family models.
- **Non-Claude agents lack accept/reject and active-time telemetry.** You can get tokens/tool-calls/cost by parsing
  logs, but metrics 3 and the active-time proxy are effectively Claude-Code-only; treat cross-agent comparisons of those
  as not-like-for-like.
- **Metrics 1, 2, 4, 27-29 are irreducibly manual.** No free tool grades acceptance criteria, counts human redirections,
  or judges "would I merge this." Budget human rubric time; the tools only collect and blind the inputs.
- **Wall-clock (metric 19) is noisy** - depends on API load and time of day; keep as reference, prefer `active_time` and
  turn/tool counts.
- **License caveats:** Arize Phoenix is Elastic License 2.0 (not OSI open-source); Label Studio's agreement
  metrics/quality dashboard are Enterprise-paid; PR-Agent's license is in transition (Apache->AGPL->Apache); Portkey's
  rich analytics UI is hosted/paywalled; Helicone may be in maintenance mode. Verify current license and star/activity
  numbers before committing - several projects move fast (litellm ~55k, promptfoo ~23k, ccusage ~17k as of July 30,
  2026), and star/commit figures are approximate.

# Sources

1. https://github.com/anthropics/claude-code-monitoring-guide
   358 stars
   updated last year
   A comprehensive guide to measuring the return on investment for Claude Code implementation in your development
   organization.
2. https://github.com/simple10/agents-observe
   631 stars
   updated last week
   Real-time observability dashboard for Claude Code and Codex agents.
   Includes powerful filtering, searching, and visualization of multi-agent sessions with full replay and token usage
   stats.
3. https://github.com/ColeMurray/claude-code-otel
   481 stars
   updated last year
   A comprehensive observability solution for monitoring Claude Code usage, performance, and costs.

- https://github.com/jhlee0409/claude-code-history-viewer
  2k stars
  updated last week
  The unified history viewer for AI coding assistants. Dual-mode token stats (billing vs conversation), cost breakdown,
  and provider distribution charts
- https://github.com/lainra/claude-code-telemetry
  39 stars
  updated last year
  Claude Code Telemetry is a lightweight bridge that captures telemetry data from Claude Code and forwards it to
  Langfuse for visualization, with a secure local turnkey installation under a minute.
- https://github.com/rockdarko/claude-code-metrics-prometheus
  15 stars
  updated 2 mounths ago
  Grafana dashboard for monitoring Claude Code CLI usage on Prometheus-compatible backends (Prometheus, VictoriaMetrics,
  Mimir, Thanos). Consumes OpenTelemetry metrics emitted via OTLP.
- https://github.com/RyanTech00/claude-telemetry
  11 stars
  updated 3 mounths ago
  Centralized Claude Code usage tracking across multiple PCs - web dashboard with 5-hour blocks, rate limits, cost
  analysis, and multi-machine aggregation
- https://github.com/TechNickAI/claude_telemetry
  27 stars
  updated 9 mounths ago
  OpenTelemetry wrapper for Claude Code CLI that logs tool calls, token usage, costs, and execution traces to Logfire,
  Sentry, Honeycomb, or Datadog. Drop-in replacement that swaps 'claude' command for 'claudia'.

---

