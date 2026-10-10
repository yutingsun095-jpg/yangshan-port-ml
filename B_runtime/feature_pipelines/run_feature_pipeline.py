"""Compatibility entry point forwarding to the single pipeline_runner.

Prefer the task-specific run_vat_feature_pipeline.py,
run_direct_indirect_feature_pipeline.py, or run_arr_to_ber_feature_pipeline.py
entry points.  This legacy name is retained only for callers that use
``run_feature_pipeline.py <TASK>``.
"""

from __future__ import annotations

import argparse
import sys

from pipeline_runner import TASK_STEPS, run_task_cli


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Forward to the unified pipeline runner.")
    parser.add_argument("task", choices=sorted(TASK_STEPS))
    args, remaining = parser.parse_known_args()
    sys.argv = [sys.argv[0], *remaining]
    run_task_cli(args.task)
