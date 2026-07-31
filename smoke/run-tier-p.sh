#!/usr/bin/env bash
# Tier P measured runs: frozen WBN probes x (baseline + exercising arm) x N runs.
# Conductor-invoked (subagent sandbox breaks CLAUDE_CONFIG_DIR auth).
# Interleaving: per (probe, run#) we alternate baseline-first / arm-first.
set -u
BENCH=/home/octrow/cybernet/cc-bench
PROBES=$BENCH/probes/web-backend-new
LOGS=$BENCH/results/tier-p-logs
RUNS=${RUNS:-3}
MODEL=${MODEL:-sonnet}
mkdir -p "$LOGS"
MANIFEST=$LOGS/manifest.tsv
touch "$MANIFEST"

# probe_id -> probe file : arms (baseline always implied)
PAIRS="
nav:graphify
nav:ast-grep
bash:rtk
gh:gh-axi
web:chrome-devtools-axi
long:headroom
"

clone_for() {
  case "$1" in
    baseline|openspec|graphify|sdd-kit) echo "$HOME/bench/web-backend-new/$1" ;;
    *) echo "$HOME/bench/web-backend-new/baseline" ;;
  esac
}

prompt_of() { # extract `prompt:` scalar (block or inline) from probe yaml
  python3 - "$1" <<'EOF'
import sys, yaml
print(yaml.safe_load(open(sys.argv[1]))["prompt"])
EOF
}

run_one() { # ARM PROBE_ID RUN_N
  local ARM=$1 PID=$2 N=$3
  local KEY="$PID-$ARM-r$N"
  if grep -q "^$KEY	" "$MANIFEST"; then echo "== skip $KEY (done)"; return; fi
  local CLONE PROMPT EXTRA=""
  CLONE=$(clone_for "$ARM")
  PROMPT=$(prompt_of "$PROBES/$PID.yaml") || { echo "FAIL prompt $PID"; return; }
  [ "$ARM" = baseline ] && EXTRA="--strict-mcp-config"
  git -C "$CLONE" checkout -q bench-base 2>/dev/null; git -C "$CLONE" clean -fdq 2>/dev/null
  local START END RC
  START=$(date +%s)
  echo "== $KEY start $(date +%H:%M:%S)"
  ( cd "$CLONE" && env CLAUDE_CONFIG_DIR="$HOME/bench/cfg-$ARM" \
      CLAUDE_CODE_ENABLE_TELEMETRY=1 OTEL_METRICS_EXPORTER=otlp OTEL_LOGS_EXPORTER=otlp \
      OTEL_EXPORTER_OTLP_PROTOCOL=grpc OTEL_EXPORTER_OTLP_ENDPOINT=http://localhost:4317 \
      OTEL_METRIC_EXPORT_INTERVAL=10000 \
      OTEL_RESOURCE_ATTRIBUTES="arm=$ARM,task=$PID,run=r$N" \
      timeout 600 claude -p "$PROMPT" --model "$MODEL" --permission-mode acceptEdits \
      --allowedTools "Bash(*)" --add-dir "$PROBES/assets" $EXTRA ) \
      > "$LOGS/$KEY.out" 2> "$LOGS/$KEY.err"
  RC=$?
  END=$(date +%s)
  git -C "$CLONE" checkout -q . 2>/dev/null; git -C "$CLONE" clean -fdq 2>/dev/null
  printf '%s\t%s\t%s\t%s\t%s\t%s\n' "$KEY" "$ARM" "$PID" "$RC" "$START" "$END" >> "$MANIFEST"
  echo "== $KEY done rc=$RC $((END-START))s"
  sleep 5
}

for N in $(seq 1 "$RUNS"); do
  for PAIR in $PAIRS; do
    PID=${PAIR%%:*}; ARM=${PAIR##*:}
    if [ $(( N % 2 )) -eq 1 ]; then
      run_one baseline "$PID" "$N"; run_one "$ARM" "$PID" "$N"
    else
      run_one "$ARM" "$PID" "$N"; run_one baseline "$PID" "$N"
    fi
  done
done
echo "TIER-P ALL DONE"
