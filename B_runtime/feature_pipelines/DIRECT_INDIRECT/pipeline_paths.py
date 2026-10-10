"""Shared CLI path resolver for reproducible feature-engineering steps.

Each pipeline script imports this module.  The module intentionally uses
``parse_known_args`` so that a task-specific script may add arguments later
without the common location flags becoming a compatibility constraint.
"""

from __future__ import annotations

import argparse
from pathlib import Path


def resolve_data_dir(default_data_dir: Path) -> Path:
    parser = argparse.ArgumentParser(add_help=False)
    parser.add_argument("--data-dir", type=Path, default=None)
    parser.add_argument("--project-root", type=Path, default=None)
    args, _ = parser.parse_known_args()

    if args.data_dir is not None and args.project_root is not None:
        raise ValueError("Use exactly one of --data-dir or --project-root.")

    if args.data_dir is not None:
        data_dir = args.data_dir.expanduser().resolve()
    elif args.project_root is not None:
        root = args.project_root.expanduser().resolve()
        data_dir = root if root.name == "项目" else root / "项目"
    else:
        data_dir = default_data_dir.resolve()

    if not data_dir.is_dir():
        raise FileNotFoundError(
            f"Data directory does not exist: {data_dir}. "
            "Provide --data-dir <directory> or --project-root <project root>."
        )
    return data_dir
