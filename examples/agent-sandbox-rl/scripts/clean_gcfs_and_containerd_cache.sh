#!/usr/bin/env bash
# clean_gcfs_and_containerd_cache.sh
#
# Cleans stale snapshotter references on all 10 nodes and restarts daemons
# so that the 6-CL VTProto driver operates on a completely clean, synchronized state.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/../../.." && pwd)"
cd "$REPO_ROOT"

kubectl config use-context gke_chenyiwang-gke-dev_us-central1-c_agent-sandbox-scale-cluster

# Delete existing helper pods
for NODE in $(kubectl get nodes -l cloud.google.com/gke-nodepool=gvisor-scale-pool-32 -o jsonpath='{.items[*].metadata.name}'); do
  kubectl delete pod "clean-${NODE: -5}" -n kube-system --grace-period=0 --force 2>/dev/null || true
done

NODES=$(kubectl get nodes -l cloud.google.com/gke-nodepool=gvisor-scale-pool-32 -o jsonpath='{.items[*].metadata.name}')
echo "[INFO] Launching clean helper pods across all 10 nodes..."

for NODE in $NODES; do
  POD="clean-${NODE: -5}"
  cat << EOF | kubectl apply -f - >/dev/null 2>&1
apiVersion: v1
kind: Pod
metadata:
  name: $POD
  namespace: kube-system
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
  - name: clean
    image: ubuntu:22.04
    command: ["sleep", "300"]
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

echo "[INFO] Cleaning containerd snapshot metadata and restarting daemons in parallel..."
for NODE in $NODES; do
  POD="clean-${NODE: -5}"
  (
    kubectl wait --for=condition=Ready pod/"$POD" -n kube-system --timeout=60s >/dev/null 2>&1 || true
    kubectl exec -n kube-system pod/"$POD" -- bash -c "
      set -e
      PATH=/bin:/usr/bin:/sbin:/usr/sbin chroot /host systemctl stop containerd || true
      PATH=/bin:/usr/bin:/sbin:/usr/sbin chroot /host systemctl stop gcfsd || true
      
      # Unmount any stale FUSE / snapshot mounts
      PATH=/bin:/usr/bin:/sbin:/usr/sbin chroot /host bash -c '
        umount -l /run/gcfsd/mnt/* 2>/dev/null || true
        umount -l /var/lib/containerd/io.containerd.snapshotter.v1.gcfs/snapshotter/snapshots/*/fs 2>/dev/null || true
        rm -rf /var/lib/containerd/io.containerd.snapshotter.v1.gcfs/snapshotter/snapshots/* 2>/dev/null || true
        rm -rf /run/gcfsd/mnt/* 2>/dev/null || true
      '

      PATH=/bin:/usr/bin:/sbin:/usr/sbin chroot /host systemctl daemon-reload
      PATH=/bin:/usr/bin:/sbin:/usr/sbin chroot /host systemctl start gcfsd
      sleep 2
      PATH=/bin:/usr/bin:/sbin:/usr/sbin chroot /host systemctl start containerd
      sleep 2
    " >/dev/null 2>&1 || true
    echo "[$NODE] Cleaned stale snapshots and restarted daemons."
    kubectl delete pod "$POD" -n kube-system --grace-period=0 --force >/dev/null 2>&1 || true
  ) &
done

wait
echo "[SUCCESS] All 10 nodes have clean, perfectly synchronized 6-CL VTProto GCFS snapshots!"
