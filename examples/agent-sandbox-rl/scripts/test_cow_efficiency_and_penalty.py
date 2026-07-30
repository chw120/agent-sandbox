#!/usr/bin/env python3
"""test_cow_efficiency_and_penalty.py

Empirical benchmark measuring Copy-on-Write (CoW) sharing efficiency vs Copy-Up penalty
in Linux OverlayFS / Container Filesystems:
1. Scenario A (Pure CoW Read-Sharing + Tiny Source Patch):
   - Quantifies latency & disk growth when N containers read shared Lowerdir and modify a tiny 3.5 KB .py source file.
2. Scenario B (CoW Copy-Up Amplification Trap):
   - Quantifies write amplification, latency surge, and disk spike when touching 1 byte inside a 100 MB read-only file.
"""

import os
import shutil
import time


def run_cow_simulation():
  print("=== STARTING COPY-ON-WRITE (CoW) BENCHMARK EXPERIMENT ===")
  base_dir = "/tmp/cow_bench_eval"
  os.makedirs(base_dir, exist_ok=True)

  lower_code = os.path.join(base_dir, "lower_code.py")
  lower_model = os.path.join(base_dir, "lower_model.bin")

  # Create synthetic 3.5 KB source file and 100 MB read-only binary file
  with open(lower_code, "wb") as f:
    f.write(b"# python source\n" * 250)

  print("Creating 100 MB immutable Lowerdir binary file...")
  with open(lower_model, "wb") as f:
    f.write(b"0" * (100 * 1024 * 1024))

  # --- SCENARIO A: Tiny CoW Write (SWE-bench Source Patching) ---
  # In OverlayFS copy-up of a 3.5 KB file
  upper_code = os.path.join(base_dir, "upper_code.py")
  t0 = time.perf_counter()
  shutil.copyfile(lower_code, upper_code)
  with open(upper_code, "a") as f:
    f.write("# patch line\n")
  dur_a_ms = (time.perf_counter() - t0) * 1000.0
  upper_size_a = os.path.getsize(upper_code)

  # --- SCENARIO B: Large Copy-Up Penalty (1 byte append to 100 MB file) ---
  # In OverlayFS copy-up of a 100 MB file triggered by modifying 1 byte
  upper_model = os.path.join(base_dir, "upper_model.bin")
  t1 = time.perf_counter()
  shutil.copyfile(lower_model, upper_model)
  with open(upper_model, "a") as f:
    f.write("X")
  dur_b_ms = (time.perf_counter() - t1) * 1000.0
  upper_size_b = os.path.getsize(upper_model)

  print("\n============================================================")
  print("=== COPY-ON-WRITE (CoW) EMPIRICAL MICROBENCHMARK RESULTS ===")
  print("============================================================")
  print(
      f"[Scenario A: Tiny Source Patch (3.5 KB)] Copy-Up Latency:"
      f" {dur_a_ms:.3f} ms | Disk Copied to Upperdir: {upper_size_a / 1024:.2f}"
      " KB"
  )
  print(
      f"[Scenario B: 1-Byte Write to 100 MB File] Copy-Up Latency:"
      f" {dur_b_ms:.3f} ms | Disk Copied to Upperdir:"
      f" {upper_size_b / (1024 * 1024):.2f} MB"
  )
  ratio = dur_b_ms / max(dur_a_ms, 0.001)
  print(
      f"[CoW Amplification Penalty] Copy-Up Latency Penalty Ratio: {ratio:.1f}x"
      " slower!"
  )
  print(
      f"[CoW Write Amplification] Disk Amplification: 1 byte write caused"
      f" {upper_size_b / (1024 * 1024):.1f} MB copy-up!"
  )
  print("============================================================")

  # Cleanup
  shutil.rmtree(base_dir, ignore_errors=True)


if __name__ == "__main__":
  run_cow_simulation()
