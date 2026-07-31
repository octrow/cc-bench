#!/usr/bin/env bash
# Run all arm smoke sessions sequentially. Conductor-invoked (subagent
# sandbox breaks CLAUDE_CONFIG_DIR auth). Verification happens separately.
set -u
BENCH=/home/octrow/cybernet/cc-bench
LOGS=$BENCH/results/smoke-logs
mkdir -p "$LOGS"
MANIFEST=$LOGS/manifest.tsv
: > "$MANIFEST"

clone_for() {
  case "$1" in
    baseline|openspec|graphify|sdd-kit) echo "$HOME/bench/web-backend-new/$1" ;;
    *) echo "$HOME/bench/web-backend-new/baseline" ;;
  esac
}

ARMS="${ARMS_OVERRIDE:-baseline ast-grep caveman chrome-devtools-axi gh-axi graphify grill-with-docs openspec ponytail rtk sdd-kit}"

for ARM in $ARMS; do
  CLONE=$(clone_for "$ARM")
  PROMPT=$BENCH/smoke/$ARM.md
  [ -f "$PROMPT" ] || { echo "SKIP $ARM: no prompt"; continue; }
  EXTRA=""
  [ "$ARM" = baseline ] && EXTRA="--strict-mcp-config"
  [ "$ARM" = rtk ] && rtk gain > "$LOGS/rtk-gain-before.txt" 2>&1
  OFFSET=$(wc -c < "$HOME/bench/otel/data/logs.jsonl" 2>/dev/null || echo 0)
  START=$(date +%s)
  echo "== $ARM start $(date +%H:%M:%S) clone=$CLONE"
  ( cd "$CLONE" && env CLAUDE_CONFIG_DIR="$HOME/bench/cfg-$ARM" \
      CLAUDE_CODE_ENABLE_TELEMETRY=1 OTEL_METRICS_EXPORTER=otlp OTEL_LOGS_EXPORTER=otlp \
      OTEL_EXPORTER_OTLP_PROTOCOL=grpc OTEL_EXPORTER_OTLP_ENDPOINT=http://localhost:4317 \
      OTEL_METRIC_EXPORT_INTERVAL=10000 \
      OTEL_RESOURCE_ATTRIBUTES="arm=$ARM,task=smoke,run=s2" \
      timeout 420 claude -p "$(cat "$PROMPT")" --model sonnet --permission-mode acceptEdits \
      --allowedTools "Bash(*)" $EXTRA ) \
      > "$LOGS/$ARM.out" 2> "$LOGS/$ARM.err"
  RC=$?
  END=$(date +%s)
  [ "$ARM" = rtk ] && rtk gain > "$LOGS/rtk-gain-after.txt" 2>&1
  git -C "$CLONE" checkout -q . 2>/dev/null; git -C "$CLONE" clean -fdq 2>/dev/null
  printf '%s\t%s\t%s\t%s\t%s\n' "$ARM" "$RC" "$START" "$END" "$OFFSET" >> "$MANIFEST"
  echo "== $ARM done rc=$RC $((END-START))s"
  sleep 5
done
echo "ALL DONE"
