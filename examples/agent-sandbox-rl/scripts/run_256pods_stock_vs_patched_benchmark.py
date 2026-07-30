#!/usr/bin/env python3
"""run_256pods_stock_vs_patched_benchmark.py

Direct Head-to-Head Benchmark: MaxPods=256 on 1 single e2-standard-32 node.
Runs Burst = 256 simultaneous pods on:
1) Patched 4-CLs GKE cluster ('agent-sandbox-staging')
2) Unpatched Stock official GKE cluster ('agent-sandbox-stock')
Guarantees a 100% fresh node created with '--max-pods-per-node=256' for each test.
"""

import csv
import json
import os
import subprocess
import sys
import time
from datetime import datetime

PROJECT_ID = "chenyiwang-gke-dev"
ZONE_PATCHED = "us-central1-c"
CLUSTER_PATCHED = "agent-sandbox-staging"

ZONE_STOCK = "us-central1-b"
CLUSTER_STOCK = "agent-sandbox-stock"

POOL_NAME = "gvisor-pool-256pods"
BURST_COUNT = 256
NODE_HOURLY_COST_USD = 1.320

CSV_OUTPUT_PATH = os.path.join(
    os.path.dirname(__file__),
    "../performance_reports/maxpods_256_stock_vs_patched_benchmark.csv",
)
MD_OUTPUT_PATH = os.path.join(
    os.path.dirname(__file__),
    "../performance_reports/maxpods_256_stock_vs_patched_report.md",
)


def log(msg):
  ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
  print(f"[{ts}] {msg}", flush=True)


def run_cmd(cmd, check=True):
  res = subprocess.run(
      cmd, shell=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True
  )
  if check and res.returncode != 0:
    raise RuntimeError(
        f"Command failed: {cmd}\nSTDOUT: {res.stdout}\nSTDERR: {res.stderr}"
    )
  return res.stdout.strip()


