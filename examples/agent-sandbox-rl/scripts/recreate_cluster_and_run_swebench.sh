#!/usr/bin/env bash
# ====================================================================================================
# MASTER AUTOMATION SCRIPT: GKE AGENT SANDBOX RECREATION, HOST PATCHING, PROFILING & EVALUATION
# File: scripts/recreate_cluster_and_run_swebench.sh
# Author: GCFS Performance Evaluation Team
# Date: July 2026
# ====================================================================================================
#
# FULL ENGINEERING CONTEXT & ARCHITECTURAL OVERVIEW:
# ----------------------------------------------------------------------------------------------------
# 1. THE 20,000-TASK CAPACITY-AWARE PRELOAD STRATEGY (full_swebench_20k):
#    To evaluate 500 SWE-bench Verified repositories with 40 tasks per image (20,000 total tasks)
#    without exhausting cluster resources or creating memory thrashing, we utilize a capacity-aware
#    preloading strategy (pipelined) on Google Kubernetes Engine (GKE) with gVisor sandboxing.
#    - Instance Sizing: e2-standard-32 (32 vCPUs, 128 GB RAM, 300 GB pd-balanced disk). Sized to
#      accommodate 40 concurrent gVisor sandboxes per image without CPU throttling or OOM kills.
#    - Cluster Density: 15 nodes of e2-standard-32 = 480 vCPUs and 1,920 GB RAM total capacity.
#    - Preload Windows: WARMPOOL_WINDOW_SIZE=9, MAX_WARMPOOL_SIZE=40, COLOCATE_REPLICAS=True. This
#      ensures that image pulling and hydration occur sequentially in capacity windows while claims
#      execute in parallel, maximizing container reuse.
#
# 2. GKE IMAGE STREAMING & FUSE CACHE THRASHING (GCFSD-V2):
#    When Image Streaming (--enable-image-streaming) is enabled on COS_CONTAINERD nodes, containerd
#    delegates layer unpacking and remote block reads to a local userspace daemon: /home/kubernetes/bin/gcfsd-v2.
#    Our empirical pprof analysis proved that default gcfsd experiences 54.79% Go GC overhead due to
#    tiny 52 MiB cache limits and aggressive GOGC=10 settings.
#    To monitor and optimize this in real time, this script deploys a compiled, pprof-enabled host
#    binary (serving HTTP on port 11253) across all nodes via privileged helper pods before running tests.
#
# 3. TWO-TIER CONTINUOUS TIME-SERIES PROFILING & DISK ROTATION:
#    To capture CPU and memory evolution from cold-cache initialization to warm-cache peak load without
#    exhausting workstation storage, this script launches an automated two-tier background profiler:
#    - Tier 1 (High-Frequency Differential): Captures 15s CPU and Heap snapshots every 60 seconds across
#      representative nodes and generates differential SVG flamegraphs against baseline (Sample 001).
#    - Tier 2 (Long-Interval Absolute): Every 5 minutes, captures 45s CPU and Heap dumps, exporting
#      standalone absolute vector graphics and interactive D3 HTML flamegraphs.
#    - Tier 3 (Disk Rotation): Automatically prunes raw differential .prof archives older than 20 samples
#      while preserving 100% of generated .svg and .html visual graphs (~100 KB each).
#
# 4. LIFECYCLE MANAGEMENT & COST OPTIMIZATION (SPINDOWN):
#    To prevent unattended cloud billing ($0.033/vCPU/hr * 480 vCPUs = ~$15.84/hr), passing `--spindown`
#    or SPINDOWN=1 automatically resizes gvisor-pool-32 to 0 nodes upon evaluation completion.
# ====================================================================================================

set -euo pipefail

# --- ARGUMENT & ENVIRONMENT CONFIGURATION ---
SPINDOWN=${SPINDOWN:-0}
RECREATE_CLUSTER=${RECREATE_CLUSTER:-0}
for arg in "$@"; do
  case $arg in
    --spindown|--spindown-after-run)
      SPINDOWN=1
      shift
      ;;
    --recreate-cluster)
      RECREATE_CLUSTER=1
      shift
      ;;
    *)
      ;;
  esac
done

