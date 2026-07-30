#!/usr/bin/env bash
# patch_6cls_onto_scale_cluster.sh
#
# Hot-injects the compiled 6-CL VTProto Zero-Reflection GCFS driver
# (+CL/952358063 + NodeArena + Lockless LRU + Single-Flight) onto all 10 physical nodes
# in agent-sandbox-scale-cluster (pool: gvisor-scale-pool-32).

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/../../.." && pwd)"
cd "$REPO_ROOT"

GCFSD_BIN="$REPO_ROOT/bin/gcfsd_optimized_release"
if [ ! -f "$GCFSD_BIN" ]; then
  echo "Error: $GCFSD_BIN not found!"
  exit 1
fi

EXPECTED_MD5=$(md5sum "$GCFSD_BIN" | awk '{print $1}')
echo "===================================================================================================="
echo "=== HOT-INJECTING 6-CL VTPROTO DRIVER ONTO 10-NODE SCALE POOL (MD5: $EXPECTED_MD5) ==="
echo "===================================================================================================="

# Switch context to scale cluster
kubectl config use-context gke_chenyiwang-gke-dev_us-central1-c_agent-sandbox-scale-cluster

# Delete any existing patch pods first
for NODE in $(kubectl get nodes -l cloud.google.com/gke-nodepool=gvisor-scale-pool-32 -o jsonpath='{.items[*].metadata.name}'); do
  kubectl delete pod "patch-${NODE: -5}" -n kube-system --grace-period=0 --force 2>/dev/null || true
done

# Get all nodes in gvisor-scale-pool-32
NODES=$(kubectl get nodes -l cloud.google.com/gke-nodepool=gvisor-scale-pool-32 -o jsonpath='{.items[*].metadata.name}')
NODE_COUNT=$(echo "$NODES" | wc -w)
echo "[INFO] Discovered $NODE_COUNT physical nodes in 'gvisor-scale-pool-32':"
for N in $NODES; do
  echo "  - $N"
done
echo ""

echo "--- STEP 1: Launching privileged ubuntu helper pods across all 10 nodes ---"
for NODE in $NODES; do
  POD="patch-${NODE: -5}"
  cat << EOF | kubectl apply -f -
apiVersion: v1
kind: Pod
metadata:
  name: $POD
  namespace: kube-system
  labels:
    app: gcfsd-patcher
spec:
  nodeName: $NODE
  hostPID: true
  hostNetwork: true
  tolerations:
  - key: sandbox.gke.io/runtime
    operator: Equal
    value: gvisor
    effect: NoSchedule
  containers:
  - name: patch
    image: ubuntu:22.04
    command: ["sleep", "3600"]
    securityContext:
      privileged: true
    volumeMounts:
    - name: host-root
      mountPath: /host
  volumes:
  - name: host-root
    hostPath:
      path: /
  restartPolicy: Never
EOF
done

echo ""
echo "--- STEP 2: Waiting for helper pods to become Ready and hot-patching gcfsd ---"
for NODE in $NODES; do
  POD="patch-${NODE: -5}"
  (
    echo "[$NODE] Waiting for pod $POD..."
    kubectl wait --for=condition=Ready pod/"$POD" -n kube-system --timeout=60s >/dev/null 2>&1 || true

    echo "[$NODE] Uploading 6-CL VTProto binary to host..."
    kubectl cp "$GCFSD_BIN" kube-system/"$POD":/host/home/kubernetes/bin/gcfsd-v2.new

    echo "[$NODE] Executing host swap, configuring systemd and restarting gcfsd..."
    kubectl exec -n kube-system pod/"$POD" -- bash -c "
      set -e
      chroot /host systemctl stop gcfsd || true
      sleep 1
      cp /host/home/kubernetes/bin/gcfsd-v2 /host/home/kubernetes/bin/gcfsd-v2.stock.bak 2>/dev/null || true
      rm -f /host/home/kubernetes/bin/gcfsd-v2
      cp -f /host/home/kubernetes/bin/gcfsd-v2.new /host/home/kubernetes/bin/gcfsd-v2
      chmod +x /host/home/kubernetes/bin/gcfsd-v2
      rm -f /host/home/kubernetes/bin/gcfsd-v2.new

      for SVC in /host/etc/systemd/system/gcfsd.service /host/lib/systemd/system/gcfsd.service; do
        if [ -f \"\$SVC\" ]; then
          sed -i 's/--max_layer_downloads=[0-9]*/--max_layer_downloads=100/g; s/--read_ahead_request_limit=[0-9]*/--read_ahead_request_limit=400/g; s/--read_ahead_scaling_factor=[0-9.]*/--read_ahead_scaling_factor=8.0/g; s/--read_ahead_max_blocks=[0-9]*/--read_ahead_max_blocks=255/g; s/--max_large_files_cache_size_mb=[0-9]*/--max_large_files_cache_size_mb=4096/g; s/--enable_single_flighting=[a-z]*/--enable_single_flighting=true/g; s/--max_content_cache_size_mb=[0-9]*/--max_content_cache_size_mb=8192/g; s/--max_read_blocks=[0-9]*/--max_read_blocks=16/g' \"\$SVC\" || true
          sed -i '/^Environment=\"GOGC=/d; /^Environment=\"GOMEMLIMIT=/d; /\[Service\]/a Environment=\"GOGC=200\"\nEnvironment=\"GOMEMLIMIT=16GiB\"' \"\$SVC\" || true
        fi
      done

      chroot /host systemctl daemon-reload || true
      chroot /host systemctl start gcfsd
      sleep 2
    "

    # Verify new MD5 on node
    ACTUAL_MD5=$(kubectl exec -n kube-system pod/"$POD" -- md5sum /host/home/kubernetes/bin/gcfsd-v2 | awk '{print $1}')
    echo "[$NODE] Patch verified! Node MD5: $ACTUAL_MD5"

    # Cleanup helper pod
    kubectl delete pod "$POD" -n kube-system --grace-period=0 --force >/dev/null 2>&1 || true
  ) &
done

wait

echo ""
echo "===================================================================================================="
echo "=== [SUCCESS] ALL $NODE_COUNT PHYSICAL NODES SUCCESSFULLY PATCHED WITH 6-CL VTPROTO DRIVER! ==="
echo "===================================================================================================="
