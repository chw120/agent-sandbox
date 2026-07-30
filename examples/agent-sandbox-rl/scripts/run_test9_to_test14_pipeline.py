#!/usr/bin/env python3
"""run_test9_to_test14_pipeline.py

Automated 3,000-Task Cold vs Warm Benchmark Suite across 4-CL gcfsd_optimized_release:

Pair 1: Standard pd-balanced / 300GB (No Secondary Disk)
- Test Case 9: Cold Start (Recreate fresh nodes, 3000 tasks)
- Test Case 10: Warm Cache (Do NOT delete nodes from Test 9, 3000 tasks)

Pair 2: Secondary Boot Disk v1 (29 MB pure Ubuntu OS base layer)
- Test Case 11: Cold Start (Recreate fresh nodes with swebench-baselayer-cache-v1, 3000 tasks)
- Test Case 12: Warm Cache (Do NOT delete nodes from Test 11, 3000 tasks)

Pair 3: Secondary Boot Disk v2 (630 MB full Conda+Python+GCC shared layers)
- Test Case 13: Cold Start (Recreate fresh nodes with swebench-baselayer-cache-v2, 3000 tasks)
- Test Case 14: Warm Cache (Do NOT delete nodes from Test 13, 3000 tasks)
"""

import os
import subprocess
import sys

PROJECT_ID = "chenyiwang-gke-dev"
ZONE = "us-central1-c"
CLUSTER_NAME = "agent-sandbox-staging"
NODE_POOL = "gvisor-pool-32"
REPO_ROOT = "/usr/local/google/home/chenyiwang/chw120/agent-sandbox"


def run_cmd(cmd, check=True, cwd=None, env=None):
  print(f"[EXEC] {' '.join(cmd)} (cwd={cwd})", flush=True)
  return subprocess.run(cmd, check=check, text=True, cwd=cwd, env=env)


def recreate_node_pool(secondary_disk_image=None):
  print(
      f"\n=== Recreating Node Pool '{NODE_POOL}' (secondary_disk={secondary_disk_image})"
      " ==="
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
      "--quiet",
  ]
  if secondary_disk_image:
    disk_image_uri = (
        f"projects/{PROJECT_ID}/global/images/{secondary_disk_image}"
    )
    cmd.append(
        f"--secondary-boot-disk=disk-image={disk_image_uri},mode=CONTAINER_IMAGE_CACHE"
    )
  run_cmd(cmd)


def run_suite_case(
    case_name, recreate_nodes, secondary_disk_image, tasks_limit=3000
):
  print(f"\n=======================================================")
  print(
      f"STARTING {case_name}: recreate_nodes={recreate_nodes},"
      f" secondary_disk={secondary_disk_image}, tasks={tasks_limit}"
  )
  print(f"=======================================================")
  if recreate_nodes:
    recreate_node_pool(secondary_disk_image=secondary_disk_image)
  else:
    print(
        f"[WARM CACHE MODE] Keeping existing nodes in pool '{NODE_POOL}'"
        " intact!"
    )

  env = os.environ.copy()
  env["OPTIMIZED_AFTER_CHANGES"] = "1"
  env["RECREATE_CLUSTER"] = "0"
  env["TASKS_LIMIT"] = str(tasks_limit)
  env["GCFSD_CONTENT_CACHE_MB"] = "8192"
  env["TUNE_KUBELET_GC_TOP2"] = "0"

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
  target = sys.argv[1] if len(sys.argv) > 1 else "test9"

  if target == "test9":
    run_suite_case(
        "Test Case 9 (Cold Start - Standard No Secondary Disk - 3000 Tasks)",
        recreate_nodes=True,
        secondary_disk_image=None,
    )
  elif target == "test10":
    run_suite_case(
        "Test Case 10 (Warm Cache - Standard No Secondary Disk - 3000 Tasks)",
        recreate_nodes=False,
        secondary_disk_image=None,
    )
  elif target == "test11":
    run_suite_case(
        (
            "Test Case 11 (Cold Start - Secondary Disk v1 29MB OS - 3000"
            " Tasks)"
        ),
        recreate_nodes=True,
        secondary_disk_image="swebench-baselayer-cache-v1",
    )
  elif target == "test12":
    run_suite_case(
        "Test Case 12 (Warm Cache - Secondary Disk v1 29MB OS - 3000 Tasks)",
        recreate_nodes=False,
        secondary_disk_image=None,
    )
  elif target == "test13":
    run_suite_case(
        (
            "Test Case 13 (Cold Start - Secondary Disk v3 Squashed 1-Layer - 3000"
            " Tasks)"
        ),
        recreate_nodes=True,
        secondary_disk_image="swebench-baselayer-cache-v3-squashed",
    )
  elif target == "test14":
    run_suite_case(
        (
            "Test Case 14 (Warm Cache - Secondary Disk v2 630MB Conda/OS - 3000"
            " Tasks)"
        ),
        recreate_nodes=False,
        secondary_disk_image=None,
    )
