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

v3.51 production boundary:
- Formal A/B signal ranking and C-tier observation ranking are separate contracts: A/B notifications use `rank/total_formal_candidates`; C observations use `watch_rank/total_observations` and never affect formal rank.
- Experimental learning modules may write sidecar diagnostics or parameter suggestions only. Release-facing docs, config examples, and notifications must not describe online learning, reinforcement learning, symbol rotation, or automatic parameter tuning as production behavior.
- Runtime parameter changes require the strict research acceptance path and explicit operator review; daily learning and online learning outputs cannot promote parameters automatically.

v3.52 release boundary:
- Strict research defaults to formal mode and full-grid execution unless `--smoke` is explicitly requested. Smoke runs remain non-formal and cannot become promotion eligible.
- Research manifests keep dataset identity separate from file location metadata, and blind access requires both a release token and its expected SHA-256 hash.
- Closed-candle startup backfill may repair internal gaps from OKX before the monitor starts. If the gap cannot be repaired, startup remains blocked instead of silently continuing on incomplete data.

v3.53 release boundary:
- Package metadata, launcher display, GUI display, and strict research artifact defaults are synchronized to `3.53.0` / `v3.53-strict`.
- Final blind acceptance requires passing portfolio evidence, not only an opened or sealed blind state. Losing, concentrated, one-sided, or insufficient blind results remain non-promotable.
- Formal data must carry closed-candle evidence through `is_closed`; only explicitly declared runtime cache compatibility may synthesize that field.
- The dashboard release check is `npm run check`, which must include lint, typecheck, tests, and build before packaging.
- Lifecycle notification state must be updated by the runtime caller that attempted delivery. Dispatch helpers must not double-count attempts or silently convert failed direct sends into sent outbox state.

v3.54 release boundary:
- Package metadata, launcher display, GUI display, and strict research artifact defaults are synchronized to `3.54.0` / `v3.54-strict`.
- Strict research cannot promote unless validation outcome windows end before blind trade windows, blind trades have full outcome tails, parameter-symbol coverage is complete for the selected parameters, validation portfolio metrics pass, cost-stress metrics pass, and the blind token hash was precommitted in the registry before unlock.
- Same-command blind token plus hash is treated as self-authorized compatibility evidence only and must not satisfy final promotion checks.
- Formal A-tier notifications are delivered through `notification_outbox` and the worker path. Runtime entrypoints submit events; they do not directly send Feishu or mark delivery status.
- Lifecycle storage must preserve separate setup and outcome state fields for auditability and old SQLite stores must migrate forward without losing existing status.

v3.56 release boundary:
- Package metadata, launcher display, and GUI display are synchronized to `3.56.26`; approved strategy identity remains `3.56.15`, and strict research identity remains `v3.56-strict`.
- The realtime signal chain must not import or start `backtest`, `training`, or ML decision modules. Daily learning and strict research remain offline sidecar flows.
- All runtime notifications use `notification_outbox` plus `LifecycleOutboxWorker`: A-tier signals, B-tier summaries, candidate health reports, status reports, startup notices, and lifecycle events.
- Formal history, runtime cache, research, and runtime frames fail fast on missing metadata or missing `is_closed`; only explicit raw ingestion may synthesize canonical metadata from confirmed OKX candles.
- Runtime risk payloads are signal-scoring payloads, not trade-execution payloads. `expected_move_pct`, `failure_probability`, and `volatility_adjusted_score` are allowed. Account balances, live positions, order quantities, margin mode, liquidation prices, and exchange execution instructions must not be release-facing signal text.
- A normalized `advisory_only` leverage suggestion may be release-facing only when it is independent of credentials, balances, positions, order APIs, and exchange maximum leverage. It must be constrained by effective stop distance, a fixed normalized risk budget, reward/risk, formal tier, and calibrated quality evidence; global cap is 5x, A-minus is capped at 1x, B-tier emits no suggestion, and missing quality calibration falls back to 1x.
- ML/shadow scoring must remain observation-only in live paths. Legacy runtime leverage-adjustment methods remain neutral; only the deterministic signal-only leverage-advice module may produce the normalized manual-review suggestion.
- The fixed three-day momentum cadence is an isolated research-only execution variant. Its registration-preceding position is reference-only, its first scored refresh is 2026-06-29 04:00 UTC, and it cannot be evaluated before 50 fully prospective refreshes.
- Linux deployment must run under the dedicated `okxsignal` user, execute preflight checks before startup, use systemd restart policy, run periodic health checks, and rotate file logs.
- `FEISHU_ENABLED` is the emergency notification switch and overrides the YAML notification default on every send path.
- `DEPLOYMENT_MODE=production` requires a valid current-version approved manifest; observation mode may run market-data validation while formal push remains fail-closed.