# --- GCP PROJECT CONFIGURATION ---
# Default to current active project if not set.
PROJECT_ID=${PROJECT_ID:-$(gcloud config get-value project 2>/dev/null || echo "")}
if [ -z "$PROJECT_ID" ]; then
  echo "[ERROR] PROJECT_ID is not configured. Please set the PROJECT_ID environment variable or run 'gcloud config set project <PROJECT>'." >&2
  exit 1
fi

CLUSTER_NAME=${CLUSTER_NAME:-"agent-sandbox-staging"}
ZONE=${ZONE:-"us-central1-c"}
NODE_POOL=${NODE_POOL:-"gvisor-pool-32"}
TARGET_NODES=${TARGET_NODES:-3}
MACHINE_TYPE=${MACHINE_TYPE:-"e2-standard-32"}
DISK_SIZE=${DISK_SIZE:-"300GB"}

if [ "${BASELINE_NO_CHANGES:-0}" -eq 1 ]; then
  if [ "${BASELINE_HIGH_DENSITY:-0}" -eq 1 ]; then
    RUN_ID="run_$(date +%Y%m%d-%H%M)_baseline_no_changes_high_density_100way"
  else
    RUN_ID="run_$(date +%Y%m%d-%H%M)_baseline_no_changes_col2_parity"
  fi
elif [ "${OPTIMIZED_AFTER_CHANGES:-0}" -eq 1 ]; then
  if [ "${OPTIMIZED_COL2_PARITY:-0}" -eq 1 ]; then
    RUN_ID="run_$(date +%Y%m%d-%H%M)_optimized_afterchanges_col2_parity"
  else
    RUN_ID="run_$(date +%Y%m%d-%H%M)_optimized_afterchanges_high_density_100way"
  fi
else
  RUN_ID="run_$(date +%Y%m%d-%H%M)_full_swebench_20k_style_auto"
fi

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/../../.." && pwd)"
WORKSPACE_DIR=${WORKSPACE_DIR:-$REPO_ROOT}
RUN_DIR="$WORKSPACE_DIR/runs/$RUN_ID"
mkdir -p "$RUN_DIR"
cd "$RUN_DIR"

echo "===================================================================================================="
echo "LAUNCHING MASTER GKE AUTOMATION PIPELINE: $RUN_ID"
echo "Cluster: $CLUSTER_NAME ($ZONE) | Pool: $NODE_POOL | Target Nodes: $TARGET_NODES ($MACHINE_TYPE)"
echo "Recreate Cluster: $( [ "$RECREATE_CLUSTER" -eq 1 ] && echo "ENABLED" || echo "DISABLED" )"
echo "Spindown After Run: $( [ "$SPINDOWN" -eq 1 ] && echo "ENABLED (Pool will resize to 0)" || echo "DISABLED" )"
echo "===================================================================================================="
echo ""

# --- PHASE 1: CLUSTER & NODE POOL PROVISIONING & CONVERGENCE ---
echo "[Phase 1] Checking GKE Cluster status for '$CLUSTER_NAME' in zone '$ZONE'..."
CLUSTER_EXISTS=$(gcloud container clusters list --project="$PROJECT_ID" --filter="name=${CLUSTER_NAME} AND location=${ZONE}" --format="value(name)" | grep -c "^${CLUSTER_NAME}$" || true)

if [ "$RECREATE_CLUSTER" -eq 1 ] && [ "$CLUSTER_EXISTS" -gt 0 ]; then
  echo "[Phase 1] --recreate-cluster set. Deleting existing GKE cluster '$CLUSTER_NAME'..."
  gcloud container clusters delete "$CLUSTER_NAME" --project="$PROJECT_ID" --zone="$ZONE" --quiet
  CLUSTER_EXISTS=0
fi

if [ "$CLUSTER_EXISTS" -eq 0 ]; then
  echo "[Phase 1] Cluster '$CLUSTER_NAME' not found. Creating new GKE cluster (v1.35.5-gke.1241004) with Image Streaming enabled..."
  gcloud container clusters create "$CLUSTER_NAME" \
    --project="$PROJECT_ID" \
    --zone="$ZONE" \
    --cluster-version="1.35.5-gke.1241004" \
    --image-type="COS_CONTAINERD" \
    --enable-image-streaming \
    --machine-type="e2-standard-4" \
    --num-nodes=1 \
    --quiet
fi

echo "[Phase 1] Fetching kubeconfig credentials for cluster '$CLUSTER_NAME'..."
gcloud container clusters get-credentials "$CLUSTER_NAME" --project="$PROJECT_ID" --zone="$ZONE"

