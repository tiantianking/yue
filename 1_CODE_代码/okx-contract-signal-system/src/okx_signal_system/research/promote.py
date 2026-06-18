from __future__ import annotations

import argparse
import getpass
from pathlib import Path

from okx_signal_system.config import project_paths
from okx_signal_system.research.approved_strategy_manifest import (
    CANDIDATE_PARAMS_FILENAME,
    approved_manifest_path,
    candidate_params_path,
    promote_candidate_manifest,
)


def build_parser() -> argparse.ArgumentParser:
    default_output_dir = project_paths().output_dir
    parser = argparse.ArgumentParser(description="Promote a strict research candidate into the approved runtime manifest.")
    parser.add_argument("--output-dir", default=str(default_output_dir))
    parser.add_argument("--run-id", default=None, help=f"read <output-dir>/research_runs/<run-id>/{CANDIDATE_PARAMS_FILENAME}")
    parser.add_argument("--candidate", default=None, help="explicit candidate_params.json path; overrides --run-id")
    parser.add_argument("--manifest", default=None, help=f"default: {approved_manifest_path(default_output_dir)}")
    parser.add_argument("--operator", default=None)
    return parser


def main(argv: list[str] | None = None) -> None:
    args = build_parser().parse_args(argv)
    output_dir = Path(args.output_dir)
    if args.candidate is None and args.run_id is None:
        raise SystemExit("--run-id is required unless --candidate is provided")
    candidate_path = args.candidate
    if candidate_path is None and args.run_id is not None:
        candidate_path = str(candidate_params_path(output_dir, args.run_id))
    manifest_path = promote_candidate_manifest(
        output_dir=output_dir,
        run_id=args.run_id,
        candidate_path=candidate_path,
        manifest_path=args.manifest,
        operator=args.operator or getpass.getuser(),
    )
    print(f"approved manifest written: {manifest_path}")


if __name__ == "__main__":
    main()
