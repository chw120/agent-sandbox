# GKE GCFS Sandbox Benchmark Automation Guide (One-Pager)
This document details how to use the automated testing and evaluation scripts to verify GCFS (Image Streaming) performance optimizations on GKE.
---
## 📌 1. Target Environment
The benchmarks and optimizations have been verified on:
* **GKE Version**: `v1.35.5-gke.1241004` (using COS with Containerd `COS_CONTAINERD` nodes)
* **Node Instance Sizing**: `e2-standard-32` (32 vCPUs, 128 GB RAM)
* **GVisor Runtime**: Sandbox configuration enabled (`--sandbox="type=gvisor"`)
---
## 🛠️ 2. GCFS Performance Optimization CLs
To evaluate the full optimization benefits, the GCFS host daemon must be compiled with the following three CL patches:
1. **[CL 945890638](http://cl/945890638) (Shared String Arena)**: Eliminates struct duplicating allocations for repeated metadata keys.
2. **[CL 946059593](http://cl/946059593) (Lock-Free Arena allocator)**: Eliminates mutex contention overhead during parallel metadata lookups.
3. **[CL 945923240](http://cl/945923240) (High-Throughput Defaults)**: Increases cache limits to 8GiB and enables single-flight remote block calls.
4. **[CL 947247005](http://cl/947247005) (Kernel VFS Caching)**: Sets attribute validity and entry lookups to 24 hours to bypass FUSE context-switches.
### How to Apply the Patches to Your Workspace
To import and apply these optimization changes into your active Google3 workspace, run the following commands from your workspace directory:
#### Option A: Using `g4` (Piper)
```bash
g4 patch 945890638
g4 patch 946059593
g4 patch 945923240
g4 patch 947247005
```
#### Option B: Using `jj` (Jujutsu)
```bash
jj git fetch
jj cherry-pick -c cl/945890638
jj cherry-pick -c cl/946059593
jj cherry-pick -c cl/945923240
jj cherry-pick -c cl/947247005
```
---
## ⚡ 3. Compilation & Preparation
Before running the benchmark script, compile the custom `gcfsd` host daemon binary in your CitC workspace:
```bash
# From your google3 workspace directory
SKYBUILD=1 blaze build //cloud/containers/riptide/fuse2:gcfsd
```
Copy the compiled binary to your repository's local `bin/` directory:
```bash
cp blaze-bin/cloud/containers/riptide/fuse2/gcfsd <workspace_dir>/bin/gcfsd
```
---
## 🚀 4. Running the Benchmark
The `recreate_cluster_and_run_swebench.sh` script automates cluster provisioning (creating the cluster automatically if missing or deleting and recreating when `--recreate-cluster` / `RECREATE_CLUSTER=1` is passed), node pool provisioning (`gvisor-pool-32`), custom daemon swapping, test suite execution, and log collection.
### Create / Recreate Cluster and Run Benchmark
To force recreate a clean GKE cluster from scratch before running:
```bash
RECREATE_CLUSTER=1 ./scripts/recreate_cluster_and_run_swebench.sh
# Or pass --recreate-cluster CLI flag:
./scripts/recreate_cluster_and_run_swebench.sh --recreate-cluster
```
### Run with Profiling Disabled (Recommended for Release Benchmarks)
To run the evaluation workload with **zero profiling/observability overhead** (maximizing raw throughput and matching the release build configuration):
```bash
# 1. Set required variables
export PROJECT_ID="your-gcp-project-id"
# 2. Disable profiling and launch evaluation
SKIP_PROFILING=1 ./scripts/recreate_cluster_and_run_swebench.sh
```
### Run unpatched Stock GKE Baseline
To run the evaluation across unpatched, default stock GKE daemons (for regression/parity verification):
```bash
BASELINE_NO_CHANGES=1 ./scripts/recreate_cluster_and_run_swebench.sh
```
### Run with Active Time-Series Profiling
To collect CPU and Heap profiling samples (`pprof`) dynamically during the run:
```bash
./scripts/recreate_cluster_and_run_swebench.sh
```
---
## 📂 5. Output Artifacts
All run summaries, execution traces, and flamegraphs are stored under the dynamically generated runs directory:
`/workspace_root/runs/run_<timestamp>_full_swebench_20k_style_auto/`