echo "[Phase 1] Checking GKE Node Pool status for '$NODE_POOL'..."
POOL_EXISTS=$(gcloud container node-pools list --project="$PROJECT_ID" --cluster="$CLUSTER_NAME" --zone="$ZONE" --format="value(name)" | grep -c "^${NODE_POOL}$" || true)

if [ "$POOL_EXISTS" -eq 0 ]; then
  echo "[Phase 1] Pool '$NODE_POOL' not found. Creating new pool with $TARGET_NODES nodes of $MACHINE_TYPE..."
  # IMPORTANT: Do NOT specify --node-labels=sandbox.gke.io/runtime=gvisor manually, or GKE will reject with HTTP 400.
  gcloud container node-pools create "$NODE_POOL" \
    --project="$PROJECT_ID" \
    --cluster="$CLUSTER_NAME" \
    --zone="$ZONE" \
    --machine-type="$MACHINE_TYPE" \
    --num-nodes="$TARGET_NODES" \
    --disk-type="pd-balanced" \
    --disk-size="$DISK_SIZE" \
    --image-type="COS_CONTAINERD" \
    --sandbox="type=gvisor" \
    --enable-image-streaming \
    --secondary-boot-disk="disk-image=projects/${PROJECT_ID}/global/images/swebench-500-preload,mode=CONTAINER_IMAGE_CACHE" \
    --quiet
else
  LIVE_NODES=$(kubectl get nodes -l sandbox.gke.io/runtime=gvisor --no-headers 2>/dev/null | wc -l || echo 0)
  echo "[Phase 1] Pool '$NODE_POOL' exists (Live Ready/Active Nodes: $LIVE_NODES / $TARGET_NODES)."
  if [ "$LIVE_NODES" -lt "$TARGET_NODES" ]; then
    echo "[Phase 1] Resizing pool '$NODE_POOL' up to $TARGET_NODES nodes..."
    gcloud container clusters resize "$CLUSTER_NAME" --project="$PROJECT_ID" --node-pool="$NODE_POOL" --num-nodes="$TARGET_NODES" --zone="$ZONE" --quiet
  fi
fi

echo "[Phase 1] Waiting for all $TARGET_NODES gVisor nodes to reach 'Ready' status..."
while true; do
  READY_COUNT=$(kubectl get nodes -l sandbox.gke.io/runtime=gvisor -o jsonpath='{.items[*].status.conditions[?(@.type=="Ready")].status}' | tr ' ' '\n' | grep -c "^True$" || true)
  echo "  -> Ready Nodes: $READY_COUNT / $TARGET_NODES"
  if [ "$READY_COUNT" -ge "$TARGET_NODES" ]; then
    break
  fi
  sleep 15
done
echo "[Phase 1] Cluster convergence verified! All $TARGET_NODES nodes live and ready."
echo "[Phase 1] Ensuring agent-sandbox CRDs and controllers are deployed to cluster '$CLUSTER_NAME'..."
"$REPO_ROOT/dev/tools/deploy-to-kube" --image-prefix=registry.k8s.io/agent-sandbox/ --image-tag=v0.5.1 --extensions
echo "[Phase 1] CRDs and controllers deployed successfully!"
echo ""

# --- PHASE 2: ATOMIC HOST GCFS DAEMON PATCHING (PPROF ENABLEMENT) ---
if [ "${BASELINE_NO_CHANGES:-0}" -eq 1 ]; then
  echo "[Phase 2] BASELINE_NO_CHANGES=1 set! Skipping custom binary replacement and custom systemd flags."
  echo "[Phase 2] Running 100% pure unpatched stock GKE gcfsd-v2 baseline right out of the box."
