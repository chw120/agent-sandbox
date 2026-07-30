#!/usr/bin/env python3
"""run_test7_and_test8_pipeline.py

Automated Controlled Benchmark Pipeline for:
- Test Case 7: Identical to Test Case 6 (630MB preloaded Secondary Boot Disk v2 + 3 CLs + 500 tasks),
               EXCEPT --max_content_cache_size_mb=2048 (Top 1 storage tuning).
- Test Case 8: Identical to Test Case 6 (--max_content_cache_size_mb=8192 + 3 CLs + 500 tasks),
               EXCEPT Kubelet Image GC tuned (--imageGCHighThresholdPercent=42 / low=28, Top 2 storage tuning).

Each test recreates clean fresh nodes in `gvisor-pool-32` with `--secondary-boot-disk=disk-image=...,mode=CONTAINER_IMAGE_CACHE`,
runs 500 tasks, collects storage reports, and appends to all_tests_comprehensive_comparison.csv.
"""

import os
import subprocess
import sys

PROJECT_ID = "chenyiwang-gke-dev"
ZONE = "us-central1-c"
CLUSTER_NAME = "agent-sandbox-staging"
NODE_POOL = "gvisor-pool-32"
DISK_IMAGE_NAME = "swebench-baselayer-cache-v2"
REPO_ROOT = "/usr/local/google/home/chenyiwang/chw120/agent-sandbox"


def run_cmd(cmd, check=True, cwd=None, env=None):
  print(f"[EXEC] {' '.join(cmd)} (cwd={cwd})", flush=True)
  return subprocess.run(cmd, check=check, text=True, cwd=cwd, env=env)


def recreate_node_pool_with_v2_disk():
  disk_image_uri = f"projects/{PROJECT_ID}/global/images/{DISK_IMAGE_NAME}"
  print(
      f"\n=== Recreating Node Pool '{NODE_POOL}' with Secondary Boot Disk v2"
      f" ({disk_image_uri}) ==="
  )
  subprocess.run(
      [
          "gcloud",
          "container",
          "node-pools",
          "delete",
          NODE_POOL,
          f"--cluster={CLUSTER_NAME}",
          f"--zone={ZONE}",
          f"--project={PROJECT_ID}",
          "--quiet",
      ],
      check=False,
  )

  cmd = [
      "gcloud",
      "container",
      "node-pools",
      "create",
      NODE_POOL,
      f"--cluster={CLUSTER_NAME}",
      f"--zone={ZONE}",
      f"--project={PROJECT_ID}",
      "--machine-type=e2-standard-32",
      "--num-nodes=3",
      "--disk-type=pd-balanced",
      "--disk-size=300",
      "--image-type=COS_CONTAINERD",
      "--sandbox=type=gvisor",
      "--enable-image-streaming",
      (
          f"--secondary-boot-disk=disk-image={disk_image_uri},mode=CONTAINER_IMAGE_CACHE"
      ),
      "--quiet",
  ]
  run_cmd(cmd)


def run_testcase(cache_mb, tune_kubelet_gc, test_name):
  print(f"\n=======================================================")
  print(
      f"STARTING {test_name}: CACHE_MB={cache_mb},"
      f" TUNE_KUBELET_GC={tune_kubelet_gc}"
  )
  print(f"=======================================================")
  recreate_node_pool_with_v2_disk()

  env = os.environ.copy()
  env["OPTIMIZED_AFTER_CHANGES"] = "1"
  env["RECREATE_CLUSTER"] = "0"
  env["TASKS_LIMIT"] = "3000"
  env["GCFSD_CONTENT_CACHE_MB"] = str(cache_mb)
  env["TUNE_KUBELET_GC_TOP2"] = str(tune_kubelet_gc)

  script_path = os.path.join(
      REPO_ROOT,
      "examples/agent-sandbox-rl/scripts/recreate_cluster_and_run_swebench.sh",
  )
  run_cmd([script_path], check=True, env=env)

  # Collect storage metrics post run
  storage_collector = os.path.join(
      REPO_ROOT,
      "examples/agent-sandbox-rl/scripts/collect_node_storage_report.py",
  )
  run_cmd(["python3", storage_collector], check=False, env=env)


if __name__ == "__main__":
  target = sys.argv[1] if len(sys.argv) > 1 else "test7"
  if target == "test7":
    run_testcase(
        cache_mb=2048,
        tune_kubelet_gc=0,
        test_name="Test Case 7 (--max_content_cache_size_mb=2048)",
    )
  elif target == "test8":
    run_testcase(
        cache_mb=8192,
        tune_kubelet_gc=1,
        test_name="Test Case 8 (Top 2 Kubelet Image GC Tuning)",
    )