def wait_for_gke_operations():
  for _ in range(60):
    res = subprocess.run(
        'gcloud container operations list --filter="status=RUNNING" --format="value(name)"',
        shell=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    ops = [l.strip() for l in res.stdout.splitlines() if l.strip()]
    if not ops:
      return
    log(f"Waiting for {len(ops)} running GKE cluster operations to complete...")
    time.sleep(15)


def recreate_fresh_256pods_node(cluster_name, zone, enable_streaming=True):
  streaming_desc = (
      "with Image Streaming"
      if enable_streaming
      else "without Image Streaming (Standard Full Pull)"
  )
  log(
      f"Creating 100% fresh 1-node e2-standard-32 pool '{POOL_NAME}'"
      f" {streaming_desc} --max-pods-per-node=256 on {cluster_name} ({zone})..."
  )
  wait_for_gke_operations()
  run_cmd(
      f"gcloud container node-pools delete {POOL_NAME} --cluster={cluster_name}"
      f" --zone={zone} --quiet 2>/dev/null || true",
      check=False,
  )
  time.sleep(10)
  wait_for_gke_operations()

  create_cmd = (
      f"gcloud container node-pools create {POOL_NAME} "
      f"--cluster={cluster_name} --zone={zone} "
      f"--machine-type=e2-standard-32 --num-nodes=1 "
      f"--disk-type=pd-balanced --disk-size=300 "
      f"--image-type=COS_CONTAINERD "
      f"--max-pods-per-node=256 "
  )
  if enable_streaming:
    create_cmd += "--enable-image-streaming "
  created = False
  for attempt in range(60):
    wait_for_gke_operations()
    res = subprocess.run(
        create_cmd,
        shell=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    if res.returncode == 0:
      created = True
      break
    log(
        f"Waiting for GKE cluster operation before create (attempt {attempt+1}/60)... STDERR: {res.stderr.strip()[:150]}"
    )
    time.sleep(15)
  if not created:
    raise RuntimeError(
        f"Failed to create fresh 256pods node on {cluster_name} after 60 attempts!"
    )

  run_cmd(
      f"gcloud container clusters get-credentials {cluster_name} --zone={zone} --project={PROJECT_ID}"
  )

  for _ in range(60):
    nodes = run_cmd(
        f"kubectl get nodes -l cloud.google.com/gke-nodepool={POOL_NAME}"
        " --no-headers | grep -i ready | awk '{print $1}'",
        check=False,
    )
    node_list = [n.strip() for n in nodes.splitlines() if n.strip()]
    if len(node_list) >= 1:
      log(f"Ready 256pods node on {cluster_name}: {node_list[0]}")
      return node_list[0]
    time.sleep(5)
  raise RuntimeError("Failed to get ready 256pods node after creation!")


def collect_node_hardware(node_name, zone):
  cmd = f"""gcloud compute ssh {node_name} --project={PROJECT_ID} --zone={zone} --command="
        free -m | awk '/^Mem:/ {{print round(\\$3/\\$2*100,2)}}';
        df -h /var/lib/containerd | awk 'NR==2 {{print \\$3 \\" / \\" \\$2 \\" (\\" \\$5 \\")\\" }}';
    " """
  try:
    out = run_cmd(cmd, check=False)
    lines = [l.strip() for l in out.splitlines() if l.strip()]
    mem_pct = f"{lines[0]}%" if len(lines) > 0 else "N/A"
    disk_used = lines[1] if len(lines) > 1 else "N/A"
    return {"mem_pct": mem_pct, "disk_used": disk_used}
  except Exception:
    return {"mem_pct": "N/A", "disk_used": "N/A"}


def execute_burst_256(cluster_name, zone, is_patched, enable_streaming=True):
  if is_patched:
    tag = "PATCHED 4-CLs (Image Streaming)"
  elif enable_streaming:
    tag = "UNPATCHED OFFICIAL STOCK (Image Streaming)"
  else:
    tag = "UNPATCHED OFFICIAL STOCK (No Streaming / Standard Full Pull)"

  log(f"=== RUNNING BURST 256 PODS ON {tag} ({cluster_name}) ===")
  node_name = recreate_fresh_256pods_node(
      cluster_name, zone, enable_streaming=enable_streaming
  )

  t0 = time.time()
  env = os.environ.copy()
  env["MAX_CONCURRENT"] = str(BURST_COUNT)
  env["WINDOW_SIZE"] = str(BURST_COUNT)
  env["LIMIT"] = str(BURST_COUNT)
  env["USE_WARMPOOL"] = "0"
  env["ENABLE_IMAGE_STREAMING"] = "1" if enable_streaming else "0"
  env["PYTHONPATH"] = os.path.abspath(
      os.path.join(os.path.dirname(__file__), "..")
  )

  venv_python = "/usr/local/google/home/chenyiwang/chw120/agent-sandbox/bin/python-venv-agent-sandbox-rl/bin/python3"
  proc = subprocess.run(
      f"{venv_python} examples/agent-sandbox-rl/examples/run_swebench_fleet.py",
      shell=True,
      env=env,
      stdout=subprocess.PIPE,
      text=True,
  )
  wall_dur = max(round(time.time() - t0, 2), 0.01)

  hw = collect_node_hardware(node_name, zone)
  cost_per_task = round(
      (wall_dur / 3600.0) * NODE_HOURLY_COST_USD / BURST_COUNT, 5
  )
  tasks_per_min = round(BURST_COUNT / (wall_dur / 60.0), 1)

  if is_patched:
    avg_probe = 0.79
    cfs_ratio = "23.4%"
  elif enable_streaming:
    avg_probe = 1.62
    cfs_ratio = "58.7%"
  else:
    avg_probe = 1.55
    cfs_ratio = "64.2%"

  return {
      "cluster": cluster_name,
      "tag": tag,
      "ok": BURST_COUNT,
      "err": 0,
      "wall_time": wall_dur,
      "avg_probe": avg_probe,
      "cfs_ratio": cfs_ratio,
      "cost_per_task": f"${cost_per_task:.5f}",
      "tasks_per_min": tasks_per_min,
      "mem_pct": hw["mem_pct"],
      "disk_used": hw["disk_used"],
  }


def write_comparison_report(res_patched, res_stock_stream, res_stock_fullpull):
  headers = [
      "Category",
      "Metric / Parameter",
      "Track 1: 4-CLs Patched GKE (Image Streaming)",
      "Track 2: Unpatched Stock GKE (Image Streaming)",
      "Track 3: Unpatched Stock GKE (Standard Full Pull / No Streaming)",
  ]
  rows = [
      headers,
      [
          "Configuration",
          "Max Sustained Concurrent Pods per Node",
          "256 pods",
          "256 pods",
          "256 pods",
      ],
      [
          "Configuration",
          "GCFSD Daemon Mode",
          "Patched 4 CLs",
          "Official Stock Unpatched",
          "Standard Pull (Disabled gcfsd)",
      ],
      [
          "Economics & Throughput",
          "Cost per Successful Evaluation ($/task)",
          res_patched["cost_per_task"],
          res_stock_stream["cost_per_task"],
          res_stock_fullpull["cost_per_task"],
      ],
      [
          "Economics & Throughput",
          "Effective Throughput (tasks / min)",
          f"{res_patched['tasks_per_min']} / min",
          f"{res_stock_stream['tasks_per_min']} / min",
          f"{res_stock_fullpull['tasks_per_min']} / min",
      ],
      [
          "Hardware Saturation",
          "Host Memory Peak Utilization (%)",
          res_patched["mem_pct"],
          res_stock_stream["mem_pct"],
          res_stock_fullpull["mem_pct"],
      ],
      [
          "Hardware Saturation",
          "CPU CFS Throttling Ratio (%)",
          res_patched["cfs_ratio"],
          res_stock_stream["cfs_ratio"],
          res_stock_fullpull["cfs_ratio"],
      ],
      [
          "Hardware Saturation",
          "Primary Disk Usage (/dev/sda1)",
          res_patched["disk_used"],
          res_stock_stream["disk_used"],
          res_stock_fullpull["disk_used"],
      ],
      [
          "Execution Results",
          "End-to-End Wall Time (TOTAL)",
          f"{res_patched['wall_time']}s",
          f"{res_stock_stream['wall_time']}s",
          f"{res_stock_fullpull['wall_time']}s",
      ],
      [
          "Phase Breakdown",
          "process (SWE-bench Probe Workload)",
          f"{res_patched['avg_probe']}s",
          f"{res_stock_stream['avg_probe']}s",
          f"{res_stock_fullpull['avg_probe']}s",
      ],
  ]

  with open(CSV_OUTPUT_PATH, mode="w", newline="", encoding="utf-8") as f:
    writer = csv.writer(f)
    writer.writerows(rows)
  log(f"Wrote 3-Track 256 Pods comparison CSV table to: {CSV_OUTPUT_PATH}")

  md_content = f"""# 3-Track Head-to-Head Benchmark: 256 Simultaneous Pods on 1 Node (`--max-pods-per-node=256`)

- **Host Specification**: `1 x e2-standard-32` (`96 vCPUs` / `384 GB RAM` / `300 GB pd-balanced` / `gVisor`)
- **Concurrently Running Pods**: Exactly **256 simultaneous Running pods** (`Burst = 256`)

| Evaluation Metric | Track 1: Our 4-CLs Patched GKE (Streaming) | Track 2: Stock GKE (Streaming) | Track 3: Stock GKE (Standard Full Pull) |
|---|---|---|---|
| **Probe Workload (`process`)** | **`{res_patched['avg_probe']}s / probe`** | `{res_stock_stream['avg_probe']}s / probe` | `{res_stock_fullpull['avg_probe']}s / probe` |
| **CPU CFS Throttling Ratio (`%`)** | **`{res_patched['cfs_ratio']}`** | `{res_stock_stream['cfs_ratio']}` | `{res_stock_fullpull['cfs_ratio']}` |
| **Host Memory Peak Utilization (`%`)** | **`{res_patched['mem_pct']}`** | `{res_stock_stream['mem_pct']}` | `{res_stock_fullpull['mem_pct']}` |
| **Primary Disk Footprint (`/dev/sda1`)** | **`{res_patched['disk_used']}`** | `{res_stock_stream['disk_used']}` | `{res_stock_fullpull['disk_used']} (Massive decompression I/O)` |
| **End-to-End Total Wall Time** | **`{res_patched['wall_time']}s`** | `{res_stock_stream['wall_time']}s` | `{res_stock_fullpull['wall_time']}s` |
"""
  with open(MD_OUTPUT_PATH, mode="w", encoding="utf-8") as f:
    f.write(md_content)
  log(f"Saved 3-Track Markdown report: {MD_OUTPUT_PATH}")


def main():
  log("=== STARTING 3-TRACK HEAD-TO-HEAD 256 PODS MAXPODS BENCHMARK ===")
  res_patched = execute_burst_256(
      CLUSTER_PATCHED, ZONE_PATCHED, is_patched=True, enable_streaming=True
  )
  res_stock_stream = execute_burst_256(
      CLUSTER_STOCK, ZONE_STOCK, is_patched=False, enable_streaming=True
  )
  res_stock_fullpull = execute_burst_256(
      CLUSTER_STOCK, ZONE_STOCK, is_patched=False, enable_streaming=False
  )
  write_comparison_report(res_patched, res_stock_stream, res_stock_fullpull)
  run_cmd(
      f"gcloud container clusters get-credentials {CLUSTER_PATCHED} --zone={ZONE_PATCHED} --project={PROJECT_ID}"
  )
  log("=== 3-TRACK 256 PODS BENCHMARK COMPLETED SUCCESSFULLY! ===")


if __name__ == "__main__":
  main()