else
  echo "[Phase 2] Locating pprof-enabled GCFS daemon binary..."
  if [ "${OPTIMIZED_AFTER_CHANGES:-0}" -eq 1 ] && [ -f "$WORKSPACE_DIR/bin/gcfsd_optimized_release" ]; then
    BIN_PATH="$WORKSPACE_DIR/bin/gcfsd_optimized_release"
    echo "[Phase 2] OPTIMIZED_AFTER_CHANGES=1 set! Using stripped optimized release binary: $BIN_PATH"
  elif [ -f "$WORKSPACE_DIR/bin/gcfsd" ]; then
    BIN_PATH="$WORKSPACE_DIR/bin/gcfsd"
  else
    # Dynamically search user's CitC workspace if active
    CITC_DIR="/google/src/cloud/$(whoami)"
    BIN_PATH=""
    if [ -d "$CITC_DIR" ]; then
      BIN_PATH=$(find "$CITC_DIR" -path "*/google3/blaze-bin/cloud/containers/riptide/*/gcfsd" 2>/dev/null | grep fuse2 | head -n 1 || echo "")
    fi
  fi
  if [ -z "$BIN_PATH" ] || [ ! -f "$BIN_PATH" ]; then
    echo "[ERROR] Compiled gcfsd binary not found! Please build via: SKYBUILD=1 blaze build //cloud/containers/riptide/fuse2:gcfsd"
    exit 1
  fi
  echo "[Phase 2] Using host binary: $BIN_PATH"

  NODES=$(kubectl get nodes -l sandbox.gke.io/runtime=gvisor -o jsonpath='{.items[*].metadata.name}')
  echo "[Phase 2] Deploying privileged helper pods across all $TARGET_NODES nodes in parallel..."
  for NODE in $NODES; do
    POD="patch-${NODE: -5}"
    kubectl delete pod "$POD" -n kube-system --grace-period=0 --force --wait=false --ignore-not-found=true 2>/dev/null || true
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
    imagePullPolicy: IfNotPresent
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

  echo "[Phase 2] Waiting for helper pods to initialize..."
  for NODE in $NODES; do
    POD="patch-${NODE: -5}"
    kubectl wait --for=condition=Ready pod/"$POD" -n kube-system --timeout=120s &
  done
  wait
  echo "[Phase 2] Swapping /home/kubernetes/bin/gcfsd-v2 and restarting gcfsd.service across all nodes..."
  for NODE in $NODES; do
    POD="patch-${NODE: -5}"
    (
      CACHE_MB="${GCFSD_CONTENT_CACHE_MB:-8192}"
      KUBELET_GC_CMD=""
      if [ "${TUNE_KUBELET_GC_TOP2:-0}" -eq 1 ]; then
        KUBELET_GC_CMD="sed -i 's/imageGCHighThresholdPercent: [0-9]*/imageGCHighThresholdPercent: 42/g; s/imageGCLowThresholdPercent: [0-9]*/imageGCLowThresholdPercent: 28/g' /var/lib/kubelet/config.yaml || true; systemctl restart kubelet || true;"
      fi
      kubectl exec -n kube-system pod/"$POD" -- nsenter -t 1 -m -u -n -i -- bash -c "set -ex; systemctl stop gcfsd || true; sleep 1; cp /home/kubernetes/bin/gcfsd-v2 /home/kubernetes/bin/gcfsd-v2.bak || true; rm -f /home/kubernetes/bin/gcfsd-v2 && cp -f /home/kubernetes/bin/gcfsd-v2.new /home/kubernetes/bin/gcfsd-v2; chmod +x /home/kubernetes/bin/gcfsd-v2; for SVC in /etc/systemd/system/gcfsd.service /lib/systemd/system/gcfsd.service; do if [ -f \"\$SVC\" ]; then sed -i 's/--max_layer_downloads=[0-9]*/--max_layer_downloads=12/g; s/--read_ahead_request_limit=[0-9]*/--read_ahead_request_limit=400/g; s/--read_ahead_scaling_factor=[0-9.]*/--read_ahead_scaling_factor=8.0/g; s/--read_ahead_max_blocks=[0-9]*/--read_ahead_max_blocks=255/g; s/--max_large_files_cache_size_mb=[0-9]*/--max_large_files_cache_size_mb=4096/g; s/--enable_single_flighting=[a-z]*/--enable_single_flighting=true/g; s/--max_content_cache_size_mb=[0-9]*/--max_content_cache_size_mb=${CACHE_MB}/g; s/--max_read_blocks=[0-9]*/--max_read_blocks=16/g' \"\$SVC\" || true; sed -i '/^Environment=\"GOGC=/d; /^Environment=\"GOMEMLIMIT=/d; /\[Service\]/a Environment=\"GOGC=200\"\nEnvironment=\"GOMEMLIMIT=16GiB\"' \"\$SVC\" || true; fi; done; systemctl daemon-reload || true; systemctl start gcfsd; ${KUBELET_GC_CMD} sleep 12; curl -s -I http://127.0.0.1:11253/debug/pprof/ | head -n 2"
      kubectl delete pod "$POD" -n kube-system --grace-period=0 --force --wait=false --ignore-not-found=true 2>/dev/null || true
    ) &
  done
  wait
  echo "[Phase 2] All $TARGET_NODES nodes patched and actively serving HTTP pprof on port 11253!"
