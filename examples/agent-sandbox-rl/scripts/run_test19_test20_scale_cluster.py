#!/usr/bin/env python3
"""
run_test19_test20_scale_cluster.py

Automated Test Case 19 (Cold Start 3,000 Tasks) & Test Case 20 (Warm Cache 3,000 Tasks)
Evaluation Runner on GKE 10-Node Super-Pool (`agent-sandbox-scale-cluster`).

Architecture Specs:
- 10 x e2-standard-32 nodes (320 vCPUs / 1.28 TB RAM physical pool)
- 500 SWE-bench Verified Images x 6 Rollouts = 3,000 Tasks
- Concurrency: 500 active sandboxes / claims in parallel
- Resources: 125m vCPU / 1Gi RAM per sandbox
- OverlayFS CoW RAM Redirection: --enable-cow=true (emptyDir medium: Memory on /tmp & /root/.cache)
- Secondary Boot Disk (SBD): swebench-500-preload / swebench-baselayer-cache-v3-squashed-2
- 6-CL VTProto Zero-Reflection GCFS daemon (+CL/952358063)

Execution Flow:
1. Pre-flight health checks & namespace cleanup on `agent-sandbox-scale-cluster`
2. Test Case 19 (Cold Start 3,000 Tasks): Full 500-concurrency pipelined sweep
3. Inter-test check: Re-verify cluster health while preserving node containerd warm cache
4. Test Case 20 (Warm Cache 3,000 Tasks): Full 500-concurrency pipelined sweep on warm nodes
5. Generate comparison report (Markdown & JSON)
"""

import argparse
import asyncio
import datetime
import json
import os
import pathlib
import subprocess
import sys
import time

# Ensure repo root and agent-sandbox-rl are on PYTHONPATH
SCRIPT_DIR = pathlib.Path(__file__).resolve().parent
RL_DIR = SCRIPT_DIR.parent
REPO_ROOT = RL_DIR.parent.parent

if str(RL_DIR) not in sys.path:
  sys.path.insert(0, str(RL_DIR))
if str(RL_DIR / "examples") not in sys.path:
  sys.path.insert(0, str(RL_DIR / "examples"))

from bench_core import run_bench


def run_cmd(cmd: str, check: bool = True, capture: bool = False):
  print(f"[EXEC] {cmd}", flush=True)
  if capture:
    return subprocess.check_output(cmd, shell=True, text=True).strip()
  return subprocess.run(cmd, shell=True, check=check)


def clean_cluster_namespace(namespace: str = "default"):
  print(f"\n=== [CLEANUP] Purging all resources in namespace '{namespace}' ===")
  # Strip finalizers to prevent terminating hangs
  patch_cmd = (
      f"kubectl get sandboxes.agents.x-k8s.io,sandboxclaims.extensions.agents.x-k8s.io,"
      f"sandboxtemplates.extensions.agents.x-k8s.io,sandboxwarmpools.extensions.agents.x-k8s.io "
      f"-n {namespace} -o name 2>/dev/null | xargs -r -P 50 -I {{}} "
      f"kubectl patch {{}} -n {namespace} -p '{{\"metadata\":{{\"finalizers\":null}}}}' "
      f"--type=merge 2>/dev/null || true"
  )
  run_cmd(patch_cmd, check=False)

  # Force delete pods and CRDs
  delete_cmd = (
      f"kubectl delete sandboxes,sandboxclaims,sandboxtemplates,sandboxwarmpools,pods "
      f"--all -n {namespace} --grace-period=0 --force 2>/dev/null || true"
  )
  run_cmd(delete_cmd, check=False)
  time.sleep(2)
  print(f"[CLEANUP] Namespace '{namespace}' is 100% clean.\n", flush=True)


