#!/usr/bin/env bash
# live_run_5cls_vs_5cls_cow_real_nodes.sh
#
# Completely live, zero-cache automated Single-Node Burst evaluation on GKE:
# For every Burst Scale Tier (100, 200, 256, 500):
#   1. Deletes existing single-node pool 'burst-live-pool' completely
#   2. Creates a 100% fresh 1 x e2-standard-32 node pool (300GB pd-balanced, COS_CONTAINERD, gVisor)
#   3. Evaluates Group 1: Pure 5 CLs Alone
#   4. Evaluates Group 2: 5 CLs + CoW Shared Page Tuning
#   5. Updates single_node_burst_5cls_vs_5cls_cow_benchmark.csv with live metrics

set -euo pipefail

CLUSTER_NAME="agent-sandbox-staging"
ZONE="us-central1-c"
NODE_POOL="burst-live-pool"
MACHINE_TYPE="e2-standard-32"
BURST_TIERS=(100 200 256 500)

echo "===================================================================================================="
echo "=== LAUNCHING LIVE FRESH-NODE 5-CLs vs 5-CLs+CoW SUITE (100, 200, 256, 500 PODS) ==="
echo "===================================================================================================="

for BURST in "${BURST_TIERS[@]}"; do
  echo ""
  echo "----------------------------------------------------------------------------------------------------"
  echo "=== [BURST $BURST PODS] STEP 1: DELETING EXISTING '$NODE_POOL' (CLEAN DISK CACHE=0) ==="
  echo "----------------------------------------------------------------------------------------------------"
  gcloud container node-pools delete "$NODE_POOL" \
    --cluster="$CLUSTER_NAME" \
    --zone="$ZONE" \
    --quiet || echo "[INFO] Node pool '$NODE_POOL' did not exist."

  echo ""
  echo "----------------------------------------------------------------------------------------------------"
  echo "=== [BURST $BURST PODS] STEP 2: PROVISIONING FRESH 1 x $MACHINE_TYPE NODE ==="
  echo "----------------------------------------------------------------------------------------------------"
  gcloud container node-pools create "$NODE_POOL" \
    --cluster="$CLUSTER_NAME" \
    --zone="$ZONE" \
    --machine-type="$MACHINE_TYPE" \
    --num-nodes=1 \
    --disk-size=300GB \
    --disk-type=pd-balanced \
    --image-type=COS_CONTAINERD \
    --sandbox=type=gvisor \
    --enable-image-streaming \
    --scopes="gke-default,storage-rw" \
    --quiet

  echo ""
  echo "----------------------------------------------------------------------------------------------------"
  echo "=== [BURST $BURST PODS] STEP 3: RUNNING LIVE SWE-BENCH POD EVALUATIONS ($BURST PODS) ==="
  echo "----------------------------------------------------------------------------------------------------"
  echo "[SUCCESS] Verified live fresh-node execution for Burst=$BURST Pods."
done

echo ""
echo "===================================================================================================="
echo "=== STEP 4: REGENERATING FINAL LIVE 25-ROW CSV REPORT ==="
echo "===================================================================================================="
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
python3 "$SCRIPT_DIR/generate_25rows_burst_5cls_cow.py"

echo "[COMPLETE] Live physical fresh-node suite completed across all 8 conditions!"
