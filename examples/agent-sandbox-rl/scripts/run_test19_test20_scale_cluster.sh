#!/usr/bin/env bash
# run_test19_test20_scale_cluster.sh
#
# Automated wrapper to run Test Case 19 (Cold 3,000 Tasks) and Test Case 20 (Warm 3,000 Tasks)
# on agent-sandbox-scale-cluster using the dedicated Python venv.
# Automatically tees stdout/stderr to a timestamped log file in performance_reports/.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/../../.." && pwd)"
REPORTS_DIR="$REPO_ROOT/examples/agent-sandbox-rl/performance_reports"
mkdir -p "$REPORTS_DIR"

cd "$REPO_ROOT"

export PYTHONPATH="examples/agent-sandbox-rl:examples/agent-sandbox-rl/examples:${PYTHONPATH:-}"
export PYTHONDONTWRITEBYTECODE=1
export TMPDIR=/tmp

PYTHON_BIN="$REPO_ROOT/bin/python-venv-agent-sandbox-rl/bin/python3"
if [ ! -f "$PYTHON_BIN" ]; then
  PYTHON_BIN="python3"
fi

TIMESTAMP=$(date -u +"%Y%m%d-%H%M%S")
LOG_FILE="$REPORTS_DIR/test19_test20_scale_run_${TIMESTAMP}.log"
LATEST_LOG="$REPORTS_DIR/test19_test20_scale_run_latest.log"

echo "===================================================================================================="
echo "=== EXECUTING TEST 19 (COLD 3000) & TEST 20 (WARM 3000) ON AGENT-SANDBOX-SCALE-CLUSTER ==="
echo "===================================================================================================="
echo "Python binary: $PYTHON_BIN"
echo "Working directory: $REPO_ROOT"
echo "Log file: $LOG_FILE"
echo ""

# Run and tee to both console and log file
$PYTHON_BIN -u examples/agent-sandbox-rl/scripts/run_test19_test20_scale_cluster.py \
  --context="gke_chenyiwang-gke-dev_us-central1-c_agent-sandbox-scale-cluster" \
  --namespace="default" \
  --node-selector="cloud.google.com/gke-nodepool=gvisor-scale-pool-32" \
  --images-file="examples/agent-sandbox-rl/swebench500_digests.txt" \
  --problems=500 \
  --rollouts=6 \
  --concurrency=500 \
  --claim-concurrency=500 \
  --cpu="125m" \
  --memory="1Gi" \
  --enable-cow=true \
  --strategies="pipelined" \
  --window-size=50 \
  --reports-dir="$REPORTS_DIR" \
  "$@" 2>&1 | tee "$LOG_FILE"

cp -f "$LOG_FILE" "$LATEST_LOG" 2>/dev/null || true
echo ""
echo "[COMPLETED] Full execution log saved to: $LOG_FILE"
echo "[COMPLETED] Latest log symlink: $LATEST_LOG"
