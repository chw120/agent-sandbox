#!/usr/bin/env python3
"""verify_sbd_cache_hits.py

Verifies Secondary Boot Disk (SBD) cache hits by inspecting
containerd-gcfs-grpc journal logs across GKE nodes for: - "Reading image cache
metadata file at mount point" - "Preparing image from cached metadata file" -
"secondaryDiskPath"
"""

import re
import subprocess
import sys
from datetime import datetime

PROJECT_ID = "chenyiwang-gke-dev"
ZONE = "us-central1-c"


def get_nodes():
  cmd = [
      "kubectl",
      "get",
      "nodes",
      "-l",
      "cloud.google.com/gke-nodepool=gvisor-pool-32",
      "-o",
      "jsonpath={.items[*].metadata.name}",
  ]
  out = subprocess.check_output(cmd, text=True).strip()
  return out.split() if out else []


def verify_node(node):
  cmd = [
      "gcloud",
      "compute",
      "ssh",
      node,
      f"--zone={ZONE}",
      f"--project={PROJECT_ID}",
      (
          "--command=sudo journalctl -u containerd-gcfs-grpc -n 500 --no-pager |"
          " grep -E 'Reading image cache metadata file|Preparing image from"
          " cached metadata file|secondaryDiskPath' | tail -n 25"
      ),
  ]
  for attempt in range(3):
    try:
      out = subprocess.check_output(cmd, text=True)
      lines = [l.strip() for l in out.splitlines() if l.strip()]
      hit_count = sum(
          1 for l in lines if "Preparing image from cached metadata file" in l
      )
      mount_count = sum(
          1
          for l in lines
          if "Reading image cache metadata file at mount point" in l
      )
      return {
          "node": node,
          "hit_count": hit_count,
          "mount_count": mount_count,
          "samples": lines[:5],
      }
    except Exception:
      import time

      time.sleep(2)
  return {"node": node, "hit_count": 0, "mount_count": 0, "samples": []}


def main():
  nodes = get_nodes()
  ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
  print(
      f"### Secondary Boot Disk (SBD) Cache Hit Evidence Report ({ts})\n"
  )
  total_hits = 0
  for n in sorted(nodes):
    res = verify_node(n)
    total_hits += res["hit_count"]
    print(f"#### Node: `{res['node']}`")
    print(
        f" - **SBD Metadata Mount Evidences**: `{res['mount_count']}` events"
    )
    print(
        " - **SBD Layer Hit Evidences (`Preparing image from cached metadata"
        f" file`)**: `{res['hit_count']}` layers served directly from SBD"
    )
    if res["samples"]:
      print(" - **Sample `containerd-gcfs-grpc` Hit Log Entries**:")
      print("```text")
      for line in res["samples"]:
        print(line)
      print("```\n")
    else:
      print(" - *No SBD hit logs captured in recent journal window.*\n")


if __name__ == "__main__":
  main()
