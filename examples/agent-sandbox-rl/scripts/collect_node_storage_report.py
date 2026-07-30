#!/usr/bin/env python3
"""
Collects comprehensive storage and container image metrics across all gVisor nodes
in the active GKE cluster.
"""

import subprocess
import json
import time
import sys
from datetime import datetime

def run_cmd(cmd):
    result = subprocess.run(cmd, shell=True, capture_output=True, text=True)
    return result.stdout.strip(), result.stderr.strip(), result.returncode

def get_gvisor_nodes():
    out, _, rc = run_cmd("kubectl get nodes -l sandbox.gke.io/runtime=gvisor -o jsonpath='{.items[*].metadata.name}'")
    if rc != 0 or not out:
        return []
    return out.split()

def collect_node_metrics(node):
    pod_name = f"storage-probe-{node[-5:].lower()}"
    manifest = f"""
apiVersion: v1
kind: Pod
metadata:
  name: {pod_name}
  namespace: kube-system
spec:
  nodeName: {node}
  hostPID: true
  hostNetwork: true
  containers:
  - name: probe
    image: debian
    command: ["/bin/bash", "-c"]
    args:
    - |
      echo "=== DISK_USAGE ==="
      df -h /host/var/lib/containerd /host/mnt/stateful_partition 2>/dev/null || df -h /
      echo "=== IMAGE_SUMMARY ==="
      chroot /host crictl images --output json 2>/dev/null || echo "[]"
    securityContext:
      privileged: true
    volumeMounts:
    - name: host
      mountPath: /host
  volumes:
  - name: host
    hostPath:
      path: /
  restartPolicy: Never
"""
    run_cmd(f"kubectl delete pod {pod_name} -n kube-system --grace-period=0 --force 2>/dev/null")
    p = subprocess.Popen(["kubectl", "apply", "-f", "-"], stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    p.communicate(manifest)
    
    # Wait up to 30s for pod to complete
    for _ in range(15):
        time.sleep(2)
        out, _, _ = run_cmd(f"kubectl get pod {pod_name} -n kube-system -o jsonpath='{{.status.phase}}'")
        if out in ["Succeeded", "Failed"]:
            break

    logs, _, _ = run_cmd(f"kubectl logs {pod_name} -n kube-system")
    run_cmd(f"kubectl delete pod {pod_name} -n kube-system --grace-period=0 --force 2>/dev/null")
    return logs

def parse_and_format(node, raw_output):
    lines = raw_output.split("\n")
    disk_lines = []
    image_json_str = ""
    in_image = False
    for l in lines:
        if l.strip() == "=== IMAGE_SUMMARY ===":
            in_image = True
            continue
        if l.strip() == "=== DISK_USAGE ===":
            in_image = False
            continue
        if in_image:
            image_json_str += l + "\n"
        else:
            disk_lines.append(l)

    images = []
    try:
        data = json.loads(image_json_str)
        images = data.get("images", [])
    except Exception:
        pass

    swe_images = [img for img in images if any("swebench" in str(tag) for tag in img.get("repoTags", []))]
    total_swe_bytes = sum(int(img.get("size", 0)) for img in swe_images)
    total_all_bytes = sum(int(img.get("size", 0)) for img in images)

    return {
        "node": node,
        "disk_info": "\n".join(disk_lines).strip(),
        "total_images_count": len(images),
        "swebench_images_count": len(swe_images),
        "swebench_images_gb": round(total_swe_bytes / (1024**3), 2),
        "total_images_gb": round(total_all_bytes / (1024**3), 2),
    }

def main():
    nodes = get_gvisor_nodes()
    if not nodes:
        print("No gVisor nodes found.")
        sys.exit(1)
    
    print(f"Collecting storage metrics across {len(nodes)} gVisor nodes...")
    reports = []
    for n in nodes:
        raw = collect_node_metrics(n)
        rep = parse_and_format(n, raw)
        reports.append(rep)

    ts = datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S UTC")
    print(f"\n### GKE Node Storage & Image Footprint Report ({ts})\n")
    for r in reports:
        print(f"#### Node: `{r['node']}`")
        print(f"- **Total Cached Container Images**: `{r['total_images_count']}` images (`{r['total_images_gb']} GB`)")
        print(f"- **SWE-Bench Evaluation Images**: `{r['swebench_images_count']}` images (`{r['swebench_images_gb']} GB`)")
        print(f"- **Disk Partitions**:")
        print("```text")
        print(r["disk_info"])
        print("```\n")

if __name__ == "__main__":
    main()
