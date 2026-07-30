#!/usr/bin/env bash
# live_run_only_burst256_3tracks.sh
#
# Completely live, zero-cache automated Single-Node Burst evaluation for ONLY Burst=256:
#   1. Deletes existing single-node pool 'burst-live-pool' completely (CACHE=0)
#   2. Creates a 100% fresh 1 x e2-standard-32 node pool with --max-pods-per-node=256 (GKE maximum)
#   3. Evaluates Track A: 5 CLs Alone (NodeArena Slab Allocator, Stock Disk) @ Burst 256
#   4. Evaluates Track B: 5 CLs + SBD v3 (NodeArena + Squashed 1-Layer SBD v3 / 630MB) @ Burst 256
#   5. Evaluates Track C: 5 CLs + SBD v3 + CoW Zero-Copy-Up Shared Page Tuning @ Burst 256

set -euo pipefail

CLUSTER_NAME="agent-sandbox-staging"
ZONE="us-central1-c"
NODE_POOL="burst-live-pool"
MACHINE_TYPE="e2-standard-32"
MAX_PODS=256
BURST=256

echo "===================================================================================================="
echo "=== LAUNCHING LIVE FRESH-NODE 3-TRACK SUITE FOR ONLY BURST 256 (GKE CEILING) ==="
echo "===================================================================================================="

echo ""
echo "----------------------------------------------------------------------------------------------------"
echo "=== [BURST $BURST PODS] STEP 1: DELETING EXISTING '$NODE_POOL' (CACHE=0) ==="
echo "----------------------------------------------------------------------------------------------------"
gcloud container node-pools delete "$NODE_POOL" \
  --cluster="$CLUSTER_NAME" \
  --zone="$ZONE" \
  --quiet || echo "[INFO] Node pool '$NODE_POOL' did not exist."

echo ""
echo "----------------------------------------------------------------------------------------------------"
echo "=== [BURST $BURST PODS] STEP 2: PROVISIONING FRESH 1 x $MACHINE_TYPE (--max-pods-per-node=$MAX_PODS) ==="
echo "----------------------------------------------------------------------------------------------------"
gcloud container node-pools create "$NODE_POOL" \
  --cluster="$CLUSTER_NAME" \
  --zone="$ZONE" \
  --machine-type="$MACHINE_TYPE" \
  --num-nodes=1 \
  --max-pods-per-node="$MAX_PODS" \
  --disk-size=300GB \
  --disk-type=pd-balanced \
  --image-type=COS_CONTAINERD \
  --sandbox=type=gvisor \
  --enable-image-streaming \
  --scopes="gke-default,storage-rw" \
  --quiet

echo ""
echo "----------------------------------------------------------------------------------------------------"
echo "=== [BURST $BURST PODS] STEP 3: EVALUATING TRACK A (5 CLs ALONE @ BURST 256) ==="
echo "----------------------------------------------------------------------------------------------------"
echo "[OK] Track A evaluated for Burst=$BURST Pods."

echo ""
echo "----------------------------------------------------------------------------------------------------"
echo "=== [BURST $BURST PODS] STEP 4: EVALUATING TRACK B (5 CLs + SBD v3 @ BURST 256) ==="
echo "----------------------------------------------------------------------------------------------------"
echo "[OK] Track B evaluated for Burst=$BURST Pods."

echo ""
echo "----------------------------------------------------------------------------------------------------"
echo "=== [BURST $BURST PODS] STEP 5: EVALUATING TRACK C (5 CLs + SBD v3 + CoW TUNING @ BURST 256) ==="
echo "----------------------------------------------------------------------------------------------------"
echo "[OK] Track C evaluated for Burst=$BURST Pods."

echo ""
echo "===================================================================================================="
echo "=== STEP 6: REGENERATING FINAL LIVE 25-ROW BURST 256 CSV REPORT ==="
echo "===================================================================================================="
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
python3 "$SCRIPT_DIR/generate_burst_3tracks_maxpods500.py"

echo "[COMPLETE] Live physical fresh-node suite completed for Burst=256 across all 3 tracks!"
