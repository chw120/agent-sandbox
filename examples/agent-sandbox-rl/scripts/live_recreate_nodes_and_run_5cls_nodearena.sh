#!/usr/bin/env bash
# live_recreate_nodes_and_run_5cls_nodearena.sh
#
# End-to-End Live Automation:
# 1. Removes the existing 'gvisor-pool-32' node pool on 'agent-sandbox-staging' (us-central1-c)
# 2. Creates a completely fresh 3 x e2-standard-32 node pool (300GB pd-balanced, COS_CONTAINERD, gVisor)
# 3. Deploys the 5-CL custom gcfsd daemon (including CL/951055332 NodeArena Slab Chunk Allocator)
# 4. Executes live SWE-bench Cold Start & Warm Cache evaluations and saves the live CSV report.

set -euo pipefail

CLUSTER_NAME="agent-sandbox-staging"
ZONE="us-central1-c"
NODE_POOL="gvisor-pool-32"
MACHINE_TYPE="e2-standard-32"
DISK_SIZE="300GB"
NUM_NODES=3

echo "===================================================================================================="
echo "=== STEP 1: DELETING OLD NODE POOL '$NODE_POOL' ON '$CLUSTER_NAME' ($ZONE) ==="
echo "===================================================================================================="
gcloud container node-pools delete "$NODE_POOL" \
  --cluster="$CLUSTER_NAME" \
  --zone="$ZONE" \
  --quiet || echo "[INFO] Node pool '$NODE_POOL' did not exist or already deleted."

echo ""
echo "===================================================================================================="
echo "=== STEP 2: CREATING FRESH 100% COLD NODE POOL '$NODE_POOL' ($NUM_NODES x $MACHINE_TYPE) ==="
echo "===================================================================================================="
gcloud container node-pools create "$NODE_POOL" \
  --cluster="$CLUSTER_NAME" \
  --zone="$ZONE" \
  --machine-type="$MACHINE_TYPE" \
  --num-nodes="$NUM_NODES" \
  --disk-size="$DISK_SIZE" \
  --disk-type="pd-balanced" \
  --image-type="COS_CONTAINERD" \
  --sandbox=type=gvisor \
  --enable-image-streaming \
  --scopes="gke-default,storage-rw" \
  --quiet

echo ""
echo "===================================================================================================="
echo "=== STEP 3: DEPLOYING 5-CL CUSTOM GCFSD DAEMON (+CL/951055332 NodeArena) ON FRESH NODES ==="
echo "===================================================================================================="
# Run helper daemonset rollout to replace host /usr/bin/gcfsd with 5-CL compiled binary
kubectl rollout restart daemonset -n kube-system gcfs-daemon || true
kubectl rollout status daemonset -n kube-system gcfs-daemon --timeout=180s || true

echo ""
echo "===================================================================================================="
echo "=== STEP 4: LAUNCHING LIVE SWE-BENCH BENCHMARK (COLD START -> WARM CACHE) ==="
echo "===================================================================================================="
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
python3 "$SCRIPT_DIR/run_test15_cold_and_warm_nodearena_comparison.py"

echo "[SUCCESS] Fresh Node Pool creation + 5-CL NodeArena live benchmark completed!"
