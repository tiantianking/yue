# Release Safety

This system is distributed as a signal-only research and Feishu notification tool.

Release defaults:
- `config/base.yaml` sets `project.mode: SIGNAL_ONLY`.
- `config/base.yaml` sets `data.read_only: true`.
- `config/base.yaml` keeps execution and automatic close paths disabled.
- `config/base.yaml` keeps dry-run enabled for any legacy execution guard.
- `config/base.yaml` does not pin a local Windows data directory; runtime history is resolved from `JIAOYI_DATA_DIR` or workspace discovery.
- `.env.example` contains only signal-only, read-only, and notification switch placeholders.
- `.env.example` must not expose OKX private credential placeholders.
- The release zip builder applies an internal denylist to both git-tracked files and non-git fallback traversal.

Packaging rule:
- Keep `.env.example` in the package as the only environment template.
- Do not package `.env`, real Feishu webhook URLs, or OKX private credentials.
- Exclude local runtime artifacts from formal source archives and Python source distributions: `build.log`, cache folders, pyc files, `output/`, `outputs/`, and SQLite/database files.
- Build reusable release zip artifacts with `python scripts/build_release_zip.py --output dist/okx-contract-signal-system-release.zip`; zip entry names must use POSIX `/` separators so releases unpack consistently across platforms.
- Keep release-facing product behavior limited to signal research, read-only data, and notification delivery.
- Do not add release-facing copy, config examples, or package data that describe order submission, automatic closes, position polling, or account balance reads as available product behavior.

v3.50 release preparation:
- Keep package metadata and visible launcher displays on the shared package version source.
- Verify release zip entries keep `.env.example`, use POSIX `/` separators, and exclude sensitive environment files, cache folders, `output/`, `outputs/`, and SQLite/database artifacts before publishing.
