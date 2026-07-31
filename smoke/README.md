# Smoke prompts

One cheap prompt per arm, designed to trigger that arm's fired-check.
Frozen by the conductor before smoke runs. Model: sonnet (haiku may skip
skills). Success = fired-check evidence appears (OTEL skill_activated,
transcript tool calls, counter delta) — NOT answer quality.

Run pattern per arm:

```bash
export CLAUDE_CONFIG_DIR=~/bench/cfg-<arm>
export CLAUDE_CODE_ENABLE_TELEMETRY=1 OTEL_METRICS_EXPORTER=otlp OTEL_LOGS_EXPORTER=otlp
export OTEL_EXPORTER_OTLP_PROTOCOL=grpc OTEL_EXPORTER_OTLP_ENDPOINT=http://localhost:4317
export OTEL_METRIC_EXPORT_INTERVAL=10000
export OTEL_RESOURCE_ATTRIBUTES="arm=<arm>,task=smoke,run=s1"
cd <clone> && claude -p "$(cat smoke/<arm>.md)" --model sonnet --permission-mode acceptEdits
```

baseline adds --strict-mcp-config. Verdict table goes to results/smoke.md.
