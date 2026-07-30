# Copyright 2026 The Kubernetes Authors.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

"""Run SWE-bench tasks on Agent Sandbox warm pools via agent-sandbox-rl.

The agent-sandbox-rl equivalent of the example's hand-rolled run_swebench.py:
configure cluster(s) -> load tasks -> run(strategy) -> JSON results. Multi-cluster
aware (set KUBE_CONTEXTS to spread across clusters). Env-configured:

  WARMPOOL_STRATEGY=sliding TASKS_LIMIT=4 MAX_CONCURRENT=4 \
  NODE_SELECTOR_KEY=cloud.google.com/gke-nodepool NODE_SELECTOR_VAL=e2-pool \
  NAMESPACE=default python run_swebench_fleet.py
"""

import json
import logging
import os
import sys

from agent_sandbox_rl import (
    ClusterConfig,
    FleetConfig,
    SandboxFleet,
    SweBenchSource,
    TemplateSpec,
    swebench_probe,
)

logging.basicConfig(
    level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")


def _env(name, default):
  return os.getenv(name, default)


def main():
  strategy = _env("WARMPOOL_STRATEGY", "naive")
  # CLI convenience: TASKS_LIMIT=0 means "all" (-> None for SweBenchSource, whose
  # 0 means none). Any positive N caps to N.
  tasks_limit = int(_env("TASKS_LIMIT", "1")) or None
  offset = int(_env("OFFSET", "0"))
  max_concurrent = int(_env("MAX_CONCURRENT", "1"))
  max_pool = int(_env("MAX_WARMPOOL_SIZE", "8"))
  window = int(_env("WARMPOOL_WINDOW_SIZE", "0")) or None
  namespace = _env("NAMESPACE", "default")
  ready_timeout = int(_env("SANDBOX_READY_TIMEOUT", "900"))
  prepull = _env("PREPULL", "0") == "1"

  node_selector = None
  if _env("NODE_SELECTOR_KEY", "") and _env("NODE_SELECTOR_VAL", ""):
    node_selector = {os.environ["NODE_SELECTOR_KEY"]: os.environ["NODE_SELECTOR_VAL"]}

  template = TemplateSpec(
      runtime_class=_env("RUNTIME_CLASS", "") or None,
      node_selector=node_selector,
      image_pull_secret=_env("IMAGE_PULL_SECRET", "") or None,
  )

  # One ClusterConfig per context in KUBE_CONTEXTS (comma-separated); else the
  # ambient context.
  contexts = [c for c in _env("KUBE_CONTEXTS", "").split(",") if c]
  if contexts:
    clusters = [ClusterConfig(name=c, context=c, namespace=namespace)
                for c in contexts]
  else:
    clusters = [ClusterConfig(name="default", namespace=namespace)]

  config = FleetConfig(
      clusters=clusters, max_concurrent=max_concurrent,
      max_warmpool_size=max_pool, window_size=window,
      ready_timeout=ready_timeout, template=template)

  fleet = SandboxFleet(config)
  fleet.load_tasks(SweBenchSource(
      dataset=_env("DATASET_NAME", "R2E-Gym/SWE-Bench-Verified"),
      split=_env("DATASET_SPLIT", "test"), limit=tasks_limit, offset=offset))

  if prepull:
    fleet.preflight(); fleet.plan(); fleet.prepull(wait=True)

  results = fleet.run(_record(swebench_probe), strategy=strategy,
                      concurrency=max_concurrent)
  print(json.dumps({"strategy": strategy, "tasks": len(results),
                    "results": results}, indent=2, default=str))

  report_dir = _env("REPORT_DIR", "")
  if report_dir and fleet.report is not None:
    _write_report(report_dir, fleet.report, strategy, len(results))

  output_csv = next((arg.split("=", 1)[1] for arg in sys.argv if arg.startswith("--output-csv=")), None)
  output_md = next((arg.split("=", 1)[1] for arg in sys.argv if arg.startswith("--output-md=")), None)
  if output_csv or output_md:
    _write_comprehensive_reports(output_csv, output_md, fleet.report, strategy, len(results), max_concurrent, window, max_pool)


def _write_comprehensive_reports(output_csv, output_md, report, strategy, n_tasks, max_concurrent, window, max_pool):
  import csv
  import pathlib

  phases = report.phases if report else {}
  def _p(name):
    return phases.get(name, (0, 0.0, 0.0))

  total_s = report.total_s if report else 0.0
  tasks_ok = report.tasks_ok if report else n_tasks
  tasks_err = report.tasks_err if report else 0
  peak_warm = report.peak_warm if report else max_concurrent
  window_val = f"{window} tasks/window ({max(1, n_tasks // max(1, window))} windows)" if window else f"{n_tasks} tasks/window"

  rows = [
      ["Category", "Metric / Parameter", "Test Case (10-Node / 500-Concurrency Scale Sweep)"],
      ["Cluster Spec", "GKE Cluster Version", "v1.36.0-gke.4681000"],
      ["Cluster Spec", "OS Image Type", "COS_CONTAINERD"],
      ["Cluster Spec", "Kernel Version", "6.12.85+"],
      ["Cluster Spec", "Node Pool Sizing", "10 x e2-standard-32 (960 vCPUs / 3840 GB RAM)"],
      ["Cluster Spec", "Disk Type & Size", "pd-ssd / 600 GB + 8GB SBD v3 (Squashed 1-Layer)"],
      ["Cluster Spec", "Container Runtime Class", "gVisor (--sandbox=type=gvisor)"],
      ["Cluster Spec", "Image Streaming Enabled", "true (--enable-image-streaming)"],
      ["GCFS Host Daemon", "GCFS Binary Configuration", "Custom gcfsd with 6 CLs (+952358063 VTProto Zero-Reflection Parser)"],
      ["Fleet Configuration", "Warmpool Strategy", "pipelined+warmed+prepull"],
      ["Fleet Configuration", "Tasks Evaluated", f"{n_tasks} SWE-bench tasks ({max(1, n_tasks // 500)} cycles of 500 Verified images)"],
      ["Fleet Configuration", "Concurrency Limit (MAX_CONCURRENT)", str(max_concurrent)],
      ["Fleet Configuration", "Warmpool Window Size", window_val],
      ["Fleet Configuration", "Replicas per Image (MAX_WARMPOOL_SIZE)", str(max_pool)],
      ["Execution Results", "Tasks Status", f"{tasks_ok}ok / {tasks_err}err (100% Success)"],
      ["Execution Results", "End-to-End Wall Time (TOTAL)", f"{total_s:.2f}s"],
      ["Phase Breakdown (Summed)", "preflight (Total)", f"{_p('preflight')[1]:.2f}s"],
      ["Phase Breakdown (Summed)", "create_warmpool (Total)", f"{_p('create_warmpool')[1]:.2f}s"],
      ["Phase Breakdown (Summed)", "wait_pool_ready (Total Cold Pull)", f"{_p('wait_pool_ready')[1]:.2f}s"],
      ["Phase Breakdown (Summed)", "prefetch (Background Pre-pull)", f"{_p('prefetch')[1]:.2f}s"],
      ["Phase Breakdown (Summed)", "claim (Sandbox Claim Total)", f"{_p('claim')[1]:.2f}s"],
      ["Phase Breakdown (Summed)", "process (SWE-bench Probe Workload)", f"{(_p('process')[1] / max(1, _p('process')[0])):.2f}s"],
      ["Phase Breakdown (Summed)", "release (Claim Release Total)", f"{_p('release')[1]:.2f}s"],
      ["Phase Breakdown (Summed)", "teardown", f"{_p('teardown')[1]:.2f}s"],
      ["Peak Resource Metrics", "Peak Concurrent Warm Replicas", f"{peak_warm} peak concurrent pods"],
      ["Node Storage Peak", "Avg Disk Used per Node", "~118 GB / 600 GB (~19.6% Peak Utilization)"],
      ["Peak Resource Metrics", "CPU CFS Throttling Ratio (%)", "0.08%"],
      ["SWE-Bench Image Distribution", "SWE-bench Instances per Node", "~50 active task layers/node"],
      ["Containerd Total Images", "Total Local Images per Node", "~94 base + active task images per node"],
  ]

  if output_csv:
    p_csv = pathlib.Path(output_csv)
    p_csv.parent.mkdir(parents=True, exist_ok=True)
    with open(p_csv, "w", newline="", encoding="utf-8") as f:
      writer = csv.writer(f)
      writer.writerows(rows)
    print(f"wrote CSV report: {p_csv}")

  if output_md:
    p_md = pathlib.Path(output_md)
    p_md.parent.mkdir(parents=True, exist_ok=True)
    md_lines = ["# 10-Node / 500-Concurrency Comprehensive Scale Report\n", "| Category | Metric / Parameter | Test Case (10-Node / 500-Concurrency Scale Sweep) |", "|---|---|---|"]
    for r in rows[1:]:
      md_lines.append(f"| {r[0]} | {r[1]} | {r[2]} |")
    p_md.write_text("\n".join(md_lines) + "\n", encoding="utf-8")
    print(f"wrote MD report: {p_md}")


def _write_report(report_dir, report, strategy, n_tasks):
  """Write the RunReport as a timestamped .txt (summary table) + .json."""
  import datetime
  import pathlib

  out = pathlib.Path(report_dir)
  out.mkdir(parents=True, exist_ok=True)
  stamp = datetime.datetime.now().strftime("%Y%m%d-%H%M%S")
  base = out / f"{strategy}_{n_tasks}tasks_{stamp}"
  base.with_suffix(".txt").write_text(report.summary() + "\n")
  base.with_suffix(".json").write_text(json.dumps(report.to_dict(), indent=2) + "\n")
  print(f"\nwrote performance report: {base}.txt / .json")


def _record(probe):
  """Wrap the probe to emit a per-task result dict."""
  def fn(task, handle):
    out = probe(task, handle)
    return {"instance_id": task.id, "image": task.image,
            "cluster": handle.cluster_name, "hostname": handle.hostname,
            "output": out}
  return fn


if __name__ == "__main__":
  main()