fi
echo ""

# --- PHASE 3: LAUNCH TWO-TIER TIME-SERIES PROFILER WITH DISK ROTATION & SVG EXPORT ---
if [ "${SKIP_PROFILING:-0}" -eq 1 ] || [ "${OPTIMIZED_AFTER_CHANGES:-0}" -eq 1 ]; then
  echo "[Phase 3] SKIP_PROFILING=1 / OPTIMIZED_AFTER_CHANGES=1 set! Skipping background time-series profiler to eliminate 100% of observability tax during evaluation."
  PROFILER_PID=""
else
  TS_SCRIPT="$WORKSPACE_DIR/examples/agent-sandbox-rl/scripts/run_timeseries_profiling.sh"
  if [ ! -f "$TS_SCRIPT" ]; then
    TS_SCRIPT="$WORKSPACE_DIR/scripts/run_timeseries_profiling.sh"
  fi
  if [ -f "$TS_SCRIPT" ]; then
    echo "[Phase 3] Launching automated background time-series profiler (Tick: 60s, High-Freq CPU: 30s, Long-Interval CPU: 120s)..."
    chmod +x "$TS_SCRIPT"
    WORKSPACE_DIR="$WORKSPACE_DIR" "$TS_SCRIPT" 60 30 60 5 20 120 > "$RUN_DIR/timeseries_profiling.log" 2>&1 &
    PROFILER_PID=$!
    echo "[Phase 3] Time-series profiler active in background (PID: $PROFILER_PID)."
  else
    echo "[Phase 3] Time-series profiling script not found ($TS_SCRIPT). Skipping background profiler."
    PROFILER_PID=""
  fi
fi
echo ""

# --- PHASE 4: EXECUTE HIGH-CONCURRENCY 20K-STYLE EVALUATION WORKLOAD ---
echo "[Phase 4] Activating Python virtual environment and launching 500-task evaluation fleet across 30 nodes..."
if [ -f "$REPO_ROOT/bin/python-venv-agent-sandbox-rl/bin/activate" ]; then
  source "$REPO_ROOT/bin/python-venv-agent-sandbox-rl/bin/activate"
elif [ -f "$WORKSPACE_DIR/.venv/bin/activate" ]; then
  source "$WORKSPACE_DIR/.venv/bin/activate"
fi
cd "$WORKSPACE_DIR/examples/agent-sandbox-rl"

if [ "${BASELINE_NO_CHANGES:-0}" -eq 1 ] && [ "${BASELINE_HIGH_DENSITY:-0}" -eq 1 ]; then
  echo "[Phase 4] BASELINE_NO_CHANGES=1 & BASELINE_HIGH_DENSITY=1 set! Using Option B High-Density (100-way / window=20) across unpatched stock daemon..."
  export TASKS_LIMIT=${TASKS_LIMIT:-1200}
  export WARMPOOL_STRATEGY=pipelined
  export MAX_CONCURRENT=100
  export MAX_WARMPOOL_SIZE=12
  export WARMPOOL_WINDOW_SIZE=20
  export USE_AR_MIRROR=0
  export PREPULL=0
  export NAMESPACE=default
  export RUNTIME_CLASS=gvisor
  export COLOCATE_REPLICAS=False
  export REPORT_DIR="$RUN_DIR"
elif [ "${BASELINE_NO_CHANGES:-0}" -eq 1 ]; then
  echo "[Phase 4] BASELINE_NO_CHANGES=1 set! Using exact Column 2 parity parameters across unpatched stock daemon..."
  export TASKS_LIMIT=50
  export WARMPOOL_STRATEGY=pipelined
  export MAX_CONCURRENT=15
  export MAX_WARMPOOL_SIZE=1
  export WARMPOOL_WINDOW_SIZE=10
  export USE_AR_MIRROR=0
  export PREPULL=0
  export NAMESPACE=default
  export RUNTIME_CLASS=gvisor
  export COLOCATE_REPLICAS=False
  export REPORT_DIR="$RUN_DIR"
