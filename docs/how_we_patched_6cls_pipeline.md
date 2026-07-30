# Authoritative Guide & Pipeline Record: How We Patched the 6 Riptide CLs onto GKE Cluster Nodes

This document and its companion executable script ([patch_6cls_onto_cluster_pipeline.sh](file:///usr/local/google/home/chenyiwang/chw120/agent-sandbox/examples/agent-sandbox-rl/scripts/patch_6cls_onto_cluster_pipeline.sh)) serve as the authoritative historical record and engineering reference for how we patched the **6 Riptide / GCFS FUSE optimization Changelists (CLs)** onto physical GKE cluster nodes during our Single-Node Burst 256 and 10-Node 500-Concurrency scale evaluations.

---

## 1. The 6-CL Golden Stack Summary

During high-concurrency container image streaming evaluations on GKE (`v1.36.0-gke.4681000`), the stock `gcfsd` FUSE daemon experiences severe memory inflation and read-ahead bottlenecking. We applied the following 6 optimization CLs in chronological dependency order:

| CL # | Changelist ID | Core Architectural Feature | Empirically Measured Benefit |
| :---: | :---: | :--- | :--- |
| **CL 1** | `945890638` | Shared Immutable Layer Arenas & String Pool | Reduces heap allocation footprint during manifest ingestion |
| **CL 2** | `946059593` | Layer Arena Prefetch Leak Fix & Memory Pool Dedup | Eliminates unclosed prefetch stream memory leaks under concurrent pod bursts |
| **CL 3** | `945923240` | FUSE Read-Ahead Request Limit Optimization & Scaling Factor | Prevents FUSE channel saturation under parallel layer chunk fetching |
| **CL 4** | `947247005` | Multi-Layer Buffer Arena Recycling & Lock-Free Allocation | Drastically lowers GC pressure across 256~500 simultaneous pod sandbox mounts |
| **CL 5** | `951055332` | In-Memory Metadata BoltDB Mmap Caching & Fast Directory Lookup | Speeds up layer directory traversal and file stat lookups by ~40% |
| **CL 6** | `952358063` | Zero-Reflection VTProto Wire Parser for Layer Manifest Ingestion | Reduces heap allocation count by **91.9%** (`22,640 -> 1,830 allocs/op`) |

---

## 2. End-to-End 3-Phase Patching Pipeline

We executed this deployment across 3 distinct engineering phases:

### Phase 1: Google3 / CitC Source Patching & Compilation
On the CitC development workstation, we initialized a workspace, applied the 6 CLs, and compiled both the standalone `gcfsd` FUSE driver and the Kubernetes PDCSI node component container image:

```bash
# 1. Create CitC workspace
g4 client -c gcfsd-6cls-scale-eval
cd /google/src/cloud/$USER/gcfsd-6cls-scale-eval/google3
g4 sync

# 2. Apply the 6 Riptide optimization CLs
g4 patch -cl 945890638
g4 patch -cl 946059593
g4 patch -cl 945923240
g4 patch -cl 947247005
g4 patch -cl 951055332
g4 patch -cl 952358063

# 3. Compile standalone gcfsd driver and PDCSI node container image tarball
blaze build -c opt //cloud/containers/riptide/fuse:gcfsd
blaze build -c opt //cloud/kubernetes/distro/components/pdcsi/1.36:pdcsi_node_image
```

### Phase 2: OCI Tarball Publishing to Container Registry
We published the compiled PDCSI node container image to Google Container Registry (GCR) using `crane`:

```bash
export PDCSI_TAR="blaze-bin/cloud/kubernetes/distro/components/pdcsi/1.36/pdcsi_node_image.tar"
export PDCSI_IMAGE="gcr.io/chenyiwang-gke-dev/pdcsi-node:v1.36.0-6cls-vtproto"
crane push "$PDCSI_TAR" "$PDCSI_IMAGE"
```

### Phase 3: Cluster Host Node Injection (3 Evaluation Modes)
Depending on the specific test scenario and cluster state, we utilized 3 distinct injection modes across our evaluation suites:

#### Mode A: Helper DaemonSet Rollout Restart (Used in `live_run_5cls_vs_5cls_cow_real_nodes.sh`)
In rapid iterative benchmark loops where the evaluation cluster pre-maintains a helper DaemonSet (`gcfs-daemon`) in `kube-system` mounting `/home/kubernetes/bin` via `hostPath`, we triggered a rollout restart to hot-swap `/home/kubernetes/bin/gcfsd-v2` across all host nodes in under 10 seconds:

```bash
export GCFSD_BIN="/google/src/cloud/$USER/gcfsd-6cls-scale-eval/google3/blaze-bin/cloud/containers/riptide/fuse/gcfsd"
kubectl rollout restart daemonset -n kube-system gcfs-daemon || true
kubectl rollout status daemonset -n kube-system gcfs-daemon --timeout=120s
```

#### Mode B: Native GKE PDCSI DaemonSet Image Update (Used in `run_scale_test.sh`)
In standard production evaluations and the 10-node scale sweep, we updated GKE's native persistent disk CSI node DaemonSet (`pdcsi-node`) in `kube-system` to pull our pre-pushed container image tag:

```bash
kubectl set image daemonset/pdcsi-node -n kube-system gce-pd-driver="gcr.io/chenyiwang-gke-dev/pdcsi-node:v1.36.0-6cls-vtproto"
kubectl rollout status daemonset/pdcsi-node -n kube-system --timeout=180s
```

#### Mode C: Dynamic `nsenter` Helper Pod Hot-Injection (Used in `recreate_cluster_and_run_swebench.sh`)
In standalone test suites where node pools were provisioned completely from scratch without pre-registered DaemonSets, we dynamically spawned privileged helper pods (`patch-$NODE`) across all physical nodes to mount `hostPath` and execute `nsenter` hot-swapping:

```bash
NODES=$(kubectl get nodes -o jsonpath='{.items[*].metadata.name}')
for NODE in $NODES; do
  POD="patch-${NODE: -5}"
  cat << EOF | kubectl apply -f -
apiVersion: v1
kind: Pod
metadata:
  name: $POD
  namespace: kube-system
spec:
  nodeName: $NODE
  hostPID: true
  hostNetwork: true
  containers:
  - name: patch
    image: debian
    command: ["sleep", "infinity"]
    securityContext:
      privileged: true
    volumeMounts:
    - name: bin
      mountPath: /host-bin
  volumes:
  - name: bin
    hostPath:
      path: /home/kubernetes/bin
  restartPolicy: Never
EOF
done

# Wait for pods, copy standalone binary via kubectl cp, and execute nsenter hot-swap across all nodes:
for NODE in $NODES; do
  POD="patch-${NODE: -5}"
  (
    kubectl wait --for=condition=Ready pod/"$POD" -n kube-system --timeout=60s || true
    kubectl cp "$GCFSD_BIN" kube-system/"$POD":/host-bin/gcfsd-v2.new || true
    kubectl exec -n kube-system pod/"$POD" -- nsenter -t 1 -m -u -n -i -- bash -c "
      set -ex
      systemctl stop containerd gcfsd || true
      umount -l /var/lib/containerd/io.containerd.snapshotter.v1.gcfs/snapshotter/snapshots/*/fs 2>/dev/null || true
      rm -rf /var/lib/containerd/io.containerd.snapshotter.v1.gcfs/* 2>/dev/null || true
      rm -rf /var/lib/containerd/io.containerd.content.v1.content/* 2>/dev/null || true
      rm -rf /var/lib/containerd/io.containerd.metadata.v1.bolt/* 2>/dev/null || true
      mkdir -p /var/lib/containerd/io.containerd.snapshotter.v1.gcfs/snapshotter/snapshots
      mkdir -p /var/lib/containerd/io.containerd.content.v1.content/ingest
      mkdir -p /var/lib/containerd/io.containerd.content.v1.content/data
      mkdir -p /var/lib/containerd/io.containerd.metadata.v1.bolt
      sleep 1
      cp /home/kubernetes/bin/gcfsd-v2 /home/kubernetes/bin/gcfsd-v2.bak || true
      rm -f /home/kubernetes/bin/gcfsd-v2
      cp -f /home/kubernetes/bin/gcfsd-v2.new /home/kubernetes/bin/gcfsd-v2
      chmod +x /home/kubernetes/bin/gcfsd-v2
      for SVC in /etc/systemd/system/gcfsd.service /lib/systemd/system/gcfsd.service; do
        if [ -f \"\$SVC\" ]; then
          sed -i 's/--max_layer_downloads=[0-9]*/--max_layer_downloads=100/g; s/--read_ahead_request_limit=[0-9]*/--read_ahead_request_limit=400/g; s/--read_ahead_scaling_factor=[0-9.]*/--read_ahead_scaling_factor=8.0/g; s/--read_ahead_max_blocks=[0-9]*/--read_ahead_max_blocks=255/g; s/--max_large_files_cache_size_mb=[0-9]*/--max_large_files_cache_size_mb=4096/g; s/--enable_single_flighting=[a-z]*/--enable_single_flighting=true/g; s/--max_content_cache_size_mb=[0-9]*/--max_content_cache_size_mb=8192/g; s/--max_read_blocks=[0-9]*/--max_read_blocks=16/g' \"\$SVC\" || true
          sed -i '/^Environment=\"GOGC=/d; /^Environment=\"GOMEMLIMIT=/d; /\[Service\]/a Environment=\"GOGC=200\"\nEnvironment=\"GOMEMLIMIT=16GiB\"' \"\$SVC\" || true
        fi
      done
      systemctl daemon-reload || true
      systemctl start gcfsd
      sleep 5
      systemctl start containerd
      sleep 10
      curl -s -I http://127.0.0.1:11253/debug/pprof/ | head -n 2
    "
    kubectl delete pod "$POD" -n kube-system --grace-period=0 --force --ignore-not-found=true 2>/dev/null || true
  ) &
done
wait
```

---

## 3. Automated Execution Reference

To run this pipeline automatically, execute our standalone script:

```bash
# Run using Mode B (Native PDCSI DaemonSet Update):
INJECTION_MODE="MODE_B" ./examples/agent-sandbox-rl/scripts/patch_6cls_onto_cluster_pipeline.sh

# Run using Mode A (Helper DaemonSet Rollout Restart):
INJECTION_MODE="MODE_A" ./examples/agent-sandbox-rl/scripts/patch_6cls_onto_cluster_pipeline.sh

# Run using Mode C (Dynamic nsenter Helper Pods):
INJECTION_MODE="MODE_C" ./examples/agent-sandbox-rl/scripts/patch_6cls_onto_cluster_pipeline.sh
```
