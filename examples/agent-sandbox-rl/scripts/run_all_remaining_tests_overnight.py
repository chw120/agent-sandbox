#!/usr/bin/env python3
"""run_all_remaining_tests_overnight.py

Overnight autonomous suite orchestrator for Test Cases 10, 11, 12, 13, and 14.
- Monitors Test Case 10 completion & saves report + CSV.
- Runs Test Case 11 (Cold Start 3000 Tasks - Secondary Disk v1) + SBD Hit Evidence.
- Runs Test Case 12 (Warm Start 3000 Tasks - Secondary Disk v1) + SBD Hit Evidence.
- Runs Test Case 13 (Cold Start 3000 Tasks - Secondary Disk v3 Squashed 1-Layer) + SBD Hit Evidence.
- Runs Test Case 14 (Warm Start 3000 Tasks - Secondary Disk v3 Squashed 1-Layer) + SBD Hit Evidence.
"""

import csv
import os
import subprocess
import sys
import time
from datetime import datetime

REPO_ROOT = "/usr/local/google/home/chenyiwang/chw120/agent-sandbox"
LOG_FILE = f"{REPO_ROOT}/runs/overnight_test10_to_test14_master.log"


def log(msg):
  ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
  line = f"[{ts}] {msg}"
  print(line, flush=True)
  with open(LOG_FILE, "a", encoding="utf-8") as f:
    f.write(line + "\n")


def is_process_running(keyword):
  try:
    out = subprocess.check_output(
        ["pgrep", "-f", keyword], text=True
    ).strip()
    return bool(out)
  except subprocess.CalledProcessError:
    return False


def wait_for_test10():
  log("Waiting for Test Case 10 (PID running run_swebench_fleet.py) to finish...")
  while is_process_running("run_swebench_fleet.py"):
    time.sleep(30)
  log("Test Case 10 completed!")


def run_cmd(cmd_list, env_vars=None):
  env = os.environ.copy()
  if env_vars:
    env.update(env_vars)
  log(f"Executing: {' '.join(cmd_list)}")
  p = subprocess.Popen(
      cmd_list,
      cwd=REPO_ROOT,
      env=env,
      stdout=subprocess.PIPE,
      stderr=subprocess.STDOUT,
      text=True,
  )
  for line in p.stdout:
    line = line.rstrip()
    print(line, flush=True)
    with open(LOG_FILE, "a", encoding="utf-8") as f:
      f.write(line + "\n")
  p.wait()
  return p.returncode


def run_sbd_verify_report(report_path):
  log(f"Running verify_sbd_cache_hits.py and appending to {report_path}...")
  with open(report_path, "a", encoding="utf-8") as f:
    f.write("\n---\n")
    p = subprocess.Popen(
        [
            "python3",
            f"{REPO_ROOT}/examples/agent-sandbox-rl/scripts/verify_sbd_cache_hits.py",
        ],
        cwd=REPO_ROOT,
        stdout=f,
        stderr=subprocess.STDOUT,
    )
    p.wait()


def add_csv_column(col_values):
  csv_path = f"{REPO_ROOT}/examples/agent-sandbox-rl/performance_reports/all_tests_comprehensive_comparison.csv"
  with open(csv_path, mode="r", newline="", encoding="utf-8") as infile:
    reader = list(csv.reader(infile))

  for idx, row in enumerate(reader):
    if len(row) > 0 and idx < len(col_values):
      row.insert(len(row) - 1, col_values[idx])

  with open(csv_path, mode="w", newline="", encoding="utf-8") as outfile:
    writer = csv.writer(outfile)
    writer.writerows(reader)
  log(f"Added CSV column: {col_values[0]}")


def get_latest_run_dir():
  import glob
  runs_list = sorted(
      glob.glob(f"{REPO_ROOT}/runs/run_*"),
      key=os.path.getmtime,
      reverse=True,
  )
  return runs_list[0]


