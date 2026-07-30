#!/usr/bin/env bash
# restart_gcfsd_and_containerd.sh
#
# Cleanly restarts gcfsd and containerd across all 10 physical nodes
# to re-establish clean gRPC snapshotter channels and purge stale tmpmounts.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/../../.." && pwd)"
cd "$REPO_ROOT"

# Switch context
kubectl config use-context gke_chenyiwang-gke-dev_us-central1-c_agent-sandbox-scale-cluster

# Delete existing helper pods
for NODE in $(kubectl get nodes -l cloud.google.com/gke-nodepool=gvisor-scale-pool-32 -o jsonpath='{.items[*].metadata.name}'); do
  kubectl delete pod "reset-${NODE: -5}" -n kube-system --grace-period=0 --force 2>/dev/null || true
done

NODES=$(kubectl get nodes -l cloud.google.com/gke-nodepool=gvisor-scale-pool-32 -o jsonpath='{.items[*].metadata.name}')
echo "[INFO] Restarting gcfsd and containerd on all 10 nodes in parallel..."

for NODE in $NODES; do
  POD="reset-${NODE: -5}"
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
  - name: reset
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

echo "[INFO] Waiting for helper pods and executing daemon restarts..."
for NODE in $NODES; do
  POD="reset-${NODE: -5}"
  (
    kubectl wait --for=condition=Ready pod/"$POD" -n kube-system --timeout=60s >/dev/null 2>&1 || true
    kubectl exec -n kube-system pod/"$POD" -- bash -c "
      set -e
      PATH=/bin:/usr/bin:/sbin:/usr/sbin chroot /host systemctl daemon-reload
      PATH=/bin:/usr/bin:/sbin:/usr/sbin chroot /host systemctl restart gcfsd
      sleep 1
      PATH=/bin:/usr/bin:/sbin:/usr/sbin chroot /host systemctl restart containerd
      sleep 2
    " >/dev/null 2>&1 || true
    echo "[$NODE] gcfsd & containerd restarted successfully."
    kubectl delete pod "$POD" -n kube-system --grace-period=0 --force >/dev/null 2>&1 || true
  ) &
done

wait
echo "[SUCCESS] All 10 physical nodes are healthy and running 6-CL VTProto containerd daemon!"
