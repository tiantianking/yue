from __future__ import annotations

import argparse
import hashlib
from collections.abc import Iterable
from pathlib import Path
from zipfile import ZIP_DEFLATED, ZipFile


_ENV_TEMPLATE_ALLOWLIST = {".env.example"}
_RELEASE_MANIFEST = Path("RELEASE_FILES.txt")
_DENIED_FILE_NAMES = {"build.log", "next-env.d.ts", "okx_signal.lock"}
_DENIED_FILE_SUFFIXES = {".log", ".pyc", ".pyo", ".pyd", ".sqlite", ".sqlite3", ".db", ".tsbuildinfo"}
_DENIED_SQLITE_SIDE_SUFFIXES = (
    ".db-journal",
    ".db-shm",
    ".db-wal",
    ".sqlite-journal",
    ".sqlite-shm",
    ".sqlite-wal",
    ".sqlite3-journal",
    ".sqlite3-shm",
    ".sqlite3-wal",
)
_DENIED_DIR_NAMES = {
    ".cache",
    ".git",
    ".mypy_cache",
    ".pytest_cache",
    ".ruff_cache",
    ".next",
    ".turbo",
    "__pycache__",
    "cache",
    "coverage",
    "dist",
    "logs",
    "node_modules",
    "output",
    "outputs",
}


def _is_denied_dir_name(part: str) -> bool:
    name = part.lower()
    return name in _DENIED_DIR_NAMES or name.endswith("-cache") or name.endswith("_cache")


def _normalise_release_path(path: Path) -> Path | None:
    relative_path = Path(path)
    if relative_path.is_absolute() or ".." in relative_path.parts:
        return None
    return Path(*relative_path.parts)


def _is_denied_release_path(relative_path: Path) -> bool:
    normalised = _normalise_release_path(relative_path)
    if normalised is None or not normalised.parts:
        return True

    parts = normalised.parts
    if any(_is_denied_dir_name(part) for part in parts[:-1]):
        return True

    name = parts[-1].lower()
    if name == ".env" or (name.startswith(".env.") and name not in _ENV_TEMPLATE_ALLOWLIST):
        return True
    if name in _DENIED_FILE_NAMES:
        return True
    if normalised.suffix.lower() in _DENIED_FILE_SUFFIXES:
        return True
    return any(name.endswith(suffix) for suffix in _DENIED_SQLITE_SIDE_SUFFIXES)


def _allowed_release_paths(paths: Iterable[Path]) -> list[Path]:
    allowed: list[Path] = []
    seen: set[Path] = set()
    for path in paths:
        normalised = _normalise_release_path(path)
        if normalised is None or _is_denied_release_path(normalised) or normalised in seen:
            continue
        seen.add(normalised)
        allowed.append(normalised)
    return allowed


def _manifest_release_files(root: Path) -> list[Path]:
    manifest_path = root / _RELEASE_MANIFEST
    if not manifest_path.is_file():
        raise RuntimeError(
            f"release manifest missing: {manifest_path}; refusing unsafe directory walk"
        )

    paths: list[Path] = []
    for line_number, raw_line in enumerate(manifest_path.read_text(encoding="utf-8").splitlines(), start=1):
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        path = _normalise_release_path(Path(line))
        if path is None or _is_denied_release_path(path):
            raise RuntimeError(f"unsafe release manifest entry at line {line_number}: {line}")
        paths.append(path)
    return _allowed_release_paths(paths)


def release_file_paths(root: Path, output_zip: Path) -> list[Path]:
    root = root.resolve()
    release_paths = _manifest_release_files(root)

    output_zip = output_zip.resolve()
    missing: list[str] = []
    available: list[Path] = []
    for relative_path in release_paths:
        source_path = (root / relative_path).resolve()
        try:
            source_path.relative_to(root)
        except ValueError:
            missing.append(relative_path.as_posix())
            continue
        if source_path == output_zip:
            continue
        if not source_path.is_file():
            missing.append(relative_path.as_posix())
            continue
        available.append(relative_path)

    if missing:
        joined = ", ".join(sorted(missing))
        raise RuntimeError(f"release file list contains missing or unsafe paths: {joined}")
    return available


def build_release_zip(root: Path, output_zip: Path, paths: Iterable[Path] | None = None) -> Path:
    root = root.resolve()
    output_zip = output_zip.resolve()
    output_zip.parent.mkdir(parents=True, exist_ok=True)

    release_paths = _allowed_release_paths(paths) if paths is not None else release_file_paths(root, output_zip)
    with ZipFile(output_zip, "w", compression=ZIP_DEFLATED) as archive:
        for relative_path in sorted(release_paths, key=lambda item: item.as_posix()):
            source_path = (root / relative_path).resolve()
            try:
                source_path.relative_to(root)
            except ValueError:
                continue
            if not source_path.is_file():
                continue
            archive.write(source_path, arcname=relative_path.as_posix())
    return output_zip


def write_sha256_file(output_zip: Path, sha256_file: Path | None = None) -> Path:
    output_zip = output_zip.resolve()
    sha256_file = sha256_file.resolve() if sha256_file is not None else output_zip.with_suffix(".sha256")
    sha256_file.parent.mkdir(parents=True, exist_ok=True)
    digest = hashlib.sha256()
    with output_zip.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    sha256_file.write_text(f"{digest.hexdigest()}  {output_zip.name}\n", encoding="ascii")
    return sha256_file


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Build a release zip from reviewed release files.")
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
    parser.add_argument(
        "--sha256",
        action="store_true",
        help="Write a sibling .sha256 file for the output zip.",
    )
    parser.add_argument(
        "--sha256-file",
        type=Path,
        default=None,
        help="Optional explicit SHA-256 sidecar path; implies --sha256.",
    )
    return parser.parse_args()


def main() -> None:
    args = _parse_args()
    output = args.output if args.output.is_absolute() else args.root / args.output
    path = build_release_zip(args.root, output)
    print(path)
    if args.sha256 or args.sha256_file is not None:
        sha256_file = args.sha256_file
        if sha256_file is not None and not sha256_file.is_absolute():
            sha256_file = args.root / sha256_file
        print(write_sha256_file(path, sha256_file))


if __name__ == "__main__":
    main()