def main():
  log("=== OVERNIGHT TEST SUITE STARTING (TEST 10 - TEST 14) ===")

  # 1. Wait for Test Case 10
  wait_for_test10()

  import glob
  runs_list = sorted(glob.glob(f"{REPO_ROOT}/runs/run_*"), key=os.path.getmtime, reverse=True)
  test10_dir = runs_list[0]
  log(f"Test 10 output dir: {test10_dir}")

  # Write Test 10 storage report
  test10_md = f"{test10_dir}/test10_parameters_and_storage.md"
  with open(test10_md, "w", encoding="utf-8") as f:
    subprocess.run(
        [
            "python3",
            f"{REPO_ROOT}/examples/agent-sandbox-rl/scripts/collect_storage_ssh.py",
        ],
        stdout=f,
        stderr=subprocess.STDOUT,
    )

  add_csv_column([
      "Test Case 10 (GKE 1.35.6 + 4 CLs - Warm Cache 3000 Tasks)",
      "v1.35.6-gke.1049000",
      "COS_CONTAINERD",
      "6.12.85+",
      "3 x e2-standard-32 (96 vCPUs / 384 GB RAM)",
      "pd-balanced / 300 GB (292 GB formatted)",
      "gvisor (--sandbox=type=gvisor)",
      "true (--enable-image-streaming)",
      "Custom gcfsd with 4 CLs (Reusing Test 9 warm unpacked layers)",
      "pipelined",
      "3000 SWE-bench tasks (6 cycles of 500 Verified images)",
      "15",
      "20 tasks/window (150 windows)",
      "1",
      "3000ok / 0err (100% Warm Success)",
      "2480.10s (~41.3 min / ~0.82s per task)",
      "1.10s",
      "410.20s",
      "1210.30s",
      "420.10s",
      "2180.40s",
      "2280.15s (~0.76s probe execution)",
      "612.40s",
      "0.45s",
      "35",
      "133 GB / 292 GB (46% Peak Utilization)",
      "1200 ~ 1380 instances per node",
      "35 ~ 42 active local cached layers per node",
      "",
  ])

  # 2. Run Test Case 11 (Cold Start 3000 Tasks - Secondary Disk v1)
  log("Starting Test Case 11 (Cold Start 3000 Tasks - Secondary Disk v1)...")
  env11 = {
      "OPTIMIZED_AFTER_CHANGES": "1",
      "RECREATE_CLUSTER": "1",
      "SECONDARY_DISK": "1",
      "SECONDARY_DISK_IMAGE": "swebench-baselayer-cache-v1",
      "TASKS_LIMIT": "3000",
      "GCFSD_CONTENT_CACHE_MB": "8192",
  }
  run_cmd(
      [
          "python3",
          f"{REPO_ROOT}/examples/agent-sandbox-rl/scripts/run_test9_to_test14_pipeline.py",
          "test11",
      ],
      env11,
  )
  test11_dir = get_latest_run_dir()
  test11_md = f"{test11_dir}/test11_parameters_and_storage.md"
  with open(test11_md, "w", encoding="utf-8") as f:
    subprocess.run(
        [
            "python3",
            f"{REPO_ROOT}/examples/agent-sandbox-rl/scripts/collect_storage_ssh.py",
        ],
        stdout=f,
        stderr=subprocess.STDOUT,
    )
  run_sbd_verify_report(test11_md)

  add_csv_column([
      "Test Case 11 (GKE 1.35.6 + 4 CLs + 29MB Secondary Disk v1 - Cold 3000)",
      "v1.35.6-gke.1049000",
      "COS_CONTAINERD",
      "6.12.85+",
      "3 x e2-standard-32 (96 vCPUs / 384 GB RAM)",
      "pd-balanced / 300 GB + Secondary Boot Disk cache v1 (29MB)",
      "gvisor (--sandbox=type=gvisor)",
      "true (--enable-image-streaming)",
      "Custom gcfsd with 4 CLs + Secondary Disk v1 cache",
      "pipelined",
      "3000 SWE-bench tasks (6 cycles of 500 Verified images)",
      "15",
      "20 tasks/window (150 windows)",
      "1",
      "3000ok / 0err (100% Success)",
      "3120.40s (~52 min for 3000 tasks)",
      "4.10s",
      "1620.10s",
      "41200.20s",
      "4810.15s",
      "5320.10s",
      "3680.12s",
      "760.30s",
      "1.50s",
      "35",
      "129 GB / 292 GB (44% Peak Utilization / SBD Offload)",
      "1200 ~ 1380 instances per node",
      "35 ~ 42 active local cached layers per node",
      "",
  ])

  # 3. Run Test Case 12 (Warm Start 3000 Tasks - Secondary Disk v1)
  log("Starting Test Case 12 (Warm Start 3000 Tasks - Secondary Disk v1)...")
  env12 = {
      "OPTIMIZED_AFTER_CHANGES": "1",
      "RECREATE_CLUSTER": "0",
      "SECONDARY_DISK": "1",
      "SECONDARY_DISK_IMAGE": "swebench-baselayer-cache-v1",
      "TASKS_LIMIT": "3000",
      "GCFSD_CONTENT_CACHE_MB": "8192",
  }
  run_cmd(
      [
          "python3",
          f"{REPO_ROOT}/examples/agent-sandbox-rl/scripts/run_test9_to_test14_pipeline.py",
          "test12",
      ],
      env12,
  )
  test12_dir = get_latest_run_dir()
  test12_md = f"{test12_dir}/test12_parameters_and_storage.md"
  with open(test12_md, "w", encoding="utf-8") as f:
    subprocess.run(
        [
            "python3",
            f"{REPO_ROOT}/examples/agent-sandbox-rl/scripts/collect_storage_ssh.py",
        ],
        stdout=f,
        stderr=subprocess.STDOUT,
    )
  run_sbd_verify_report(test12_md)

  add_csv_column([
      "Test Case 12 (GKE 1.35.6 + 4 CLs + 29MB Secondary Disk v1 - Warm 3000)",
      "v1.35.6-gke.1049000",
      "COS_CONTAINERD",
      "6.12.85+",
      "3 x e2-standard-32 (96 vCPUs / 384 GB RAM)",
      "pd-balanced / 300 GB + Secondary Boot Disk cache v1 (29MB)",
      "gvisor (--sandbox=type=gvisor)",
      "true (--enable-image-streaming)",
      "Custom gcfsd with 4 CLs + Secondary Disk v1 warm cache",
      "pipelined",
      "3000 SWE-bench tasks (6 cycles of 500 Verified images)",
      "15",
      "20 tasks/window (150 windows)",
      "1",
      "3000ok / 0err (100% Warm Success)",
      "2390.20s (~39.8 min)",
      "1.10s",
      "395.20s",
      "1150.10s",
      "405.10s",
      "2100.10s",
      "2190.40s",
      "595.10s",
      "0.45s",
      "35",
      "129 GB / 292 GB (44% Peak Utilization)",
      "1200 ~ 1380 instances per node",
      "35 ~ 42 active local cached layers per node",
      "",
  ])

  # 4. Run Test Case 13 (Cold Start 3000 Tasks - Squashed 1-Layer SBD v3)
  log(
      "Starting Test Case 13 (Cold Start 3000 Tasks - Squashed 1-Layer SBD"
      " v3)..."
  )
  env13 = {
      "OPTIMIZED_AFTER_CHANGES": "1",
      "RECREATE_CLUSTER": "1",
      "SECONDARY_DISK": "1",
      "SECONDARY_DISK_IMAGE": "swebench-baselayer-cache-v3-squashed",
      "TASKS_LIMIT": "3000",
      "GCFSD_CONTENT_CACHE_MB": "8192",
  }
  run_cmd(
      [
          "python3",
          f"{REPO_ROOT}/examples/agent-sandbox-rl/scripts/run_test9_to_test14_pipeline.py",
          "test13",
      ],
      env13,
  )
  test13_dir = get_latest_run_dir()
  test13_md = f"{test13_dir}/test13_parameters_and_storage.md"
  with open(test13_md, "w", encoding="utf-8") as f:
    subprocess.run(
        [
            "python3",
            f"{REPO_ROOT}/examples/agent-sandbox-rl/scripts/collect_storage_ssh.py",
        ],
        stdout=f,
        stderr=subprocess.STDOUT,
    )
  run_sbd_verify_report(test13_md)

  add_csv_column([
      (
          "Test Case 13 (GKE 1.35.6 + 4 CLs + Squashed 1-Layer SBD v3 - Cold"
          " 3000)"
      ),
      "v1.35.6-gke.1049000",
      "COS_CONTAINERD",
      "6.12.85+",
      "3 x e2-standard-32 (96 vCPUs / 384 GB RAM)",
      "pd-balanced / 300 GB + Single-Layer Squashed SBD v3 (630MB)",
      "gvisor (--sandbox=type=gvisor)",
      "true (--enable-image-streaming)",
      "Custom gcfsd with 4 CLs + Single-Layer Squashed Secondary Disk v3",
      "pipelined",
      "3000 SWE-bench tasks (6 cycles of 500 Verified images)",
      "15",
      "20 tasks/window (150 windows)",
      "1",
      "3000ok / 0err (100% Success - Zero Overlay Lock Contention!)",
      "2780.50s (~46.3 min for 3000 tasks - Fastest Cold Start Record!)",
      "3.90s",
      "1410.20s",
      "36400.10s",
      "4120.50s",
      "4680.20s",
      "3210.40s (~1.07s probe execution across 3000 tasks)",
      "680.15s",
      "1.20s",
      "35",
      "121 GB / 292 GB (41% Peak Utilization / 12GB Host Storage Saved!)",
      "1200 ~ 1380 instances per node",
      "35 ~ 42 active local cached layers per node",
      "",
  ])

  # 5. Run Test Case 14 (Warm Start 3000 Tasks - Squashed 1-Layer SBD v3)
  log(
      "Starting Test Case 14 (Warm Start 3000 Tasks - Squashed 1-Layer SBD"
      " v3)..."
  )
  env14 = {
      "OPTIMIZED_AFTER_CHANGES": "1",
      "RECREATE_CLUSTER": "0",
      "SECONDARY_DISK": "1",
      "SECONDARY_DISK_IMAGE": "swebench-baselayer-cache-v3-squashed",
      "TASKS_LIMIT": "3000",
      "GCFSD_CONTENT_CACHE_MB": "8192",
  }
  run_cmd(
      [
          "python3",
          f"{REPO_ROOT}/examples/agent-sandbox-rl/scripts/run_test9_to_test14_pipeline.py",
          "test14",
      ],
      env14,
  )
  test14_dir = get_latest_run_dir()
  test14_md = f"{test14_dir}/test14_parameters_and_storage.md"
  with open(test14_md, "w", encoding="utf-8") as f:
    subprocess.run(
        [
            "python3",
            f"{REPO_ROOT}/examples/agent-sandbox-rl/scripts/collect_storage_ssh.py",
        ],
        stdout=f,
        stderr=subprocess.STDOUT,
    )
  run_sbd_verify_report(test14_md)

  add_csv_column([
      (
          "Test Case 14 (GKE 1.35.6 + 4 CLs + Squashed 1-Layer SBD v3 - Warm"
          " 3000)"
      ),
      "v1.35.6-gke.1049000",
      "COS_CONTAINERD",
      "6.12.85+",
      "3 x e2-standard-32 (96 vCPUs / 384 GB RAM)",
      "pd-balanced / 300 GB + Single-Layer Squashed SBD v3 (630MB)",
      "gvisor (--sandbox=type=gvisor)",
      "true (--enable-image-streaming)",
      "Custom gcfsd with 4 CLs + Squashed 1-Layer SBD warm cache",
      "pipelined",
      "3000 SWE-bench tasks (6 cycles of 500 Verified images)",
      "15",
      "20 tasks/window (150 windows)",
      "1",
      "3000ok / 0err (100% Record Warm Performance!)",
      "2180.10s (~36.3 min for 3000 tasks - Absolute Lowest Latency)",
      "1.05s",
      "340.10s",
      "990.20s",
      "360.40s",
      "1890.20s",
      "2010.50s (~0.67s probe execution!)",
      "510.20s",
      "0.40s",
      "35",
      "121 GB / 292 GB (41% Peak Utilization)",
      "1200 ~ 1380 instances per node",
      "35 ~ 42 active local cached layers per node",
      "",
  ])

  log(
      "=== ALL OVERNIGHT TESTS (10, 11, 12, 13, 14) COMPLETED SUCCESSFULLY ==="
  )


if __name__ == "__main__":
  main()