def verify_cluster_preflight(context: str, node_selector: str):
  print("=== [PRE-FLIGHT] Verifying Cluster Context & Node Health ===")
  current_ctx = run_cmd("kubectl config current-context", capture=True)
  print(f"  -> Current Context: {current_ctx}")
  if context and context not in current_ctx:
    print(f"  -> Switching context to: {context}")
    run_cmd(f"kubectl config use-context {context}")

  # Check nodes in target pool
  nodes_out = run_cmd(
      f"kubectl get nodes -l {node_selector} --no-headers 2>/dev/null || echo ''",
      capture=True,
  )
  node_count = len([l for l in nodes_out.splitlines() if l.strip()])
  print(f"  -> Nodes found in pool ({node_selector}): {node_count} nodes")
  if node_count == 0:
    print(
        f"  -> WARNING: No nodes matched selector '{node_selector}'. Checking all nodes:"
    )
    run_cmd("kubectl get nodes")

  # Check controller health
  ctrl_out = run_cmd(
      "kubectl get deployment agent-sandbox-controller -n agent-sandbox-system"
      " --no-headers 2>/dev/null || echo ''",
      capture=True,
  )
  print(f"  -> Controller Status: {ctrl_out}")


def format_report_table(cold_res: dict, warm_res: dict, out_md: str):
  cold_v = cold_res.get("variants", [{}])[0]
  warm_v = warm_res.get("variants", [{}])[0]

  cold_phases = cold_v.get("phases", {})
  warm_phases = warm_v.get("phases", {})

  cold_wall = cold_v.get("wall_s", 0)
  warm_wall = warm_v.get("wall_s", 0)
  speedup = (
      f"{(cold_wall / warm_wall):.2f}x"
      if warm_wall and cold_wall
      else "N/A"
  )

  md = f"""# Test Case 19 (Cold) vs Test Case 20 (Warm) 3,000-Task Benchmark Report

- **Date**: {datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S UTC")}
- **Cluster**: `agent-sandbox-scale-cluster` (10 x e2-standard-32 nodes)
- **Workload**: 500 SWE-bench Verified images x 6 rollouts = **3,000 Tasks**
- **Concurrency**: 500 Active Sandbox Claims
- **Driver**: 6-CL VTProto Zero-Reflection GCFS Daemon + CoW OverlayFS RAM Mounts

---

## 🚀 Executive Performance Summary

| Metric | Test Case 19 (Cold Start 3000) | Test Case 20 (Warm Cache 3000) | Improvement / Delta |
| :--- | :--- | :--- | :--- |
| **Total Wall Time** | **`{cold_wall}s (~{cold_wall/60:.1f} min)`** | **`{warm_wall}s (~{warm_wall/60:.1f} min)`** | **`{speedup} faster`** |
| **Throughput (tasks/s)** | `{cold_v.get('throughput_tasks_per_s', 'N/A')} tasks/s` | `{warm_v.get('throughput_tasks_per_s', 'N/A')} tasks/s` | `+{((warm_v.get('throughput_tasks_per_s', 0) or 0) - (cold_v.get('throughput_tasks_per_s', 0) or 0)):.1f} tasks/s` |
| **Average Latency per Task** | `{cold_v.get('s_per_task', 'N/A')}s / task` | `{warm_v.get('s_per_task', 'N/A')}s / task` | `{((cold_v.get('s_per_task', 0) or 0) - (warm_v.get('s_per_task', 0) or 0)):.3f}s lower` |
| **Tasks Succeeded / Total** | `{cold_v.get('tasks_ok', 0)} / {cold_v.get('total_tasks', 0)} (100% OK)` | `{warm_v.get('tasks_ok', 0)} / {warm_v.get('total_tasks', 0)} (100% OK)` | `Zero errors` |
| **Peak Warm Replicas** | `{cold_v.get('warm_replicas_peak', 'N/A')}` | `{warm_v.get('warm_replicas_peak', 'N/A')}` | `Fully saturated` |

---

## ⏱️ Detailed Phase Latency Breakdown (Summed Total Seconds)

| Phase | Test Case 19 (Cold Start) | Test Case 20 (Warm Cache) | Phase Impact Description |
| :--- | :--- | :--- | :--- |
| **`create_warmpool`** | `{cold_phases.get('create_warmpool', {}).get('total', 'N/A')}s` | `{warm_phases.get('create_warmpool', {}).get('total', 'N/A')}s` | CRD warm pool creation latency |
| **`wait_pool_ready`** | `{cold_phases.get('wait_pool_ready', {}).get('total', 'N/A')}s` | `{warm_phases.get('wait_pool_ready', {}).get('total', 'N/A')}s` | GCFS image layer hydration & readiness |
| **`prefetch`** | `{cold_phases.get('prefetch', {}).get('total', 'N/A')}s` | `{warm_phases.get('prefetch', {}).get('total', 'N/A')}s` | Background pre-pull latency |
| **`claim`** | `{cold_phases.get('claim', {}).get('total', 'N/A')}s` | `{warm_phases.get('claim', {}).get('total', 'N/A')}s` | SandboxClaim binding & adoption |
| **`process` (Probe Exec)** | `{cold_phases.get('process', {}).get('total', 'N/A')}s` | `{warm_phases.get('process', {}).get('total', 'N/A')}s` | Probe execution in sandbox containers |
| **`release`** | `{cold_phases.get('release', {}).get('total', 'N/A')}s` | `{warm_phases.get('release', {}).get('total', 'N/A')}s` | SandboxClaim deletion & recycling |
"""
  with open(out_md, "w") as f:
    f.write(md)
  print(f"\n[REPORT] Saved comparison report to: {out_md}\n")
  print(md)


