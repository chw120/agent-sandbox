#!/usr/bin/env python3
"""collect_storage_ssh.py

Robust SSH storage & containerd image collector across GKE nodes.
Uses base64-encoded remote execution script to prevent quote escaping errors.
"""

import base64
import subprocess
import sys
from datetime import datetime

PROJECT_ID = "chenyiwang-gke-dev"
ZONE = "us-central1-c"

REMOTE_PY = """
import json, subprocess
try:
  out = subprocess.check_output(['crictl', 'images', '--output', 'json'], text=True)
  data = json.loads(out)
  images = data.get('images', [])
  tot_cnt = len(images)
  tot_gb = round(sum(int(i.get('size',0)) for i in images)/1024**3, 2)
  swe = [i for i in images if any('swebench' in t for t in i.get('repoTags',[]))]
  swe_cnt = len(swe)
  swe_gb = round(sum(int(i.get('size',0)) for i in swe)/1024**3, 2)
  print(f"IMAGES:{tot_cnt},{tot_gb},{swe_cnt},{swe_gb}")
except Exception as e:
  print("IMAGES:0,0,0,0")
"""
B64_SCRIPT = base64.b64encode(REMOTE_PY.encode("utf-8")).decode("ascii")


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


def collect_node(node):
  cmd = [
      "gcloud",
      "compute",
      "ssh",
      node,
      f"--zone={ZONE}",
      f"--project={PROJECT_ID}",
      (
          "--command=df -h /var/lib/containerd && echo '===SEP===' && sudo"
          f" python3 -c \"import"
          f" base64;exec(base64.b64decode('{B64_SCRIPT}').decode('utf-8'))\""
      ),
  ]
  for attempt in range(3):
    try:
      out = subprocess.check_output(cmd, text=True)
      parts = out.split("===SEP===")
      df_text = parts[0].strip()
      img_line = [
          l for l in parts[1].split("\n") if l.startswith("IMAGES:")
      ][0]
      tot_cnt, tot_gb, swe_cnt, swe_gb = img_line.split("IMAGES:")[1].split(
          ","
      )
      return {
          "node": node,
          "df": df_text,
          "total_count": tot_cnt,
          "total_gb": tot_gb,
          "swe_count": swe_cnt,
          "swe_gb": swe_gb,
      }
    except Exception:
      import time

      time.sleep(2)
  return {
      "node": node,
      "df": "N/A",
      "total_count": 0,
      "total_gb": 0,
      "swe_count": 0,
      "swe_gb": 0,
  }


def main():
  nodes = get_nodes()
  ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
  print(f"### GKE Node Storage & Image Footprint Report ({ts})\n")
  for n in sorted(nodes):
    info = collect_node(n)
    print(f"#### Node: `{info['node']}`")
    print(
        " - **Total Cached Container Images**:"
        f" `{info['total_count']}` images (`{info['total_gb']} GB`)"
    )
    print(
        " - **SWE-Bench Evaluation Images**:"
        f" `{info['swe_count']}` images (`{info['swe_gb']} GB`)"
    )
    print(" - **Disk Usage (`/var/lib/containerd`)**:")
    print("```text")
    print(info["df"])
    print("```\n")


if __name__ == "__main__":
  main()
