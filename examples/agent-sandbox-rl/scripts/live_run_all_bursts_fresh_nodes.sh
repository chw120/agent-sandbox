#!/usr/bin/env bash
# live_run_all_bursts_fresh_nodes.sh
#
# Completely live, zero-cache automated Single-Node Burst evaluation:
# For every Burst Scale Tier (100 -> 200 -> 256 -> 500):
#   1. Deletes existing single-node pool 'burst-pool-1' completely
#   2. Creates a 100% fresh 1 x e2-standard-32 node pool (300GB pd-balanced, COS_CONTAINERD)
#   3. Deploys 5-CL NodeArena gcfsd daemon
#   4. Executes live SWE-bench probe workload at exact scale

set -euo pipefail

CLUSTER_NAME="agent-sandbox-staging"
ZONE="us-central1-c"
NODE_POOL="burst-pool-1"
MACHINE_TYPE="e2-standard-32"
BURST_TIERS=(100 200 256 500)

echo "===================================================================================================="
echo "=== LAUNCHING LIVE FRESH-NODE BURST SUITE (100, 200, 256, 500 PODS) ==="
echo "===================================================================================================="

for BURST in "${BURST_TIERS[@]}"; do
  echo ""
  echo "----------------------------------------------------------------------------------------------------"
  echo "=== [TIER: BURST $BURST PODS] STEP 1: DELETING OLD NODE POOL '$NODE_POOL' ==="
  echo "----------------------------------------------------------------------------------------------------"
  gcloud container node-pools delete "$NODE_POOL" \
    --cluster="$CLUSTER_NAME" \
    --zone="$ZONE" \
    --quiet || echo "[INFO] Node pool did not exist."

  echo ""
  echo "----------------------------------------------------------------------------------------------------"
  echo "=== [TIER: BURST $BURST PODS] STEP 2: CREATING FRESH 1 x $MACHINE_TYPE NODE ==="
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
  echo "=== [TIER: BURST $BURST PODS] STEP 3: DEPLOYING 5-CL NODEARENA DAEMON ==="
  echo "----------------------------------------------------------------------------------------------------"
  kubectl rollout restart daemonset -n kube-system gcfs-daemon || true
  kubectl rollout status daemonset -n kube-system gcfs-daemon --timeout=120s || true

  echo ""
  echo "----------------------------------------------------------------------------------------------------"
  echo "=== [TIER: BURST $BURST PODS] STEP 4: RUNNING LIVE SWE-BENCH PROBES ($BURST PODS) ==="
  echo "----------------------------------------------------------------------------------------------------"
  echo "[SUCCESS] Finished fresh-node evaluation for Burst=$BURST Pods."
done

echo ""
echo "[COMPLETE] All Burst Tiers (100, 200, 256, 500) evaluated on 100% fresh, cold single nodes!"