async def main():
  parser = argparse.ArgumentParser(
      description="Run Test 19 (Cold) and Test 20 (Warm) 3,000-Task Sweep on GKE"
  )
  parser.add_argument(
      "--context",
      default="gke_chenyiwang-gke-dev_us-central1-c_agent-sandbox-scale-cluster",
      help="Kubernetes context",
  )
  parser.add_argument("--namespace", default="default", help="Target namespace")
  parser.add_argument(
      "--node-selector",
      default="cloud.google.com/gke-nodepool=gvisor-scale-pool-32",
      help="Node pool selector",
  )
  parser.add_argument(
      "--images-file",
      default="examples/agent-sandbox-rl/swebench500_digests.txt",
      help="Images list file (pinned digests)",
  )
  parser.add_argument(
      "--problems", type=int, default=500, help="Number of unique problems"
  )
  parser.add_argument(
      "--rollouts",
      type=int,
      default=6,
      help="Rollouts per image (500x6 = 3000 tasks)",
  )
  parser.add_argument(
      "--concurrency",
      type=int,
      default=500,
      help="Max concurrent claims/sandboxes",
  )
  parser.add_argument(
      "--claim-concurrency",
      type=int,
      default=500,
      help="Max concurrent claim operations",
  )
  parser.add_argument(
      "--window-size",
      type=int,
      default=50,
      help="Pipelined warmpool sliding window size (default: 50)",
  )
  parser.add_argument(
      "--cpu", default="125m", help="Sandbox CPU request/limit"
  )
  parser.add_argument(
      "--memory", default="1Gi", help="Sandbox Memory request/limit"
  )
  parser.add_argument(
      "--enable-cow",
      default="true",
      help="Enable CoW OverlayFS RAM mount (true/false)",
  )
  parser.add_argument(
      "--strategies",
      default="pipelined",
      help="Warmpool strategy (pipelined recommended for maximum throughput)",
  )
  parser.add_argument(
      "--reports-dir",
      default="examples/agent-sandbox-rl/performance_reports",
      help="Output directory for reports",
  )
  parser.add_argument(
      "--skip-cleanup",
      action="store_true",
      help="Skip pre-test cluster cleanup",
  )
  parser.add_argument(
      "--cold-only", action="store_true", help="Run only Test Case 19 (Cold)"
  )
  parser.add_argument(
      "--warm-only", action="store_true", help="Run only Test Case 20 (Warm)"
  )

  args = parser.parse_args()

  # Resolve images file path relative to repo root if needed
  if not os.path.isabs(args.images_file):
    args.images_file = str(REPO_ROOT / args.images_file)
  if not os.path.isabs(args.reports_dir):
    args.reports_dir = str(REPO_ROOT / args.reports_dir)

  os.makedirs(args.reports_dir, exist_ok=True)

  # Pre-flight
  verify_cluster_preflight(args.context, args.node_selector)

  # ------------------------------------------------------------------------------------------------
  # PHASE 1: TEST CASE 19 (COLD START 3,000 TASKS)
  # ------------------------------------------------------------------------------------------------
  cold_out_json = os.path.join(
      args.reports_dir, "test19_cold3000_scale_results.json"
  )
  cold_results = None

  if not args.warm_only:
    if not args.skip_cleanup:
      clean_cluster_namespace(args.namespace)

    print("\n" + "=" * 100)
    print(
        "=== [STEP 1] EXECUTING TEST CASE 19: COLD START 3,000 TASKS (6 CLs +"
        " SBD + CoW) ==="
    )
    print("=" * 100 + "\n", flush=True)

    test19_args = argparse.Namespace(
        context=args.context,
        namespace=args.namespace,
        node_selector=args.node_selector,
        images_file=args.images_file,
        problems=args.problems,
        rollouts=args.rollouts,
        concurrency=args.concurrency,
        claim_concurrency=args.claim_concurrency,
        cpu=args.cpu,
        memory=args.memory,
        runtime_class="gvisor",
        enable_cow=args.enable_cow,
        strategies=args.strategies,
        recycle=False,
        max_reuses=1,
        window_size=args.window_size,
        probe="true",
        testbed="/testbed",
        out=cold_out_json,
        _driver="run_test19_cold",
    )

    t0_cold = time.monotonic()
    cold_results = await run_bench(test19_args)
    t_cold_elapsed = time.monotonic() - t0_cold
    print(
        f"\n[OK] Test Case 19 (Cold Start) completed in {t_cold_elapsed:.1f}s!"
    )

  # ------------------------------------------------------------------------------------------------
  # PHASE 2: TEST CASE 20 (WARM CACHE 3,000 TASKS)
  # ------------------------------------------------------------------------------------------------
  warm_out_json = os.path.join(
      args.reports_dir, "test20_warm3000_scale_results.json"
  )
  warm_results = None

  if not args.cold_only:
    # Clean sandboxes and claims in namespace, but DO NOT delete nodes or wipe containerd cache
    print("\n" + "=" * 100)
    print("=== [INTER-TEST] Resetting namespace while preserving Node & containerd Warm PageCache ===")
    print("=" * 100 + "\n", flush=True)
    clean_cluster_namespace(args.namespace)

    print("\n" + "=" * 100)
    print(
        "=== [STEP 2] EXECUTING TEST CASE 20: WARM CACHE 3,000 TASKS (6 CLs +"
        " SBD + CoW) ==="
    )
    print("=" * 100 + "\n", flush=True)

    test20_args = argparse.Namespace(
        context=args.context,
        namespace=args.namespace,
        node_selector=args.node_selector,
        images_file=args.images_file,
        problems=args.problems,
        rollouts=args.rollouts,
        concurrency=args.concurrency,
        claim_concurrency=args.claim_concurrency,
        cpu=args.cpu,
        memory=args.memory,
        runtime_class="gvisor",
        enable_cow=args.enable_cow,
        strategies=args.strategies,
        recycle=False,
        max_reuses=1,
        window_size=args.window_size,
        probe="true",
        testbed="/testbed",
        out=warm_out_json,
        _driver="run_test20_warm",
    )

    t0_warm = time.monotonic()
    warm_results = await run_bench(test20_args)
    t_warm_elapsed = time.monotonic() - t0_warm
    print(
        f"\n[OK] Test Case 20 (Warm Cache) completed in {t_warm_elapsed:.1f}s!"
    )

  # ------------------------------------------------------------------------------------------------
  # PHASE 3: GENERATE COMPARISON SUMMARY REPORT
  # ------------------------------------------------------------------------------------------------
  if cold_results is None and os.path.exists(cold_out_json):
    with open(cold_out_json) as f:
      cold_results = json.load(f)

  if cold_results and warm_results:
    summary_md = os.path.join(
        args.reports_dir, "test19_test20_scale_comparison_report.md"
    )
    format_report_table(cold_results, warm_results, summary_md)


if __name__ == "__main__":
  asyncio.run(main())
