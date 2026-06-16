# Release Safety

This system is distributed as a signal-only research and Feishu notification tool.

Release defaults:
- `config/base.yaml` sets `project.mode: SIGNAL_ONLY`.
- `config/base.yaml` sets `data.read_only: true`.
- `config/base.yaml` keeps execution and automatic close paths disabled.
- `config/base.yaml` keeps dry-run enabled for any legacy execution guard.
- `.env.example` contains only signal-only, read-only, and notification switch placeholders.
- `.env.example` must not expose OKX private credential placeholders.

Packaging rule:
- Keep `.env.example` in the package as the only environment template.
- Do not package `.env`, real Feishu webhook URLs, or OKX private credentials.
- Keep release-facing product behavior limited to signal research, read-only data, and notification delivery.
- Do not add release-facing copy, config examples, or package data that describe order submission, automatic closes, position polling, or account balance reads as available product behavior.