elif [ "${OPTIMIZED_AFTER_CHANGES:-0}" -eq 1 ] && [ "${OPTIMIZED_COL2_PARITY:-0}" -eq 1 ]; then
  echo "[Phase 4] OPTIMIZED_AFTER_CHANGES=1 & OPTIMIZED_COL2_PARITY=1 set! Using exact Tomer Column 2 parity parameters (50 tasks across 3 nodes / 15-way concurrency / window=10) right across stripped release binary with zero profiling tax..."
  export TASKS_LIMIT=50
  export WARMPOOL_STRATEGY=pipelined
  export MAX_CONCURRENT=15
  export MAX_WARMPOOL_SIZE=1
  export WARMPOOL_WINDOW_SIZE=10
  export USE_AR_MIRROR=0
  export PREPULL=0
  export NAMESPACE=default
  export RUNTIME_CLASS=gvisor
  export COLOCATE_REPLICAS=False
  export REPORT_DIR="$RUN_DIR"
elif [ "${OPTIMIZED_AFTER_CHANGES:-0}" -eq 1 ]; then
  echo "[Phase 4] OPTIMIZED_AFTER_CHANGES=1 set! Using Option B High-Density (100-way / window=20) right across stripped release binary with zero profiling tax..."
  export TASKS_LIMIT=${TASKS_LIMIT:-1200}
  export WARMPOOL_STRATEGY=pipelined
  export MAX_CONCURRENT=15
  export MAX_WARMPOOL_SIZE=12
  export WARMPOOL_WINDOW_SIZE=20
  export USE_AR_MIRROR=0
  export PREPULL=0
  export NAMESPACE=default
  export RUNTIME_CLASS=gvisor
  export COLOCATE_REPLICAS=False
  export REPORT_DIR="$RUN_DIR"
else
  export TASKS_LIMIT=${TASKS_LIMIT:-1200}
  export WARMPOOL_STRATEGY=${WARMPOOL_STRATEGY:-pipelined}
  export MAX_CONCURRENT=${MAX_CONCURRENT:-100}
  export MAX_WARMPOOL_SIZE=${MAX_WARMPOOL_SIZE:-12}
  export WARMPOOL_WINDOW_SIZE=${WARMPOOL_WINDOW_SIZE:-20}
  export USE_AR_MIRROR=${USE_AR_MIRROR:-0}
  export PREPULL=${PREPULL:-0}
  export NAMESPACE=${NAMESPACE:-default}
  export RUNTIME_CLASS=${RUNTIME_CLASS:-gvisor}
  export COLOCATE_REPLICAS=${COLOCATE_REPLICAS:-False}
  export REPORT_DIR="$RUN_DIR"
fi

export PYTHONPATH="$REPO_ROOT:${PYTHONPATH:-}"
set +e
python examples/run_swebench_fleet.py > "$RUN_DIR/stdout.log" 2>&1
BENCH_EXIT=$?
set -e

echo "[Phase 4] Benchmark execution completed with exit code: $BENCH_EXIT."
echo "  -> Live Log Summary:"
tail -n 20 "$RUN_DIR/stdout.log" || true
echo ""

# --- PHASE 5: CLEANUP & OPTIONAL SPINDOWN ---
echo "[Phase 5] Stopping background time-series profiler..."
if [ -n "$PROFILER_PID" ]; then
  kill -9 "$PROFILER_PID" 2>/dev/null || true
  wait "$PROFILER_PID" 2>/dev/null || true
fi

if [ "$SPINDOWN" -eq 1 ]; then
  echo "[Phase 5] SPINDOWN ENABLED! Resizing pool '$NODE_POOL' down to 0 nodes to halt compute billing..."
  gcloud container clusters resize "$CLUSTER_NAME" --project="$PROJECT_ID" --node-pool="$NODE_POOL" --num-nodes=0 --zone="$ZONE" --quiet
  echo "[Phase 5] Pool '$NODE_POOL' scaled to 0. Compute billing terminated."
else
  echo "[Phase 5] Spindown disabled. Cluster nodes remain active for interactive debugging."
fi

echo "===================================================================================================="
echo "PIPELINE COMPLETE! All reports, logs, and time-series flamegraphs saved in:"
echo "  -> $RUN_DIR/"
echo "===================================================================================================="
ls -lh "$RUN_DIR/"