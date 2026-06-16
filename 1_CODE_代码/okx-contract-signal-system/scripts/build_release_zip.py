from __future__ import annotations

import argparse
import subprocess
from collections.abc import Iterable
from pathlib import Path
from zipfile import ZIP_DEFLATED, ZipFile


def _git_tracked_files(root: Path) -> list[Path]:
    result = subprocess.run(
        ["git", "ls-files", "-z"],
        cwd=root,
        check=True,
        capture_output=True,
        text=False,
    )
    return [Path(name.decode("utf-8")) for name in result.stdout.split(b"\0") if name]


def _walk_files(root: Path, output_zip: Path) -> list[Path]:
    output_zip = output_zip.resolve()
    paths: list[Path] = []
    for path in root.rglob("*"):
        if not path.is_file() or ".git" in path.parts:
            continue
        if path.resolve() == output_zip:
            continue
        paths.append(path.relative_to(root))
    return paths


def release_file_paths(root: Path, output_zip: Path) -> list[Path]:
    root = root.resolve()
    try:
        return _git_tracked_files(root)
    except (FileNotFoundError, subprocess.CalledProcessError):
        return _walk_files(root, output_zip)


def build_release_zip(root: Path, output_zip: Path, paths: Iterable[Path] | None = None) -> Path:
    root = root.resolve()
    output_zip = output_zip.resolve()
    output_zip.parent.mkdir(parents=True, exist_ok=True)

    release_paths = list(paths) if paths is not None else release_file_paths(root, output_zip)
    with ZipFile(output_zip, "w", compression=ZIP_DEFLATED) as archive:
        for relative_path in sorted(release_paths, key=lambda item: item.as_posix()):
            source_path = root / relative_path
            if not source_path.is_file():
                continue
            archive.write(source_path, arcname=relative_path.as_posix())
    return output_zip


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Build a release zip from repository files.")
    parser.add_argument(
        "--root",
        type=Path,
        default=Path(__file__).resolve().parents[1],
        help="Repository root to package.",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("dist/okx-contract-signal-system-release.zip"),
        help="Output zip path.",
    )
    return parser.parse_args()


def main() -> None:
    args = _parse_args()
    output = args.output if args.output.is_absolute() else args.root / args.output
    path = build_release_zip(args.root, output)
    print(path)


if __name__ == "__main__":
    main()
