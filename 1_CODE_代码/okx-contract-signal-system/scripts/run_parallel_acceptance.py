from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
SRC_ROOT = PROJECT_ROOT / "src"
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

def load_env_file(path: Path | None = None) -> None:
    env_file = path or PROJECT_ROOT / ".env"
    if not env_file.is_file():
        return
    for raw_line in env_file.read_text(encoding="utf-8-sig").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        if key:
            os.environ.setdefault(key, value)


from okx_signal_system.research.parallel_acceptance import (
    AcceptanceTrackConfig,
    configured_tracks,
    load_parallel_acceptance_config,
    run_parallel_acceptance,
)


def update_track_source(track: AcceptanceTrackConfig) -> dict[str, object]:
    script = track.updater_script
    if script is None:
        return {"status": "SKIPPED", "reason": "no_updater_script"}
    if not script.is_file():
        raise FileNotFoundError(f"forward updater missing for {track.track_id}: {script}")
    completed = subprocess.run(
        [sys.executable, str(script)],
        cwd=str(script.parent),
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=600,
        check=False,
    )
    if completed.returncode != 0:
        raise RuntimeError(
            f"forward update failed for {track.track_id}\n"
            f"stdout:\n{completed.stdout}\n"
            f"stderr:\n{completed.stderr}"
        )
    stdout = completed.stdout.strip()
    try:
        payload = json.loads(stdout)
    except json.JSONDecodeError:
        payload = {"status": "UPDATED", "stdout": stdout}
    return payload


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Refresh registered research shadows and run frozen parallel forward acceptance."
    )
    parser.add_argument(
        "--skip-source-update",
        action="store_true",
        help="Do not run per-track forward-evidence updater scripts before governance.",
    )
    parser.add_argument(
        "--no-notify",
        action="store_true",
        help="Do not send research-only Feishu summaries.",
    )
    parser.add_argument("--config", type=Path, default=None)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    load_env_file()
    config = load_parallel_acceptance_config(args.config)
    source_updates: dict[str, object] = {}
    if not args.skip_source_update:
        for track in configured_tracks(config):
            source_updates[track.track_id] = update_track_source(track)

    result = run_parallel_acceptance(config, notify=not args.no_notify)
    output = {
        "source_updates": source_updates,
        "parallel_acceptance": result,
        "production_effect": "NONE",
        "automatic_ordering": False,
        "automatic_promotion": False,
        "automatic_parameter_changes": False,
    }
    print(json.dumps(output, ensure_ascii=False, indent=2, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
