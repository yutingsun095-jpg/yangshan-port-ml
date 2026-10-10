"""Portable, ordered entry point for the three feature-engineering pipelines.

No model is trained here.  The runner only invokes the existing feature steps
in their required order and forwards a single data-location argument to each.
"""

from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path


TASK_STEPS = {
    "VAT": (
        "VAT",
        [
            "04_build_high_snapshots.py",
            "05_add_gate_features.py",
            "06_add_dynamic_features.py",
            "06b_add_missing_vat_features.py",
            "06d_repair_trajectory_distance.py",
            "07_prepare_group_split.py",
        ],
    ),
    "DIRECT_INDIRECT": (
        "DIRECT_INDIRECT",
        [
            "24_build_direct_indirect_snapshots.py",
            "25a_add_gate_features_direct_indirect.py",
            "25c_add_dynamic_features_direct_indirect.py",
            "25d_add_missing_features_direct_indirect.py",
            "25e_repair_trajectory_distance_direct_indirect.py",
            "26_prepare_direct_indirect_split.py",
            "27_merge_port_status_direct_indirect.py",
        ],
    ),
    "ARR_TO_BER": (
        "ARR_TO_BER",
        [
            "36_build_arr_to_ber_snapshots.py",
            "37_add_arr_to_ber_spatial_features.py",
            "38_add_arr_to_ber_dynamic_features.py",
            "39_merge_arr_to_ber_port_status.py",
            "40_prepare_arr_to_ber_model_data.py",
        ],
    ),
}


def resolve_data_dir(args: argparse.Namespace) -> Path:
    if args.data_dir is not None and args.project_root is not None:
        raise ValueError("Use exactly one of --data-dir or --project-root.")

    package_dir = Path(__file__).resolve().parent
    if args.data_dir is not None:
        data_dir = args.data_dir.expanduser().resolve()
    elif args.project_root is not None:
        candidate = args.project_root.expanduser().resolve()
        data_dir = candidate if candidate.name == "项目" else candidate / "项目"
    else:
        # Default data location is beside a copied delivery package; this is
        # only a data default, never a source-code search location.
        data_dir = package_dir.parent / "项目"

    if not data_dir.is_dir():
        raise FileNotFoundError(f"Data directory does not exist: {data_dir}")
    return data_dir


def run_task_cli(task: str) -> None:
    parser = argparse.ArgumentParser(description=f"Run ordered {task} feature generation only.")
    source_subdir, steps = TASK_STEPS[task]
    location = parser.add_mutually_exclusive_group()
    location.add_argument("--project-root", type=Path, help="Project folder containing 项目/.")
    location.add_argument("--data-dir", type=Path, help="Data folder (normally <project-root>/项目).")
    parser.add_argument("--python", dest="python_executable", default=sys.executable)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    data_dir = resolve_data_dir(args)
    # Pipeline code is always loaded from this delivery package.  In contrast,
    # --data-dir / --project-root is forwarded only to locate input/output data.
    script_dir = Path(__file__).resolve().parent / source_subdir
    commands = []
    for step in steps:
        script = script_dir / step
        if not script.is_file():
            raise FileNotFoundError(f"Missing pipeline step: {script}")
        commands.append([args.python_executable, str(script), "--data-dir", str(data_dir)])

    print(f"Task: {task}")
    print(f"Pipeline code directory: {Path(__file__).resolve().parent}")
    print(f"Data dir: {data_dir}")
    for command in commands:
        print(" ".join(f'\"{part}\"' if " " in part else part for part in command))
    if args.dry_run:
        return

    for index, command in enumerate(commands, start=1):
        print(f"\n=== [{index}/{len(commands)}] {Path(command[1]).name} ===")
        subprocess.run(command, check=True)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("task", choices=sorted(TASK_STEPS))
    parsed, remaining = parser.parse_known_args()
    sys.argv = [sys.argv[0], *remaining]
    run_task_cli(parsed.task)
