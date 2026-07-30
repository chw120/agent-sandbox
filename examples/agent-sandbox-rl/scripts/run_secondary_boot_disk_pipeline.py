#!/usr/bin/env python3
"""
run_secondary_boot_disk_pipeline.py

Automated Secondary Boot Disk Preloading (Base-Layer Pre-caching via `crane` +
`gke-disk-image-builder`) and 3-CL GCFS Patched Evaluation Pipeline.

1. Scans SWE-bench Verified tasks using `crane manifest` to identify top shared base container images.
2. Invokes `gke-disk-image-builder` to bake a Compute Engine persistent disk image (`swebench-baselayer-cache-v1`).
3. Provisions `gvisor-pool-32` node pool with `--secondary-boot-disk=...mode=CONTAINER_IMAGE_CACHE`.
4. Patches host GCFS daemon (`gcfsd`) across nodes with the 3 Optimization CLs (945890638, 946059593, 945923240).
5. Runs controlled 1:1 Test 3 Parity evaluation (50 tasks, pipelined, 15-way concurrency, window=10).
"""

import json
import os
import subprocess
import sys
import time

PROJECT_ID = "chenyiwang-gke-dev"
ZONE = "us-central1-c"
CLUSTER_NAME = "agent-sandbox-staging"
NODE_POOL = "gvisor-pool-32"
DISK_IMAGE_NAME = "swebench-baselayer-cache-v2"
REPO_ROOT = "/usr/local/google/home/chenyiwang/chw120/agent-sandbox"
BUILDER_DIR = os.path.join(
    REPO_ROOT, "bin", "ai-on-gke-tools", "gke-disk-image-builder"
)
BUILDER_BIN = os.path.join(BUILDER_DIR, "gke-disk-image-builder")

# Seed images shared across SWE-bench Verified python tasks (630 MB full Conda+OS layers)
SEED_IMAGES = [
    "gcr.io/chenyiwang-gke-dev/swebench-seed:v1",
]


def run_cmd(cmd, check=True, cwd=None, env=None):
  print(f"[EXEC] {' '.join(cmd)} (cwd={cwd})", flush=True)
  return subprocess.run(cmd, check=check, text=True, cwd=cwd, env=env)


def phase1_crane_scan():
  print("\n=== [Phase 1] Crane Common Base Layer Discovery ===")
  digests = {}
  for img in SEED_IMAGES:
    try:
      out = subprocess.check_output(
          ["crane", "manifest", img], text=True, stderr=subprocess.DEVNULL
      )
      data = json.loads(out)
      for l in data.get("layers", []):
        digests[l["digest"]] = digests.get(l["digest"], 0) + 1
    except Exception as e:
      print(f"  -> Warning scanning {img}: {e}")
  print(
      f"[Phase 1] Crane identified {len(digests)} unique layers across seed"
      " images."
  )
  return SEED_IMAGES


def phase2_bake_disk_image(seed_images):
  print(
      f"\n=== [Phase 2] Baking GCE Disk Image '{DISK_IMAGE_NAME}' via"
      " gke-disk-image-builder ==="
  )
  # Check if disk image already exists in GCP project
  check = subprocess.run(
      [
          "gcloud",
          "compute",
          "images",
          "describe",
          DISK_IMAGE_NAME,
          f"--project={PROJECT_ID}",
      ],
      capture_output=True,
  )
  if check.returncode == 0:
    print(
        f"[Phase 2] Existing disk image '{DISK_IMAGE_NAME}' found in project"
        f" {PROJECT_ID}. Reusing existing baked image."
    )
    return f"projects/{PROJECT_ID}/global/images/{DISK_IMAGE_NAME}"

  cmd = [
      BUILDER_BIN,
      f"-project-name={PROJECT_ID}",
      f"-zone={ZONE}",
      f"-image-name={DISK_IMAGE_NAME}",
      "-disk-size-gb=100",
      "-timeout=30m",
      "-image-pull-auth=ServiceAccountToken",
  ]
  for img in seed_images:
    cmd.append(f"-container-image={img}")
  run_cmd(cmd, cwd=BUILDER_DIR)
  return f"projects/{PROJECT_ID}/global/images/{DISK_IMAGE_NAME}"


def phase3_recreate_node_pool(disk_image_uri):
  print(
      f"\n=== [Phase 3] Recreating Node Pool '{NODE_POOL}' with Secondary Boot"
      f" Disk ({disk_image_uri}) ==="
  )
  # Delete node pool if exists
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

  # Create node pool with --secondary-boot-disk
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


def phase4_run_benchmark():
  print(
      "\n=== [Phase 4] Patching 3 Optimization CLs & Running Test 6 (Full"
      " 630MB Secondary Boot Disk Preload: 500 Tasks) ==="
  )
  env = os.environ.copy()
  env["OPTIMIZED_AFTER_CHANGES"] = "1"
  env["OPTIMIZED_COL2_PARITY"] = "1"
  env["TASKS_LIMIT"] = "50"
  env["MAX_CONCURRENT"] = "15"
  env["WARMPOOL_WINDOW_SIZE"] = "10"
  env["MAX_WARMPOOL_SIZE"] = "1"
  env["RECREATE_CLUSTER"] = "0"

  run_cmd(
      [
          os.path.join(
              REPO_ROOT,
              "examples/agent-sandbox-rl/scripts/recreate_cluster_and_run_swebench.sh",
          )
      ],
      check=True,
      env=env,
  )


if __name__ == "__main__":
  seed_images = phase1_crane_scan()
  disk_image_uri = phase2_bake_disk_image(seed_images)
  phase3_recreate_node_pool(disk_image_uri)
  phase4_run_benchmark()
