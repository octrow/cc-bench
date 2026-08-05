#!/usr/bin/env bash
# One-shot setup so `bench` works from any repo on a fresh machine.
# Idempotent: re-running it changes nothing that is already in place.
#
# Does three things: puts `bench` on PATH, starts the OTEL collector that
# receives Claude Code telemetry, and turns telemetry on in
# ~/.claude/settings.json (merging into the env block, never clobbering it).
set -euo pipefail

REPO=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
OTEL_DIR="$HOME/bench/otel"
SETTINGS="${CLAUDE_CONFIG_DIR:-$HOME/.claude}/settings.json"
CONTAINER=bench-otel
IMAGE=otel/opentelemetry-collector-contrib:latest

for tool in uv docker claude; do
  command -v "$tool" >/dev/null || { echo "install: '$tool' not found on PATH" >&2; exit 1; }
done

# 1. bench on PATH. --editable so the clone stays the source of truth: bench
# resolves arms/ and results/ relative to this repo, not to a copy in a venv.
echo "== installing bench from $REPO"
uv tool install --editable "$REPO" --force >/dev/null
command -v bench >/dev/null || {
  echo "install: bench installed but not on PATH -- add ~/.local/bin to PATH" >&2
  exit 1
}

# 2. OTEL collector: append-only file exporter, one JSONL per signal.
mkdir -p "$OTEL_DIR/data"
if [ ! -f "$OTEL_DIR/config.yaml" ]; then
  cat > "$OTEL_DIR/config.yaml" <<'YAML'
receivers:
  otlp:
    protocols:
      grpc:
        endpoint: 0.0.0.0:4317

processors:
  batch:

exporters:
  file/metrics:
    path: /data/metrics.jsonl
  file/logs:
    path: /data/logs.jsonl

service:
  pipelines:
    metrics:
      receivers: [otlp]
      processors: [batch]
      exporters: [file/metrics]
    logs:
      receivers: [otlp]
      processors: [batch]
      exporters: [file/logs]
YAML
  echo "== wrote $OTEL_DIR/config.yaml"
fi

if docker ps --format '{{.Names}}' | grep -qx "$CONTAINER"; then
  echo "== collector already running"
elif docker ps -a --format '{{.Names}}' | grep -qx "$CONTAINER"; then
  docker start "$CONTAINER" >/dev/null && echo "== collector started"
else
  docker run -d --name "$CONTAINER" --restart unless-stopped \
    -p 4317:4317 \
    -v "$OTEL_DIR/config.yaml:/etc/otelcol-contrib/config.yaml" \
    -v "$OTEL_DIR/data:/data" \
    "$IMAGE" >/dev/null
  echo "== collector created"
fi

# 3. Telemetry on for every session. Merge, so existing env vars survive.
python3 - "$SETTINGS" <<'PY'
import json, pathlib, shutil, sys

WANT = {
    "CLAUDE_CODE_ENABLE_TELEMETRY": "1",
    "OTEL_METRICS_EXPORTER": "otlp",
    "OTEL_LOGS_EXPORTER": "otlp",
    "OTEL_EXPORTER_OTLP_PROTOCOL": "grpc",
    "OTEL_EXPORTER_OTLP_ENDPOINT": "http://localhost:4317",
    "OTEL_METRIC_EXPORT_INTERVAL": "10000",
}

path = pathlib.Path(sys.argv[1])
settings = json.loads(path.read_text()) if path.exists() and path.stat().st_size else {}
env = settings.setdefault("env", {})
missing = {k: v for k, v in WANT.items() if env.get(k) != v}
if not missing:
    print("== telemetry already enabled")
    raise SystemExit
if path.exists():
    shutil.copy2(path, path.with_suffix(".json.bak"))
    print(f"== backed up {path} -> {path.name}.bak")
env.update(missing)
path.parent.mkdir(parents=True, exist_ok=True)
path.write_text(json.dumps(settings, indent=2) + "\n")
print(f"== telemetry enabled in {path} ({len(missing)} var(s) added)")
PY

echo
echo "done. usage:"
echo "  cd <your repo> && bench      # work as usual, get the numbers on exit"
echo "  bench --no-launch            # measure the last session you forgot to wrap"
echo "  bench report                 # median + IQR over everything collected"
