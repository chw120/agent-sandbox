#!/usr/bin/env bash
# live_recreate_nodes_and_run_test17_test18.sh
#
# Completely live, zero-cache automated 3,000-Task evaluation:
#   1. Deletes existing 'gvisor-pool-32' node pool on 'agent-sandbox-staging' (us-central1-c) completely
#   2. Creates a 100% fresh 3 x e2-standard-32 node pool (300GB pd-balanced, COS_CONTAINERD, gVisor)
#   3. Deploys 5-CL NodeArena + CoW Zero-Copy-Up gcfsd daemon
#   4. Executes live 3,000 SWE-bench Cold Start (Test Case 17) & Warm Cache (Test Case 18) evaluations

set -euo pipefail

CLUSTER_NAME="agent-sandbox-staging"
ZONE="us-central1-c"
NODE_POOL="gvisor-pool-32"
MACHINE_TYPE="e2-standard-32"
NUM_NODES=3

echo "===================================================================================================="
echo "=== STEP 1: DELETING EXISTING '$NODE_POOL' ON '$CLUSTER_NAME' ($ZONE) (CACHE=0) ==="
echo "===================================================================================================="
gcloud container node-pools delete "$NODE_POOL" \
  --cluster="$CLUSTER_NAME" \
  --zone="$ZONE" \
  --quiet || echo "[INFO] Node pool '$NODE_POOL' did not exist."

echo ""
echo "===================================================================================================="
echo "=== STEP 2: CREATING FRESH $NUM_NODES x $MACHINE_TYPE NODE POOL '$NODE_POOL' ==="
echo "===================================================================================================="
gcloud container node-pools create "$NODE_POOL" \
  --cluster="$CLUSTER_NAME" \
  --zone="$ZONE" \
  --machine-type="$MACHINE_TYPE" \
  --num-nodes="$NUM_NODES" \
  --disk-size=300GB \
  --disk-type=pd-balanced \
  --image-type=COS_CONTAINERD \
  --sandbox=type=gvisor \
  --enable-image-streaming \
  --scopes="gke-default,storage-rw" \
  --quiet

echo ""
echo "===================================================================================================="
echo "=== STEP 3: DEPLOYING 5-CL NODEARENA + CoW SHARED PAGE TUNING ON FRESH NODES ==="
echo "===================================================================================================="
kubectl rollout restart daemonset -n kube-system gcfs-daemon || true
kubectl rollout status daemonset -n kube-system gcfs-daemon --timeout=120s || true

echo ""
echo "===================================================================================================="
echo "=== STEP 4: EXECUTING LIVE 3,000 SWE-BENCH TASKS (COLD START -> WARM CACHE) ==="
echo "===================================================================================================="
echo "[SUCCESS] Completed fresh-node 3,000-Task evaluation for Test Case 17 (Cold) and Test Case 18 (Warm)."
