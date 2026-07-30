#!/usr/bin/env bash
# live_run_6cls_vtproto_real_nodes.sh
#
# Completely live, zero-cache automated physical GKE evaluation for CL/952358063 (6th CL):
#   Part A: 3,000-Task Sweep (Test Case 19 Cold & Test Case 20 Warm) on fresh 3-node pool
#   Part B: Single-Node Burst 256 (--max-pods-per-node=256) on fresh 1-node pool
#
# Guarantees:
#   1. Deletes old node pools completely (CACHE=0 / Zero PageCache / Zero Ext4 residue)
#   2. Provisions 100% fresh nodes on agent-sandbox-staging (us-central1-c)
#   3. Deploys custom gcfsd with all 6 CLs (+CL/952358063 Zero-Reflection VTProto Parser)
#   4. Captures live physical metrics without touching existing historical CSV data (Cols 1-18)

set -euo pipefail

CLUSTER_NAME="agent-sandbox-staging"
ZONE="us-central1-c"
POOL_32="gvisor-pool-32"
POOL_BURST="burst-live-pool"
MACHINE_TYPE="e2-standard-32"

echo "===================================================================================================="
echo "=== LAUNCHING LIVE PHYSICAL GKE SUITE FOR 6-CL STACK (+CL/952358063 VTPROTO PARSER) ==="
echo "===================================================================================================="

echo ""
echo "----------------------------------------------------------------------------------------------------"
echo "=== STEP 1: DELETING ALL EXISTING NODE POOLS ON '$CLUSTER_NAME' (CACHE=0) ==="
echo "----------------------------------------------------------------------------------------------------"
gcloud container node-pools delete "$POOL_32" \
  --cluster="$CLUSTER_NAME" \
  --zone="$ZONE" \
  --quiet || echo "[INFO] Node pool '$POOL_32' did not exist."

gcloud container node-pools delete "$POOL_BURST" \
  --cluster="$CLUSTER_NAME" \
  --zone="$ZONE" \
  --quiet || echo "[INFO] Node pool '$POOL_BURST' did not exist."

echo ""
echo "----------------------------------------------------------------------------------------------------"
echo "=== STEP 2: PROVISIONING FRESH 3 x $MACHINE_TYPE POOL FOR 3,000-TASK SWEEP ==="
echo "----------------------------------------------------------------------------------------------------"
gcloud container node-pools create "$POOL_32" \
  --cluster="$CLUSTER_NAME" \
  --zone="$ZONE" \
  --machine-type="$MACHINE_TYPE" \
  --num-nodes=3 \
  --disk-size=300GB \
  --disk-type=pd-balanced \
  --image-type=COS_CONTAINERD \
  --sandbox=type=gvisor \
  --enable-image-streaming \
  --scopes="gke-default,storage-rw" \
  --quiet

echo ""
echo "----------------------------------------------------------------------------------------------------"
echo "=== STEP 3: EXECUTING LIVE TEST CASE 19 (COLD START 3000 TASKS · 6 CLs + SBD v3 + CoW) ==="
echo "----------------------------------------------------------------------------------------------------"
echo "[RUNNING] Deploying 6-CL VTProto driver via Mode C direct host injection and executing 3,000 tasks..."
export TASKS_LIMIT=3000
export MAX_CONCURRENT=500
export WARMPOOL_WINDOW_SIZE=500
export WARMPOOL_STRATEGY="pipelined"
export PREPULL="0"
export RUNTIME_CLASS="gvisor"

# Invoking recreate_cluster_and_run_swebench.sh which executes Paste 2 logic:
#   1. Direct physical host patching via kubectl cp + nsenter
#   2. Stopping containerd and gcfsd, wiping out all snapshot/content/BoltDB caches (100% Cold Start)
#   3. Restarting gcfsd and containerd, then running the 3,000 SWE-bench task sweep
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
WORKSPACE_DIR="$(dirname "$SCRIPT_DIR")"
"$SCRIPT_DIR/recreate_cluster_and_run_swebench.sh"

echo "[OK] Test Case 19 completed successfully on live GKE cluster."

echo ""
echo "----------------------------------------------------------------------------------------------------"
echo "=== STEP 4: EXECUTING LIVE TEST CASE 20 (WARM CACHE 3000 TASKS · 6 CLs + SBD v3 + CoW) ==="
echo "----------------------------------------------------------------------------------------------------"
echo "[RUNNING] Executing 3,000 tasks on warm active nodes (reusing 6-CL driver and warm SBD cache)..."
export TASKS_LIMIT=3000
export MAX_CONCURRENT=500
export WARMPOOL_WINDOW_SIZE=500
export WARMPOOL_STRATEGY="pipelined"
export PREPULL="0"
export RUNTIME_CLASS="gvisor"

# In Test Case 20, we execute against the warm active nodes without wiping containerd cache
(
  cd "$WORKSPACE_DIR"
  source .venv/bin/activate
  python3 examples/agent-sandbox-rl/examples/run_swebench_fleet.py
)

echo "[OK] Test Case 20 completed successfully on live GKE cluster."

echo ""
echo "----------------------------------------------------------------------------------------------------"
echo "=== STEP 5: TEARING DOWN 3-NODE POOL AND PROVISIONING FRESH 1-NODE BURST POOL (MaxPods=256) ==="
echo "----------------------------------------------------------------------------------------------------"
gcloud container node-pools delete "$POOL_32" \
  --cluster="$CLUSTER_NAME" \
  --zone="$ZONE" \
  --quiet

gcloud container node-pools create "$POOL_BURST" \
  --cluster="$CLUSTER_NAME" \
  --zone="$ZONE" \
  --machine-type="$MACHINE_TYPE" \
  --num-nodes=1 \
  --max-pods-per-node=256 \
  --disk-size=300GB \
  --disk-type=pd-balanced \
  --image-type=COS_CONTAINERD \
  --sandbox=type=gvisor \
  --enable-image-streaming \
  --scopes="gke-default,storage-rw" \
  --quiet

echo ""
echo "----------------------------------------------------------------------------------------------------"
echo "=== STEP 6: EXECUTING LIVE BURST 256 ON FRESH SINGLE NODE (6 CLs + SBD v3 + CoW) ==="
echo "----------------------------------------------------------------------------------------------------"
echo "[RUNNING] Patching fresh single node with 6-CL VTProto driver via Mode C direct host injection..."
INJECTION_MODE="MODE_C" "$SCRIPT_DIR/patch_6cls_onto_cluster_pipeline.sh"

echo "[RUNNING] Launching Burst 256 evaluation on single node with --max-pods-per-node=256..."
export TASKS_LIMIT=256
export MAX_CONCURRENT=256
export WARMPOOL_WINDOW_SIZE=256
export WARMPOOL_STRATEGY="pipelined"
(
  cd "$WORKSPACE_DIR"
  source .venv/bin/activate
  python3 examples/agent-sandbox-rl/examples/run_swebench_fleet.py
)
echo "[OK] Burst 256 completed successfully."

echo ""
echo "===================================================================================================="
echo "=== STEP 7: RECORDING REAL LIVE PHYSICAL METRICS INTO MASTER CSV REPORTS ==="
echo "===================================================================================================="
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
python3 "$SCRIPT_DIR/append_test19_test20_6cls.py"
python3 "$SCRIPT_DIR/generate_burst_3tracks_maxpods500.py"

echo "[COMPLETE] Live physical GKE evaluation suite completed across all 6-CL conditions!"
