## 2026-07-01 - Task: v3.56.36 anti-overfit and future-leakage hard gate
### What was done
- Converted the zero-tolerance research rule into blocking pre-PnL candidate checks.
- Required a hashed 6–10 month sealed historical holdout, defaulting to 8 months, with zero prior opens and a matching split manifest.
- Required a complete family trial ledger, point-in-time field evidence, a complete code dependency manifest, and SHA256 verification of every evidence file.
- Required purge to cover the declared maximum holding and label horizon, with a positive embargo.
- Expanded AST leakage scanning to indirect negative shifts, backward fills, forward/nearest `merge_asof`, and backward/bidirectional interpolation.
- Kept H22, V357, H27, runtime signals, forward ledgers, and SIGNAL_ONLY boundaries unchanged.
- Permanent new-session reminder: unresolved overfitting or future-leakage risk means reject; holdout opens once only; no post-PnL rescue; H22 and V357 require real forward confirmation.
### Testing
- `D:\JIAOYI-CX\LOCAL_DEPS\venv\Scripts\python.exe -m pytest tests/test_research_automation.py -q` -> `18 passed`.
- `D:\JIAOYI-CX\LOCAL_DEPS\venv\Scripts\python.exe scripts/system_check.py source` -> passed.
- Full `D:\JIAOYI-CX\LOCAL_DEPS\venv\Scripts\python.exe -m pytest -q` -> passed; only the repository's existing skipped tests remained skipped.
- `python -m compileall scripts/system_check.py tests/test_research_automation.py src/okx_signal_system` -> passed.
- `git diff --check` -> passed with line-ending warnings only.
### Notes
- Modified files: `scripts/system_check.py`, `config/research_candidates/PRE_PNL_CANDIDATE_TEMPLATE.json`, `tests/test_research_automation.py`, `README.md`, `docs/RESEARCH_ROBUSTNESS_SCREEN_CN.md`, `docs/PROJECT_OVERVIEW_CN.md`, version metadata, release manifest, `docs/V3.56.36_RELEASE_CN.md`, and `progress.md`.
- Rollback: revert the v3.56.36 files listed above; do not modify existing H22/V357/H27 ledgers or frozen protocols.

## 2026-07-01 - Task: v3.56.35 desktop dashboard connection repair
### What was done
- Reproduced the failure state: the main Python signal process was running, but `127.0.0.1:3001` had no listener and the browser therefore showed a connection failure.
- Changed the desktop launcher to prefer the project-local Next.js CLI through `node.exe`, bypassing the Windows `npm.cmd` shim that could fail silently.
- Added an npm fallback, port-readiness polling, early-exit reporting, and a 20-second startup timeout so the browser opens only after the dashboard is actually ready.
- Added three-attempt retry handling for transient 15-minute closed-candle REST failures so one symbol's temporary network error does not immediately stop startup monitoring.
- Kept strategy, signal, data, backtest, forward-acceptance, and SIGNAL_ONLY boundaries unchanged.
### Testing
- Direct local Next.js CLI startup reached `http://127.0.0.1:3001` and reported ready.
- `D:\JIAOYI-CX\LOCAL_DEPS\venv\Scripts\python.exe -m pytest tests/test_desktop_runtime.py tests/test_release_safety.py -q` -> `51 passed`.
- Full `D:\JIAOYI-CX\LOCAL_DEPS\venv\Scripts\python.exe -m pytest -q` -> passed after the local runtime cache refreshed.
- Runtime restart verification: Dashboard root/API/candle endpoints returned HTTP 200; 15-minute backfill was complete for 21/21 symbols; OKX WebSocket connected with 21 subscriptions; latest scan status returned `running` with no error.
- Added regression coverage for direct local Next.js launch, npm fallback, and transient 15-minute backfill retries.
### Notes
- Modified files: `gui.py`, `tests/test_desktop_runtime.py`, `pyproject.toml`, `src/okx_signal_system/__init__.py`, release metadata/manifests, `docs/V3.56.35_RELEASE_CN.md`, and `progress.md`.
- Rollback: revert this entry and the listed v3.56.35 dashboard-launch files.

## 2026-06-16 - Task: v3.40 focused regression tests for execution safety and signal quality
### What was done
- Added focused regression coverage for manual-confirmation-only auto-stop behavior, reduce-only order propagation, explicit TP/SL safety handling, lifecycle idempotency/stable persistence, and labeler/execution simulator consistency.
- Kept changes limited to tests and did not modify source code.
### Testing
- `pytest tests/test_okx_mapping.py tests/test_desktop_runtime.py tests/test_signal_lifecycle.py tests/test_signal_quality_labeler.py tests/test_signal_quality.py` -> failed: 38 passed, 3 failed. Failures: TP/SL safety test did not raise, existing `test_gui_runtime_dependencies_import` could not import `gui` under the global pytest runner, reduce_only propagation stayed false.
- `pytest tests/test_okx_mapping.py::test_place_order_does_not_silently_drop_tp_sl tests/test_desktop_runtime.py::test_manual_confirmation_auto_stop_trigger_does_not_close_live_order tests/test_desktop_runtime.py::test_realtime_place_order_preserves_reduce_only tests/test_signal_lifecycle.py::test_lifecycle_record_signal_is_idempotent_and_persistence_is_stable tests/test_signal_quality_labeler.py::test_label_signal_matches_execution_simulator_result` -> failed: 3 passed, 2 failed. Passing: manual-only no live close, lifecycle stable/idempotent, labeler/execution consistency. Failing: TP/SL safety and reduce_only propagation.
### Notes
- Modified files: `tests/test_okx_mapping.py` added regression coverage that rejects silent TP/SL dropping before sending an OKX order; `tests/test_desktop_runtime.py` added manual-only no-live-close and reduce_only propagation regressions; `tests/test_signal_lifecycle.py` added lifecycle idempotency and stable persistence regression; `tests/test_signal_quality_labeler.py` added labeler/execution simulator consistency regression; `progress.md` records this verification round.
- Rollback: revert this log entry and the added test blocks in the four test files, or use the current git diff as the rollback point for this validation-only change set.

## 2026-06-16 - Task: v3.40 execution safety, tiering, and runtime gate consolidation
### What was done
- Consolidated the remaining audit items into v3.40: the OKX adapter now rejects silent TP/SL attachment and blocks live orders by default, realtime order mapping now preserves `reduce_only`, manual-confirmation runtime paths no longer auto-close by default, signal quality correlation uses same-direction grouping with a sample floor, and lifecycle payloads expose stable event summaries.
- Bumped the application version to v3.40 and documented the new runtime safety gates in the architecture notes.
### Testing
- `D:\JIAOYI-CX\LOCAL_DEPS\venv\Scripts\python.exe -m pytest tests/test_okx_mapping.py tests/test_desktop_runtime.py tests/test_config.py tests/test_signal_lifecycle.py tests/test_signal_quality_labeler.py tests/test_signal_quality.py` -> `46 passed`.
- `D:\JIAOYI-CX\LOCAL_DEPS\venv\Scripts\python.exe -m pytest tests/test_desktop_runtime.py::test_live_signal_monitor_auto_close_disabled_by_default tests/test_desktop_runtime.py::test_realtime_place_order_preserves_reduce_only tests/test_okx_mapping.py::test_place_order_is_disabled_by_default tests/test_config.py::test_base_config_locks_okx_and_disables_live_orders` -> `4 passed`.
- `D:\JIAOYI-CX\LOCAL_DEPS\venv\Scripts\python.exe -m compileall gui.py main.py src` -> passed.
- `D:\JIAOYI-CX\LOCAL_DEPS\venv\Scripts\python.exe -m pytest` -> `157 passed`.
- `npm.cmd run lint` in `dashboard` -> passed.
- `npm.cmd run build` in `dashboard` -> passed.
- Runtime API observation: `http://127.0.0.1:3001/api/dashboard` returned running dashboard state with 21 symbols and connected websocket; `/api/candles/BTC-USDT-SWAP?timeframe=15m&limit=1` returned local history candles.
- `git diff --check` -> passed with line-ending warnings only.
### Notes
- Modified files: `config/base.yaml` added `auto_close_enabled: false`; `docs/SYSTEM_ARCHITECTURE.md` recorded v3.40 runtime safety gates; `gui.py`, `main.py`, `pyproject.toml`, `src/okx_signal_system/__init__.py`, and `start.bat` were bumped to v3.40; `src/okx_signal_system/exchange/okx.py` now blocks silent TP/SL attachment and defaults live orders off; `src/okx_signal_system/exchange/realtime.py` now preserves `reduce_only` and disables auto-close by default; `src/okx_signal_system/signal_quality/correlation.py`, `selector.py`, `labeler.py`, `lifecycle.py`, and `signal_quality/execution.py` carry the signal-quality and lifecycle consolidation; `tests/*` cover the new runtime and signal-quality gates; `progress.md` records the verification and implementation round.
- Rollback: revert the v3.40 version bump and safety-gate edits in the listed files, then remove this log entry; the current git diff is the rollback point.

## 2026-06-16 - Task: SIGNAL_ONLY runtime isolation for execution entrypoints
### What was done
- Removed transaction execution and account/position query methods from the formal realtime runtime API, leaving only market data, candles, WebSocket, and backfill paths.
- Disabled automatic close behavior in the live signal monitor and removed real position polling from the runtime loop.
- Updated runtime regression tests to assert the signal-only API surface and the absence of execution imports in the formal realtime path.
### Testing
- `D:\JIAOYI-CX\LOCAL_DEPS\venv\Scripts\python.exe -m pytest tests/test_desktop_runtime.py::test_realtime_runtime_api_is_signal_only tests/test_desktop_runtime.py::test_realtime_runtime_does_not_import_execution_functions tests/test_desktop_runtime.py::test_live_signal_monitor_auto_close_disabled_by_default tests/test_desktop_runtime.py::test_websocket_client_uses_15m_candle_channel tests/test_desktop_runtime.py::test_realtime_api_reports_failed_websocket_connect` -> `5 passed`.
- `D:\JIAOYI-CX\LOCAL_DEPS\venv\Scripts\python.exe -m compileall src\\okx_signal_system\\exchange\\realtime.py src\\okx_signal_system\\ml\\trading_brain.py` -> passed.
- `D:\JIAOYI-CX\LOCAL_DEPS\venv\Scripts\python.exe -m pytest tests/test_desktop_runtime.py tests/test_data_layer.py` -> `35 passed`.
### Notes
- Modified files: `src/okx_signal_system/exchange/realtime.py` removed execution/account methods and stripped real hold-management calls from the live monitor; `src/okx_signal_system/ml/trading_brain.py` removed account/position status logging that depended on the removed API surface; `tests/test_desktop_runtime.py` now asserts the signal-only runtime surface and execution-import absence; `progress.md` appended this entry.
- Rollback: revert the changes in the three listed source/test files and delete this log entry; that restores the pre-isolation runtime surface.

## 2026-06-16 - Task: v3.41 notification and release safety
### What was done
- Locked release-facing defaults to signal-only research, read-only data, and Feishu notification switches.
- Removed OKX private credential placeholders from `.env.example`, kept the Feishu webhook as an empty placeholder, and included only `.env.example` in the PyInstaller data list.
- Added release safety documentation and focused regression checks for environment templates, config defaults, packaging exclusion, gitignore rules, and release docs.
### Testing
- `D:\JIAOYI-CX\LOCAL_DEPS\venv\Scripts\python.exe -m pytest tests/test_config.py tests/test_release_safety.py` -> `8 passed`.
- `rg -n --hidden -S "(https://open\.feishu\.cn/open-apis/bot/v2/hook|https://.*larksuite.*open-apis/bot/v2/hook|OKX_API_KEY=.+|OKX_SECRET_KEY=.+|OKX_PASSPHRASE=.+|live_order_enabled:\s*true|auto_close_enabled:\s*true|OKX_LIVE_ORDER_ENABLED=true)" .env.example .gitignore okx_signal.spec start.bat config docs tests/test_release_safety.py` -> no release/config hits; only `tests/test_release_safety.py` contains forbidden strings as negative-test fixtures.
### Notes
- Modified files: `.env.example` now exposes only signal-only/read-only/notification switches and no OKX private key placeholders; `config/base.yaml` now marks `project.mode: SIGNAL_ONLY` and `data.read_only: true`; `okx_signal.spec` includes `.env.example` without packaging `.env`; `start.bat` defaults `SIGNAL_ONLY`, `DATA_READ_ONLY`, and `FEISHU_ENABLED`; `docs/RELEASE_SAFETY.md` documents release packaging rules; `tests/test_release_safety.py` adds release safety regression coverage; `progress.md` records this verification round.
- Rollback: revert the listed files to the previous git diff state, or remove the appended `v3.41 notification and release safety` log entry plus the paired release-safety edits and tests.

## 2026-06-16 - Task: Shadow 计分与 A/B/C 分层
### What was done
- Kept shadow quality scoring旁路化, so the optional quality model no longer changes tiering or double-counts into rank score.
- Reworked tier assignment so formal triggers stay in A/B only, while C is reserved for non-formal observation signals.
- Added focused regression coverage for shadow adjustment once-only application, formal-trigger non-C behavior, and ranked output retention.
### Testing
- `D:\JIAOYI-CX\LOCAL_DEPS\venv\Scripts\python.exe -m pytest tests/test_signal_quality.py tests/test_signal_quality_shadow.py tests/test_signal_scan_service.py` -> `17 passed`.
- `D:\JIAOYI-CX\LOCAL_DEPS\venv\Scripts\python.exe -m pytest tests/test_signal_scan_service.py::test_signal_scan_service_applies_shadow_adjustment_once tests/test_signal_quality.py::test_assign_tiers_keeps_non_formal_observation_in_c tests/test_signal_quality.py::test_assign_tiers_marks_insufficient_correlation_samples_as_c_observation` -> `3 passed`.
### Notes
- Modified files: `src/okx_signal_system/signal_service/scan.py` removed shadow adjustment from `candidate_rank_score` so it is counted once via `effective_score`; `src/okx_signal_system/signal_quality/selector.py` now assigns A/B to formal triggers and C only to non-formal observation signals; `tests/test_signal_quality.py` updated tiering expectations and added a non-formal C case; `tests/test_signal_scan_service.py` added a shadow-once regression; `progress.md` records this round.
- Rollback: revert the listed files to the previous git diff state, or remove this appended log entry and the paired selector/scan/test edits.

## 2026-06-16 - Task: Document SIGNAL_ONLY realtime runtime boundary
### What was done
- Added architecture documentation for the v3.41 realtime runtime boundary: formal realtime entrypoints keep market data, candles, WebSocket, and closed-data backfill, while execution and account/position methods stay unavailable.
### Testing
- Documentation-only follow-up to the already verified SIGNAL_ONLY runtime isolation tests in this log.
### Notes
- Modified files: `docs/SYSTEM_ARCHITECTURE.md` documents the SIGNAL_ONLY realtime runtime boundary; `progress.md` records this documentation follow-up.
- Rollback: remove the appended SIGNAL_ONLY section from `docs/SYSTEM_ARCHITECTURE.md` and delete this log entry.

## 2026-06-16 - Task: v3.41 signal-only release wording and packaging safety
### What was done
- Converged release-facing copy to signal-only semantics across README, release safety docs, Feishu notification text, panel labels, and generated reports.
- Removed qty/leverage/margin/live-order/position wording from Feishu and panel user-facing text while keeping internal risk fields intact for existing runtime contracts.
- Removed forced PyInstaller hidden imports for `okx.trade` and `okx_signal_system.exchange.position_monitor`, and kept `.env.example` as the only environment template included in the package.
- Added and updated focused tests for release-facing wording, panel labels, Feishu signal text, signal-only report copy, and package exclusion.
### Testing
- `D:\JIAOYI-CX\LOCAL_DEPS\venv\Scripts\python.exe -m pytest tests/test_feishu_notify.py tests/test_panel_view.py tests/test_reporting_signal.py tests/test_release_safety.py` -> `21 passed`.
- `D:\JIAOYI-CX\LOCAL_DEPS\venv\Scripts\python.exe -m compileall src\okx_signal_system\notify\feishu.py src\okx_signal_system\signal_service\app.py src\okx_signal_system\signal_service\job.py src\okx_signal_system\reporting\report_builder.py src\okx_signal_system\scheduler.py` -> passed.
- `rg -n -S "(正式交易信号|实盘下单|下单提醒|仓位:|杠杆:|保证金止损风险|open_positions:|live order|执行指令|okx\.trade|position_monitor|FEISHU_WEBHOOK_URL=https?://|OKX_API_KEY=.+|OKX_SECRET_KEY=.+|OKX_PASSPHRASE=.+)" README.md docs .env.example okx_signal.spec start.bat src\okx_signal_system\notify\feishu.py src\okx_signal_system\signal_service\app.py src\okx_signal_system\reporting\report_builder.py` -> no matches.
### Notes
- Modified files: `README.md` now states signal-only release boundaries; `docs/RELEASE_SAFETY.md` tightens release safety wording; `.env.example`, `config/base.yaml`, `start.bat`, and `okx_signal.spec` retain signal-only/read-only/package exclusion defaults; `src/okx_signal_system/notify/feishu.py` removes trading-style wording from notifications; `src/okx_signal_system/signal_service/app.py` removes trading-style labels from the panel; `src/okx_signal_system/signal_service/job.py` emits `mode: signal_only`; `src/okx_signal_system/reporting/report_builder.py` uses signal-only report copy; `src/okx_signal_system/scheduler.py` passes the renamed notification status field; `tests/test_feishu_notify.py`, `tests/test_panel_view.py`, `tests/test_reporting_signal.py`, and `tests/test_release_safety.py` cover the new wording and package safety gates; `progress.md` records this verification round.
- Rollback: revert the listed files to the previous git diff state, or remove this appended log entry plus the paired signal-only wording, packaging, and test edits.

## 2026-06-16 - Task: v3.41 SIGNAL_ONLY integration, stability verification, and delivery
### What was done
- Converged the desktop, CLI, Streamlit panel, release docs, package metadata, Feishu copy, and report copy to the v3.41 SIGNAL_ONLY product boundary: market data, signal scoring, ranking, lifecycle tracking, and notification only.
- Removed formal runtime execution/account/position entrypoints from the realtime API path, stopped GUI startup from using the legacy auto-stop monitor, and switched the desktop observation table to the signal lifecycle store.
- Fixed scan-cycle stability so checked K-line timestamps are committed only after a symbol finishes processing, and strategy/quality/risk exceptions are isolated per symbol as retryable `scan_error` health rows.
- Kept shadow quality scoring as a single adjustment and kept A/B for formal triggered candidates while reserving C for non-formal observation candidates.
- Tightened release safety: `.env` is not packaged, `.env.example` has no OKX private key placeholders, defaults remain SIGNAL_ONLY/read-only, and package/docs tests guard against reintroducing live execution wording.
### Testing
- `D:\JIAOYI-CX\LOCAL_DEPS\venv\Scripts\python.exe -m compileall gui.py main.py src tests` -> passed.
- `D:\JIAOYI-CX\LOCAL_DEPS\venv\Scripts\python.exe -m pytest` -> `167 passed`.
- `npm.cmd run lint` in `dashboard` -> passed.
- `npm.cmd run build` in `dashboard` -> passed.
- `Invoke-WebRequest http://127.0.0.1:3001/api/dashboard` -> returned dashboard JSON with 21 configured symbols, `closed_backfill.all_complete: true`, latest scan `websocket.connected: true`, `closed_kline_backfill.status: healthy`, `signal_closed_bar_gate.status: healthy`, lifecycle summary present, and `daily_learning_review.status: healthy`.
- Browser plugin page observation was attempted for `http://127.0.0.1:3001`, but the in-app browser was blocked by enterprise network policy; no workaround was used.
### Notes
- Modified files: `.env.example` removes private OKX credential placeholders; `README.md`, `docs/RELEASE_SAFETY.md`, and `docs/SYSTEM_ARCHITECTURE.md` document the signal-only boundary; `config/base.yaml`, `start.bat`, `main.py`, `gui.py`, `pyproject.toml`, and `src/okx_signal_system/__init__.py` align defaults and versioning to v3.41; `okx_signal.spec` excludes real `.env` and legacy execution packaging; `src/okx_signal_system/exchange/realtime.py` removes execution/account APIs from formal runtime; `src/okx_signal_system/ml/trading_brain.py`, `src/okx_signal_system/notify/feishu.py`, `src/okx_signal_system/reporting/report_builder.py`, `src/okx_signal_system/scheduler.py`, `src/okx_signal_system/signal_service/app.py`, and `src/okx_signal_system/signal_service/job.py` align signal-only wording and status payloads; `src/okx_signal_system/signal_service/scan.py` fixes checked-bar commit timing and per-symbol scan exception isolation; `src/okx_signal_system/signal_quality/selector.py` fixes formal/non-formal tiering; test files under `tests/` add release, runtime, panel, Feishu, selector, and scan stability regressions.
- Runtime-generated files observed during verification: `outputs/pushed_signals.sqlite3` and `outputs/signal_lifecycle.json` are local state outputs, not source changes.
- Rollback: use git to revert this commit after it is created, or before commit revert the listed source/test/doc/config files to the previous index state and delete this appended progress entry.

## 2026-06-16 - Task: v3.41 audit follow-up test reproducibility and release safety
### What was done
- Marked historical-data-dependent tests as integration tests and made them skip when `JIAOYI_DATA_DIR` is not configured, points to a missing directory, or does not contain the required local history dataset/files.
- Added release packaging guards for source distributions and Git source archives so formal releases exclude real environment files, runtime databases, caches, pyc files, build logs, and old output directories.
- Extended release safety tests to cover runtime artifact exclusion rules and the formal realtime API surface, while keeping the runtime API free of trade execution methods.
### Testing
- `D:\JIAOYI-CX\LOCAL_DEPS\venv\Scripts\python.exe -m pytest tests/test_release_safety.py -q` -> `11 passed`.
- `Remove-Item Env:JIAOYI_DATA_DIR -ErrorAction SilentlyContinue; D:\JIAOYI-CX\LOCAL_DEPS\venv\Scripts\python.exe -m pytest -m integration tests/test_backtest.py tests/test_data_layer.py tests/test_features.py tests/test_reporting_signal.py tests/test_strict_research.py -q` -> `18 skipped`.
- `$env:JIAOYI_DATA_DIR='D:\JIAOYI-CX\__missing_history_for_tests__'; D:\JIAOYI-CX\LOCAL_DEPS\venv\Scripts\python.exe -m pytest -m integration tests/test_backtest.py tests/test_data_layer.py tests/test_features.py tests/test_reporting_signal.py tests/test_strict_research.py -q` -> `18 skipped`.
- `$env:JIAOYI_DATA_DIR=''; D:\JIAOYI-CX\LOCAL_DEPS\venv\Scripts\python.exe -m pytest tests/test_backtest.py tests/test_data_layer.py tests/test_features.py tests/test_reporting_signal.py tests/test_strict_research.py -q` -> passed with historical-data tests skipped.
- `D:\JIAOYI-CX\LOCAL_DEPS\venv\Scripts\python.exe -m pytest --collect-only -q` -> collected `161` tests.
### Notes
- Modified files: `tests/_integration.py` adds the shared historical data skip helper; `tests/test_backtest.py`, `tests/test_data_layer.py`, `tests/test_features.py`, `tests/test_reporting_signal.py`, and `tests/test_strict_research.py` mark local-history tests as integration and call the skip helper; `pyproject.toml` registers the `integration` marker; `.gitignore`, `.gitattributes`, and `MANIFEST.in` exclude local runtime artifacts from source tracking guidance and formal release outputs; `docs/RELEASE_SAFETY.md` documents the runtime-artifact exclusion rule; `tests/test_release_safety.py` verifies the release exclusion rules and formal realtime API surface; `progress.md` records this round.
- Rollback: revert the files listed above to the previous git diff state and remove this appended progress entry; for release packaging only, remove `.gitattributes` and `MANIFEST.in` and restore the previous `.gitignore` patterns.

## 2026-06-16 - Task: v3.42 user-visible time sync and release display alignment
### What was done
- Synchronized the release-facing version display to v3.42 across the launcher, GUI, package metadata, and architecture boundary note.
- Aligned GUI signal rows, Streamlit signal views, scheduler summaries, and Feishu signal/status cards to show北京时间 for user-visible timestamps while keeping internal UTC handling unchanged.
- Added regression coverage for the Beijing-time presentation on the desktop UI and notification paths.
### Testing
- `D:\JIAOYI-CX\LOCAL_DEPS\venv\Scripts\python.exe -m compileall gui.py src\okx_signal_system\notify\feishu.py src\okx_signal_system\scheduler.py src\okx_signal_system\signal_service\app.py tests\test_desktop_runtime.py tests\test_feishu_notify.py tests\test_panel_view.py` -> passed.
- `D:\JIAOYI-CX\LOCAL_DEPS\venv\Scripts\python.exe -m pytest tests/test_desktop_runtime.py tests/test_feishu_notify.py tests/test_panel_view.py -q` -> passed.
- `D:\JIAOYI-CX\LOCAL_DEPS\venv\Scripts\python.exe -m pytest -q` -> passed with `18 skipped` historical-data integration tests.
### Notes
- Modified files: `gui.py` now shows Beijing time in the signal table and top-right clock; `src/okx_signal_system/notify/feishu.py` standardizes visible notification times; `src/okx_signal_system/scheduler.py` shows Beijing time in summaries; `src/okx_signal_system/signal_service/app.py` renders the current signal time in Beijing time; `docs/SYSTEM_ARCHITECTURE.md` documents the display rule; `tests/test_desktop_runtime.py`, `tests/test_feishu_notify.py`, and `tests/test_panel_view.py` lock in the behavior; `progress.md` records this round.
- Rollback: revert the listed files to the previous git diff state and remove this appended progress entry.

## 2026-06-16 - Task: P1-13/P1-14/P1-15 user-visible signal surface audit
### What was done
- Added a signal-only Feishu observation interface and moved formal GUI, CLI, and realtime A-tier push paths off the legacy trading-parameter signature.
- Fixed the GUI lifecycle table so visible headers match lifecycle values: entry reference, latest close, invalidation price, lifecycle status, observed bars, and signal timeframe.
- Removed the dashboard hard-coded Windows Python path and aligned dashboard history lookup with the Python backend by letting scripts resolve `JIAOYI_DATA_DIR` and config roots unless explicit dashboard overrides are set.
- Added focused regressions for Feishu signal-only signatures, legacy wrapper output, GUI lifecycle row values, and dashboard runtime path rules.
### Testing
- `D:\JIAOYI-CX\LOCAL_DEPS\venv\Scripts\python.exe -m pytest tests/test_feishu_notify.py tests/test_signal_lifecycle.py` -> `21 passed`.
- `node --experimental-strip-types --test src/lib/runtime-paths.test.ts` in `dashboard` -> `3 passed`.
- `npm.cmd run lint` in `dashboard` -> passed.
- `npm.cmd run build` in `dashboard` -> passed.
### Notes
- Modified files: `src/okx_signal_system/notify/feishu.py` adds signal-only `send_signal_observation`, keeps `send_signal_alert` signal-only, and makes the legacy card wrapper call by keyword without emitting account fields; `src/okx_signal_system/notify/__init__.py` exports the new interface; `main.py`, `gui.py`, and `src/okx_signal_system/exchange/realtime.py` route formal Feishu signal pushes through the signal-only interface; `gui.py` fixes lifecycle table headers and row values; `dashboard/src/lib/runtime-paths.ts`, `dashboard/src/lib/server-data.ts`, `dashboard/src/app/api/candles/[symbol]/route.ts`, `dashboard/scripts/read-candles.py`, and `dashboard/scripts/read-history-summary.py` align dashboard Python and history path resolution; `dashboard/README.md` documents dashboard runtime environment behavior; `docs/SYSTEM_ARCHITECTURE.md` documents that formal Feishu signals omit trading execution semantics; `tests/test_feishu_notify.py`, `tests/test_signal_lifecycle.py`, and `dashboard/src/lib/runtime-paths.test.ts` add focused coverage; `progress.md` records this round.
- Rollback: revert the listed files to the previous git diff state and remove this appended progress entry; no database, training, or backtest core files were intentionally changed for this task.

## 2026-06-16 - Task: v3.43 audit-driven signal-only stability optimization
### What was done
- Upgraded the release to v3.43 and made GUI, CLI, and the Windows launcher derive visible version text from the package version so future code changes do not leave stale version labels.
- Fixed SIGNAL_ONLY backtesting so accepted signals with no exchange `qty` are no longer dropped; the backtest now generates research-only sizing plus `outcome`, `net_r`, and `final_net_r` for training, quality scoring, and reports.
- Added strict backtest-result validation across grid search, research artifacts, daily learning, startup quality, rolling validation, reports, and CLI output so empty or malformed backtest tables cannot silently feed downstream modules.
- Made `OKXRealtimeAPI({})` and dashboard history readers tolerate missing local historical datasets by resolving history lazily through the shared Python data discovery path instead of hardcoded Windows paths.
- Tightened Feishu signal notification to expose a signal-only API and Beijing-time visible timestamps while keeping legacy wrapper calls as non-trading compatibility shims.
- Added `tests/__init__.py` so release archives can import `tests._integration` from a clean extraction.
### Testing
- `D:\JIAOYI-CX\LOCAL_DEPS\venv\Scripts\python.exe -m pytest tests\test_backtest_signal_only.py tests\test_backtest.py tests\test_desktop_runtime.py tests\test_release_safety.py -q` -> passed with historical-data integration tests skipped.
- `D:\JIAOYI-CX\LOCAL_DEPS\venv\Scripts\python.exe -m pytest tests\test_feishu_notify.py tests\test_panel_view.py -q` -> passed.
- `D:\JIAOYI-CX\LOCAL_DEPS\venv\Scripts\python.exe -m pytest -q` -> passed with historical-data integration tests skipped.
- `D:\JIAOYI-CX\LOCAL_DEPS\venv\Scripts\python.exe -m compileall src tests main.py gui.py` -> passed.
- `npm.cmd run check` in `dashboard` -> lint and production build passed.
### Notes
- Modified files: `src/okx_signal_system/backtest/runner.py` adds research sizing, standard R outputs, empty result schema, and validation; `src/okx_signal_system/backtest/cli.py`, `src/okx_signal_system/backtest/grid_search.py`, `src/okx_signal_system/backtest/research.py`, `src/okx_signal_system/ml/rolling_backtest.py`, `src/okx_signal_system/training/daily_learning.py`, `src/okx_signal_system/training/startup_quality.py`, and `src/okx_signal_system/reporting/report_builder.py` consume validated backtest results; `src/okx_signal_system/exchange/realtime.py` lazily resolves local history; `dashboard/scripts/read-candles.py`, `dashboard/scripts/read-history-summary.py`, `dashboard/src/app/api/candles/[symbol]/route.ts`, `dashboard/src/lib/runtime-paths.ts`, and `dashboard/src/lib/server-data.ts` remove hardcoded local history assumptions; `src/okx_signal_system/notify/feishu.py` and `src/okx_signal_system/notify/__init__.py` expose signal-only notification helpers; `main.py`, `gui.py`, `start.bat`, `pyproject.toml`, and `src/okx_signal_system/__init__.py` synchronize v3.43 version display; `docs/SYSTEM_ARCHITECTURE.md` documents the v3.43 signal-only backtest behavior; tests under `tests/` cover release version consistency, clean archive imports, realtime lazy history lookup, Feishu signal-only API, lifecycle table values, strict report/research validation, and the accepted-signal-without-qty regression; `progress.md` records this round.
- Rollback: revert this commit after it is created, or before commit restore the listed files from the previous index state and remove this appended progress entry.

## 2026-06-16 - Task: v3.44 backtest outcome compatibility closure
### What was done
- Upgraded the release metadata to v3.44 so package metadata, GUI display, CLI banner, and the Windows launcher continue to use one shared package version.
- Tightened SIGNAL_ONLY backtest outcome mapping so downstream quality training only receives the supported `TP`, `SL`, and `TIMEOUT` classes.
- Removed the last `qty`/`leverage` parameters from the legacy Feishu signal-card compatibility wrapper, while keeping the wrapper routed through the signal-only alert path.
- Documented the v3.44 signal-only backtest boundary and locked regressions for the supported outcome set, Feishu signal-only signatures, and stale package metadata.
### Testing
- `D:\JIAOYI-CX\LOCAL_DEPS\venv\Scripts\python.exe -m pytest -q` -> passed with historical-data integration tests skipped.
- `D:\JIAOYI-CX\LOCAL_DEPS\venv\Scripts\python.exe -m compileall src tests main.py gui.py` -> passed.
- `npm.cmd run check` in `dashboard` -> lint and production build passed.
- `node --experimental-strip-types --test src/lib/runtime-paths.test.ts` in `dashboard` -> passed.
### Notes
- Modified files: `src/okx_signal_system/backtest/runner.py` maps all non-TP/SL exits to `TIMEOUT` and rejects unsupported result outcomes; `tests/test_backtest_signal_only.py` asserts the supported outcome set and rejection path; `src/okx_signal_system/notify/feishu.py` removes execution-style parameters from the legacy wrapper; `src/okx_signal_system/ml/trading_brain.py` stops passing those fields; `tests/test_feishu_notify.py` covers all Feishu signal signatures; `src/okx_signal_system/__init__.py`, `pyproject.toml`, and `src/okx_contract_signal_system.egg-info/PKG-INFO` bump the package metadata to v3.44; `tests/test_release_safety.py` covers the egg-info version; `docs/SYSTEM_ARCHITECTURE.md` documents the supported outcome classes; `progress.md` records this round.
- Rollback: revert this commit after it is created, or before commit restore the listed files from the previous index state and remove this appended progress entry.

## 2026-06-17 - Task: v3.45 SIGNAL_ONLY acceptance report follow-up
### What was done
- Upgraded release metadata to v3.45 so package metadata and version-derived GUI/CLI/launcher displays stay synchronized after this code change.
- Added a shared `SignalOutcomeSimulator` and routed backtest exits plus quality labels through one entry anchoring, stop/target, timeout, MFE/MAE, and outcome simulation path.
- Consolidated scheduler, report job, realtime monitor, and TradingBrain observation scanning onto `SignalScanService`; formal A-tier candidates remain the only candidates that create lifecycle records or formal Feishu signal pushes, while B/C candidates stay as summaries or observations.
- Replaced formal scan payload risk output with signal-only risk fields and stopped applying leverage-factor semantics to formal signal risk evaluation.
- Split historical data reads from runtime K-line cache writes and increased runtime cache retention to the strategy warm-up scale.
### Testing
- `D:\JIAOYI-CX\LOCAL_DEPS\venv\Scripts\python.exe -m pytest tests/test_signal_quality_labeler.py tests/test_backtest.py tests/test_backtest_signal_only.py -q` -> passed with historical-data integration tests skipped.
- `D:\JIAOYI-CX\LOCAL_DEPS\venv\Scripts\python.exe -m pytest tests/test_signal_scan_service.py tests/test_learning_lock.py tests/test_reporting_signal.py -q` -> passed with historical-data integration tests skipped.
- `D:\JIAOYI-CX\LOCAL_DEPS\venv\Scripts\python.exe -m pytest tests/test_config.py tests/test_data_layer.py tests/test_desktop_runtime.py tests/test_release_safety.py -q` -> passed with historical-data integration tests skipped.
- `D:\JIAOYI-CX\LOCAL_DEPS\venv\Scripts\python.exe -m pytest -q` -> passed with historical-data integration tests skipped.
- `D:\JIAOYI-CX\LOCAL_DEPS\venv\Scripts\python.exe -m compileall src tests main.py gui.py` -> passed.
- `npm.cmd run check` in `dashboard` -> lint and production build passed.
- `git diff --check` -> passed.
### Notes
- Modified files: `src/okx_signal_system/signal_quality/outcome.py` adds the shared outcome simulator; `src/okx_signal_system/signal_quality/execution.py` and `src/okx_signal_system/backtest/runner.py` use it for quality labels and backtest exits; `src/okx_signal_system/signal_service/scan.py`, `src/okx_signal_system/scheduler.py`, `src/okx_signal_system/signal_service/job.py`, and `src/okx_signal_system/ml/trading_brain.py` consolidate formal/observation scan paths through `SignalScanService`; `src/okx_signal_system/exchange/realtime.py` and `src/okx_signal_system/paths.py` separate historical reads from runtime cache writes; `tests/test_signal_quality_labeler.py`, `tests/test_signal_scan_service.py`, `tests/test_data_layer.py`, `tests/test_config.py`, and `tests/test_learning_lock.py` add focused regressions; `docs/SYSTEM_ARCHITECTURE.md`, `pyproject.toml`, `src/okx_signal_system/__init__.py`, and `src/okx_contract_signal_system.egg-info/PKG-INFO` document and version the v3.45 boundary.
- SQLite lifecycle event/outbox tables were not added because they are a database schema change and need explicit approval before implementation.
- Rollback: revert this commit after it is created, or before commit restore the listed source/test/doc/version files from the previous index state and remove this appended progress entry.

## 2026-06-17 - Task: v3.46 SQLite lifecycle event and outbox persistence
### What was done
- Upgraded formal signal lifecycle persistence from JSON to SQLite and kept the existing lifecycle store API used by GUI, realtime monitoring, and scan services.
- Added `lifecycle_records` for current signal state, `lifecycle_events` for state-change history, and `notification_outbox` for A-tier Feishu push pending/sent/failed tracking.
- Added one-time migration from legacy `signal_lifecycle.json` into the SQLite lifecycle store and connected realtime/GUI A-tier push boundaries to update outbox status.
- Bumped release metadata to v3.46 and documented the lifecycle storage boundary.
### Testing
- `D:\JIAOYI-CX\LOCAL_DEPS\venv\Scripts\python.exe -m pytest tests/test_signal_lifecycle.py` -> `9 passed`.
- `D:\JIAOYI-CX\LOCAL_DEPS\venv\Scripts\python.exe -m pytest tests/test_signal_lifecycle.py tests/test_desktop_runtime.py tests/test_signal_runtime.py tests/test_feishu_notify.py` -> `43 passed`.
- `D:\JIAOYI-CX\LOCAL_DEPS\venv\Scripts\python.exe -m pytest tests/test_release_safety.py tests/test_signal_lifecycle.py tests/test_desktop_runtime.py tests/test_signal_runtime.py tests/test_feishu_notify.py` -> `56 passed`.
- `D:\JIAOYI-CX\LOCAL_DEPS\venv\Scripts\python.exe -m compileall src tests main.py gui.py` -> passed.
- `D:\JIAOYI-CX\LOCAL_DEPS\venv\Scripts\python.exe -m pytest -q` -> passed with historical-data integration tests skipped.
- `npm.cmd run check` in `dashboard` -> lint and production build passed.
- `git diff --check` -> passed.
### Notes
- Modified files: `src/okx_signal_system/signal_quality/lifecycle.py` replaces JSON lifecycle persistence with SQLite records/events/outbox tables and legacy JSON migration; `src/okx_signal_system/exchange/realtime.py` records formal A-tier push outbox status; `gui.py` records GUI A-tier push outbox status; `tests/test_signal_lifecycle.py` covers schema, events, outbox, idempotency, and migration; `tests/test_desktop_runtime.py` covers realtime outbox status updates; `docs/SYSTEM_ARCHITECTURE.md`, `pyproject.toml`, `src/okx_signal_system/__init__.py`, and `src/okx_contract_signal_system.egg-info/PKG-INFO` document and version the v3.46 boundary; `progress.md` records this round.
- Rollback: revert this commit after it is created, or before commit restore the listed files from the previous index state and remove this appended progress entry.

## 2026-06-17 - Task: P0-2 runtime backfill read-only history protection
### What was done
- Added a read-only guard to `DataGapHandler` so runtime backfill paths cannot write to the configured read-only historical data root.
- Changed closed-candle runtime backfill to resolve and write the runtime cache by default instead of falling back to `find_lightweight_history`.
- Removed the dashboard 5m closed-backfill entrypoint's explicit historical-root write target so it follows the runtime cache default.
- Added focused regressions proving read-only files keep the same hash/mtime while closed backfill writes the runtime cache.
### Testing
- `D:\JIAOYI-CX\LOCAL_DEPS\venv\Scripts\python.exe -m pytest tests/test_data_layer.py::test_gap_handler_respects_read_only_guard tests/test_data_layer.py::test_closed_backfill_service_writes_runtime_cache_without_mutating_history tests/test_config.py::test_find_runtime_cache_uses_config_runtime_cache_root` -> `3 passed`.
- `D:\JIAOYI-CX\LOCAL_DEPS\venv\Scripts\python.exe -m pytest tests/test_data_layer.py -m "not integration"` -> `20 passed, 4 deselected`.
### Notes
- Modified files: `src/okx_signal_system/data/gap_handler.py` adds `read_only` protection and marks sync failed if a write is refused; `src/okx_signal_system/data/closed_backfill.py` writes closed backfill output to runtime cache by default; `main.py` stops directing the 5m dashboard backfill to the historical dataset parent; `tests/test_data_layer.py` adds focused hash/mtime and runtime-cache regressions; `progress.md` records this round.
- Rollback: restore the listed files from the previous index state and remove this appended progress entry; no database, outcome policy, notification, version, or documentation files were intentionally changed for this task.

## 2026-06-17 - Task: v3.45 acceptance follow-up closure
### What was done
- Unified the remaining v3.45 follow-up surfaces so true observation candidates stay separate from formal signals, non-push formal candidates no longer leak into tier C, and A/B Feishu messages carry the quality-model旁路字段.
- Bumped release metadata and visible package versioning to v3.47 so code, GUI, package metadata, and docs stay aligned.
- Locked the new behavior with focused regressions for tiering, near-breakout observation, Feishu quality-model rendering, config snapshotting, read-only history protection, and signal outcome consistency.
### Testing
- `D:\JIAOYI-CX\LOCAL_DEPS\venv\Scripts\python.exe -m pytest tests/test_signal_quality.py tests/test_signal_scan_service.py tests/test_feishu_notify.py tests/test_signal_quality_labeler.py tests/test_data_layer.py tests/test_config.py -q` -> passed.
- `D:\JIAOYI-CX\LOCAL_DEPS\venv\Scripts\python.exe -m compileall src tests main.py gui.py` -> passed.
- `npm.cmd run check` in `dashboard` -> lint and production build passed.
- `D:\JIAOYI-CX\LOCAL_DEPS\venv\Scripts\python.exe -m pytest -q` -> passed with historical-data integration tests skipped.
- `git diff --check` -> passed.
### Notes
- Modified files: `src/okx_signal_system/signal_quality/selector.py`, `src/okx_signal_system/signal_quality/observation.py`, `src/okx_signal_system/signal_service/scan.py`, `tests/test_signal_quality.py`, and `tests/test_signal_scan_service.py` tighten tiering/observation behavior; `src/okx_signal_system/notify/feishu.py`, `src/okx_signal_system/exchange/realtime.py`, `gui.py`, and `tests/test_feishu_notify.py` carry quality-model fields into A/B signal messages; `pyproject.toml`, `src/okx_signal_system/__init__.py`, `src/okx_contract_signal_system.egg-info/PKG-INFO`, and `docs/SYSTEM_ARCHITECTURE.md` sync version/doc release notes; `progress.md` records this round.
- Rollback: restore the listed files from the previous index state and remove this appended progress entry; this round does not require database migration rollback or runtime-cache cleanup.

## 2026-06-17 - Task: realtime pandas concat FutureWarning guard
### What was done
- Adjusted live K-line append merging so empty or all-NA realtime cache frames do not participate in pandas concat before the new candle row is applied.
- Preserved the existing non-empty merge, duplicate timestamp overwrite, sort, and cache retention behavior for valid realtime rows.
- Added a focused regression covering empty and all-NA realtime cache append paths.
### Testing
- `D:\JIAOYI-CX\LOCAL_DEPS\venv\Scripts\python.exe -m pytest tests/test_data_layer.py::test_realtime_store_preserves_quote_volume tests/test_data_layer.py::test_realtime_store_overwrites_same_bar_without_dtype_error tests/test_data_layer.py::test_realtime_store_appends_to_empty_or_all_na_cache_without_concat tests/test_data_layer.py::test_realtime_store_writes_runtime_cache_without_mutating_history -q` -> `5 passed`.
- `D:\JIAOYI-CX\LOCAL_DEPS\venv\Scripts\python.exe -m pytest tests/test_data_layer.py::test_realtime_store_preserves_quote_volume tests/test_data_layer.py::test_realtime_store_overwrites_same_bar_without_dtype_error tests/test_data_layer.py::test_realtime_store_appends_to_empty_or_all_na_cache_without_concat tests/test_data_layer.py::test_realtime_store_writes_runtime_cache_without_mutating_history -W error::FutureWarning -q` -> `5 passed`.
- `git diff --check -- src/okx_signal_system/exchange/realtime.py tests/test_data_layer.py` -> passed; Git reported existing LF-to-CRLF working-copy normalization warnings only.
### Notes
- Modified files: `src/okx_signal_system/exchange/realtime.py` adds the narrow live-row concat helper and uses it in `RealtimeDataStore.append_candle`; `tests/test_data_layer.py` adds the empty/all-NA realtime cache regression; `progress.md` records this round.
- Rollback: restore `src/okx_signal_system/exchange/realtime.py` and `tests/test_data_layer.py` from the previous index state, then remove this appended progress entry.

## 2026-06-17 - Task: reusable release zip packaging script
### What was done
- Added a reusable release zip builder that packages repository files and writes every zip entry with POSIX `/` separators.
- Added a focused release safety regression proving generated zip entries do not contain backslashes and include a nested path.
- Documented the release zip command in the release safety guide.
### Testing
- `D:\JIAOYI-CX\LOCAL_DEPS\venv\Scripts\python.exe -m pytest tests/test_release_safety.py -q` -> `14 passed`.
- `git diff --check` -> passed.
### Notes
- Modified files: `scripts/build_release_zip.py` adds the reusable zip generation entrypoint; `tests/test_release_safety.py` verifies release zip entry names use POSIX separators and include a nested path; `docs/RELEASE_SAFETY.md` documents the release zip command and separator rule; `progress.md` records this round.
- Rollback: restore `scripts/build_release_zip.py`, `tests/test_release_safety.py`, and `docs/RELEASE_SAFETY.md` from the previous index state, then remove this appended progress entry.

## 2026-06-17 - Task: v3.47 near-breakout ATR adaptive observation
### What was done
- Replaced the C-tier near-breakout watch threshold with ATR-based distance gating and propagated the ATR distance into scan health output and observation payloads.
- Kept the existing public observation shape intact for current callers, while adding an optional ATR distance field to observation candidates.
- Added focused regressions proving the observation gate behaves by ATR ratio rather than fixed price percentage across different price levels.
### Testing
- `D:\JIAOYI-CX\LOCAL_DEPS\venv\Scripts\python.exe -m pytest tests\test_signal_quality.py tests\test_signal_scan_service.py -q` -> `23 passed`.
### Notes
- Modified files: `src/okx_signal_system/signal_quality/observation.py` now computes near-breakout eligibility from ATR distance and returns the ATR ratio alongside price gap; `src/okx_signal_system/signal_quality/candidate.py` adds optional `breakout_distance_atr` to `ObservationCandidate`; `src/okx_signal_system/signal_service/scan.py` includes ATR breakout distance in health payloads and observation payloads and scores observations from ATR distance; `tests/test_signal_quality.py` adds ATR-threshold regression coverage; `tests/test_signal_scan_service.py` verifies scan health and observation payload exposure; `docs/SYSTEM_ARCHITECTURE.md` documents the ATR-based C-tier observation rule; `progress.md` records this round.
- Rollback: restore the listed source, test, and docs files from the previous index state, then remove this appended progress entry.

## 2026-06-17 - Task: v3.47 lifecycle terminal states and outbox auto-enqueue
### What was done
- Extended lifecycle storage to persist target price and terminal result timestamps, and added compatibility columns to the SQLite store without rebuilding existing databases.
- Added confirmed-state result transitions for target reached, stop reached, and hold-time timeout, while keeping pre-confirmation invalidated and expired behavior unchanged.
- Wired lifecycle events to auto-create deterministic notification outbox rows from the event identity so each state change is queued once without sending network traffic.
- Expanded focused lifecycle coverage for target, stop, timeout result, automatic outbox enqueueing, and legacy JSON migration.
### Testing
- `D:\JIAOYI-CX\LOCAL_DEPS\venv\Scripts\python.exe -m pytest tests/test_signal_lifecycle.py -q` -> `10 passed`.
### Notes
- Modified files: `src/okx_signal_system/signal_quality/lifecycle.py` now stores `take_profit`, adds `TARGET_REACHED/STOP_REACHED/TIMEOUT_RESULT`, migrates missing SQLite columns, and auto-enqueues deterministic outbox records for lifecycle events; `tests/test_signal_lifecycle.py` covers target/stop/timeout transitions, outbox auto-enqueue, and legacy JSON migration; `progress.md` records this round.
- Rollback: restore `src/okx_signal_system/signal_quality/lifecycle.py` and `tests/test_signal_lifecycle.py` from the previous index state, then remove this appended progress entry.

## 2026-06-17 - Task: v3.48 acceptance optimization closure
### What was done
- Bumped package metadata and visible GUI/CLI/launcher version sources to v3.48 after this code change.
- Completed the v3.47 follow-up closure that could be verified locally: ATR-relative C-tier observations, SQLite lifecycle terminal result states, deterministic lifecycle notification outbox enqueueing, shared research sizing/cost helpers, correlation sample floor, typed runtime config helpers, reusable release ZIP packaging, and pandas concat FutureWarning protection.
- Routed runtime Feishu delivery through `NotificationDispatcher` for A-tier signals, B-tier summaries, status reports, startup notification, and candidate health reports, leaving legacy Feishu helpers only as compatibility functions.
- Changed signal-card timing so user-facing signal generation time is Beijing time derived from the signal K-line timestamp when available, with a separate Beijing notification send time; runtime status JSON now also exposes Beijing display timestamps.
- Documented the v3.48 lifecycle, dispatcher, Beijing-time, C-tier observation, correlation, and research sizing boundaries.
### Testing
- `D:\JIAOYI-CX\LOCAL_DEPS\venv\Scripts\python.exe -m compileall -q src main.py gui.py tests` -> passed.
- `D:\JIAOYI-CX\LOCAL_DEPS\venv\Scripts\python.exe -m pytest tests/test_feishu_notify.py tests/test_config.py tests/test_costs.py tests/test_signal_quality.py tests/test_signal_quality_labeler.py tests/test_signal_scan_service.py tests/test_signal_lifecycle.py tests/test_data_layer.py tests/test_release_safety.py -q` -> passed with expected historical-data skips.
- `D:\JIAOYI-CX\LOCAL_DEPS\venv\Scripts\python.exe -m pytest -q` -> passed with expected historical-data skips.
- `npm.cmd run check` in `dashboard` -> lint and production build passed.
- `git diff --check` -> passed; Git reported existing LF-to-CRLF working-copy normalization warnings only.
- Direct `v347_acceptance_audit_cn.md` file lookup on Desktop/repo did not find the source report, so closure was based on the locally observable v3.47/v3.48 acceptance gaps and subagent read-only scans.
### Notes
- Modified files: `scripts/build_release_zip.py` adds the reusable release ZIP builder; `docs/RELEASE_SAFETY.md` documents ZIP packaging rules; `docs/SYSTEM_ARCHITECTURE.md` documents the v3.48 acceptance boundaries; `gui.py` routes notifications through the dispatcher and exposes Beijing status timestamps; `main.py` routes startup and signal notifications through the dispatcher; `pyproject.toml`, `src/okx_signal_system/__init__.py`, and `src/okx_contract_signal_system.egg-info/PKG-INFO` bump metadata to v3.48; `src/okx_signal_system/backtest/runner.py`, `src/okx_signal_system/risk/costs.py`, and `src/okx_signal_system/signal_quality/execution.py` share research sizing/cost assumptions; `src/okx_signal_system/config.py`, `src/okx_signal_system/signal_service/job.py`, `src/okx_signal_system/scheduler.py`, and `src/okx_signal_system/ml/trading_brain.py` consume typed runtime config and dispatcher paths; `src/okx_signal_system/exchange/realtime.py` handles concat warnings, dispatcher notification routing, and Beijing status output; `src/okx_signal_system/notify/__init__.py`, `src/okx_signal_system/notify/dispatcher.py`, and `src/okx_signal_system/notify/feishu.py` add the dispatcher and Beijing signal/send time behavior; `src/okx_signal_system/signal_quality/candidate.py`, `src/okx_signal_system/signal_quality/observation.py`, and `src/okx_signal_system/signal_service/scan.py` carry ATR-relative C-tier observations; `src/okx_signal_system/signal_quality/correlation.py` and `src/okx_signal_system/signal_quality/selector.py` enforce the correlation sample floor and explicit overrides; `src/okx_signal_system/signal_quality/lifecycle.py` adds terminal result states, compatible SQLite columns, and outbox auto-enqueueing; tests under `tests/` cover the release ZIP, config helpers, cost helpers, realtime concat guard, Feishu dispatcher/time behavior, lifecycle terminal states/outbox, ATR observation, labeler consistency, and scan service behavior.
- Rollback: revert this commit after it is created, or before commit restore the listed files from the previous index state, remove `scripts/build_release_zip.py` and `src/okx_signal_system/notify/dispatcher.py`, then remove this appended progress entry.

## 2026-06-17 - Task: release denylist, cooldown index, and historical volatility guard
### What was done
- Hardened release ZIP packaging with an internal denylist that filters sensitive env files, SQLite/database artifacts, caches, pyc files, build logs, and outputs in both git-tracked and fallback traversal paths.
- Changed backtest cooldown handling to track the real bar index cutoff instead of decrementing by candidate count.
- Replaced the extreme-volatility detector's full-sequence average with a historical-only expanding mean to remove future leakage.
- Added focused regressions for release packaging safety, cooldown progression, and volatility lookahead safety.
### Testing
- `pytest tests/test_release_safety.py tests/test_backtest_signal_only.py tests/test_features.py -q` -> passed (`28 passed, 3 skipped`).
### Notes
- Modified files: `scripts/build_release_zip.py` adds the internal denylist and path guard; `src/okx_signal_system/backtest/runner.py` switches cooldown to a real bar index cutoff; `src/okx_signal_system/features/indicators.py` uses historical-only expanding volatility baseline; `tests/test_release_safety.py`, `tests/test_backtest_signal_only.py`, and `tests/test_features.py` add the focused regressions; `docs/RELEASE_SAFETY.md` records the packaging rule; `progress.md` records this round.
- Rollback: restore the listed files from the previous index state, then remove this appended progress entry.

## 2026-06-17 - Task: v3.49 acceptance report closure
### What was done
- Implemented v3.48 acceptance follow-up closure for research promotion, runtime configuration injection, lifecycle notification consumption, release packaging safety, cooldown indexing, and volatility lookahead protection.
- Added common-calendar train/validation/blind research splits with purge/embargo buffers, finite-PF filtering, parameter-neighborhood stability gates, per-fold train/freeze/validate walk-forward behavior, and three-scenario historical cost stress replay artifacts.
- Routed backtest, quality-label execution, GUI scan, realtime scan, sizing, and cost defaults through `RuntimeConfig` while preserving explicit test overrides.
- Updated lifecycle terminal evaluation to use OHLC outcome rules and added lifecycle outbox worker delivery through `NotificationDispatcher`; lifecycle notification times are Beijing time.
- Bumped package and visible version sources to v3.49.0.
### Testing
- `python -m py_compile src\okx_signal_system\backtest\research.py src\okx_signal_system\backtest\runner.py tests\test_strict_research.py` -> passed.
- `py -3.12 -m pytest tests\test_strict_research.py tests\test_backtest.py tests\test_backtest_signal_only.py tests\test_costs.py tests\test_config.py tests\test_signal_lifecycle.py tests\test_feishu_notify.py tests\test_release_safety.py tests\test_features.py -q` -> passed with expected historical-data skips.
- Full compile, full pytest, dashboard check/build, release zip build, and git diff checks will be run before commit.
### Notes
- Modified files: `src/okx_signal_system/backtest/research.py` adds research split, stability, walk-forward, blind, and cost stress logic; `src/okx_signal_system/backtest/grid_search.py` rejects infinite/low-sample PF ranking; `src/okx_signal_system/backtest/runner.py` adds runtime risk injection, real-index cooldown, and regime trade metadata; `src/okx_signal_system/config.py`, `src/okx_signal_system/risk/costs.py`, `src/okx_signal_system/risk/model.py`, `src/okx_signal_system/signal_quality/execution.py`, `gui.py`, and `src/okx_signal_system/exchange/realtime.py` extend runtime config injection; `src/okx_signal_system/signal_quality/lifecycle.py` and `src/okx_signal_system/notify/dispatcher.py` close lifecycle outbox delivery; `scripts/build_release_zip.py` and `docs/RELEASE_SAFETY.md` harden release packaging; `src/okx_signal_system/features/indicators.py` removes lookahead volatility averaging; version metadata files and tests under `tests/` cover this round.
- Rollback: revert the v3.49 commit after it is created, or before commit restore the listed files from the previous index state and remove this appended progress entry.

## 2026-06-17 - Task: v3.49 final validation and package
### What was done
- Completed final validation for the v3.49 acceptance closure after integrating all parallel worker changes.
- Built the desktop release ZIP at `C:\Users\26492\Desktop\okx-contract-signal-system-v3.49.0.zip` and verified its entries use POSIX separators and exclude sensitive/runtime artifacts while retaining `.env.example`.
### Testing
- `python -m compileall -q src main.py gui.py tests` -> passed.
- `py -3.12 -m pytest -q` -> passed with expected historical-data skips.
- `npm.cmd run check` in `dashboard` -> lint and production build passed.
- `git diff --check` -> passed; Git reported LF-to-CRLF working-copy normalization warnings only.
- Release ZIP verification -> 159 entries, 0 backslash entries, 0 denied sensitive/runtime artifacts, `.env.example` retained.
### Notes
- Modified files: same v3.49 acceptance closure files listed in the preceding progress entry; this entry records final verification and package generation only.
- Rollback: revert the v3.49 commit after it is created, and delete `C:\Users\26492\Desktop\okx-contract-signal-system-v3.49.0.zip` if the packaged artifact should be removed.

## 2026-06-17 - Task: v3.50 version and release-safety preparation
### What was done
- Updated the shared package version source and package metadata to `3.50.0`; desktop, CLI, and launcher displays already read from this shared source.
- Cleaned low-risk `realtime.py` release-facing wording from automatic/live trading language to signal-only realtime monitoring language without changing runtime behavior.
- Documented the v3.50 release-preparation verification rule for version display and release ZIP safety.
- Verified the release ZIP denylist still excludes sensitive environment files, runtime caches, output directories, and SQLite/database artifacts while retaining `.env.example`.
### Testing
- `D:\JIAOYI-CX\LOCAL_DEPS\venv\Scripts\python.exe -m compileall -q src\okx_signal_system\__init__.py src\okx_signal_system\exchange\realtime.py tests\test_release_safety.py` -> passed.
- `D:\JIAOYI-CX\LOCAL_DEPS\venv\Scripts\python.exe -m pytest tests\test_release_safety.py -q` -> `17 passed`.
- `D:\JIAOYI-CX\LOCAL_DEPS\venv\Scripts\python.exe -m pytest tests\test_desktop_runtime.py -q` -> `15 passed`.
- `D:\JIAOYI-CX\LOCAL_DEPS\venv\Scripts\python.exe scripts\build_release_zip.py --output dist\okx-contract-signal-system-v3.50.0-check.zip` plus ZIP entry inspection -> `159` entries, `.env.example` retained, `0` denied sensitive/runtime artifacts; the temporary check ZIP was deleted after verification.
- `rg -n "自动下单|实盘下单|下单提醒|启动实盘交易监控|Failed to start live trading|实时交易所API模块|OKX_API_KEY=.+|OKX_SECRET_KEY=.+|OKX_PASSPHRASE=.+|FEISHU_WEBHOOK_URL=https?://" -S src\okx_signal_system\exchange\realtime.py README.md docs .env.example okx_signal.spec scripts\build_release_zip.py tests\test_release_safety.py` -> no matches.
### Notes
- Modified files: `pyproject.toml` sets project metadata to `3.50.0`; `src/okx_signal_system/__init__.py` sets the shared runtime version to `3.50.0`; `src/okx_contract_signal_system.egg-info/PKG-INFO` aligns packaged metadata to `3.50.0`; `src/okx_signal_system/exchange/realtime.py` changes only module/docstring/error wording from trading to signal-only monitoring; `docs/RELEASE_SAFETY.md` records v3.50 release-preparation checks; `progress.md` records this round.
- Other working-tree changes in research, runner, and lifecycle files were already present from parallel work and were not modified by this round.
- Rollback: restore `pyproject.toml`, `src/okx_signal_system/__init__.py`, `src/okx_contract_signal_system.egg-info/PKG-INFO`, `src/okx_signal_system/exchange/realtime.py`, and `docs/RELEASE_SAFETY.md` from the previous index state, then remove this appended progress entry.

## 2026-06-17 - Task: v3.50 lifecycle outbox runtime integration
### What was done
- Connected lifecycle outbox consumption to the actual GUI, realtime monitor, and scheduler scan loops so lifecycle events are attempted after each scan/publish pass.
- Reused the scheduler-owned lifecycle store for scheduler scans so lifecycle events generated during a cycle are visible to the scheduler outbox worker.
- Added a compatible worker retry cap that moves repeatedly failing outbox rows to `DEAD_LETTER` without changing the existing SQLite schema.
- Added focused tests for worker dead-letter behavior and runtime entrypoint outbox consumption.
### Testing
- `pytest tests/test_signal_lifecycle.py tests/test_lifecycle_outbox_runtime.py tests/test_desktop_runtime.py -q` -> `30 passed`.
### Notes
- Modified files: `src/okx_signal_system/signal_quality/lifecycle.py` adds the outbox retry limit, `DEAD_LETTER` marking, summary count, and preserves sent/dead-letter rows when re-enqueued; `src/okx_signal_system/signal_quality/__init__.py` exports the lifecycle outbox worker for runtime modules; `src/okx_signal_system/scheduler.py` passes its lifecycle store into scans and runs the lifecycle outbox worker after each scheduler cycle; `src/okx_signal_system/exchange/realtime.py` owns and runs a lifecycle outbox worker after realtime scan publishing; `gui.py` lazily owns and runs a lifecycle outbox worker after GUI signal checks; `tests/test_signal_lifecycle.py` covers dead-letter transition; `tests/test_lifecycle_outbox_runtime.py` covers scheduler and realtime runtime worker calls; `docs/SYSTEM_ARCHITECTURE.md` documents the runtime consumption and dead-letter rule; `progress.md` records this round.
- Concurrent working-tree changes in research, daily learning, version metadata, release safety, and scheduler/GUI/realtime neighboring logic were already present or made by other agents and were not reverted.
- Rollback: revert only the lifecycle-outbox hunks in the listed source/doc/test files, delete `tests/test_lifecycle_outbox_runtime.py`, and remove this appended progress entry; avoid restoring whole files because several listed files contain concurrent non-outbox changes.

## 2026-06-17 - Task: daily learning sidecar and scheduler B-tier notification consistency
### What was done
- Locked daily learning review to candidate discovery and sidecar reporting only: reports and candidate payloads now always expose `promotion_eligible=false` and `promotion_allowed=false`, even when old auto-promotion config flags are enabled.
- Kept formal parameter promotion dependent on the strict research pipeline by marking daily learning candidates with `strict_research_pipeline_required` and moving strict research imports to candidate-search execution only.
- Aligned scheduler notifications with GUI/realtime behavior by sending B-tier summaries through the existing `NotificationDispatcher` path with per-candle summary de-duplication.
- Updated GUI daily-learning runtime status and architecture notes so operators do not see daily learning as an auto-promotion path.
### Testing
- `D:\JIAOYI-CX\LOCAL_DEPS\venv\Scripts\python.exe -m py_compile src\okx_signal_system\training\daily_learning.py src\okx_signal_system\scheduler.py gui.py tests\test_daily_learning_review.py tests\test_scheduler_notifications.py` -> passed.
- `D:\JIAOYI-CX\LOCAL_DEPS\venv\Scripts\python.exe -m pytest tests\test_daily_learning_review.py tests\test_scheduler_notifications.py -q` -> `10 passed`.
- `D:\JIAOYI-CX\LOCAL_DEPS\venv\Scripts\python.exe -m pytest tests\test_feishu_notify.py tests\test_scheduler_notifications.py -q` -> `19 passed`.
- `D:\JIAOYI-CX\LOCAL_DEPS\venv\Scripts\python.exe -m pytest tests\test_daily_learning_review.py tests\test_learning_lock.py -q` -> `10 passed`.
### Notes
- Modified files: `src/okx_signal_system/training/daily_learning.py` adds explicit non-promotion report fields and requires strict research for formal promotion; `src/okx_signal_system/scheduler.py` returns scan selection to the scheduler cycle and sends B-tier summaries through the dispatcher; `gui.py` displays daily learning as non-promotion in runtime module status; `tests/test_daily_learning_review.py` proves daily learning cannot auto-promote even when gate checks pass and legacy config flags are true; `tests/test_scheduler_notifications.py` proves scheduler B-tier summaries are sent and marked; `docs/SYSTEM_ARCHITECTURE.md` documents daily learning and notification boundaries; `progress.md` records this round.
- Concurrent working-tree changes were present in research, lifecycle, scheduler, GUI, docs, and version/release files; this round did not revert them or touch `research.py`, lifecycle outbox implementation, or version metadata.
- Rollback: revert only the hunks for daily-learning non-promotion, scheduler B-tier summary dispatch, GUI daily-learning status, the added tests, and the related architecture note; delete `tests/test_scheduler_notifications.py`; then remove this appended progress entry. Avoid whole-file restore because several listed files contain concurrent non-task changes.

## 2026-06-17 - Task: v3.50 strict research acceptance closure
### What was done
- Changed formal research splitting to use one global timestamp boundary set for all symbols; missing bars now reduce per-symbol samples without moving train/validation/blind dates.
- Made strict research fail closed with `STRICT_SPLIT_UNAVAILABLE` instead of silently falling back to per-symbol 75/25 splits; legacy fallback now requires explicit `--legacy-split`.
- Changed shared-parameter ranking to aggregate portfolio PF from total winning net PnL divided by absolute total losing net PnL, while reporting symbol PF distribution, profitable-symbol ratio, and contribution concentration.
- Locked blind-set evaluation by default and added an explicit unlock path that writes a blind access manifest with hashes, git commit, token hash, and first access time.
- Added purged walk-forward validation to formal artifacts/checklist, strengthened neighbor stability gates, and recomputed stress costs from trade facts with fee/slippage/funding components.
### Testing
- `python -m compileall -q src main.py gui.py tests` -> passed.
- `py -3.12 -m pytest tests\test_strict_research.py tests\test_signal_lifecycle.py tests\test_lifecycle_outbox_runtime.py tests\test_daily_learning_review.py tests\test_scheduler_notifications.py -q` -> passed with expected integration skips.
- `py -3.12 -m pytest -q` -> passed with expected historical-data skips.
- `npm.cmd run check` in `dashboard` -> lint and production build passed.
- `py -3.12 -m pytest tests\test_release_safety.py tests\test_desktop_runtime.py tests\test_feishu_notify.py -q` -> `50 passed`.
- `git diff --check` -> passed; Git reported LF-to-CRLF working-copy normalization warnings only.
### Notes
- Modified files: `src/okx_signal_system/backtest/research.py` enforces strict timestamp splits, blind locking, portfolio PF aggregation, purged walk-forward acceptance, stronger neighbor stability, and trade-fact cost replay; `src/okx_signal_system/backtest/research_cli.py` exposes explicit legacy split and blind unlock flags; `src/okx_signal_system/backtest/runner.py` records fee/slippage/funding cost components and market regime in backtest trades; `tests/test_strict_research.py` covers strict split boundaries, fail-closed split behavior, portfolio PF aggregation, real blind lock checks, neighbor ratio gating, and cost replay funding effects; `docs/SYSTEM_ARCHITECTURE.md` documents the v3.50 strict research closure; `progress.md` records this round.
- Concurrent v3.50 changes from subagents are recorded above and were not reverted.
- Rollback: revert this commit after it is created, or before commit restore the listed research/runner/CLI/test/doc files from the previous index state and remove this appended progress entry.

## 2026-06-17 - Task: v3.51 notification ranking contract and release boundary
### What was done
- Separated formal A/B ranking from C-tier observation ranking so high-scoring C watches no longer affect A/B rank, A-tier selection, B-tier ordering, or formal notification totals.
- Routed realtime/CLI A-tier notification callbacks through the full candidate payload when available while preserving the legacy two-argument callback contract.
- Bumped shared package/version metadata and visible version source to `3.51.0`.
- Documented the v3.51 production boundary: experimental learning paths can emit diagnostics and suggestions only, not production automatic parameter tuning.
### Testing
- `D:\JIAOYI-CX\LOCAL_DEPS\venv\Scripts\python.exe -m pytest tests\test_signal_quality.py tests\test_signal_scan_service.py tests\test_desktop_runtime.py tests\test_release_safety.py tests\test_scheduler_notifications.py` -> `60 passed`.
- `git diff --check` -> passed; Git reported LF-to-CRLF working-copy normalization warnings only.
### Notes
- Modified files: `src/okx_signal_system/signal_quality/candidate.py` adds `watch_rank` for C-tier observation candidates; `src/okx_signal_system/signal_quality/selector.py` ranks formal candidates and observation candidates separately; `src/okx_signal_system/signal_service/scan.py` writes `rank/total_formal_candidates` for A/B and `watch_rank/total_observations` for C into health and payloads; `src/okx_signal_system/exchange/realtime.py` sends candidate-aware callbacks and keeps notification totals formal-only; `main.py` uses the candidate-aware A-tier dispatcher path when realtime supplies a candidate; `gui.py` and `src/okx_signal_system/scheduler.py` use formal-only totals for A/B notifications and B-tier summaries; `pyproject.toml`, `src/okx_signal_system/__init__.py`, and `src/okx_contract_signal_system.egg-info/PKG-INFO` set version `3.51.0`; `docs/RELEASE_SAFETY.md` and `docs/SYSTEM_ARCHITECTURE.md` document ranking and learning-production boundaries; `tests/test_signal_quality.py`, `tests/test_signal_scan_service.py`, `tests/test_desktop_runtime.py`, and `tests/test_release_safety.py` add focused regressions for ranking, payload, callback, version, and docs.
- Concurrent working-tree changes in research, data quality, lifecycle, scheduler dedupe, and related tests were present from other agents and were not reverted.
- Rollback: revert only the hunks listed above for ranking contract, callback payload, version metadata, docs, and focused tests, then remove this appended progress entry; avoid whole-file restore because several touched files also contain concurrent non-task changes.

## 2026-06-17 - Task: v3.51 lifecycle durability and notification key consistency
### What was done
- Changed lifecycle retention so `SignalLifecycleStore(max_records=...)` only limits the in-memory view and does not physically delete SQLite lifecycle records, events, or outbox rows.
- Enabled SQLite WAL, busy timeout, and NORMAL synchronous mode for lifecycle storage, and added compatible outbox lease columns for old databases.
- Made outbox polling return only due rows, added atomic claim/lease handling for workers, and added retry backoff without clearing active leases on duplicate enqueue.
- Expanded scheduler B-tier summary de-duplication keys with strategy version, parameter hash, and candidate identity hash.
### Testing
- `D:\JIAOYI-CX\LOCAL_DEPS\venv\Scripts\python.exe -m py_compile src\okx_signal_system\signal_quality\lifecycle.py src\okx_signal_system\notify\signal_dedupe.py src\okx_signal_system\scheduler.py tests\test_signal_lifecycle.py tests\test_scheduler_notifications.py` -> passed.
- `D:\JIAOYI-CX\LOCAL_DEPS\venv\Scripts\python.exe -m pytest tests\test_signal_lifecycle.py tests\test_scheduler_notifications.py tests\test_feishu_notify.py -q` -> `37 passed`.
- `git diff --check` -> passed; Git reported LF-to-CRLF working-copy normalization warnings only.
### Notes
- Modified files: `src/okx_signal_system/signal_quality/lifecycle.py` preserves SQLite history under `max_records`, initializes SQLite durability pragmas, migrates lease columns, filters pending rows by `available_at`, and claims worker rows with leases/backoff; `src/okx_signal_system/notify/signal_dedupe.py` adds strategy/parameter/candidate dimensions to B-tier summary keys; `src/okx_signal_system/scheduler.py` passes current strategy params and candidates into B-tier summary keys and stores key metadata; `tests/test_signal_lifecycle.py` covers max-record durability, due-only pending rows, claim leases, and duplicate enqueue lease preservation; `tests/test_scheduler_notifications.py` covers B-tier summary key variation by version, params, and candidates; `docs/SYSTEM_ARCHITECTURE.md` documents the new lifecycle/outbox/key behavior; `progress.md` records this round.
- Concurrent working-tree changes exist in unrelated files from other agents, including research, data quality, version metadata, release safety, GUI/main, realtime, and signal ranking files; this round did not modify or revert those areas.
- Rollback: revert only the lifecycle/outbox/key hunks in the listed source, test, and docs files, then remove this appended progress entry; avoid whole-file restore while parallel-agent changes remain in the same working tree.

## 2026-06-17 - Task: v3.51 data reliability closure
### What was done
- Tightened formal data quality audit so any unclosed historical row fails, while runtime cache audit can explicitly allow only one final open candle and exclude that tail row from formal-quality checks.
- Added audit failures and report fields for NaN/Inf numeric values, timestamp boundary drift, irregular intervals and internal gaps, OHLC invalidity, symbol/timeframe mismatches, and invalid quote volume.
- Extended closed-candle backfill status with internal gap count, maximum missing bars, continuous tail bars, minimum tail requirement, and required history bars; any internal gap or insufficient continuous tail now prevents `all_complete=true`.
- Added focused data-layer regressions for all-open historical rows, runtime tail open handling, structural/value quality failures, 1/2/10/180/500-bar internal gaps, and insufficient continuous tail history.
### Testing
- `D:\JIAOYI-CX\LOCAL_DEPS\venv\Scripts\python.exe -m pytest tests\test_data_layer.py -q` -> passed with expected integration skips.
- `D:\JIAOYI-CX\LOCAL_DEPS\venv\Scripts\python.exe -m pytest tests\test_data_layer.py tests\test_signal_scan_service.py tests\test_daily_learning_review.py -q` -> passed with expected integration skips.
- `git diff --check -- src\okx_signal_system\data\quality.py src\okx_signal_system\data\closed_backfill.py tests\test_data_layer.py docs\SYSTEM_ARCHITECTURE.md` -> passed; Git reported LF-to-CRLF working-copy normalization warnings only.
### Notes
- Modified files: `src/okx_signal_system/data/quality.py` adds strict audit metrics, open-row policy, and runtime-tail compatibility option; `src/okx_signal_system/data/closed_backfill.py` adds closed-only status evaluation with internal-gap and continuous-tail gates; `tests/test_data_layer.py` adds the focused reliability regressions; `docs/SYSTEM_ARCHITECTURE.md` documents the data reliability closure; `progress.md` records this round.
- This round did not modify `research.py`, `lifecycle.py`, notification files, or version metadata. Concurrent working-tree changes in unrelated files were left intact.
- Rollback: revert only the data reliability hunks in the listed source, test, and docs files, then remove this appended progress entry; avoid whole-file restore because `docs/SYSTEM_ARCHITECTURE.md` and `progress.md` contain parallel-agent changes.

## 2026-06-17 - Task: v3.51 combined hardening and final validation
### What was done
- Synchronized the strict research default version to `v3.51-strict` and added a release-safety regression so the CLI default cannot drift from the core research entrypoint again.
- Documented the strict research warmup-window evaluation, canonical data manifest hashing, and one-time SQLite blind registry boundary in the architecture doc.
- Completed the combined hardening pass across data reliability, strict research, lifecycle durability, ranking separation, notification context, and version consistency.
### Testing
- `py -3.12 -m pytest tests\test_release_safety.py::test_strict_research_default_version_matches_cli_release tests\test_strict_research.py tests\test_data_layer.py -q` -> passed with expected integration skips.
- `python -m compileall -q src main.py gui.py tests` -> passed.
- `py -3.12 -m pytest -q` -> passed with expected integration skips.
- `npm.cmd run check` in `dashboard` -> lint and production build passed.
- `git diff --check` -> passed; Git reported LF-to-CRLF working-copy normalization warnings only.
### Notes
- Modified files: `src/okx_signal_system/backtest/research.py` now defaults research artifacts to `v3.51-strict`; `tests/test_release_safety.py` locks the CLI/core research version match; `docs/SYSTEM_ARCHITECTURE.md` records the v3.51 strict research hardening; `progress.md` records this final validation round.
- The earlier concurrent-agent changes in data reliability, lifecycle, notification ranking, and release safety remain intact and were validated together in the final full test run.
- Rollback: revert the v3.51 research default/version hunk in `src/okx_signal_system/backtest/research.py`, the new release-safety regression in `tests/test_release_safety.py`, and the added v3.51 architecture note, then remove this appended progress entry.

## 2026-06-17 - Task: v3.51 quality model timestamp-group split and feature schema lock
### What was done
- Changed quality model walk-forward validation so train, purge, and validation windows advance by candle timestamp groups instead of raw rows, keeping same-timestamp multi-symbol samples in one segment.
- Locked quality model feature selection to the explicit signal-quality feature schema so future outcome columns and accidental numeric columns cannot enter training features.
- Added focused regressions for same-timestamp multi-symbol split integrity and feature-column leakage.
### Testing
- `D:\JIAOYI-CX\LOCAL_DEPS\venv\Scripts\python.exe -m pytest tests\test_signal_quality_model.py -q` -> `7 passed`.
- `D:\JIAOYI-CX\LOCAL_DEPS\venv\Scripts\python.exe -m pytest tests\test_signal_quality_model.py tests\test_signal_quality_shadow.py tests\test_signal_quality_features.py -q` -> `12 passed`.
### Notes
- Modified files: `src/okx_signal_system/signal_quality/model.py` adds the explicit feature schema, filters requested/inferred features through it, and performs walk-forward splitting by timestamp groups; `tests/test_signal_quality_model.py` adds regressions for grouped splits and feature leakage; `docs/SYSTEM_ARCHITECTURE.md` documents the quality model split and feature boundary; `progress.md` records this round.
- Rollback: revert only the quality-model hunks in `src/okx_signal_system/signal_quality/model.py`, the added tests in `tests/test_signal_quality_model.py`, and the appended architecture/log entries from this round.

## 2026-06-17 - Task: v3.51 lifecycle outcome simulator alignment
### What was done
- Changed lifecycle research outcome evaluation to reuse `SignalOutcomeSimulator` from the first closed candle after signal time, so TP/SL/TIMEOUT no longer wait for the pattern to become `CONFIRMED`.
- Kept `TIMEOUT_RESULT` aligned with labeler/execution by requiring a complete `max_hold_bars` observation window before emitting timeout results.
- Added regressions for TP reached inside the first post-signal candle before confirmation, and for incomplete tail data not producing timeout labels.
### Testing
- `D:\JIAOYI-CX\LOCAL_DEPS\venv\Scripts\python.exe -m py_compile src\okx_signal_system\signal_quality\lifecycle.py src\okx_signal_system\signal_quality\outcome.py src\okx_signal_system\signal_quality\execution.py src\okx_signal_system\signal_quality\labeler.py tests\test_signal_lifecycle.py tests\test_signal_quality_labeler.py` -> passed.
- `D:\JIAOYI-CX\LOCAL_DEPS\venv\Scripts\python.exe -m pytest tests\test_signal_lifecycle.py tests\test_signal_quality_labeler.py -q` -> `28 passed`.
- `git diff --check -- src\okx_signal_system\signal_quality\lifecycle.py src\okx_signal_system\signal_quality\outcome.py src\okx_signal_system\signal_quality\execution.py src\okx_signal_system\signal_quality\labeler.py tests\test_signal_lifecycle.py tests\test_signal_quality_labeler.py docs\SYSTEM_ARCHITECTURE.md` -> passed; Git reported LF-to-CRLF working-copy normalization warnings only.
### Notes
- Modified files: `src/okx_signal_system/signal_quality/lifecycle.py` evaluates terminal research outcomes through `SignalOutcomeSimulator` before confirmation and suppresses incomplete-window timeouts; `tests/test_signal_lifecycle.py` adds lifecycle/labeler parity and incomplete-tail regressions; `tests/test_signal_quality_labeler.py` locks incomplete-tail labeler behavior; `docs/SYSTEM_ARCHITECTURE.md` documents lifecycle outcome anchoring and complete-timeout behavior; `progress.md` records this round.
- This round did not modify `outcome.py`, `execution.py`, or `labeler.py`; they were included in compile validation because the lifecycle result contract depends on them.
- Concurrent working-tree changes in research, model, lifecycle outbox, and related docs/logs were present and were not reverted.
- Rollback: revert only the lifecycle outcome hunks in `src/okx_signal_system/signal_quality/lifecycle.py`, the added/adjusted regressions in `tests/test_signal_lifecycle.py` and `tests/test_signal_quality_labeler.py`, the lifecycle sentence added to `docs/SYSTEM_ARCHITECTURE.md`, and this appended progress entry.

## 2026-06-17 - Task: v3.52 comprehensive research, lifecycle, notification, quality, and data hardening
### What was done
- Hardened strict research defaults so formal runs use all loaded symbols and the full grid by default, while explicit smoke runs are marked non-formal and cannot be promotion eligible.
- Split research dataset identity from source-path metadata, required a fixed blind-release token hash, and scoped blind registry identity to dataset identity plus blind timerange instead of commit or selected parameters.
- Integrated lifecycle outcome alignment, A-tier/outbox duplicate prevention, timestamp-group quality-model validation, explicit quality feature schema, and closed-candle internal-gap repair before startup blocking.
- Bumped shared package/version metadata and strict research artifact identity to `3.52.0` / `v3.52-strict`.
### Testing
- `py -3.12 -m pytest -q tests/test_strict_research.py tests/test_release_safety.py` -> passed with expected integration skips.
- `py -3.12 -m pytest -q tests/test_signal_lifecycle.py tests/test_signal_quality_labeler.py tests/test_lifecycle_outbox_runtime.py tests/test_scheduler_notifications.py tests/test_desktop_runtime.py tests/test_feishu_notify.py tests/test_signal_quality_model.py tests/test_signal_quality_shadow.py tests/test_signal_quality_features.py` -> passed.
- `py -3.12 -m pytest -q tests/test_data_layer.py` -> passed with expected integration skips.
- Full validation pending in the same task before commit and release zip.
### Notes
- Modified files: `src/okx_signal_system/backtest/research.py` separates dataset identity/location hashes, requires blind token hashes, fixes blind registry scope, tracks research mode/grid coverage, and separates pre-blind from final blind checks; `src/okx_signal_system/backtest/research_cli.py` makes formal research the default and adds explicit smoke mode; `src/okx_signal_system/data/closed_backfill.py` attempts internal gap repair before blocking startup; `src/okx_signal_system/signal_quality/lifecycle.py` aligns lifecycle outcomes with the shared simulator and marks matching triggered outbox rows sent after A-tier push; `src/okx_signal_system/signal_quality/model.py` locks feature schema and timestamp-group splits; `pyproject.toml`, `src/okx_signal_system/__init__.py`, and `src/okx_contract_signal_system.egg-info/PKG-INFO` set version `3.52.0`; `tests/test_strict_research.py`, `tests/test_data_layer.py`, `tests/test_lifecycle_outbox_runtime.py`, `tests/test_signal_lifecycle.py`, `tests/test_signal_quality_model.py`, and `tests/test_release_safety.py` add regressions; `docs/SYSTEM_ARCHITECTURE.md` documents v3.52 behavior; `progress.md` records this round.
- Rollback: revert only the v3.52 hunks in the listed source, test, version, and docs files, then remove this appended progress entry. Avoid whole-file restore because several files contain parallel-agent changes from this task.

### Testing
- `python -m compileall -q src main.py gui.py tests` -> passed.
- `py -3.12 -m pytest -q` -> passed with expected integration skips.
- `npm.cmd run check` in `dashboard` -> lint and production build passed.
- `git diff --check` -> passed; Git reported LF-to-CRLF working-copy normalization warnings only.
### Notes
- Final validation covered the v3.52 research hardening, lifecycle/outbox consistency, quality-model split/schema lock, and data backfill repair changes.

## 2026-06-17 - Task: v3.52 final validation closure
### What was done
- Re-verified the v3.52 research hardening, lifecycle/outbox consistency, quality-model split/schema lock, and data backfill repair changes after the final documentation sync.
- Confirmed the release version and strict research version are synchronized to `3.52.0` and `v3.52-strict`.
### Testing
- `python -m compileall -q src main.py gui.py tests` -> passed.
- `py -3.12 -m pytest -q` -> passed with expected integration skips.
- `npm.cmd run check` in `dashboard` -> lint and production build passed.
- `git diff --check` -> passed; Git reported LF-to-CRLF working-copy normalization warnings only.
### Notes
- Modified files in this closure: `docs/RELEASE_SAFETY.md` and `docs/SYSTEM_ARCHITECTURE.md` for v3.52 behavior notes; no code change in this substep.
- Rollback: remove this appended progress entry only.

## 2026-06-17 - Task: v3.53 comprehensive audit closure
### What was done
- Closed the v3.52 counterexample audit as v3.53 by making final blind acceptance depend on explicit blind portfolio evidence instead of lock/open state alone.
- Canonicalized strict research dataset identity so dataset name and row order do not change identity, while duplicate timestamps fail fast.
- Extended validation and blind frames with outcome tail history and required complete `max_hold_bars` windows before emitting timeout outcomes.
- Routed backtest slippage and fee calculation through shared `CostConfig`, and kept formal historical data strict about required `is_closed`.
- Changed gap detection to fail closed on unreadable data and to process minor gaps instead of skipping them.
- Hardened dashboard `npm run check` to include lint, production typecheck, isolated test typecheck, Node tests, and production build.
- Clarified lifecycle notification ownership so direct-send callers mark sent/failed state, dispatcher only sends, sent rows do not increment attempts, and repeated failure marking is idempotent.
- Bumped package/version metadata, GUI/launcher visible version source, and strict research artifact identity to `3.53.0` / `v3.53-strict`.
### Testing
- `python -m compileall -q src main.py gui.py tests` -> passed.
- `py -3.12 -m pytest -q` -> passed with expected local-data/integration skips.
- `npm.cmd run check` in `dashboard` -> lint, typecheck, Node tests, and production build passed; Node reported experimental Type Stripping warnings only.
- `git diff --check` -> passed; Git reported LF-to-CRLF working-copy normalization warnings only.
### Notes
- Modified files: `src/okx_signal_system/backtest/research.py` adds canonical content identity, blind portfolio acceptance, outcome-tail split windows, grid hash metadata, and `v3.53-strict`; `src/okx_signal_system/backtest/runner.py` uses shared `CostConfig` and complete timeout windows; `src/okx_signal_system/signal_quality/outcome.py` suppresses incomplete tail timeouts; `src/okx_signal_system/data/loader.py`, `src/okx_signal_system/data/quality.py`, and `src/okx_signal_system/data/gap_handler.py` enforce formal closed-candle data and fail closed on gap detection errors; `src/okx_signal_system/notify/dispatcher.py`, `src/okx_signal_system/scheduler.py`, `gui.py`, and `src/okx_signal_system/signal_quality/lifecycle.py` align direct-send outbox ownership; dashboard config files add full check coverage; tests add regressions for the audit counterexamples; release metadata and docs are synchronized to v3.53.
- Rollback: revert only the v3.53 hunks in the listed source, dashboard, test, version, and docs files, then remove this appended progress entry.

## 2026-06-17 - Task: v3.54 v3.53 counterexample audit closure
### What was done
- Rebuilt strict research periods around explicit warmup, trade, and outcome windows so validation outcome tails cannot overlap blind trade windows and blind trades retain full outcome tails.
- Added parameter-by-symbol cell coverage and selected-parameter symbol coverage gates, plus fail-closed validation portfolio and cost-stress metric checks.
- Added a two-phase blind registry flow: precommit stores token hash before blind access, unlock reads the stored hash, and same-command token+hash is self-authorized compatibility evidence that cannot pass promotion.
- Persisted separate lifecycle `setup_state` and `outcome_state` fields with migration support, and separated analysis stop from setup invalidation in payloads.
- Routed formal A-tier notifications through `notification_outbox` and the worker path instead of direct Feishu sends from GUI, realtime, scheduler, or main runtime.
- Fixed dashboard history path resolution for explicit Windows drive paths, UNC paths, POSIX paths, dataset paths, and lightweight-history roots.
- Bumped shared package/version metadata and strict research artifact identity to `3.54.0` / `v3.54-strict`.
### Testing
- `py -3.12 -m pytest -q tests/test_strict_research.py tests/test_backtest_signal_only.py` -> passed with expected integration skips.
- `py -3.12 -m pytest -q tests/test_signal_lifecycle.py tests/test_lifecycle_outbox_runtime.py tests/test_scheduler_notifications.py tests/test_desktop_runtime.py tests/test_feishu_notify.py` -> passed.
- `npm.cmd run check` in `dashboard` -> lint, typecheck, 9 Node tests, and production build passed; Node reported experimental Type Stripping warnings only.
- Full validation pending before commit and release zip.
### Notes
- Modified files include strict research/grid search and CLI, lifecycle store/tests, A-tier notification runtime paths/tests, dashboard runtime path tests, version metadata, release docs, and this progress entry.
- Rollback: revert only the v3.54 hunks in the listed source, dashboard, test, version, and docs files, then remove this appended progress entry.

## 2026-06-17 - Task: v3.55 lightweight realtime signal chain closure
### What was done
- Isolated the realtime chain from research/training/ML decision roots: `main.py`, `gui.py`, `exchange/realtime.py`, `scheduler.py`, and `signal_service/*` no longer import or start backtest, training, daily learning, or ML decision modules.
- Routed runtime notifications through `notification_outbox` and `LifecycleOutboxWorker`: A-tier signals, B-tier summaries, candidate health reports, status reports, startup notices, and lifecycle events now share one delivery/retry path.
- Enforced fail-fast candle structure for formal/runtime data. Missing `is_closed` is rejected, runtime append requires an explicit closed flag, and raw OKX ingestion is the only permissive metadata conversion path.
- Kept lifecycle outcome ownership in `SignalOutcomeSimulator` while lifecycle storage tracks setup and outcome state separately.
- Changed runtime risk payloads to signal-scoring fields: `expected_move_pct`, `failure_probability`, and `volatility_adjusted_score`, with execution/account fields left unset in formal payloads.
- Made ML/shadow scoring observation-only in live paths and kept offline methods for research diagnostics.
- Bumped shared package/version metadata and strict research artifact identity to `3.55.0` / `v3.55-strict`.
### Testing
- `py -3.12 -m pytest tests/test_realtime_research_isolation.py tests/test_lifecycle_outbox_runtime.py tests/test_scheduler_notifications.py tests/test_desktop_runtime.py tests/test_signal_scan_service.py tests/test_shadow_trading.py tests/test_strategy_risk.py -q` -> passed.
- `python -m compileall -q src main.py gui.py tests` -> passed.
- `py -3.12 -m pytest -q` -> passed with expected local-data/integration skips.
- `npm.cmd run check` in `dashboard` -> lint, typecheck, 9 Node tests, and production build passed; Node reported experimental Type Stripping warnings only.
- `git diff --check` -> passed; Git reported LF-to-CRLF working-copy normalization warnings only.
### Notes
- Modified files include runtime entrypoints, signal service runtime helpers, data loader/gap handler, notification dispatcher/outbox tests, lifecycle tests, ML observation locks, risk model, version metadata, release docs, and this progress entry.
- Rollback: revert only the v3.55 hunks in the listed source, test, version, and docs files, then remove this appended progress entry.

## 2026-06-18 - Task: v3.56.7 duplicate v3.56.6 dashboard health audit closure
### What was done
- Independently verified the re-uploaded v3.56.6 ZIP/SHA pair and confirmed it differed from the previously accepted content tree.
- Fixed offline/current stale-symbol evidence merging, invalid-age fail-closed behavior, fresh-authority scoping, and manifest blocking-reason semantics.
- Split actual runtime push permission from Dashboard operational health so the UI cannot claim a backend notification block that Python does not enforce.
- Replaced per-update full candle sorting with an ordered realtime fast path while preserving strict anomaly fallback and closed-bar precedence.
- Preserved both v3.56.6 audit/observation documents and restored a complete deterministic distribution source list.
- Bumped shared package and approved strategy version metadata to v3.56.7.
### Scope
- Dashboard health aggregation, realtime in-memory candle merge performance, release metadata, tests, and documentation. Strategy, scan decisions, runtime-cache storage boundaries, manifest semantic validation, lifecycle outbox, and notification delivery were not changed.

## 2026-06-20 - Task: v3.56.8 shadow ensemble and desktop runtime release
### What was done
- Integrated the frozen 4h Donchian plus volatility-compression shadow ensemble into the desktop runtime and Dashboard as an isolated research-only channel.
- Added strict closed-15m-to-4h resampling, frozen reference-universe checks, candidate/protocol validation, isolated SQLite persistence, status JSON output, and local cache smoke verification.
- Fixed Dashboard Python subprocess invocation for Windows `py -3.x` launchers and quoted interpreter paths; GUI now passes its active Python interpreter and strips inherited insecure TLS overrides before starting the Dashboard.
- Added the 5m target-range historical backfill utility and included all new runtime, config, candidate, test, script, and documentation files in the release list.
- Bumped package, GUI/launcher, and approved strategy version metadata to `3.56.8`; strict research identity remains `v3.56-strict`.
### Testing
- `git diff --check` -> passed with LF-to-CRLF normalization warnings only.
- `py -3.12 -m compileall -q src main.py gui.py tests scripts/backfill_5m_history_range.py scripts/check_shadow_ensemble_local.py` -> passed.
- `py -3.12 -m pytest` -> `352 passed, 18 skipped` before the version/documentation sync.
- `npm run check` in `dashboard` -> lint, typecheck, 21 Node tests, and production build passed.
- `py -3.12 scripts/check_shadow_ensemble_local.py` -> `running`, 21 eligible symbols, 0 skipped symbols.
### Notes
- The shadow channel does not enter the formal lifecycle, notification outbox, approved manifest, account, or order paths.
- Because approved strategy version remains synchronized with the package version, a `3.56.7` manifest fails closed under `3.56.8` and must be re-promoted from valid strict-research artifacts.

## 2026-06-21 - Task: v3.56.9 signal-only leverage guidance release
### What was done
- Added a deterministic `LeverageAdvice` module for formal signal manual review without account, position, credential, order, or private exchange dependencies.
- Bounded guidance by effective stop distance, a normalized 0.5% loss budget, an 8% reference margin fraction, signal score, reward/risk, and calibrated quality evidence.
- Enforced a 5x global cap, 1x cap for A-minus shadow signals, no guidance for B-tier signals, and 1x fallback for missing quality calibration.
- Integrated advisory-only guidance into direct and lifecycle-outbox A-tier notification dispatch while preserving the existing formal signal gate.
- Added release tests and synchronized package, launcher/GUI, approved strategy, distribution, release manifest, architecture, and safety documentation to `3.56.9`.
### Testing
- Targeted leverage/notification/outbox tests passed.
- Full Python test suite passed after correcting the distribution source list and release-facing advisory label.
- Dashboard `npm run check` passed: lint, typecheck, 21 Node tests, and production build.
### Notes
- This release does not add order placement, cancellation, account balance, live position, or automatic sizing behavior.
- A valid `3.56.8` approved manifest fails closed under `3.56.9` and must be re-promoted from valid strict-research artifacts.

## 2026-06-21 - Task: v3.56.10 deployment readiness closure
### What was done
- Fixed the Feishu emergency environment switch so it overrides YAML and is checked at every send call.
- Added the missing scheduler module CLI entrypoint.
- Added deployment preflight and runtime health-check scripts with observation/production modes.
- Added Linux systemd service, periodic health timer, logrotate policy, low-privilege service user layout, and an installation script that preserves runtime data and copies only the reviewed release allow-list.
- Converted the dependency lock to UTF-8 Linux format, removed test-only packages, and pinned the runtime WebSocket client.
- Added the complete Chinese deployment-before/after checklist, incident stop-push rules, upgrade and rollback procedures.
- Synchronized application, approved strategy, package, documentation, and release metadata to `3.56.10`.
### Safety boundary
- Observation deployment is allowed without a manifest, but formal notifications remain fail-closed.
- Production deployment requires a legitimate current-version strict-research approved manifest; no manifest was fabricated.
- Private OKX credentials remain prohibited and automatic execution remains disabled.

## 2026-06-24 - Task: v3.56.11 21-symbol panel and lightweight system closure
### What was done
- Confirmed the live runtime was already scanning and subscribing to all 21 configured OKX swaps; the desktop listbox height of 3 caused the misleading display.
- Updated the desktop panel to show the configured symbol count, expose up to 10 rows, retain scrolling, and show an explicit degraded title when configuration loading fails.
- Added hard runtime coverage checks for configuration, scan rows, WebSocket subscriptions, and closed-backfill symbol details.
- Consolidated source, preflight, runtime, shadow, and research gates into `scripts/system_check.py`; retained small source-only compatibility wrappers while removing them from the release package.
- Removed obsolete release modules: position monitoring, old ML stack, duplicate notification package, Streamlit app, and daily-learning runtime module.
- Removed Plotly, Streamlit, and unrelated transitive dependencies from runtime requirements.
- Updated Linux deployment and documentation to call the unified checker.
- Preserved one historical June 21 candidate-health dead letter for audit while making only dead letters from the latest 24 hours block current health.
- Synchronized application, approved strategy, package, documentation, and release metadata to `3.56.11`.
### Verification
- Python compileall passed.
- Full Python suite: `369 passed, 18 skipped`.
- Dashboard `npm run check` passed: lint, typecheck, 21 tests, and Next.js production build.
- Unified observation check passed: 21 configured, 21 scan rows, 21 WebSocket subscriptions, 21 closed-backfill rows, shadow eligible 21, skipped 0.
### Safety boundary
- SIGNAL_ONLY, data read-only, dry-run, no live order, and no automatic close boundaries remain unchanged.
- Formal A-grade push remains fail-closed because no legitimate current-version approved manifest exists.

## 2026-06-24 - Task: v3.56.12 automated research gate closure
### What was done
- Upgraded the pre-PnL candidate schema to v2 with structured family, parameter-space, and data-gate declarations.
- Added AST-based future-leak scanning for negative shifts, negative return transforms, centered rolling windows, forward iloc access, and future-label references.
- Added an automatic family registry and structural similarity gate against momentum, reversal, the 4h Donchian volatility-compression family, current candidates, and archived failures.
- Replaced self-reported parameter counts with derived free-parameter and grid-combination audits; hard limits are four free parameters and 216 combinations.
- Added complete-trade concentration gates for symbol, month, one trade, top three trades, effective positive-trade count, and minimum validation trade count.
- Added automatic baseline, 1.5x, and 2x cost replay from trade facts, including provenance checks that reject legacy total-cost multiplication.
- Made failed-strategy archival automatic and idempotent, with Desktop-folder preference and project-output fallback; data deferrals are not mislabeled as strategy failures.
- Added a data-readiness command that merges retained multi-year history with the latest runtime cache and enforces history, integrity, coverage, and incremental-data thresholds.
- Kept every new gate in the existing `scripts/system_check.py` entry and removed a duplicate runtime filename implementation.
- Synchronized package, approved strategy, documentation, tests, and release metadata to `3.56.12`.
### Verification
- Python compileall passed.
- Full Python suite: `376 passed, 18 skipped`.
- Dashboard `npm run check` passed: lint, typecheck, 21 tests, and production build.
- Data readiness passed with 21/21 coverage, 21/21 history-qualified symbols, and the integrity gate passed.
- A real WebSocket scan subscribed to 21/21 symbols, refreshed the runtime status, and left formal push closed by the missing current-version manifest.
- Current outbox health: failed 0, pending 0; the June 21 historical dead letter remains for audit.
- Shadow ensemble passed with 21 eligible symbols and zero skipped symbols.
### Safety boundary
- The gate can reject, report, generate derived stress evidence, and archive failures; it cannot auto-promote parameters or create an approved manifest.
- SIGNAL_ONLY, read-only market data, no live order, and no automatic close remain unchanged.

## 2026-06-27 - Task: v3.56.27 H22 staggered 3x3 momentum shadow
### What was done
- Froze and evaluated three equal-weight 14-day momentum cohorts with fixed 0/1/2-day calendar offsets and a three-day refresh cadence per cohort.
- Historical base/stress PF were 1.1754/1.0926, base/stress maximum drawdown were 8.12%/9.88%, and aggregate turnover fell 29.42% versus the daily parent path.
- Random-time, direction-reversal, 15-minute delay, calendar-phase, leave-one-phase-out, symbol, month, and top-day concentration checks passed; the middle stress segment remained near break-even, so the result is forward-shadow-only.
- Added a reusable staggered fixed-cadence weight constructor and extended the existing fixed-cadence forward runtime instead of duplicating it.
- Registered a separate daily aggregate forward ledger with no historical backfill. Registration is 2026-06-26T16:12:35Z and the first fully prospective entry is 2026-06-27T04:00:00Z.
- Kept the existing fixed three-day track independent and unchanged.
### Verification
- Historical H22 evaluation passed base/stress, random-time, direction-reversal, 15-minute delay, phase robustness, concentration, and future-leakage checks.
- Targeted fixed-cadence, parallel-acceptance, and release-safety tests passed.
- Full Python compileall and pytest suite passed with only the existing environment-dependent skips.
- Source and observation preflight system checks passed; missing explicit safety environment values and the absent formal approved manifest remained non-blocking warnings.
- Unified parallel acceptance refreshed all four research-shadow tracks without notifications, and the v3.56.27 release ZIP plus SHA-256 sidecar were built successfully.
### Safety boundary
- H22 is an execution variant of the existing momentum family, not an independent Alpha and not an A-grade strategy.
- Formal signals, Feishu formal-push gates, approved manifests, leverage, accounts, positions, and order paths are unchanged.

## 2026-06-27 - Task: H23-H27 independent mechanism and diversification continuation
### What was done
- H23 static liquidity-risk premium was stopped before PnL. Its Amihud-style rank had a 0.9484 median correlation with inverse trailing quote-volume rank and mainly became a persistent long-small/short-BTC-ETH size sort. No residualization, exclusions, or threshold rescue was allowed.
- H24 seven-day lottery-upside-concentration was frozen before PnL and then rejected. Base/stress PF were 1.0685/0.8474, stress return was -9.18%, stress maximum drawdown was 20.60%, random-time empirical p-value was 0.2255, and single-symbol positive contribution was 25.18%. The stronger five-day sensitivity result remained ineligible for post-result reselection.
- H25 common-liquidity-shock premium was stopped before PnL. Rolling correlation and rolling-beta representations of the same mechanism had only 0.5552 median rank agreement, 0.3209 tenth-percentile agreement, and 48.37% same-side slot agreement, so the signal definition was not invariant enough to open returns.
- H26 fixed 50/50 H22 plus v357 combination was frozen and evaluated. Daily return correlation was only 0.0514 and combined base/stress PF were 1.1974/1.0335, but base/stress maximum drawdown expanded to 29.52%/37.23% and maximum loss streaks worsened to 13/15 days. The combination was rejected without weight search, member removal, or risk-scale changes.
- H23, H24, H25, and H26 were written to `C:\Users\26492\Desktop\失败策略` with Chinese failure summaries and structured results where available.
- H27 forward diversification observation was frozen. It reads only the independent prospective H22 and v357 ledgers, does not backfill history, does not select weights, and measures future correlation, shared loss days, shared drawdowns, and worst common windows.
- Added an isolated H27 observer under `HISTORY_PACKAGES_20260621/RESEARCH/h27_h22_v357_forward_diversification_observation_v1/`. Initial status is `RECORD_ONLY_SAMPLE_INCOMPLETE`: H22 closed observations 0, v357 closed trades 2, data-quality checks passing.
### Current recovery point
- Application version remains `3.56.27`; no formal runtime, signal, A-grade, Feishu, leverage, account, position, order, or approved-manifest logic changed.
- Next resume sequence: inspect this final progress entry and `docs/PROJECT_OVERVIEW_CN.md`; verify Git status; refresh H22 and v357 ledgers; run the H27 observer; record only while sample gates are incomplete; then continue independent candidate intake from failure-family de-duplication and pre-PnL audit.
- Do not rerun H23-H26 parameter variants, search H22/v357 portfolio weights, reopen rejected sensitivity neighbors, or open the sealed official holdout.
### Documentation continuity rule
- Every completed research test, failure archive, forward-track change, or next-step transition must update both `progress.md` and `docs/PROJECT_OVERVIEW_CN.md` in the same work cycle.
- The two files must agree on version, active tracks, archived failures, sample state, prohibited rescue actions, and the single next recovery entry point.
- Documentation-only continuity updates do not require an application version bump, but they must be committed and pushed so a new session can recover from GitHub without reconstructing state from chat history.

## 2026-06-27 - Task: H27 refresh, H28-H30 intake closure, and global family-registry repair
### What was done
- Refreshed the frozen H22 staggered 3x3 forward ledger. Closed data reached 2026-06-26 15:00 UTC, protocol/ledger/data/snapshot integrity all passed, and the track remains `RECORD_ONLY_SAMPLE_INCOMPLETE` with zero fully prospective closed observations; the first eligible entry remains 2026-06-27 04:00 UTC.
- Refreshed the v357 shadow ensemble and acceptance adapter. Runtime coverage remained 21/21, the adapter reported six fully prospective observations over four closed-data days, and no production or formal-signal behavior changed.
- Refreshed H27 after both source ledgers. It remains `RECORD_ONLY_SAMPLE_INCOMPLETE` with H22 closed observations 0, v357 non-warmup closed trades 2, one common daily row, and data-quality checks passing. Correlation and shared-loss judgments remain unavailable and were not forced.
- H28 42-day downside-beta-asymmetry was audited without opening returns. It had 150 weekly observations, acceptable turnover, neighbor stability and concentration, but log/simple-return full portfolio agreement was only 73.33% versus the frozen 90% minimum and H19 same-side overlap was 28.67% versus the 25% maximum.
- H29 42-day dispersion-shock exposure was audited without opening returns. Top-decile dispersion shocks contained only 42.42% negative-market hours and a positive median market return; standard-deviation versus MAD definitions produced only 0.67% identical portfolios and median/p10 rank agreement of 0.5779/0.0436.
- A global de-duplication review then found that H28 repeated archived MC02 downside-beta asymmetry and H29 repeated archived MC05 dispersion expansion. Both were reclassified as duplicate re-audits rather than new independent Alpha families. Their additional failures remain useful evidence, but no new family credit is assigned.
- H30 funding-rate uncertainty was stopped at intake with no audit/backtest implementation and no PnL access. High funding volatility had no unique ex-ante direction and repeated the payer/data channel already closed by H21, I017 and I018; residualization or window search is prohibited.
- Added MC01-MC08, realized-funding, option-flow, H22 and H26 families plus aliases to `config/research_family_registry.json`. Future intake must search both this registry and `MECHANISM_CANDIDATE_REGISTER_20260624.json` before any implementation.
- Archived H28, H29 and H30 Chinese failure summaries to `C:\Users\26492\Desktop\失败策略`; added duplicate-reclassification evidence for H28 and H29.
### Verification and safety
- Application version remains `3.56.27`; no formal runtime, signal, notification, leverage, account, position, order or approved-manifest logic changed.
- H28/H29 future returns and PnL remained closed; H30 stopped before implementation; H27 remained record-only.
- Do not reopen MC01-MC08, H21, H23-H30, I016-I018 or C001 through renaming, residualization, direction reversal, symbol exclusion, threshold changes or neighbor selection.
### Current recovery point
- Refresh H22 and v357, then H27, and record only until sample gates are due.
- Before proposing any new Alpha, search `config/research_family_registry.json`, `MECHANISM_CANDIDATE_REGISTER_20260624.json`, Desktop failure archives and this progress log. Only a mechanism with a distinct payer, unique direction, causal observable fields, sufficient coverage and acceptable cost scale may proceed to a return-blind structural audit.
- The independent candidate queue is currently empty; do not manufacture a candidate from the closed funding, downside-beta, liquidity, dispersion, option-directional-flow, momentum/reversal or price-pattern families.

## 2026-06-27 - Task: v3.56.28 machine-enforced global failure-family deduplication
### What was done
- Refreshed H22, v357 and H27 before new intake work. Local closed data did not advance: H22 remains at zero fully prospective closed observations, v357 remains at two non-warmup closed trades under the H27 adapter, and H27 remains `RECORD_ONLY_SAMPLE_INCOMPLETE` with data quality passing.
- Rechecked the direct local contract-data audit and the option-surface admission audit. OHLCV, volume, spot/perpetual, mark/index, signed trades, funding, options surface and the currently short native positioning fields have no unused admissible Alpha route under the frozen policy.
- Identified the remaining process defect: `run_family_duplicate_gate` compared structured family signatures but did not machine-check registered historical aliases or the existing failure-fingerprint library. A renamed candidate could therefore reach implementation before a human found the duplicate.
- Added exact historical alias rejection for candidate IDs registered under prior family names.
- Added failure-fingerprint rejection using split mechanism tags. Underscore-connected terms now retain the full token and also expose component tokens, so `downside_market_beta`, `same_hour_seasonality` and similar relabels cannot hide their family.
- Embedded 30 failure/data-ineligibility fingerprints in `config/research_family_registry.json`, covering price, momentum/reversal, breakout, liquidity, spot/perpetual, mark/index, order flow, funding, OI/leverage, liquidation, options, calendar, position ratios, trade-size, borrow, ADL, insurance fund, contract granularity, L2, cross-asset lead-lag, macro events, cross-venue, pair convergence and price-level/trend-pullback families.
- Tightened source validation so a release fails if the registry contains fewer than 30 fingerprints.
- Added regression tests for alias rejection, option-surface relabel rejection and a distinct-family non-rejection counterexample.
- Bumped application/package metadata and release documentation to `3.56.28`; approved strategy remains `3.56.15` and strict research identity remains `v3.56-strict`.
### Verification
- Python compileall passed for source, entrypoints, tests and the unified checker.
- Targeted research/candidate/release tests: 44 passed.
- Full Python suite: 490 passed, 18 skipped only because `JIAOYI_DATA_DIR` is not configured, 0 failed.
- Dashboard `npm run check` passed: lint, TypeScript, 21 Node tests and production build.
- `scripts/system_check.py source` passed with 23 structured families and 30 failure fingerprints.
- Counterexample checks confirmed downside-beta maps to `FP04_PRICE_PATH_RISK_FACTORS`, same-hour seasonality maps to `FP16_CALENDAR_INTRADAY`, and an unrelated validator-queue example maps to no fingerprint.
- Built the v3.56.28 release ZIP and verified its SHA-256 sidecar after final documentation sync.
### Safety boundary
- This release changes research admission only. Formal signals, A-grade logic, Feishu delivery, leverage guidance, accounts, positions, orders and approved manifests are unchanged.
- No new candidate PnL was opened and no failed family was rescued.
### Current recovery point
- Commit and push the reviewed v3.56.28 research-gate release after final diff and staged-scope checks.
- After release, keep refreshing H22, v357 and H27. New Alpha implementation remains forbidden unless a genuinely new causal field with adequate point-in-time history passes structure, alias and fingerprint gates.

## 2026-06-27 - Task: H22/V357/H27 forward evidence refresh after v3.56.28 release
### What was done
- Confirmed local `master` and `origin/master` both point to commit `7e1fb642` for the pushed v3.56.28 research-gate release before touching forward evidence.
- Refreshed the frozen H22 staggered 3x3 ledger. Closed data advanced through `2026-06-27T04:15:00Z`; one fully prospective refresh is now active from the frozen `2026-06-27T04:00:00Z` entry, but no observation has closed yet.
- H22 remains `RECORD_ONLY_SAMPLE_INCOMPLETE`; protocol, ledger, data-quality and daily-snapshot-chain integrity all pass. No PF, return, drawdown or concentration judgment was produced from the still-open first observation.
- Refreshed the frozen V357 shadow ensemble and acceptance adapter. Coverage remains 21/21 symbols, elapsed closed-data days increased to 5, and fully prospective observations remain 6.
- Refreshed H27 only after both source ledgers. H27 remains `RECORD_ONLY_SAMPLE_INCOMPLETE`: H22 closed observations 0, V357 closed trades 2 under the frozen adapter, common daily rows increased from 1 to 2, and data quality passes.
### Safety boundary
- No strategy, parameter, formal signal, A-grade, Feishu, leverage, account, position, order or approved-manifest logic changed.
- H22/V357 weights were not searched or combined; H27 remains observation-only and cannot promote automatically.
- Application version remains `3.56.28`; this is a documentation continuity update only.
### Current recovery point
- Continue refreshing H22, V357 and H27 in that order. Do not evaluate H22 performance until the first fully prospective observation closes, and do not evaluate H27 diversification until its frozen day/trade sample gates are due.
- New Alpha implementation remains blocked unless a genuinely new causal field with adequate point-in-time history passes structured-family, historical-alias and failure-fingerprint gates before any PnL access.

## 2026-06-27 - Task: tenth-wave literature funnel for independent strategy intake
### What was done
- Continued the strategy search through the frozen funnel instead of generating another indicator grid. Reviewed six primary research directions covering hidden crypto factors, traditional-asset integration, sentiment/rotation/security shocks, blockchain information, portable limit-order-book microstructure, broad crypto characteristics, perpetual pricing bounds and information-theory market states.
- Screened eight candidate mechanisms in `TENTH_WAVE_LITERATURE_INTAKE_20260627.json` before any return or PnL access.
- TW01 latent-factor exposure failed because factor sign, payer and direction were not uniquely identified and factor-number/rotation choices exceeded the freedom budget.
- TW02 equity-technology integration, TW03 sentiment/rotation/security shocks and TW04 blockchain fundamentals failed because they require external point-in-time fields outside the frozen local-OKX boundary; TW03 also had competing continuation/reversal interpretations.
- TW05 portable order-book microstructure had the clearest payer, but required continuous one-second L2 and trade history, used cross-venue Binance evidence and duplicated closed order-flow/L2 fingerprints.
- TW06 expanded OHLCV characteristic screening mapped directly to momentum, reversal, breakout and price-path risk fingerprints. TW07 perpetual theoretical-bound convergence duplicated closed spot/perpetual and funding families. TW08 return entropy lacked a unique direction and duplicated the rejected dispersion family.
- No candidate passed payer, direction, local data and global family de-duplication together. No implementation, return-blind audit, backtest, PnL, validation or locked holdout was opened.
### Evidence and artifacts
- Added `TENTH_WAVE_LITERATURE_INTAKE_20260627.json` and its Chinese explanation `TENTH_WAVE_LITERATURE_INTAKE_20260627_CN.md` under the local-only discovery archive.
- Primary evidence identifiers recorded in the register: `arXiv:2601.07664`, `arXiv:2510.14435`, `arXiv:2602.00776`, `arXiv:2405.15716`, `arXiv:2212.06888` and `arXiv:2310.04907`.
- JSON syntax validation passed.
### Safety boundary
- Application version remains `3.56.28`; no runtime, formal signal, A-grade, Feishu, leverage, account, position, order or approved-manifest logic changed.
- No new external dataset, cross-venue strategy, continuous L2 collection or bulk download was introduced.
### Current recovery point
- Continue the funnel, but search for a genuinely new causal field rather than another OHLCV transformation. A candidate may be implemented only after distinct payer, unique direction, sufficient point-in-time OKX history, acceptable costs and the three de-duplication gates all pass.
- Continue refreshing H22, V357 and H27 independently; their frozen rules and sample gates remain unchanged.

## 2026-06-27 - Task: eleventh-wave OKX positioning and native-OI evidence recheck
### What was done
- Rechecked the most promising existing non-price fields instead of generating another indicator family: official OKX all-account long/short ratio, top-trader account ratio, top-trader position ratio and the existing local native-open-interest archive.
- Confirmed that the positioning-ratio data gate is mature: 18 eligible OKX swaps, 826 exact common daily timestamps from 2024-03-22 through 2026-06-25, no forward fill and no cross-exchange data.
- I055 top-trader average long-versus-short position-size difference and I056 top-trader-versus-all-account disagreement still pass their frozen structural screens, but both fail the economic mechanism gate. Aggregate ratios cannot distinguish informed directional speculation from market making, basis, spot, option or cross-market hedging; the payer, entry price, leverage, holding horizon and hedge legs remain unobserved.
- A targeted primary-evidence search found no grade-B-or-better study that fixes follow-versus-contrarian direction for these exact OKX positioning fields. I055 and I056 therefore remain rejected before PnL; I057 remains a prohibited algebraic recombination of the two.
- Re-ran the existing local native-OI pre-PnL screen. The common native sample remains 16 bases from 2026-04-07 through 2026-06-04. I023 and I025 remain short-sample duplicate warnings; I024 remains unobservable under the frozen rule. No OI candidate matured.
### Evidence and artifacts
- Added `ELEVENTH_WAVE_POSITIONING_EVIDENCE_RECHECK_20260627.json` and `ELEVENTH_WAVE_POSITIONING_EVIDENCE_RECHECK_20260627_CN.md` under the local-only discovery archive.
- Evidence reviewed includes `arXiv:2602.00776`, `arXiv:2601.07664`, `arXiv:2606.00060` and `arXiv:2310.14973`. These support L2/trade microstructure, latent/external factors, cost-aware price forecasting or OI data-quality concerns, but not a unique directional top-trader-ratio Alpha.
- Added a concise Chinese failure note to the Desktop `失败策略` folder and updated its JSON index.
### Safety boundary
- No future return, PnL, validation segment or locked holdout was opened.
- No sign reversal, smoothing, frequency change, symbol exclusion or I055/I056 recombination was attempted.
- Application version remains `3.56.28`; runtime, formal signal, A-grade, Feishu, leverage, account, position, order and approved-manifest logic are unchanged.
### Current recovery point
- Keep positioning ratios as diagnostic controls only. Recheck native OI only after local coverage materially extends; do not repeatedly rerun the same short sample.
- Continue H22, V357 and H27 forward evidence and continue searching only for a new OKX point-in-time field with an observable payer and unique ex-ante direction.

## 2026-06-27 - Task: twelfth-wave official OKX field refresh and OI state correction
### What was done
- Re-ran the frozen Stage4 official-field scanner instead of generating another strategy family. Seven official OKX routes were refreshed and four pagination semantics were probed directly without reading returns or PnL.
- Corrected the OI state distinction. The short local imported hourly OI archive remains uneven and covers roughly 16 bases from 2026-04 to 2026-06, but the official OKX daily OI route now has 18/18 symbols and 908 exact common daily timestamps from 2023-12-31 through 2026-06-26.
- Daily OI therefore no longer fails the history gate. It remains closed because I023-I025 are price-plus-OI continuation, reversal or deleveraging expressions that hit `FP12_OPEN_INTEREST_LEVERAGE_PROXY` and prior momentum, reversal and liquidation families; payer, direction and cost compensation are not independently identified before PnL.
- Margin loan ratio, aggregate taker volume and aggregate contract OI/volume still expose 180 daily rows and 179 calendar days. Direct tests with `end`, `before`, `after` and `begin` did not retrieve older windows for margin or taker routes.
- BTC/ETH option OI, volume and put/call aggregate routes expose 72 daily rows and 71 days, below the frozen 365-day history gate.
- Insurance-fund and liquidation endpoints can move to an older recent page with the `after` parameter, but they remain system-level or short-window event records, duplicate the closed liquidation/insurance-pressure families and violate the no-rare-marginal-event research policy. No bulk download or candidate was opened.
### Evidence and artifacts
- Added `TWELFTH_WAVE_OFFICIAL_FIELD_REFRESH_20260627.json` and `TWELFTH_WAVE_OFFICIAL_FIELD_REFRESH_20260627_CN.md` under the local-only discovery archive.
- Refreshed Stage4 measured `ready_route_count=0`, one history-valid failed family and six data-rejected routes.
- Primary evidence recorded: `arXiv:2310.14973`, `arXiv:2602.00776` and `arXiv:2212.06888`. These support OI measurement caution, continuous-L2 microstructure or perpetual/spot/funding convergence, not an independent daily-OI Alpha.
- Added a concise Chinese note to the Desktop `失败策略` folder and updated its JSON index.
### Safety boundary
- No future return, PnL, validation segment or locked holdout was opened.
- No I023-I025 parameter, sign, horizon, symbol subset or filter was changed after the 908-day history correction.
- No new data collector, bulk download, runtime strategy, formal signal, A-grade, Feishu, leverage, account, position, order or approved-manifest logic was introduced.
- Application version remains `3.56.28`.
### Current recovery point
- Treat positioning ratios and official daily OI as closed-family diagnostics, not waiting candidates. Do not rerun them merely because more dates arrive.
- Recheck the current 179-day and 71-day official routes only if the API itself exposes materially longer history. Continue H22, V357 and H27 forward evidence and search only for a genuinely different OKX contract-level field with an observable payer and unique ex-ante direction.

## 2026-06-27 - Task: thirteenth-wave participant-constraint, inventory-hedging and contract-structure screen
### What was done
- Continued the independent strategy-search line in parallel with H22, V357 and H27. Screened three economically distinct families and nine ideas before any return or PnL access: risk-tier boundary pressure, collateral haircut pressure, loan quota/rate constraints, dealer inventory externalization, funding-aware inventory rebalancing, index-component hedging, contract granularity, platform position-limit saturation and premium-rule pressure.
- Probed current official OKX field semantics. BTC-USDT position tiers expose 99 current risk tiers with IMR/MMR, size ranges and maximum leverage, but no historical schedule or account-position distribution. Current instrument metadata expose tick, lot, minimum size, leverage and partial position-limit fields, but not their point-in-time history.
- The interest-rate/loan-quota and collateral-discount endpoints are current snapshots without historical timestamps or actual account utilization. The collateral route returned roughly 950 currency/tier records, but applying the current schedule to prior years would be look-ahead bias.
- The BTC-USDT index-component route returned a current five-venue basket including Coinbase, Bitget, Binance, OKX and Bybit. Historical component weights are unavailable and the route is explicitly cross-venue, outside the frozen boundary.
- Premium history paginates backward, but its economic identity is still perpetual premium, mark-index and funding convergence; it therefore hits the already closed FP06/FP07/FP11 families rather than creating a new contract-rule Alpha.
- Dealer inventory and inventory-hedging mechanisms are economically valid, but public trades, OHLCV, positioning ratios and current rules do not reveal dealer inventory, customer/dealer identity, hedge legs or the sign of forced externalization. The proposed expressions therefore duplicate closed order-flow, L2, funding, positioning and option-hedging families.
### Evidence and artifacts
- Added `THIRTEENTH_WAVE_CONSTRAINT_INVENTORY_CONTRACT_SCREEN_20260627.json` and `THIRTEENTH_WAVE_CONSTRAINT_INVENTORY_CONTRACT_SCREEN_20260627_CN.md` under the local-only discovery archive.
- Recorded primary evidence from `arXiv:2102.04591`, `arXiv:2106.06974`, `arXiv:2605.06405`, `arXiv:2212.06888`, `arXiv:1009.2329` and `arXiv:2605.29309`.
- Added a concise Chinese failure note to the Desktop `失败策略` folder and updated its JSON index.
### Decision
- All nine ideas have an economically plausible story, but zero have point-in-time observable constrained exposure, zero have a unique ex-ante trade direction, and zero pass the global failure-family de-duplication gate.
- No return-blind implementation, PnL, validation or locked holdout was opened. No T13 idea receives a C or H number.
### Safety boundary
- Current risk tiers, collateral haircuts, loan quotas, index weights and instrument rules were not retroactively applied to historical returns.
- No dealer inventory was inferred from OHLCV or aggregate trades; no cross-venue constituent history, continuous L2 or rare rule-change/liquidation event archive was collected.
- Runtime, formal signal, A-grade, Feishu, leverage, account, position, order and approved-manifest logic remain unchanged. Application version remains `3.56.28`.
### Current recovery point
- Continue H22, V357 and H27 forward evidence independently while keeping a separate mechanism-intake line active.
- Treat position tiers, collateral schedules, loan quotas, dealer-inventory proxies, index components and current contract rules as diagnostics only. Admit a new implementation only when a genuinely new OKX contract-level field has point-in-time history, observable utilization or exposure, a unique payer/direction and passes all three de-duplication gates before PnL.

## 2026-06-27 - Task: fourteenth-wave unregistered local-route and listing-age screen
### What was done
- Refreshed the actual local import boundary across 12 directories instead of proposing another OHLCV transform. No local OKX coin-margined perpetual history, dated-delivery futures history or signed spot-trade archive was found. The only USD-contract archive is the already-audited BTC/ETH option trade history; large long-history liquidity and OI archives are primarily Binance/vendor material and remain excluded.
- Screened seven directions: contract listing age, coin-margined versus USDT-margined collateral feedback, dated-futures roll pressure, signed spot-flow leadership, option-expiry pinning/release, maker-taker fee changes and funding cap/floor saturation.
- Completed one formal return-blind structural audit for listing age. In the frozen 18-symbol universe, OP is present at the 2022-06-01 research boundary and the other 17 contracts were already listed. The age ranking is therefore time-invariant across the full history rather than a recurring causal update.
- Under the predeclared long-old/short-young four-by-four expression, the young side is permanently OP/SOL/NEAR/AVAX and each selected symbol occupies 25% of one side, above the frozen 20% slot-concentration cap. Eight mature symbols share the exact 2019-11-12 listing date, so the old basket also depends on arbitrary tie-breaking. The expression overlaps H15 listing stabilization and persistent size/liquidity/survival selection.
- The remaining six ideas stopped at the data, direction or global de-duplication gate: no local parallel collateral or dated-futures universe; no signed spot flow; no option OI/dealer ownership/gamma sign; no point-in-time OKX fee schedule; and funding saturation collapses to the closed premium, mark-index and funding families.
### Evidence and artifacts
- Added `FOURTEENTH_WAVE_UNREGISTERED_ROUTE_AND_LISTING_AGE_SCREEN_20260627.json` and the matching Chinese report under the local-only discovery archive.
- Recorded primary evidence from `arXiv:1906.03430`, `arXiv:2109.02776`, `arXiv:2212.06888`, `arXiv:2010.08992` and `arXiv:2211.00496`.
- Added a concise Chinese failure note to the Desktop `失败策略` folder and updated its JSON index.
### Decision
- Zero of seven ideas has a unique ex-ante direction and passes concentration, representation invariance and global family de-duplication. No future return, PnL, validation or locked holdout was opened.
- T14-L01 and T14-R02 through T14-R07 receive no C or H number and must not be implemented or backtested.
### Change-control boundary
- No application source, runtime configuration, strategy implementation or version behavior changed. Version remains `3.56.28`.
- Research artifacts and project documentation were updated locally only. No Git commit and no GitHub push were performed, following the rule that documentation-only research updates are not pushed.
### Current recovery point
- Continue H22, V357 and H27 prospective evidence while keeping the intake funnel active.
- Do not rescue listing age by adding APT/ARB/HYPE, changing basket size or choosing a favorable tie-break. Continue only with a genuinely new existing-local or official OKX contract-level point-in-time field that has observable exposure, unique direction and a non-duplicate payer.

## 2026-06-27 - Task: fifteenth-wave historical borrowing-rate and public-flow screen
### What was done
- Compared 371 official OKX API paths with the existing route inventory and screened six overlooked public route groups without opening future returns or PnL.
- Found a new multiyear route: historical all-margin hourly borrowing rates from at least 2022-01 through 2026-06. The daily files are tiny and contain currency, rate and time; no bulk archive was saved.
- A return-blind audit used 54 monthly sample days and 1,164 complete hours for 18 coins plus USDT. About 84.88% of coin-hour values were at the common 0.01 floor; the median hour had three distinct rates and two coins above the floor.
- The top-four boundary was tied in 60.48% of hours and the bottom-four boundary in 100%. Maximum symbol slot shares were 22.55% and 24.87%, above the frozen 20% cap.
- Direction was not unique: high coin borrowing can indicate bearish demand or scarce borrow and covering pressure; high USDT borrowing can indicate leveraged continuation or crowded reversal. The route also overlaps the closed borrowing and funding/carry families.
- Per-contract taker volume duplicated H6-H8 order-flow work. Option expiry, strike and taker/block routes exposed only latest snapshots. Public copy-trading flow had a clear mechanical follower channel, but the leaderboard retained only five recent versions and completed positions only three months, creating historical selection bias.
### Evidence and artifacts
- Added the fifteenth-wave JSON and Chinese report under the local-only discovery archive.
- Added F22 historical borrowing rates and F23 public copy-trading positions to the research data-route inventory with closed current-cycle states.
- Added a concise Desktop failure note and updated its JSON index.
### Decision
- One new multiyear data route was found, but no route passed direction, point-in-time selection, tie, concentration and global de-duplication gates. New C candidates remain zero and no PnL was opened.
### Change-control boundary
- No application code, runtime configuration or version behavior changed. Version remains `3.56.28`.
- No collector or bulk archive was added. No Git commit and no GitHub push were performed.
### Current recovery point
- Continue H22, V357 and H27 prospective evidence while keeping the official-route intake funnel active.
- Do not rescue F22 by reversing direction, dropping floor-rate currencies, combining it with price/funding/OI or selecting favorable ties. Do not backfill current lead traders or start a collector without a separately frozen prospective protocol and approval.

## 2026-06-27 - Task: H22/V357/H27 refresh and sixteenth-wave public-state constraint screen
### What was done
- Refreshed H22, V357 and H27 in the frozen order before new intake work.
- H22 closed data advanced through `2026-06-27T06:00:00Z`; the first fully prospective refresh remains active, closed observations remain 0, and protocol, ledger, data-quality and snapshot-chain integrity all pass.
- V357 remains at 21/21 symbol coverage, 5 closed-data days and 6 fully prospective observations.
- H27 remains `RECORD_ONLY_SAMPLE_INCOMPLETE`: H22 closed observations 0, V357 adapter closed trades 2, common daily rows 2, and data quality passing. No diversification judgment was forced.
- Compared the current official OKX API document with locally registered research routes. Roughly 50 public-like paths were not explicitly named in prior research text; ordinary ticker, candle, trade, order-book, option and event routes were rejected immediately by existing family fingerprints.
- Performed new semantic or live-response probes on seven route groups: copy-trader performance/state extensions, dynamic price limits, futures settlement state, call-auction imbalance, option-family recent trades, option/event tick bands and the economic calendar.
- Confirmed that a current top lead trader can expose 366 daily PnL rows from `2025-06-26` through `2026-06-26`. This does not solve the historical-selection problem: leaderboard membership still has only five recent point-in-time versions, current AUM/follower metrics are not a historical panel, and symbol-level completed positions remain roughly three months.
- Dynamic price limits expose only a current snapshot and have continuation-versus-reversal ambiguity. Futures settlement history is documented as three months and belongs to dated futures/options rather than the 21 perpetual-swap universe. Call-auction data are current listing/reopen state, option-family trades are only the latest 100, tick bands are current OPTION/EVENTS rules, and the economic calendar is an authenticated external event feed with three-month standard history.
### Evidence and artifacts
- Added `SIXTEENTH_WAVE_PUBLIC_STATE_AND_CONSTRAINT_ROUTE_SCREEN_20260627.json` and the matching Chinese report under the local-only discovery archive.
- Updated `docs/PROJECT_OVERVIEW_CN.md` so the forward status, completed funnel work and recovery entry remain aligned with this progress log.
### Decision
- Zero route groups passed point-in-time selection, unique direction, adequate history and global de-duplication together. No new C or H candidate was assigned; future returns, PnL, validation and the sealed holdout remained closed.
### Change-control boundary
- No strategy, parameter, runtime configuration, formal signal, A-grade, Feishu, leverage, account, position, order or approved-manifest behavior changed.
- Application version remains `3.56.28`. This is forward evidence plus research documentation only; no Git commit or GitHub push is required.
### Current recovery point
- Continue H22, V357 and H27 in the frozen order. H22 performance must not be evaluated until its first prospective observation closes, and H27 remains observation-only until its frozen day/trade gates are due.
- Keep the mechanism funnel active, but do not backfill current copy-trading winners, reconstruct historical price limits from current rules, or reopen short settlement, auction, option-trade or macro-event windows. Admit implementation only for a genuinely new OKX contract-level point-in-time field with observable exposure, a unique payer/direction and full global de-duplication before PnL.

## 2026-06-27 - Task: seventeenth-wave public lending and yield-history screen
### What was done
- Refreshed H22, V357 and H27. No new closed sample arrived.
- Audited the public borrowing and lending-rate history without opening future returns.
- Of 54 monthly samples from 2022-01 through 2026-06, 48 fully covered the fixed 18 coins plus USDT. Historical and current amount fields were empty.
- The median complete month had 3 distinct coin lending rates. Top-four and bottom-four boundary ties occurred in 72.92% and 97.92% of samples; forced tie-break concentration reached 22.92% and 23.96%.
- The borrow-rate leg closely overlaps F22, while the added saver-yield field still exposes no utilization, borrower size or unique direction.
- ETH/SOL APY histories were limited to about one year and two assets. Other reviewed routes were outside the public contract-level research boundary or were sparse event records.
- Official history modules are now exhausted under the current boundary: trades duplicate H6-H8, one-minute candles duplicate OHLCV, funding duplicates closed funding families, order-book modules are excluded L2, and borrowing rates failed F22/F34.
### Evidence and decision
- Added `SEVENTEENTH_WAVE_PUBLIC_LENDING_AND_YIELD_HISTORY_SCREEN_20260627.json` and its Chinese report; updated the project overview.
- F34 failed before PnL on missing utilization, sparse tied rates, concentration, direction ambiguity and family duplication. No new C/H candidate was assigned.
### Change-control boundary
- Version remains `3.56.28`. No application code, runtime behavior or production signal changed, so no commit or remote push is required.
### Current recovery point
- Continue H22, V357 and H27 in the frozen order. Treat F22/F34 as closed diagnostics and only admit a genuinely new contract-level point-in-time field that directly exposes constrained quantity or mechanical flow.

## 2026-06-27 - Task: eighteenth-wave missed-positive-structure recheck
### What was done
- Refreshed H22, V357 and H27 before the recheck. H22 closed data advanced through `2026-06-27T06:45:00Z` but the first prospective position remains active with zero closed observations. V357 remains at 5 closed-data days and 6 fully prospective observations. H27 remains record-only with 2 common daily rows and passing data quality.
- Reconfirmed that public liquidation, insurance-fund and ADL fields were already closed in the twelfth wave, and block/spread trades were already closed in the fifteenth wave. No renamed route was reopened.
- Re-ran the read-only feasibility inventory to locate any structure that had passed pre-PnL gates but had not received a frozen outcome review.
- P6 slow 28-day time-series trend was rediscovered as structurally feasible, but its existing frozen calibration had already failed: 28-day base PF was about 1.238, while 21-day and 42-day neighborhood means were negative and the candidate underperformed the common-market trend control. Its existing decision remains `FAIL_STOP_NO_RESCUE`.
- The momentum quality/synchronization overlay had passed the return-blind structure gate and had a frozen protocol from 2026-06-23. Its frozen evaluation was therefore executed without changing any rule.
- Across 1002 periods, the baseline produced PF 1.1394, total return +69.16% and maximum drawdown -28.91%. The overlay produced PF 0.9763, total return -9.81% and maximum drawdown -36.19%. Under stress costs, overlay PF was 0.8653 and total return -33.94%.
- The overlay improved q05/CVaR and improved segment CVaR in all three segments, but failed the fixed drawdown-reduction, return-retention and stress-PF gates. It was rejected and may not be rescued by changing risk scales, windows, anchors or segment boundaries.
- Rechecked tail quality, idiosyncratic skewness, beta, volume-price efficiency, idiosyncratic shock reversal, pair mean reversion, round-number breakout, support/resistance and liquidity-filtered momentum. All were already rejected before PnL for duplication, concentration, unstable neighborhoods, evidence-sign conflict, excessive turnover or lack of persistent feasible pairs.
### Evidence and artifacts
- Added `MOMENTUM_QUALITY_SYNCHRONIZATION_OVERLAY_RESULT.json` and its Chinese result report.
- Added `EIGHTEENTH_WAVE_MISSED_POSITIVE_STRUCTURE_RECHECK_20260627.json` and the matching Chinese report.
- Updated `docs/PROJECT_OVERVIEW_CN.md` so the forward status, P6 closure, overlay failure and recovery rules remain aligned.
### Decision
- No missed local candidate or overlay survives existing frozen evidence. No new C/H candidate and no new forward shadow were admitted.
- P6 remains closed, and the quality/synchronization overlay is `REJECT_AND_ARCHIVE_NO_RESCUE`.
### Change-control boundary
- No application strategy, runtime configuration, formal signal, A-grade, Feishu, leverage, account, position, order or approved-manifest behavior changed.
- Application version remains `3.56.28`. Research results and documentation remain local; no Git commit or GitHub push is required.
### Current recovery point
- Continue H22, V357 and H27 in the frozen order. Do not reopen P6, the rejected overlay or a failed feasibility script under a new name.
- The next intake must be a genuinely new mechanism, not an omitted local script, and must pass payer/direction, point-in-time causality, cost scale and all global de-duplication gates before any outcome is opened.

## 2026-06-27 - Task: nineteenth-wave literature and pure-volume mechanism screen
### What was done
- Refreshed H22, V357 and H27 in the frozen order. H22 closed data advanced through `2026-06-27T07:00:00Z`; the first prospective position remains active with zero closed observations and all integrity checks passing. V357 remains at 21/21 symbols, 5 closed-data days and 6 fully prospective observations. H27 remains record-only with 2 common daily rows and passing data quality.
- Rechecked recent primary crypto-asset-pricing and market-structure research against every existing local failure family before implementation.
- The strongest replicated cross-sectional factors in the updated empirical asset-pricing evidence map entirely to already tested families: two-week momentum to H22, industry momentum to H19, beta/downside beta to the beta and H28 screens, idiosyncratic skewness to its pre-PnL rejection, and expected shortfall to the tail-quality/low-volatility rejection.
- Newer jump-risk, blockchain-information, hidden-factor, sentiment, security-shock and on-chain-flow studies require option-implied distributions, chain data, external indices, traditional-market variables or rare events. These inputs violate the frozen local OKX-only boundary or duplicate the closed tail, lottery, downside-beta, kurtosis, co-jump and dispersion families.
- Screened a pure volume-variability mechanism using only the semantics of local closed OHLCV. It failed intake before implementation because high abnormal volume can mean information continuation or speculative-attention reversal, stable volume also has opposite quality-versus-low-required-return interpretations, and point-in-time size/free-float normalization is unavailable.
- Confirmed that adding price direction to volume variability would duplicate the already closed volume-burst attention, zero-return liquidity, volume-volatility elasticity, volume-share migration and volume-price efficiency families.
- Reconfirmed the volatility-compression stop-cascade route as the existing P01/P02 breakout family. P01 locked PF was about 0.842 and P02 calibration/locked PF about 0.898/0.619; both had 0 of 8 neighborhoods positive in validation and lock. H19 industry momentum also remains a closed failure with base PF about 0.951 and drawdown about -37.92%.
### Evidence and artifacts
- Added `NINETEENTH_WAVE_LITERATURE_AND_VOLUME_MECHANISM_SCREEN_20260627.json` and its Chinese report under the local-only discovery archive.
- Updated `docs/PROJECT_OVERVIEW_CN.md` so the latest forward timestamp, literature mappings, pure-volume rejection and prohibited rescue rules remain aligned.
### Decision
- No literature route or pure-volume route passed local point-in-time data, unique payer, unique direction and global de-duplication together.
- New C candidates, H candidates and forward shadows remain zero. Future returns, PnL and the sealed holdout remained closed.
### Change-control boundary
- No application strategy, runtime configuration, formal signal, A-grade, Feishu, leverage, account, position, order or approved-manifest behavior changed.
- Application version remains `3.56.28`. Research artifacts remain local; no Git commit or GitHub push is required.
### Current recovery point
- Continue H22, V357 and H27 in the frozen order. Do not reopen H19, P01/P02, pure volume variability, external sentiment/on-chain/event factors or option-implied jump premia under a local proxy name.
- Admit only a genuinely new OKX contract-level point-in-time field exposing constrained quantity or mechanical flow, or a future primary mechanism with one unique direction that is fully measurable from existing local data.

## 2026-06-27 - Task: twentieth-wave volatility-state and local-data asset screen
### What was done
- Refreshed H22, V357 and H27 in the frozen order. H22 closed data advanced through `2026-06-27T08:30:00Z`; the first prospective position remains active with zero closed observations and all protocol, ledger, data-quality and snapshot-chain checks passing. V357 remains at 21/21 symbols, 5 closed-data days and 6 fully prospective observations. H27 closed data advanced through `2026-06-27T08:00:00Z` and remains record-only with 2 common daily rows.
- Screened realized volatility-of-volatility, downside/upside correlation asymmetry, hidden-state/return-entropy continuation and dynamic crypto-network mechanisms before implementation.
- Realized volatility-of-volatility had no unique long/short return direction and would be a risk-sizing overlay or a duplicate of volatility, tail-loss, kurtosis and dispersion-risk families. The most direct estimators also require option data unavailable as a multi-year 21-perpetual panel.
- Downside/upside correlation asymmetry was mapped to H28 conditional-beta asymmetry and H29 dispersion-shock exposure. H28 had failed representation and duplication gates; H29 had failed bad-state semantics, representation and duplication gates. Neither may be reopened under a correlation label.
- Reconfirmed that I054 entropy continuation failed its mechanism-evidence gate, MC07 volatility transition is a non-Alpha diagnostic, TW08 entropy state failed unique-direction and dispersion gates, and SW08 dynamic network centrality exceeded the freedom budget and duplicated closed lead-lag/liquidity-network families.
- Audited the local data inventory rather than creating another OHLCV transformation. The OKX causal-metadata database is healthy: 178,835 mark-price rows, 179,307 index rows, 6,190 funding rows, 756 open-interest rows and 84 instrument snapshots, with the latest collector cycle passing.
- Current native OKX open interest has only 36 hourly observations per symbol from `2026-06-24T13:00:00Z` through `2026-06-27T08:00:00Z`. Instrument rules have only four daily snapshots per symbol. Neither is ready for Alpha research.
- Confirmed that the legacy 96,753-row derivative-momentum event table is not a new causal panel: it contains future return, MFE and MAE columns, is event-selected, and its open-interest/taker enrichments came from Binance UM metrics. Taker-total-volume coverage was only about 2.24% and L2 coverage about 0.87%. It is prohibited for new OKX-only Alpha intake.
- Found one genuine constrained-quantity near miss in the native daily instrument snapshots and registered it as `F39_PUBLIC_INSTRUMENT_CONSTRAINT_CHANGE_SNAPSHOTS`. Fields include platform OI limits, user same-direction position limits, position-ratio limits, maximum market-order size and announced future rule changes.
- In the four-day sample, ATOM platform OI limit fell from 15.1 million to 14.7 million, DOT user position limit fell from 2.6 million to 1.1 million, and HYPE tick size fell from 0.01 to 0.001. Six maximum-market-order-size changes were also announced for 2026-06-29.
- F39 failed the Alpha direction and history gates. Available limits constrain long and short opening capacity symmetrically; all 84 `longPosRemainingQuota` and `shortPosRemainingQuota` fields were empty, and four days cannot support segmented, cost or stability testing. It remains a prospective diagnostic route only.
### Evidence and artifacts
- Added `TWENTIETH_WAVE_VOLATILITY_STATE_AND_LOCAL_DATA_ASSET_SCREEN_20260627.json` and the matching Chinese report.
- Updated `docs/PROJECT_OVERVIEW_CN.md` with the latest forward timestamp, local-data audit, F39 status and prohibited rescue rules.
### Decision
- No volatility-state or local-data route passed unique direction, adequate independent history and global de-duplication together.
- F39 is `DIRECT_CONSTRAINED_QUANTITY_NEAR_MISS_NO_ALPHA`: no C/H number, no PnL, no forward shadow and no production effect.
### Change-control boundary
- The existing OKX metadata collector continues unchanged; no new collector, application strategy, runtime configuration, formal signal, A-grade, Feishu, leverage, account, position, order or approved-manifest behavior changed.
- Application version remains `3.56.28`. Research artifacts remain local; no Git commit or GitHub push is required.
### Current recovery point
- Continue H22, V357 and H27 in the frozen order. Allow native OKX OI and instrument snapshots to accumulate naturally, but do not evaluate F39 as Alpha until adequate independent history and an asymmetric long/short quota or actual forced-flow quantity exist.
- Do not infer direction from symmetric limits, treat empty quota fields as zero, combine F39 with price/funding/OI to manufacture a sign, or reuse Binance/event-selected tables under an OKX-only label.

## 2026-06-27 - Task: twenty-first-wave forced-flow and constraint endpoint exhaustion
### What was done
- Removed F39 from the active validation critical path. It remains natural-collection diagnostics only because four snapshot days and symmetric long/short constraints cannot be repaired by waiting a few more hours or by combining price, funding or OI to manufacture a sign.
- Preserved the latest frozen forward evidence baseline without changing any rule: H22 remains at one active and zero closed observations through `2026-06-27T08:30:00Z`; V357 remains at 21/21 symbols, 5 closed-data days and 6 fully prospective observations; H27 remains record-only with 2 V357 adapter closed trades and 2 common daily rows through `2026-06-27T08:00:00Z`.
- Screened four official OKX public endpoint families before implementation: filled liquidation orders, insurance-fund/ADL state, position tiers and margin boundaries, and funding-rate cap/floor plus settlement state.
- Filled liquidation orders directly expose `posSide`, buy/sell side, quantity, bankruptcy price and time, but they map to H6-H8, FP13 liquidation flow and FP22 ADL pressure. The local fixed-universe multi-year liquidation panel is absent, and post-liquidation continuation versus exhaustion remains non-unique.
- Insurance-fund updates expose balance, amount, update type, ADL type and decrement rate, but do not identify whether long or short bankrupt inventory caused the update. The route maps to FP22/FP23 and has no unique future direction.
- Position tiers expose real constrained quantities and IMR/MMR boundaries, but apply symmetrically to long and short positions, provide current-state rules rather than a multi-year point-in-time panel, and duplicate the risk-tier/F39/open-interest-leverage family.
- Funding-rate cap/floor and settlement-state fields identify the paying side and payment bound, but do not select continuation versus unwind and map entirely to the already closed FP11, I017, I018, H21 and H30 funding family.
### Evidence and artifacts
- Added `TWENTY_FIRST_WAVE_FORCED_FLOW_AND_CONSTRAINT_ENDPOINT_EXHAUSTION_20260627.json` and the matching Chinese report under the local-only discovery archive.
- Updated `docs/PROJECT_OVERVIEW_CN.md` so F39 is explicitly off the active critical path and all four official endpoint routes are protected against relabeling and reopening.
### Decision
- New C candidates: 0. New H candidates: 0. New forward shadows: 0. Future returns, PnL and the sealed holdout remained closed.
- The funnel continues immediately; F39 no longer blocks candidate intake.
### Change-control boundary
- No application strategy, runtime configuration, formal signal, A-grade, Feishu, leverage, account, position, order or approved-manifest behavior changed.
- Application version remains `3.56.28`. This was research and documentation only, so no Git commit or GitHub push is required.
### Current recovery point
- Continue H22, V357 and H27 in the frozen order when new closed data is available.
- Keep F39 on natural collection only. Admit the next mechanism only if it has independent point-in-time history, an observable payer or forced quantity, and one ex-ante direction before any outcome is opened.

## 2026-06-27 - Task: twenty-second-wave recent primary research and local-field exhaustion
### What was done
- Refreshed H22, V357 and H27 in the frozen order using the existing local dependency environment. H22 closed data advanced through `2026-06-27T09:30:00Z`, with one active and zero closed fully prospective observations; all protocol, ledger, data-quality and snapshot-chain checks pass. V357 remains at 21/21 symbols, 5 closed-data days and 6 fully prospective observations. H27 remains record-only through `2026-06-27T08:00:00Z`, with H22 closed observations 0, V357 adapter closed trades 2 and 2 common daily rows.
- Rechecked recent primary crypto asset-pricing research. The updated 63-characteristic study's statistically significant long-short spreads remain two-week momentum, one- and two-month industry momentum, beta, idiosyncratic skewness and 5 percent expected shortfall. These map to H22, H19, MC02/H28, the existing idiosyncratic-skewness rejection and the tail-quality/FP04 family.
- Screened distance from all-time highs or lows. It was not among the study's statistically significant spread list, has no directly observable payer, has continuation-versus-anchor-reversal ambiguity, and duplicates FP01, FP02 and FP30. It was rejected before implementation.
- Screened recent explainable crypto microstructure evidence. Its stable structures require one-second Binance Futures order books and trades, and map to already closed FP08 order-flow continuation, FP09 absorption and FP25 L2 queue-imbalance families. It failed exchange, data, de-duplication and freedom-budget gates.
- Screened recent hidden-factor crypto pricing evidence. The proposed equity-industry, profitability, Fear and Greed, Altcoin Season and hacked-value inputs require external traditional-market, sentiment, rotation-index or security-event data; they do not expose a new OKX contract-level payer or forced quantity.
- Exhaustively inspected the local causal SQLite schema and all 84 raw instrument snapshot JSON objects rather than relying on report summaries. The database now contains 178,835 mark rows, 179,307 index rows, 6,190 funding rows, 777 hourly OI rows and 84 instrument snapshots.
- All 6,190 funding records use `formula_type=withRate` and `method=current_period`, so there is no formula or settlement-method regime variation. Hourly OI has only 37 points per symbol and remains both history-incomplete and mapped to FP12.
- The only instrument fields with observed time variation were `maxPlatOILmt`, `posLmtAmt`, `tickSz` and `upcChg`, all already mapped to F39 symmetric constraints or FP24 instrument granularity. `longPosRemainingQuota`, `shortPosRemainingQuota` and `maxPlatOICoinLmt` remained empty in every snapshot. No omitted side-specific directional field was found.
### Evidence and artifacts
- Added `TWENTY_SECOND_WAVE_RECENT_PRIMARY_RESEARCH_AND_LOCAL_FIELD_EXHAUSTION_20260627.json` and the matching Chinese report under the local-only discovery archive.
- Updated `docs/PROJECT_OVERVIEW_CN.md` with the new forward timestamps, literature mapping, raw-field exhaustion and prohibited relabeling rules.
### Decision
- New C candidates: 0. New H candidates: 0. New forward shadows: 0. Future returns, PnL and the sealed holdout remained closed.
- Recent primary evidence reinforces existing families, and the local causal database contains no unregistered time-varying asymmetric field.
### Change-control boundary
- No application strategy, runtime configuration, formal signal, A-grade, Feishu, leverage, account, position, order or approved-manifest behavior changed.
- Application version remains `3.56.28`. Research and documentation changes remain local; no Git commit or GitHub push is required.
### Current recovery point
- Continue H22, V357 and H27 only when new closed data is available; sample-incomplete tracks remain record-only.
- Continue the mechanism funnel, but do not reopen all-time-high/low distance, funding formula labels, external hidden factors, one-second L2/order-flow families or stable contract metadata under new names.
- Admit implementation only after a genuinely new primary mechanism or OKX contract-level point-in-time field passes observable-payer/forced-quantity, unique-direction, independent-history, cost-scale and global de-duplication gates.

## 2026-06-27 - Task: twenty-third-wave OKX changelog and price-limit boundary screen
### What was done
- Reviewed the current OKX API change log through the 2026-06-23 update and screened every new or upcoming route that could plausibly expose public constrained quantity or mechanical contract flow.
- Found one genuinely new contract-level field route: the upcoming `initPxLmtPct`, `floatPxLmtPct` and `maxPxLmtPct` instrument parameters scheduled for production on 2026-06-30. Registered it as `F40_UPCOMING_PRICE_LIMIT_XYZ_PARAMETERS`, a direct order-rejection boundary near miss rather than a C/H candidate.
- Confirmed the mechanical semantics from the official price-limit rules: buy orders above the current upper limit and sell orders below the current lower limit are rejected; perpetual limits use the index, X/Y/Z bands and recent average premium.
- Probed the production instruments endpoint for all 21 fixed-universe swaps. None currently returned any XYZ field, including separate BTC and HYPE checks. The current public price-limit endpoint did return buy and sell limits.
- Ran a return-blind, near-synchronous 21-symbol cross-sectional probe using current last, mark, index and buy/sell limit values. Median total band width was about 2.106 percent of index; the nearest upper and lower rooms were about 0.499 and 0.504 percent. Band-midpoint premium versus last-index premium correlation was about 0.915, and room asymmetry versus mark-index premium correlation was about 0.584.
- Treated the current correlations as diagnostic rather than performance evidence. They indicate that price-limit band placement is largely an expression of the already observed perpetual-index premium, so the route is not structurally independent from FP07.
- Exact historical reconstruction failed before implementation: XYZ point-in-time history does not exist, production fields were not yet live, local 200 ms best-bid/ask history is absent, the full rule uses additional undisclosed inputs, and OKX may change parameters or formulas without separate announcements.
- The future return direction also failed. Proximity to an upper or lower rejection boundary can mean constrained continuation, flow exhaustion or reversal; selecting a branch after outcomes would be overfitting.
- Screened the new ELP consolidated order book. Although it separates total visible and non-ELP quantity, it requires 100-200 ms L2 history, violates the no-continuous-L2 boundary, has no unique future aggressive-flow direction and duplicates FP25/order-book families.
- Rejected OKUSD limits, Signal Clone and cool-off rejection as authenticated, account-specific or non-contract-level routes. Confirmed that the 2026-06-09 security-fund update makes prior ADL/insurance fields empty or deprecated, further weakening FP22/FP23 rather than unlocking them.
### Evidence and artifacts
- Added `TWENTY_THIRD_WAVE_OKX_CHANGELOG_AND_PRICE_LIMIT_BOUNDARY_SCREEN_20260627.json` and the matching Chinese report under the local-only discovery archive.
- Updated `docs/PROJECT_OVERVIEW_CN.md` with F40, the 21-symbol structural probe, exact-reconstruction failure, ELP rejection and natural-collection rule.
### Decision
- New field near misses: 1 (`F40`). New C candidates: 0. New H candidates: 0. New forward shadows: 0. Future returns, PnL and the sealed holdout remained closed.
- F40 is `DIRECT_ORDER_REJECTION_BOUNDARY_NEAR_MISS_DUPLICATE_NO_HISTORY` and is off the active critical path. The existing raw instrument JSON collector can capture the fields after go-live without any new collector or waiting step.
### Change-control boundary
- No application strategy, runtime configuration, formal signal, A-grade, Feishu, leverage, account, position, order or approved-manifest behavior changed.
- Application version remains `3.56.28`. Research and documentation changes remain local; no Git commit or GitHub push is required.
### Current recovery point
- Continue H22, V357 and H27 only when new closed data is available.
- Do not wait for F40 or add a price-limit collector. Let the existing instrument raw-JSON snapshots capture XYZ after production launch, but do not promote it without independent point-in-time history, one ex-ante direction and proven independence from FP07.
- Do not reopen ELP/L2, current price-limit proximity, account-level stablecoin quotas, Signal Clone, cool-off states or deprecated ADL fields under new names.

## 2026-06-27 - Task: twenty-fourth-wave pre-market rebase and OI-capacity screen
### What was done
- Refreshed H22, V357 and H27 in the frozen order. H22 closed data advanced through `2026-06-27T10:00:00Z`, with one active and zero closed observations and all integrity checks passing. V357 remains at 21/21 symbols, 5 closed-data days and 6 fully prospective observations. H27 remains record-only with H22 closed observations 0, V357 adapter closed trades 2 and 2 common daily rows.
- Screened OKX pre-market rebase, pre-open auction cancellation, platform OI remaining capacity and X-Perps before any outcome access.
- Confirmed official semantics: `state=rebase` means a SWAP cannot trade during rebasing; `ruleType=pre_market` and `rebase_contract` identify pre-market contracts; pre-open ending can batch-cancel buy orders above index or sell orders below index.
- Probed all 401 current public SWAP instruments. All 401 were `live`; 399 were `normal` and only 2 were `pre_market`. The two pre-market contracts were `OPENAI-USDT-SWAP` and `ANTHROPIC-USDT-SWAP`, both stock-category instruments outside the fixed 21 crypto-perpetual universe. No active `rebase` state or `rebase_contract` sample existed.
- Registered `F41_PREMARKET_REBASE_AND_PREOPEN_ORDER_CANCELLATION` as a direct forced-cancellation near miss, then rejected it before PnL. Rebase is non-tradable, current samples are outside the fixed universe, the event is listing/transition-specific, historical auction books and cancellation quantities are absent, and post-open continuation versus auction-overheat reversal is not unique. It also overlaps H15, F40 and L2/order-book families.
- Audited platform OI remaining capacity using current open interest divided by `maxPlatOILmt`. Only 1 of 21 fixed-universe symbols had a populated USD ceiling; no symbol had a coin-denominated ceiling or nonempty long/short remaining quota.
- The only limited symbol was `ATOM-USDT-SWAP`: platform ceiling USD 14,700,000, current OI USD 3,629,440.041, utilization about 24.69 percent and remaining capacity about 75.31 percent. This is far from binding and the ceiling rejects both long and short new openings symmetrically.
- Registered `F42_PLATFORM_OI_REMAINING_CAPACITY`, then rejected it as a sparse, symmetric, not-binding, short-history duplicate of F39 and FP12. Combining it with price, funding or momentum to choose a sign is prohibited.
- Screened X-Perps. The official change log classifies them as perpetual-style expiry `FUTURES` with funding. They are outside the fixed SWAP universe, lack local multi-year history and duplicate FP06 term-structure and FP11 funding families.
### Evidence and artifacts
- Added `TWENTY_FOURTH_WAVE_PREMARKET_REBASE_AND_OI_CAPACITY_SCREEN_20260627.json` and the matching Chinese report under the local-only discovery archive.
- Updated `docs/PROJECT_OVERVIEW_CN.md` with the latest forward timestamp, F41/F42 results, current 401-instrument probe, ATOM utilization and prohibited rescue rules.
### Decision
- New near-miss routes: 2 (`F41`, `F42`). New C candidates: 0. New H candidates: 0. New forward shadows: 0. Future returns, PnL and the sealed holdout remained closed.
- Neither pre-market forced cancellation nor platform OI remaining capacity provides a recurring, tradable, directional fixed-universe Alpha.
### Change-control boundary
- No application strategy, runtime configuration, formal signal, A-grade, Feishu, leverage, account, position, order or approved-manifest behavior changed.
- Application version remains `3.56.28`. Research and documentation changes remain local; no Git commit or GitHub push is required.
### Current recovery point
- Continue H22, V357 and H27 only when new closed data is available.
- Do not wait for F41/F42, collect pre-market L2 or add X-Perps. F39-F42 remain diagnostics off the active critical path.
- Admit only a recurring fixed-21 point-in-time mechanism with a public side-specific forced quantity or payer and one ex-ante return direction independent of listing, L2, OI, funding and mark-index families.

## 2026-06-27 - Task: twenty-fifth-wave sequential breakthrough risk-overlay validation
### What was done
- Stopped scanning field names and re-audited whether an incomplete low-freedom H22 improvement route already existed. Confirmed that downside-risk weighting, funding-carry tilt, liquidity admission, membership-change rebalance, rank-conviction weighting and sector balancing had all already been evaluated and rejected or archived.
- Reconfirmed that H14 spot-to-perpetual price discovery was already rejected before PnL because its innovation is algebraically a spot-perpetual basis change, its typical magnitude is below cost and hourly bars cannot establish within-hour spot-first ordering.
- Reconfirmed that the old momentum-quality/synchronization overlay had already failed frozen historical evaluation by turning positive base return negative, worsening maximum drawdown and producing stress PF below one.
- Froze `RO01_H22_PANIC_REBOUND_GROSS_EXPOSURE_GUARD_V1` before any outcome access. RO01 left H22 membership, direction, 4-in/6-out hysteresis, staggered cohorts and relative symbol weights unchanged; it scaled gross exposure from 1.0 to 0.5 only when the frozen 18-symbol market had a negative completed seven-day return and a completed one-day rebound above the shifted prior-20-day daily volatility.
- Ran a return-blind structural audit on 1,020 ready daily entries. RO01 triggered 38 times, or 3.73 percent; the alternate median-return representation triggered 44 times; event Jaccard was 82.22 percent; segment counts were 9, 21 and 8. Membership and direction invariance passed.
- RO01 failed the frozen turnover gate before PnL. Mean target turnover rose from 0.163644 for the unchanged H22 parent to 0.189788, a 15.98 percent increase versus the locked 10 percent maximum. Future returns and PnL remained closed. RO01 was archived with no rescue.
- After RO01 termination, froze the predetermined fallback `RO02_H22_LOWER_TAIL_DEPENDENCE_GUARD_V1`. RO02 scaled gross exposure to 0.5 when at least 6 of 18 symbols had a completed one-day return below their own shifted prior-60-day 10th percentile. Its alternate representation used the shifted prior-60-day median minus 1.5 median absolute deviations.
- RO02 also had 1,020 ready observations. It triggered 127 times, or 12.45 percent; the alternate expression triggered 195 times; event Jaccard was 65.13 percent; segment counts were 34, 51 and 41. Jaccard versus RO01 was 0 and versus the old quality-overlay reduced-risk days was 12.44 percent, so uniqueness and de-duplication passed.
- RO02 failed the same frozen turnover gate more strongly. Mean target turnover rose to 0.238889, a 45.98 percent increase versus the H22 parent. Future returns and PnL remained closed. RO02 was archived with no rescue.
### Evidence and artifacts
- Added `ro01_h22_panic_rebound_guard_v1/PROTOCOL_LOCKED_BEFORE_PNL.json`, its return-blind probe, `PRE_PNL_RESULT.json` and Chinese report under the local-only discovery archive.
- Added the corresponding frozen protocol, probe, result and Chinese report for `ro02_h22_lower_tail_dependence_guard_v1`.
- Added `TWENTY_FIFTH_WAVE_BREAKTHROUGH_RISK_OVERLAY_SEQUENCE_20260627.json` and its Chinese report.
- Archived RO01 and RO02 failure summaries and Chinese explanations under the desktop `失败策略` folder.
- Updated `docs/PROJECT_OVERVIEW_CN.md` with both results and the new prohibited-route boundary.
### Decision
- RO01 and RO02 were both rejected before PnL. New independent Alpha candidates: 0. New risk-overlay survivors: 0. New forward shadows: 0. Future returns, PnL and the sealed holdout remained closed.
- Daily binary H22 gross-exposure switching is now a closed route. It mechanically trades the gross-exposure boundary in addition to H22 membership changes and caused 15.98 percent and 45.98 percent target-turnover increases before any hoped-for drawdown benefit was examined.
- Do not rescue either route by changing windows, thresholds or the 0.5 scale, or by adding persistence, cooldown, minimum duration or hysteresis after seeing the turnover failures.
### Change-control boundary
- No application strategy, runtime configuration, formal signal, A-grade, Feishu, leverage, account, position, order or approved-manifest behavior changed. The original H22 forward ledger remains unchanged.
- Application version remains `3.56.28`. The new scripts and evidence are isolated research-archive artifacts outside the application repository, so no version bump, Git commit or GitHub push is required.
### Current recovery point
- Continue H22, V357 and H27 only when new closed data is available.
- Return to the independent-mechanism funnel. Do not test another daily binary H22 gross-exposure overlay on the same history under a new market-state name.
- A future risk implementation may only be reconsidered if its cost neutrality is demonstrated before PnL and it is not designed by modifying RO01 or RO02 after failure.

## 2026-06-27 - Task: twenty-sixth-wave cadence-aligned volatility-budget validation
### What was done
- Refreshed the frozen forward tracks before new research. H22 closed data advanced through `2026-06-27T11:15:00Z`, with one fully prospective rebalance, one active observation and zero closed observations; protocol, ledger, data-quality and snapshot-chain checks all pass. V357 remains at 21/21 symbols, 5 closed-data days and 6 fully prospective observations. H27 was refreshed at `2026-06-27T11:35:29Z` and remains record-only with H22 closed observations 0, V357 adapter closed trades 2 and 2 common daily rows.
- Tested an initial return-blind implementation that scaled the three H22 staggered cohorts independently. It reduced target turnover by 6.79 percent and had 90.34 percent primary/alternate event Jaccard, but failed before PnL because unequal cohort scaling changed the aggregate sign when cohort targets partially cancelled. This version was stopped without opening outcomes.
- Froze `RO03_H22_CADENCE_ALIGNED_VOLATILITY_BUDGET_V1` before outcome access. RO03 leaves H22 membership, direction, 4-in/6-out hysteresis, staggered construction and relative symbol weights unchanged. It uses a 20-day completed market volatility estimate, a shifted prior-252-estimate 75th-percentile threshold with minimum 126 estimates, and updates one uniform 0.5/1.0 scale only on the frozen three-day base cadence.
- RO03 passed every return-blind structural gate on 952 ready observations: 264 high-volatility event days, 88 high-volatility refresh events, 27.73 percent event-day fraction, 87.37 percent primary/alternate Jaccard and segment event-day counts of 105, 102 and 57. All 28 scale changes occurred on the frozen cadence. Mean target turnover fell from 0.166579 to 0.156162, a 6.25 percent reduction, while aggregate sign and relative-weight invariance passed.
- Opened the frozen historical outcomes once after the protocol and pre-PnL hashes were locked. Parent H22 had base PF 1.1536, total return 67.46 percent and maximum drawdown 19.93 percent. RO03 had base PF 1.1688, total return 63.76 percent and maximum drawdown 20.55 percent; stress PF 1.0855 and delayed-15-minute PF 1.1676. Return retention was 94.52 percent and the target-turnover ratio was 0.9375.
- RO03 failed the two decisive frozen gates. Full-sample maximum drawdown worsened by 3.08 percent instead of improving by at least 10 percent, and the random circular-shift empirical p-value was 0.8343. Two of three segments improved, but the observed scale path was not distinguishable from randomly shifted risk paths. RO03 was rejected and archived without rescue.
- Rechecked recent primary evidence. The current asset-pricing evidence continues to map significant return characteristics to already registered return, momentum, beta, skewness and tail-risk families; hidden-factor work requires external traditional-market, sentiment, rotation or security-event data; explainable microstructure work requires one-second Binance order books and trades; execution-constrained auto-tuning is validation governance rather than a new Alpha mechanism.
### Evidence and artifacts
- Added the frozen protocol, reproducible runner, pre-PnL result, historical result, interval returns, random-shift trials and Chinese report under `ro03_h22_cadence_aligned_volatility_budget_v1`.
- Added `TWENTY_SIXTH_WAVE_CADENCE_ALIGNED_VOLATILITY_BUDGET_20260627.json` and the matching Chinese report.
- Archived RO03 and its failure summary under `C:\Users\26492\Desktop\失败策略\RO03_H22_CADENCE_ALIGNED_VOLATILITY_BUDGET_V1`.
- Updated `docs/PROJECT_OVERVIEW_CN.md` with the latest forward timestamps, RO03 structural pass, frozen historical failure and prohibited rescue rules.
### Decision
- RO03 is `REJECT_AND_ARCHIVE_NO_RESCUE`. New independent Alpha candidates: 0. New risk-overlay survivors: 0. New forward shadows: 0. The sealed holdout remained closed.
- Lower turnover and a slightly higher base PF do not override the failed drawdown and randomization gates. Do not change the 20-day, 252-day, 75th-percentile, 0.5-scale or three-day constants, and do not add persistence, cooldown, hysteresis or independent cohort scaling.
- Daily and cadence-aligned H22 volatility-state scaling are now closed on this history. Do not reopen them under another market-state name.
### Change-control boundary
- No application strategy, runtime configuration, formal signal, A-grade, Feishu, leverage, account, position, order or approved-manifest behavior changed. The original H22 forward ledger remains unchanged.
- Application version remains `3.56.28`. The new runner and evidence are isolated research-archive artifacts outside the application repository, so no version bump, Git commit or GitHub push is required.
### Current recovery point
- Continue H22, V357 and H27 only when new closed data is available; H22 still has no closed prospective observation and must not be judged early.
- Return to the independent-mechanism funnel. Do not reopen RO01, RO02, RO03, the old momentum-quality overlay, or any daily/three-day H22 risk-scale variant.
- Admit a new implementation only if it exposes a new payer or forced quantity, one ex-ante direction, independent point-in-time history, acceptable cost scale and full global de-duplication before any PnL access.

## 2026-06-27 - Task: twenty-seventh-wave causal BTC beta-hedge validation
### What was done
- Refreshed the frozen forward tracks first. H22 closed data advanced through `2026-06-27T11:30:00Z`, with one fully prospective rebalance, one active observation and zero closed observations; all integrity checks pass. V357 remains at 21/21 symbols, 5 closed-data days and 6 fully prospective observations. H27 was refreshed at `2026-06-27T11:55:34Z` and remains record-only with H22 closed observations 0, V357 adapter closed trades 2 and 2 common daily rows.
- Re-audited the closed risk-overlay space and identified one route structurally different from RO01-RO03: remove H22 residual systematic BTC beta with one explicit hedge leg instead of switching aggregate gross exposure. Global checks found no prior frozen H22 beta-hedge implementation; the route was classified as a risk overlay only, not a new Alpha family.
- Froze `RO04_H22_CAUSAL_BTC_BETA_HEDGE_V1` before outcome access. The primary beta uses 60 completed 04:00-to-04:00 daily intervals with minimum 40 observations; the alternate representation uses 90 intervals with minimum 60. The hedge is negative causal rolling beta clipped to plus or minus 0.50 research notional, applied only to BTC-USDT-SWAP and updated only on the frozen three-day base cadence. H22 ranks, hysteresis, staggered cohorts, all non-BTC targets and the original forward ledger remain unchanged.
- RO04 passed every return-blind structural gate on 960 ready observations. The median absolute parent beta was 0.1299, the 90th percentile was 0.2372 and the maximum was 0.3768. The 0.50 cap never bound; median ex-ante residual beta after the hedge was zero. Primary 60-day and alternate 90-day hedge paths had Spearman 0.8883 and median absolute difference 0.0351. All 326 hedge changes occurred on the frozen schedule. Target turnover rose only 3.28 percent and mean gross exposure rose 9.95 percent, both within frozen limits.
- Opened the frozen historical outcomes once after protocol and pre-PnL hashes were locked. Parent H22 had base PF 1.1620, total return 73.23 percent, maximum drawdown 19.93 percent and realized BTC beta -0.0797. RO04 had base PF 1.1475, total return 64.00 percent, maximum drawdown 18.93 percent and realized BTC beta -0.0111. Stress PF was 1.0628 and delayed-15-minute PF was 1.1474.
- RO04 removed 86.02 percent of realized absolute BTC beta with only 3.28 percent additional target turnover, and two of three segments improved drawdown. It nevertheless failed the frozen risk-benefit gates: full-sample drawdown improved only 5.01 percent versus the required 10 percent, and the random circular-shift empirical p-value was 0.4830. The hedge path also lost about 5.10 percent gross in aggregate and reduced total return retention to 87.39 percent. RO04 was rejected and archived without rescue.
- Screened recent primary research again. One-second crypto microstructure still requires Binance order books and trades and duplicates closed order-flow/L2 families; hidden-factor pricing requires external equity, sentiment, rotation and security-event data; probabilistic volatility forecasting supplies risk forecasts rather than a unique return payer; pump-and-dump work depends on external event confirmation and illiquid token universes.
### Evidence and artifacts
- Added the frozen protocol, reproducible runner, pre-PnL result, historical result, interval returns, random-shift trials, hashes and Chinese report under `ro04_h22_causal_btc_beta_hedge_v1`.
- Added `TWENTY_SEVENTH_WAVE_CAUSAL_BTC_BETA_HEDGE_20260627.json` and the matching Chinese report.
- Archived RO04 and its failure summary under `C:\Users\26492\Desktop\失败策略\RO04_H22_CAUSAL_BTC_BETA_HEDGE_V1`.
- Updated `docs/PROJECT_OVERVIEW_CN.md` with the latest forward timestamps, RO04 structural pass, historical failure and prohibited rescue rules.
### Decision
- RO04 is `REJECT_AND_ARCHIVE_NO_RESCUE`. New independent Alpha candidates: 0. New risk-overlay survivors: 0. New forward shadows: 0. The sealed holdout remained closed.
- Beta reduction alone is not sufficient. Do not change the 60-day or 90-day windows, replace BTC with ETH, add a second hedge asset, alter the 0.50 cap or three-day cadence, or add thresholds, deadbands, cooldowns, volatility, funding or OI filters.
- H22 gross scaling and dynamic beta-hedge variants are now both closed on this history and may not be reopened under new names.
### Change-control boundary
- No application strategy, runtime configuration, formal signal, A-grade, Feishu, leverage, account, position, order or approved-manifest behavior changed. The original H22 forward ledger remains unchanged.
- Application version remains `3.56.28`. The new runner and evidence are isolated research-archive artifacts outside the application repository, so no version bump, Git commit or GitHub push is required.
### Current recovery point
- Continue H22, V357 and H27 only when new closed data is available; H22 still has no closed prospective observation and must not be judged early.
- Return to the independent-mechanism funnel. Do not reopen RO01-RO04, the old momentum-quality overlay, daily or cadence-aligned gross scaling, or dynamic BTC/ETH beta hedges.
- Admit a new implementation only after observable payer or forced quantity, unique ex-ante direction, independent point-in-time history, cost scale and global de-duplication all pass before PnL access.

## 2026-06-27 - Task: twenty-eighth-wave residual-ranking and recent-mechanism screen
### What was done
- Refreshed the frozen forward tracks. H22 closed data advanced through `2026-06-27T11:45:00Z`, with one fully prospective rebalance, one active observation and zero closed observations; all integrity checks pass. V357 remains at 21/21 symbols, 5 closed-data days and 6 fully prospective observations. H27 was refreshed at `2026-06-27T12:15:11Z`; closed data also advanced through `11:45:00Z`, but H22 closed observations remain 0, V357 adapter closed trades remain 2 and common daily rows remain 2.
- Re-ran the existing one-week plain-beta feasibility scan to confirm it was not an omitted family. The weekly route had 131 rebalances and 29.71 percent mean one-way turnover, but its weight correlation with the 7-day low-volatility family was -0.8263, its same-side overlap with 14-day momentum was 29.68 percent versus the frozen 25 percent duplicate gate, TRX occupied 24.24 percent of short slots versus the 20 percent concentration cap, and the primary source reported opposing one-week and one-month economic signs. It remains `EVIDENCE_CONFLICT_HOLD_NO_PNL`.
- Located the historical ambiguity around the unnumbered 14-day beta-residual ranking reference. Froze `RBL01_H22_14D_BETA_RESIDUAL_REPRESENTATION_V1` as a return-blind representation and global-de-duplication audit only. The primary factor is equal-weight BTC/ETH 15-minute return; the alternate factor is the equal-weight return of the 18-symbol H22 universe; both use a shifted 30-day rolling beta and 14-day residual sum, then retain H22's 4-in/6-out hysteresis, three staggered cohorts and three-day cadence.
- RBL01 used 1,020 observations without opening any future return or PnL. The two causal factor definitions were stable: score-rank Spearman median 0.9670, 10th percentile 0.8947, top/bottom same-side overlap 87.51 percent, aggregate-weight same-side overlap 87.70 percent and aggregate-weight cosine 0.9067.
- The residual path nevertheless duplicated raw H22. Mean same-side weight overlap was 83.32 percent, median overlap 84.62 percent, mean weight cosine 0.8505 and held-set Jaccard 0.7532. Target turnover was 0.9742 times raw H22. Both frozen duplicate thresholds were hit, and residualization introduced no new observable payer or forced quantity. RBL01 therefore stopped before PnL as `REJECT_BEFORE_PNL_DUPLICATE_FP01_NO_NEW_PAYER`.
- Screened current primary research. Cross-chain negative spillovers propose attention-driven capital reallocation, but the evidence requires chain-level on-chain activity across multiple blockchains; the local fixed universe lacks comparable point-in-time chain activity, and a price-only proxy collapses to closed cross-asset lead-lag or reversal fingerprints. AdaptiveTrend combines 6-hour trend, rolling Sharpe selection, dynamic volatility trailing stops, market-cap filters and a 70/30 net-long tilt; it duplicates closed trend and state families, requires unavailable point-in-time market-cap history and adds market beta rather than a new payer. Path-dependent funding design is theoretical contract engineering rather than an empirical directional signal and maps to closed funding and mark-index families.
- Checked the official OKX API change log through this cycle. Its latest listed date remains 2026-06-23; the upcoming price-limit XYZ, OKUSD, Signal Clone, ELP and cool-off routes have already been screened, and no new contract-level asymmetric historical field appeared.
### Evidence and artifacts
- Added `rbl01_h22_14d_beta_residual_representation_v1/PROTOCOL_LOCKED_BEFORE_PNL.json`, the reproducible return-blind runner, result and Chinese report.
- Added `TWENTY_EIGHTH_WAVE_RESIDUAL_AND_RECENT_MECHANISM_SCREEN_20260627.json` and the matching Chinese report.
- Archived RBL01 and its failure summary under `C:\Users\26492\Desktop\失败策略\RBL01_H22_14D_BETA_RESIDUAL_REPRESENTATION_V1`.
- Updated `docs/PROJECT_OVERVIEW_CN.md` with the latest forward timestamp, plain-beta closure, RBL01 duplication result and current prohibited rescue rules.
### Decision
- New independent Alpha candidates: 0. New risk-overlay survivors: 0. New forward shadows: 0. Future returns, PnL and the sealed holdout remained closed.
- Plain beta and 14-day beta-residual ranking are no longer unresolved omissions. Do not reopen them by changing beta windows, factor mixes, rank counts, refresh cadence or residual model.
- Cross-chain substitution cannot be approximated with local token returns alone, and AdaptiveTrend-style rolling performance selection or 70/30 market-beta loading is not an independent low-freedom mechanism.
### Change-control boundary
- No application strategy, runtime configuration, formal signal, A-grade, Feishu, leverage, account, position, order or approved-manifest behavior changed.
- Application version remains `3.56.28`. Research artifacts and documentation only; no version bump, Git commit or GitHub push is required.
### Current recovery point
- Continue H22, V357 and H27 only when new closed data is available; H22 still has no closed prospective observation and must not be judged early.
- Return to the independent-mechanism funnel. Do not reopen RO01-RO04, plain beta, 14-day beta-residual momentum, the old momentum-quality overlay, daily/cadence gross scaling or dynamic BTC/ETH beta hedges.
- Admit a new implementation only after observable payer or forced quantity, unique ex-ante direction, independent point-in-time history, acceptable cost scale and global de-duplication all pass before PnL access.

## 2026-06-27 - Task: twenty-ninth-wave order-flow complexity exhaustion
### What was done
- Refreshed the frozen forward tracks. H22 closed data advanced through `2026-06-27T12:30:00Z`, with one fully prospective rebalance, one active observation and zero closed observations; all integrity checks pass. V357 remains at 21/21 symbols, 5 closed-data days and 6 fully prospective observations. H27 was refreshed at `2026-06-27T12:48:30Z`, with closed data through `12:00:00Z`, H22 closed observations 0, V357 adapter closed trades 2 and common daily rows 2.
- Rechecked all 32 candidate-factory ideas. Only I032 trade-size asymmetry and I033 multi-scale trade-size/price-response reached the cost or complexity gates after passing the earlier policy, data, mechanism and original duplicate checks.
- I032 remains closed: its frozen edge ceiling was 10 bp versus 16 bp base and 32 bp stress round-trip costs. It is now registered as `FP19_TRADE_SIZE_ASYMMETRY`.
- I033 had an 80 bp edge ceiling but required 6 parameters and 729 combinations versus the limits of 4 and 216. It is now registered as `FP20_MULTI_SCALE_TRADE_RESPONSE`.
- Inspected the frozen order-flow data. One- and five-minute bars contain aggregate buy/sell size, total trade count, imbalance and price response, but not side-specific counts, size quantiles or large/small directional volume. The raw archive contains these source fields, but rebuilding it cannot cure the frozen complexity failure or exact FP20 closure.
- H6 already covered multi-scale signed-flow persistence and price-response efficiency. Its validation gross PF was 1.2316, but base PF was 0.1810, stress PF 0.0848 and base mean -0.1443 percent. H7 separately covered absorption/reversal.
- Primary evidence also shows immediate price impact depends on spread, price gaps and opposite-side depth, which require disallowed L2 data. Recent trade-sequence entropy evidence predicts magnitude rather than direction.
- The OKX change log still lists 2026-06-23 as the latest dated change; no new multi-year asymmetric contract field appeared.
### Evidence and artifacts
- Added `TWENTY_NINTH_WAVE_ORDERFLOW_COMPLEXITY_EXHAUSTION_20260627.json` and its Chinese report.
- Archived the I033 failure summary under `C:\Users\26492\Desktop\失败策略\I033_MULTI_SCALE_TRADE_RESPONSE`.
### Decision
- I032: `REJECT_BEFORE_PNL_COST_CEILING_AND_FP19_CLOSED`.
- I033: `REJECT_BEFORE_PNL_COMPLEXITY_EXACT_FP20_AND_MISSING_CANONICAL_NON_L2_EXPRESSION`.
- New independent Alpha candidates, risk-overlay survivors and forward shadows: 0. No future returns, PnL or sealed holdout were opened.
### Change-control boundary
- No application strategy or runtime configuration changed. Application version remains `3.56.28`; no version bump, commit or GitHub push is required.
### Current recovery point
- Continue H22, V357 and H27 only when new closed data is available.
- The signed-trade size-response branch is exhausted. Do not reopen FP08, FP09, FP17, FP19, FP20 or FP25 under new names.
- Return to the independent-mechanism funnel outside closed order-flow and L2 families.

## 2026-06-27 - Task: thirtieth-wave strategy-forum inspiration screen
### What was done
- Screened current automated-trading and crypto forum discussions as idea sources only. The main recurring themes were opening-range breakout with relative volume, Gao-style intraday momentum, EWMAC plus carry, fractional-tick microstructure and adaptive regime switching.
- Mapped opening-range breakout to FP03, P01/P02 and the price-volume confirmation family. In crypto, selecting UTC or funding-settlement boundaries as a synthetic open would also duplicate H17/H18 calendar-intraday families.
- Mapped Gao-style intraday momentum to FP16, H17/H18 and ordinary short-horizon momentum. A 24/7 market lacks the cash-equity opening auction and overnight reset that motivates the original session effect.
- Mapped EWMAC plus carry to trend/momentum and funding-carry families already closed by FP01, FP11, H21 and H30. The useful lesson is pooled scaling rather than symbol-specific re-estimation, but that is research governance, not a new payer.
- Mapped professional fractional-tick microstructure to FP08, FP09, FP17, FP19, FP20 and FP25. Existing H6/H7 and I032/I033 evidence already shows realistic costs and data boundaries eliminate the route for this project.
- Mapped adaptive trend/reversion switching to MC07, I054 and RO01-RO04. Rolling Sharpe selection, Hurst, hidden states and volatility-dependent exits add freedom without a new payer.
- Considered forum popularity itself as a crowding signal, but it requires an external point-in-time social corpus, deletion/survivorship handling and text classification outside the local-only data boundary.
### Evidence and artifacts
- Added `THIRTIETH_WAVE_STRATEGY_FORUM_INSPIRATION_SCREEN_20260627.json` and its Chinese report.
- Updated `docs/PROJECT_OVERVIEW_CN.md` with the forum-theme mapping and the rule that forum popularity is not evidence.
### Decision
- New independent Alpha candidates, risk-overlay survivors and forward shadows: 0. No future returns, PnL or sealed holdout were opened.
- Continue using forums only to discover new constrained payers or forced quantities. Do not admit indicator recipes, selected time windows, adaptive state switching or reported win rates as candidate evidence.
### Change-control boundary
- No application strategy or runtime configuration changed. Application version remains `3.56.28`; no version bump, commit or GitHub push is required.

## 2026-06-27 - Task: thirty-first-wave incremental source delta audit
### What was done
- Refreshed the frozen forward tracks in order. H22 closed data advanced through `2026-06-27T12:45:00Z`, with one fully prospective rebalance, one active observation and zero closed observations; protocol, ledger, data-quality and snapshot-chain checks all pass. V357 remains at 21/21 symbols, 5 closed-data days and 6 fully prospective observations. H27 was refreshed at `2026-06-27T13:04:16Z` and remains record-only with H22 closed observations 0, V357 adapter closed trades 2 and 2 common daily rows.
- Rechecked the official OKX API change log on 2026-06-27. Its latest dated entry remains 2026-06-23. The upcoming XYZ price-limit parameters, OKUSD endpoints, Signal Clone, ELP consolidated order book and cool-off rejection are unchanged from the routes already screened in waves 23 and 24; no new contract-level side-specific historical field appeared.
- Rechecked the current broad crypto asset-pricing evidence. The significant return characteristics remain two-week momentum, one- and two-month industry momentum, one-week beta, one-month idiosyncratic skewness and one-week five-percent expected shortfall. These map exactly to H22, failed H19, the closed or held beta/H28 routes and archived skewness/tail-quality families.
- Rechecked recent explainable crypto microstructure work. Its tradable representation uses Binance Futures one-second order books and trades, spread/depth/order-flow features and a CatBoost pipeline. It therefore violates the single-OKX/no-continuous-L2 boundary, exceeds the low-freedom budget and duplicates FP08, FP09, FP17, FP20 and FP25.
- Used the recent systematic falsification of common intraday OHLCV strategies only as cross-market research-governance caution. It is not crypto evidence and did not create or reject a local candidate by itself.
- Reconfirmed that hidden-factor crypto pricing requires external equity-industry, sentiment, rotation or security-event data; price-only substitutes collapse into H19, beta, momentum/state or external-event families.
### Evidence and artifacts
- Added `THIRTY_FIRST_WAVE_INCREMENTAL_SOURCE_DELTA_AUDIT_20260627.json` and the matching Chinese report under the local-only discovery archive.
- Updated `docs/PROJECT_OVERVIEW_CN.md` with the latest H22/H27 timestamps, the incremental source result and a rule that broad source screens are rerun only after a material source-date, field-schema or local-data-route change.
### Decision
- New independent Alpha candidates: 0. New risk-overlay survivors: 0. New forward shadows: 0. Future returns, PnL and the sealed holdout remained closed.
- Broad forum, common OHLCV, factor-review and OKX change-log scans are now delta-triggered rather than repeatedly rerun without new inputs. This is a research-efficiency boundary, not a stop to the independent-mechanism funnel.
### Change-control boundary
- No application strategy, runtime configuration, formal signal, A-grade, Feishu, leverage, account, position, order or approved-manifest behavior changed.
- Application version remains `3.56.28`. Research and documentation changes remain local; no version bump, Git commit or GitHub push is required.
### Current recovery point
- Continue H22, V357 and H27 only under their frozen protocols; H22 still has no closed prospective observation and must not be judged early.
- Continue the independent-mechanism funnel only when a source or local data route exposes a recurring side-specific constrained quantity, actual forced-flow quantity, unique ex-ante direction and independent point-in-time history. Do not reopen H19, beta, skewness, expected-shortfall, common intraday indicator, order-flow or L2 families under new labels.

## 2026-06-27 - Task: thirty-second-wave tail-network and liquidity-risk screen
### What was done
- Refreshed the frozen forward tracks again after a new closed 15-minute bar became available. H22 closed data advanced through `2026-06-27T13:00:00Z`, with one fully prospective rebalance, one active observation and zero closed observations; all integrity checks pass. V357 remains at 21/21 symbols, 5 closed-data days and 6 fully prospective observations. H27 refreshed at `2026-06-27T13:17:39Z` and remains record-only with H22 closed observations 0, V357 adapter closed trades 2 and 2 common daily rows.
- Screened the new June 2026 paper `Crashing Together, Rallying Apart`, which estimates separate dynamic Huesler-Reiss extreme-value graphs for joint crashes and rallies. The paper reports a near-complete stable lower-tail graph, a thinner increasingly sectoral upper-tail graph and a BTC/ETH systemic core.
- Registered the mechanism intake as `I058_LOWER_TAIL_NETWORK_CENTRALITY_PREMIUM` for gate analysis only. It failed before implementation because no payer or unique return direction is identified: high crash connectedness may command a risk premium or may instead be an exposure to avoid. The paper studies systemic risk rather than future cross-sectional returns.
- The route also failed structural and complexity gates. The paper notes that dense extremal graphs make ordinary binary centrality measures uninformative; its balanced survivor panel excludes failed assets; and the implementation requires marginal time-series filtering, heavy-tail transforms, extreme-value graph estimation, sparsity selection and long rolling windows beyond the low-freedom budget.
- Global mapping hit MC02 downside beta, MC05 dispersion, MC06 correlation reintegration, MC08 co-jump exhaustion, tail-quality/expected-shortfall and TW08 network-complexity routes. I058 was closed without code, PnL or rescue.
- Screened the `Extremity Premium` paper. It uses the external Crypto Fear and Greed Index, predicts spread and uncertainty intensity rather than a directional return, is sensitive to functional form and maps to FP05, MC07 and external sentiment families.
- Screened `Slippage-at-Risk`. It derives liquidation execution risk from current Hyperliquid order-book microstructure and predicts systemic stress, not asset return direction. It requires continuous L2 and maps to FP25, FP13, FP22, FP23 and adjacent H6/H7 order-flow families.
- Audited whether the new tail-network evidence should alter H27. It does not: H27 already freezes shared-loss fractions, top-five joint-loss share, worst common 10/20-day windows and base/stress correlations. Adding an extremal graphical model after registration would change the frozen protocol and add substantial post-registration method freedom.
### Evidence and artifacts
- Added `THIRTY_SECOND_WAVE_TAIL_NETWORK_AND_LIQUIDITY_RISK_SCREEN_20260627.json` and the matching Chinese report under the local-only discovery archive.
- Updated `docs/PROJECT_OVERVIEW_CN.md` with the latest forward timestamps, I058 closure, the related sentiment/L2 mappings and the decision to keep H27 frozen unchanged.
### Decision
- I058 is `REJECT_I058_AT_PAYER_DIRECTION_COMPLEXITY_AND_GLOBAL_DUPLICATE_GATES_NO_PNL_NO_RESCUE`.
- New independent Alpha candidates: 0. New risk-overlay survivors: 0. New forward shadows: 0. Future returns, PnL and the sealed holdout remained closed.
- Dynamic tail-network centrality is now a closed independent-Alpha route. Do not reopen it with alternative copulas, graphical lasso, quantile networks, downside connectedness, centrality scores, sector graphs or different tail thresholds without a genuinely new observable payer and unique return direction.
### Change-control boundary
- No application strategy, runtime configuration, formal signal, A-grade, Feishu, leverage, account, position, order or approved-manifest behavior changed.
- Application version remains `3.56.28`. Research and documentation changes remain local; no version bump, Git commit or GitHub push is required.
### Current recovery point
- Continue H22, V357 and H27 frozen evidence; H22 still has no closed prospective observation and must not be judged early.
- Resume the independent-mechanism funnel outside tail-network, sentiment-spread, volatility-state, liquidation-risk and L2 liquidity families. Admit only recurring OKX contract-level side-specific constrained quantities or actual forced-flow quantities with independent point-in-time history and one ex-ante return direction.

## 2026-06-27 - Task: thirty-third-wave local causal route delta audit
### What was done
- Refreshed the frozen forward tracks. H22 remains through `2026-06-27T13:00:00Z` with one fully prospective rebalance, one active observation and zero closed observations; all integrity checks pass. V357 remains at 21/21 symbols, 5 closed-data days and 6 fully prospective observations. H27 refreshed at `2026-06-27T13:32:11Z` and remains record-only with H22 closed observations 0, V357 adapter closed trades 2 and 2 common daily rows.
- Used the twenty-second-wave exhaustive SQLite and raw instrument-JSON audit as the fixed baseline. Scanned the retained historical-data roots and non-research history-package directories for data-like files modified after `2026-06-27T10:00:52Z`, while excluding research outputs, forward ledgers, result files and outcome-labelled derivatives.
- Found only three updated files: the existing `okx_causal_metadata.sqlite3`, its coverage report and its collector heartbeat. No new independent raw-data directory, exchange, endpoint family, table or schema key appeared.
- The database still contains the same seven tables and no new Alpha-relevant columns. Mark-price and index-price tables each added exactly 21 rows, one normal 4-hour append per symbol, and remain the exhausted F04 route.
- Funding history remains at 6,190 rows with every record still `formula_type=withRate` and `method=current_period`; no formula or settlement-method regime change appeared.
- Hourly OI increased from 777 to 861 rows, exactly four new hourly observations per each of 21 symbols. Coverage is now only 41 hours per symbol from `2026-06-24T13:00:00Z` through `2026-06-27T13:00:00Z`, still far below an independent-history gate and still mapped to FP12.
- Instrument snapshots remain at 84 rows, 21 symbols across four days, with the same 50 raw keys. `longPosRemainingQuota`, `shortPosRemainingQuota` and `maxPlatOICoinLmt` remain empty in all 84 rows. `initPxLmtPct`, `floatPxLmtPct` and `maxPxLmtPct` are still absent before their stated production target date.
- Existing rule changes remain only ATOM `maxPlatOILmt`, DOT `posLmtAmt`, HYPE `tickSz` and six `upcChg` announcements, already mapped to F39 symmetric constraints or FP24 granularity.
### Evidence and artifacts
- Added `THIRTY_THIRD_WAVE_LOCAL_CAUSAL_ROUTE_DELTA_AUDIT_20260627.json` and the matching Chinese report under the local-only discovery archive.
- Updated `docs/PROJECT_OVERVIEW_CN.md` with the latest forward refresh, exact data deltas and the trigger conditions for the next full local-route audit.
### Decision
- Status: `COMPLETE_NO_NEW_CAUSAL_ROUTE`.
- New independent Alpha candidates: 0. New risk-overlay survivors: 0. New forward shadows: 0. Future returns, PnL and the sealed holdout remained closed.
- Normal hourly OI, four-hour mark/index and collector-heartbeat appends are not new mechanisms. Do not retest closed families each time these series grow by a few rows.
### Change-control boundary
- No application strategy, runtime configuration, formal signal, A-grade, Feishu, leverage, account, position, order or approved-manifest behavior changed.
- Application version remains `3.56.28`. Research and documentation changes remain local; no version bump, Git commit or GitHub push is required.
### Current recovery point
- Continue H22, V357 and H27 frozen evidence. H22 still has no closed prospective observation and must not be judged early.
- Do not rerun the complete local data-route audit until a new table, schema key, independent raw directory, nonempty side-specific quota, actual forced-flow field or materially sufficient independent history appears. The existing collector continues naturally and needs no new code or downloader.
- Continue the independent-mechanism funnel only outside all closed price-path, funding, OI, rule-snapshot, tail-network, sentiment, order-flow and L2 families.

## 2026-06-28 - Task: H22 Binance external price-path robustness gate
### What was done
- Froze `H22_BINANCE_EXTERNAL_PRICE_ROBUSTNESS_V1` before opening any cross-exchange outcome. The audit was classified as external robustness only, not a new Alpha family, and kept the H22 universe, 84 four-hour formation window, 4-in/6-out hysteresis, three staggered cohorts, three-day cadence, 04:00 UTC execution time and 0.4 operational research scale unchanged.
- Used only closed OKX and Binance USD-M 15-minute K-lines, strictly resampled to complete four-hour bars. Binance funding, OI, long-short ratios, taker-flow metrics, order books and L2 were excluded.
- Evaluated 1,020 common decisions from 2023-09-01 04:00 UTC through 2026-06-16 04:00 UTC. The cross-sectional rank Spearman median was 1.000000 and its 10th percentile was 0.997936; mean same-side weight overlap was 98.9955 percent and mean weight cosine was 0.996220.
- The decisive frozen integrity gate failed before outcome access. Exact target-weight rows matched only 77.9412 percent of the time versus the locked 90 percent minimum. Small fourth-to-sixth rank differences propagated through hysteresis and staggered cohorts, so the two venues did not reproduce an identical enough H22 target path.
- Stopped without opening any Binance-path PF, return, drawdown or sealed outcome. The 90 percent gate was not relaxed after the result.
- Refreshed the separate original 14-day momentum and fixed 4-in/6-out daily forward ledger through `2026-06-27T13:00:00Z`. Both variants now contain 4 fully prospective rebalances and 3 closed observations; data quality, protocol, ledger and daily snapshot-chain integrity all pass. The acceptance state remains `NOT_EVALUATED_SAMPLE_INCOMPLETE`, with 46 rebalances, 56 closed-data days and 86 closed-data days remaining to the frozen 50/60/90 milestones.
- Refreshed the actual H22 staggered 3x3 forward ledger separately. H22 remains at one fully prospective rebalance, one active observation and zero closed observations through `2026-06-27T13:00:00Z`; its next expected refresh is `2026-06-28T04:00:00Z` and all integrity checks pass.
- Refreshed V357 with 21/21 symbol coverage and no skipped symbols. Its acceptance adapter remains at 5 closed-data days and 6 fully prospective observations. H27 refreshed at `2026-06-27T16:28:34Z` and remains `RECORD_ONLY_SAMPLE_INCOMPLETE`, with H22 closed observations 0, V357 adapter closed trades 2 and 2 common daily rows.
### Evidence and artifacts
- Added `HISTORY_PACKAGES_20260621/RESEARCH/h22_binance_external_robustness_v1/PROTOCOL_LOCKED_BEFORE_RESULTS.json`.
- Added the read-only reproducible runner `run_external_robustness.py`, `RESULT.json` and `RESULTS_CN.md` in the same isolated research directory.
- Updated `docs/PROJECT_OVERVIEW_CN.md` with the integrity result and the new cross-exchange research boundary.
### Decision
- Status: `STOP_BEFORE_OUTCOME_INTEGRITY_GATE_FAILED`.
- Decision: `NO_EXTERNAL_ROBUSTNESS_CONCLUSION`.
- The result neither rejects nor promotes H22. H22 remains an OKX-only frozen forward research shadow and must complete its original 60/90-day and 50/80-observation acceptance gates.
- Do not lower the 90 percent target-path gate, remove symbols or dates, change ranks/windows/cadence, or search venue weights, lead-lag thresholds or price-divergence variants.
### Change-control boundary
- No application strategy, runtime configuration, formal signal, A-grade, Feishu, leverage, account, position, order or approved-manifest behavior changed.
- Application version remains `3.56.28`. The new files are isolated research artifacts and documentation only, so no version bump, Git commit or GitHub push is required.
### Current recovery point
- Continue H22, V357 and H27 frozen forward evidence under their original protocols.
- Treat ordinary Binance K-lines only as redundancy and data-quality references; do not use them to reopen cross-exchange lead-lag or divergence research.
- Continue the independent-mechanism funnel only when a genuinely new payer, forced quantity or causal point-in-time field appears and passes the existing global de-duplication and cost-scale gates before PnL.

## 2026-06-28 - Task: thirty-fourth-wave residual mechanism boundary audit
### What was done
- Refreshed the frozen forward tracks again. H22 remains through `2026-06-27T13:00:00Z` with one fully prospective rebalance, one active observation and zero closed observations. V357 remains at 21/21 symbols, 5 closed-data days and 6 fully prospective observations. H27 refreshed at `2026-06-27T16:48:51Z` and remains record-only with H22 closed observations 0, V357 adapter closed trades 2 and 2 common daily rows.
- Verified that all MC01-MC08 candidates and all 32 candidate-factory V2 ideas are already terminal. The only official multi-year OI route remains an exact FP12 duplicate; no pending candidate was overlooked.
- Screened `W34_R01_WEEKEND_LIQUIDITY_RESET_REVERSAL`. Without point-in-time participant identity, weekend inventory or institutional-flow history, the implementation is ordinary short-horizon reversal restricted to a selected calendar subset. It duplicates FP02, H17 and H18 and was rejected at intake before code or PnL.
- Screened `W34_R02_FUNDING_INTERVAL_COMPRESSION_UNWIND`. The economic idea was that a shorter settlement interval would mechanically accelerate crowded-side carrying costs, but the admitted history contains 2,594 common intervals and every one is eight hours. All 6,190 funding records also retain the same `withRate/current_period` formula-method pair. With zero events, the route failed the data gate before PnL.
- Screened `W34_R03_SECURITY_FUND_DECLINE_OR_ADL_ONSET`. There is no multi-year local archive, no side-specific forced quantity and no unique ex-ante direction. The route duplicates FP13, FP22 and FP23. The official 2026-06-09 API update further removes or empties balance, threshold, event and decline-rate fields and stops normal-state ADL-warning pushes.
- Rechecked `W34_R04_PRICE_LIMIT_BAND_PROXIMITY`. The XYZ fields have a 2026-06-30 production target, no historical point-in-time archive and no unique continuation-versus-reversion direction, so F40 remains diagnostic only with no C admission or implementation.
- Mapped recent primary research. Explainable one-second microstructure requires continuous L2/trades and CatBoost; funding-aware market making requires maker inventory and execution; resolution-aware binary-market perpetual design targets a different market. None fits the local signal-only fixed-OKX boundary.
### Evidence and artifacts
- Added `THIRTY_FOURTH_WAVE_RESIDUAL_MECHANISM_BOUNDARY_AUDIT_20260628.json` and the matching Chinese report under the local-only discovery archive.
- Updated `docs/PROJECT_OVERVIEW_CN.md` with the four closed residual routes and the new prohibited-reopen boundary.
### Decision
- Status: `COMPLETE_NO_NEW_INDEPENDENT_CAUSAL_ROUTE`.
- New independent Alpha candidates: 0. New risk-overlay survivors: 0. New forward shadows: 0. Future returns, PnL and the sealed holdout remained closed.
- Do not reopen weekend/Monday calendar reversal, funding-interval compression, security-fund/ADL diagnostics or price-limit proximity by changing weekdays, settlement windows, state labels or by backfilling future fields.
### Change-control boundary
- No application strategy, runtime configuration, formal signal, A-grade, Feishu, leverage, account, position, order or approved-manifest behavior changed.
- Application version remains `3.56.28`. Research and documentation changes remain local; no version bump, Git commit or GitHub push is required.
### Current recovery point
- Continue H22, V357 and H27 only under their frozen prospective protocols; none is currently due for judgement.
- The next independent implementation is allowed only after a new contract-level side-specific constrained quantity or actual forced-flow field appears with sufficient point-in-time history, one unique ex-ante return direction and no hit against the existing failure-fingerprint library.

## 2026-06-28 - Task: H22 fixed long/short sleeve decomposition
### What was done
- Froze `H22_LONG_SHORT_SIDE_DECOMPOSITION_V1` as a parent-strategy diagnostic only. The audit preserved the 18-symbol universe, 84 four-hour formation window, 4-in/6-out hysteresis, three staggered cohorts, three-day cadence, account-net target weights, base/stress costs and archived OKX funding semantics.
- The first run was stopped because the parent stress result did not replicate. No sleeve conclusion was accepted. The reconstruction was then corrected to match the released parent implementation exactly: exclude the first two staggered-cohort warm-up entries and use the frozen 2.0 adverse-funding multiplier under stress. No diagnostic balance gate was changed.
- After correction, the parent H22 base and stress PF, total return and maximum drawdown replicated with zero error over 1,017 periods from 2023-09-03 04:00 UTC through 2026-06-16 04:00 UTC.
- The long sleeve produced base PF 1.1070, total return +26.91% and maximum drawdown -20.07%; under stress it produced PF 1.0759, total return +17.82% and drawdown -22.38%. It was positive in S1 and S2 but lost about 10.00% base and 11.62% stress in S3.
- The short sleeve produced base PF 1.0042 but compounded total return -1.80% and maximum drawdown -24.20%; under stress PF was 0.9856, total return -6.65% and drawdown -25.62%. It lost in S1 and S2 but returned about +16.58% base and +14.32% stress in S3.
- The long sleeve accounted for 95.88% of the two sleeves' positive base net-return sum, above the locked 75% balance cap. Both total-return, positive-segment and contribution-balance gates failed.
### Evidence and artifacts
- Added `h22_long_short_side_decomposition_v1/PROTOCOL_LOCKED_BEFORE_RESULTS.json`, `run_side_decomposition.py`, `RESULT.json` and `RESULTS_CN.md` under the local-only discovery archive.
- Updated `docs/PROJECT_OVERVIEW_CN.md` with the side-source result and anti-redesign boundary.
### Decision
- Status: `ONE_SIDED_OR_UNBALANCED_HISTORICAL_SUPPORT_NO_REDESIGN`.
- H22 historical Alpha is primarily long-winner continuation. The short sleeve is not a stable standalone Alpha, but it provided material regime diversification in S3 when the long sleeve failed and reduced the complete parent's drawdown to about 8%-10%.
- Do not create a post-hoc long-only strategy, remove the short sleeve, search 60/40 or 70/30 weights, or add bull/bear switching. The long-only sleeve exceeds the 15% drawdown gate and fails the latest segment; the short-only sleeve has negative full-sample compounded return.
- H22 remains the complete frozen market-neutral forward shadow and must be judged only under its original prospective acceptance protocol.
### Change-control boundary
- No application strategy, runtime configuration, formal signal, A-grade, Feishu, leverage, account, position, order or approved-manifest behavior changed.
- Application version remains `3.56.28`. Research and documentation changes remain local; no version bump, Git commit or GitHub push is required.
### Current recovery point
- Continue H22, V357 and H27 frozen forward evidence. Do not use the opened side decomposition to alter H22 construction.
- Continue independent search only when a truly new payer or side-specific forced quantity appears; do not reinterpret long-side dominance as permission to create a one-sided momentum strategy.

## 2026-06-28 - Task: thirty-fifth-wave factor sufficiency and numeraire invariance audit
### What was done
- Screened the February 2026 version of `Cryptocurrency as an Investable Asset Class: Coming of Age` and the primary momentum-liquidity evidence against the complete local failure map. The robust source-level factor set remains size, two-week momentum and price-to-new-address value, with on-chain adoption, funding premia and market segmentation as separate inputs.
- Closed `W35_R01_CRYPTO_SIZE_FACTOR` before PnL. The fixed OKX survivor universe has no point-in-time historical market capitalization or supply history; quote volume and OI are not size and map back to MC01, SRM08 and the closed OI families.
- Closed `W35_R02_PRICE_TO_NEW_ADDRESS_VALUE` at the data boundary. No admitted asset-level new-address history exists, and price, volume, nominal unit price or contract age cannot reconstruct network adoption without changing the factor semantics.
- Proved `W35_R03_BTC_ETH_NUMERAIRE_MOMENTUM` is an exact algebraic duplicate of H22 before implementation. At each common decision time, BTC-, ETH-, equal-weight-market- or index-numeraire returns subtract the same scalar from every cross-sectional score, so ranks, 4-in/6-out selections, staggered-cohort targets and turnover are unchanged. Beta residualization is already closed by RBL01.
- Closed `W35_R04_LIQUIDITY_CONDITIONED_MOMENTUM` at the global duplicate gate. Liquidity only selects or weights the universe while the directional signal remains H22 two-week momentum; it maps to the archived momentum-quality overlay, MC01 and SRM08.
- Closed higher-order factor interactions at the data and complexity gates and closed segmentation/carry at the policy and duplicate gates. No future return, PnL or sealed holdout was opened.
### Evidence and artifacts
- Added `THIRTY_FIFTH_WAVE_FACTOR_SUFFICIENCY_AND_NUMERAIRE_INVARIANCE_20260628.json` and the matching Chinese report under the local-only discovery archive.
- Updated `docs/PROJECT_OVERVIEW_CN.md` with the numeraire-invariance proof and the new factor-route boundary.
### Decision
- Status: `COMPLETE_NO_NEW_INDEPENDENT_FACTOR_ROUTE`.
- New independent Alpha candidates: 0. New risk-overlay survivors: 0. New forward shadows: 0. New failure-archive directories: 0 because every route stopped before becoming an implemented strategy candidate.
- Do not reopen BTC/ETH/market-numeraire momentum, liquidity-filtered H22, volume/OI size proxies, price-only network value or nonlinear factor combinations under new names.
### Change-control boundary
- No application strategy, runtime configuration, formal signal, A-grade, Feishu, leverage, account, position, order or approved-manifest behavior changed.
- Application version remains `3.56.28`. Research and documentation changes remain local; no version bump, Git commit or GitHub push is required.
### Current recovery point
- Continue H22, V357 and H27 frozen prospective evidence under their original protocols.
- Admit another implementation only when a new OKX contract-level point-in-time field exposes a side-specific constrained quantity or actual forced flow with sufficient history, one ex-ante direction, acceptable cost scale and no existing failure-fingerprint hit.

## 2026-06-28 - Task: thirty-sixth-wave external primary mechanism evidence search
### What was done
- Searched recent primary research and current OKX official mechanism documentation for recurring side-specific forced quantities rather than indicator recipes. Sources included the 2025-2026 ADL optimization papers, the June 2026 Hyperliquid visible-TWAP study, the public-data metaorder-identification paper, funding-aware market-making research and the OKX API change log through its latest dated entry of 2026-06-23.
- Refined the ADL route with materially stronger semantics. OKX and the formal ADL papers confirm that bankrupt positions are offset against opposing profitable or highly levered positions. However, OKX performs the offset through the ADL mechanism rather than exposing a future public order-book quantity; public data still lacks contract-level matched size, remaining quantity, queue composition and completion state. The June 9 API change also deprecates historical ADL/security-fund types and empties key trigger fields. The route therefore remains closed under FP13, FP22, FP23 and I045.
- Screened publicly visible TWAP intent from `Trading in the Sunshine or in the Shade`. The paper identifies preannounced parent-order direction and schedule as a distinct liquidity mechanism, but OKX exposes TWAP details only through account algo-order functions rather than a public all-market parent-order feed. Inferring private TWAPs from print spacing or repeated size would require excessive thresholds and collapse into H6, I032, I033 and FP20.
- Rechecked hidden metaorder reconstruction. Primary methodological evidence shows public trades cannot reliably identify the source of order-flow autocorrelation or true parent-order completion. No participant identity, parent-order ID, target size or remaining quantity exists in the admitted archive, so the route failed semantics, complexity and duplicate gates.
- Rechecked funding-aware maker inventory. It is a private inventory and quote-control mechanism, not a public directional forced flow, and is outside the signal-only product boundary while duplicating closed funding families if reduced to funding alone.
### Evidence and artifacts
- Added `THIRTY_SIXTH_WAVE_EXTERNAL_PRIMARY_MECHANISM_EVIDENCE_20260628.json` and the matching Chinese report under the local-only discovery archive.
- Updated `NEGATIVE_EVIDENCE_MAP_CN.md` and `docs/PROJECT_OVERVIEW_CN.md` with the ADL execution-channel distinction and the public-TWAP observability boundary.
### Decision
- Status: `COMPLETE_MATERIAL_SEMANTIC_DELTAS_NO_NEW_EXECUTABLE_ROUTE`.
- New independent Alpha candidates: 0. New risk-overlay survivors: 0. New forward shadows: 0. Future returns, PnL and the sealed holdout remained closed.
- ADL affected-side semantics are clearer, but no public future order quantity exists. Public TWAP intent is a genuine mechanism, but OKX does not expose market-wide parent-order terms or history. Neither route may be implemented from inferred price or trade patterns.
### Change-control boundary
- No application strategy, runtime configuration, formal signal, A-grade, Feishu, leverage, account, position, order or approved-manifest behavior changed.
- Application version remains `3.56.28`. Research and documentation changes remain local; no version bump, Git commit or GitHub push is required.
### Current recovery point
- Continue H22, V357 and H27 frozen prospective evidence under their original protocols.
- Continue external primary-source delta search only for public contract-level fields that expose side, remaining forced quantity and completion state. Do not infer these from latent inventory, risk flags, trade clustering or private algo-order behavior.

## 2026-06-28 - Task: thirty-seventh-wave transparent forced-flow latency screen
### What was done
- Audited transparent perpetual venues to determine whether a real mechanism exists with public side, forced quantity and completion semantics. Hyperliquid and dYdX official liquidation rules were used as the strongest observable examples, then mapped back to the fixed OKX-only signal boundary.
- Hyperliquid liquidations first send market orders to the public book. For positions above 100,000 USDC, the first liquidation slice is 20 percent, followed by a 30-second cooldown; deeper insolvency can transfer the position to the liquidator vault. This proves that public side-specific forced-quantity rules can exist in practice.
- The Hyperliquid route nevertheless failed the new advance-notice gate. The forced order begins when the liquidation threshold is crossed, so observability and execution are effectively simultaneous. Exploitation would require reconstructing account margin state, mark prices and order-book conditions at block or sub-second cadence, importing venue-native node data and competing as a low-latency execution system rather than a 15-minute Feishu signal system.
- dYdX similarly creates protocol liquidation orders with a formula-driven fillable price and matches them against resting liquidity, but the orders are generated at the liquidation event rather than published with a stable minutes-or-hours lead. The liquidated fraction is governance-configurable and the route requires dYdX chain/indexer state.
- Rechecked the OKX boundary. OKX removed its historical liquidation-order REST endpoint in 2023 and directs users to the real-time WebSocket liquidation-orders channel. This supplies post-trigger event flow, not a multi-year pre-event remaining queue, and the existing family is already closed through FP13, R3 and H6-H8.
- Added the `ADVANCE_NOTICE_AND_ACTIONABLE_LEAD_TIME_GATE`: public direction and quantity are insufficient unless the field becomes observable before execution with enough time for the current system to ingest, validate, compute and notify without colocated or sub-second infrastructure.
### Evidence and artifacts
- Added `THIRTY_SEVENTH_WAVE_TRANSPARENT_FORCED_FLOW_LATENCY_SCREEN_20260628.json` and the matching Chinese report under the local-only discovery archive.
- Updated `NEGATIVE_EVIDENCE_MAP_CN.md` and `docs/PROJECT_OVERVIEW_CN.md` with the new lead-time gate and transparent-venue benchmark.
### Decision
- Status: `COMPLETE_EXTERNAL_MECHANISM_EXISTS_BUT_NO_OKX_TRANSFERABLE_SIGNAL`.
- New independent Alpha candidates: 0. New risk-overlay survivors: 0. New forward shadows: 0. Future returns, PnL and the sealed holdout remained closed.
- Transparent venues prove that side-specific forced quantities can exist, but current examples become observable only at or immediately before execution. They are low-latency venue-native opportunities, not transferable OKX 15-minute signals.
### Change-control boundary
- No application strategy, runtime configuration, formal signal, A-grade, Feishu, leverage, account, position, order or approved-manifest behavior changed.
- Application version remains `3.56.28`. Research and documentation changes remain local; no version bump, Git commit or GitHub push is required.
### Current recovery point
- Continue H22, V357 and H27 frozen prospective evidence under their original protocols.
- Continue external search only for contract-level forced plans published minutes or hours before execution, with remaining quantity, completion state, point-in-time history and an OKX-equivalent public field. Do not treat same-block liquidation visibility as advance notice.

## 2026-06-28 - Task: thirty-eighth-wave scheduled auction and settlement screen
### What was done
- Screened scheduled collateral auctions, exchange delistings and forced settlements, token unlocks and emissions, dated-futures or option expiry, and announced rule or migration changes under the new actionable-lead-time gate.
- Added `SCHEDULED_EVENT_IS_NOT_FORCED_TRADE_GATE`: a known timestamp, released quantity, settlement amount or auction lot is not a directional signal unless it maps to an unavoidable public executable order side with remaining unexecuted quantity.
- Added `RECURRING_FIXED_UNIVERSE_SAMPLE_GATE`: rare delistings, migrations, unlocks, security incidents and one-off settlements cannot become a strategy family without survivorship-safe recurring history across the fixed mature OKX universe.
- Public DeFi collateral auctions are real forced-disposal mechanisms, but they transfer collateral to bidders rather than forcing bidders to sell or hedge on OKX. Bidder inventory can be held, sold on-chain, transferred to another venue or hedged through multiple instruments, so no unique OKX perpetual direction or remaining executable quantity exists. The required on-chain auction history is also outside the frozen local-only boundary.
- Delisting and forced-settlement announcements provide a deadline and price rule but not aggregate long-short imbalance, pre-deadline voluntary closure quantity or a future public order side. Cash settlement can extinguish matched claims internally and the events are sparse, distressed and already excluded through C016, C020, F29 and FR07.
- Token unlock and emission schedules expose transferable supply rather than a forced sale. Actual seller identity, venue, sell quantity, remaining amount and completion state remain unknown; C017 and C019 therefore stay withdrawn.
- Expiry and settlement routes remain closed because the main universe is perpetual, local option history lacks contract OI, dealer ownership and gamma sign, and pinning versus post-expiry release are opposite branches. Rule-change schedules similarly expose an activation time but no side-specific constrained inventory.
### Evidence and artifacts
- Added `THIRTY_EIGHTH_WAVE_SCHEDULED_AUCTION_AND_SETTLEMENT_SCREEN_20260628.json` and the matching Chinese report under the local-only discovery archive.
- Updated `NEGATIVE_EVIDENCE_MAP_CN.md` and `docs/PROJECT_OVERVIEW_CN.md` with the scheduled-event and recurring-sample gates.
### Decision
- Status: `COMPLETE_SCHEDULED_EVENTS_DO_NOT_EXPOSE_RECURRING_FORCED_ORDER_FLOW`.
- New independent Alpha candidates: 0. New risk-overlay survivors: 0. New forward shadows: 0. Future returns, PnL and the sealed holdout remained closed.
- Scheduled auctions and settlements can expose a time or accounting quantity, but none exposes a recurring unavoidable OKX public order side with remaining executable quantity and sufficient fixed-universe history.
### Change-control boundary
- No application strategy, runtime configuration, formal signal, A-grade, Feishu, leverage, account, position, order or approved-manifest behavior changed.
- Application version remains `3.56.28`. Research and documentation changes remain local; no version bump, Git commit or GitHub push is required.
### Current recovery point
- Continue H22, V357 and H27 frozen prospective evidence under their original protocols.
- Continue external search only for recurring execution mandates in mature instruments where the same venue publicly exposes side, remaining quantity and completion progress before orders reach the market. Do not reopen delisting, unlock, migration, maintenance, expiry or cross-market auction-spillover families.

## 2026-06-28 - Task: thirty-ninth-wave historical dynamic-universe feasibility and streaming pilot
### What was done
- Audited whether the fixed 21-survivor universe can be replaced by a point-in-time OKX universe without adding cross-exchange data, continuous L2 or permanent raw one-minute storage.
- Confirmed that the current public SWAP instrument endpoint exposes only live instruments, while the official historical-data web service exposes 595 historical swap families and includes delisted examples such as CAT, WSM and WAVES.
- Confirmed that ordinary history-candles requests reject delisted CAT with code 51001, but the official historical download service still generates CAT-USDT-SWAP daily candlestick archives through its delisting date. The archive contains confirmed one-minute OHLCV rows suitable for causal 4-hour aggregation.
- Froze a point-in-time product filter for the pilot: on each UTC date retain a base only when both BASE-USDT-SWAP and BASE-USDT spot appear in that same day's official archives. Spot prices are not used as a signal; same-day spot existence is only a product-classification field that removes equity X-Perps and most pre-market-only swaps without a present-day hand-selected token list.
- Added and ran the research-only `dynamic_universe_pilot_v1/run_dynamic_universe_pilot.py`. It fetches official daily archives into memory, retries transient network errors, verifies archive hashes, keeps only confirmed rows, creates the same-day spot/swap intersection and aggregates swaps to UTC-aligned 4-hour bars without persisting raw zip or one-minute CSV files.
- The three-date end-to-end pilot passed. On 2023-07-01 the eligible universe was 91 instruments and produced 546 complete 4-hour rows; on 2025-01-01 it was 204 instruments and produced 1,224 rows; on 2026-06-15 it was 209 instruments and produced 1,254 rows. Every eligible instrument produced all six expected daily 4-hour bars and no incomplete instrument remained.
- The 2026 probe observed 360 USDT swaps but only 209 same-day USDT spot/swap intersections; the 151 automatically excluded swap-only products included AAPL, ADBE, AMD, AMZN, ANTHROPIC, ARM, ASML and AVGO. This confirms that point-in-time spot intersection removes the new equity/per-market expansion mechanically.
- Estimated the full 2023-07-01 through 2026-06-16 source transfer at roughly 13-22 GB. The retained 4-hour panel is expected to remain lightweight if archives are streamed, hashed, aggregated and discarded day by day.
### Evidence and artifacts
- Added `THIRTY_NINTH_WAVE_DYNAMIC_UNIVERSE_FEASIBILITY_20260628.json` and the matching Chinese report.
- Added the research-only reproducible pilot under `dynamic_universe_pilot_v1`.
- Updated `docs/PROJECT_OVERVIEW_CN.md` with the positive feasibility result and the remaining pre-outcome policy gates.
### Decision
- Status: `PILOT_PASS_DYNAMIC_POINT_IN_TIME_UNIVERSE_RECONSTRUCTABLE`.
- The dynamic-universe route survives technical feasibility, delisted-history access, point-in-time classification and 4-hour aggregation gates. It is not yet a new Alpha candidate and no strategy PnL or sealed holdout was opened.
- Before a full run, freeze minimum listing age, prior-volume liquidity eligibility and stablecoin/pegged-asset exclusions without outcomes. Then run a continuous 30-day streaming reliability pilot that persists only 4-hour Parquet and daily universe manifests.
### Change-control boundary
- No application strategy, runtime configuration, formal signal, A-grade, Feishu, leverage, account, position, order or approved-manifest behavior changed.
- Application version remains `3.56.28`. The new script is isolated research tooling; no version bump, Git commit or GitHub push is required.
### Current recovery point
- Continue H22, V357 and H27 under their existing frozen forward protocols.
- Next research step is the return-blind universe-policy freeze and 30-day streaming pilot. H22 formation, hysteresis, staggered cohorts, cadence and cost model must remain unchanged when the later survivorship-bias audit is opened.

## 2026-06-28 - Task: fortieth-wave H22 dynamic-universe survivorship audit
### What was done
- Completed the full official OKX dynamic-universe build for the Asia/Shanghai source-calendar interval 2023-07-01 through 2026-06-16. The retained UTC-timestamped panel contains 1,082 daily Parquet files, 1,203,424 rows and 307 historical USDT swaps; raw one-minute archives were streamed and discarded.
- Corrected the source-calendar interpretation before final acceptance. OKX daily archives are partitioned by Asia/Shanghai day, so each source date contains UTC opens at the prior 16:00/20:00 and current 00:00/04:00/08:00/12:00. All 1,082 Parquet hashes were recomputed after this correction; date coverage, hashes, 240-minute completeness, OHLCV validity and global symbol/timestamp uniqueness passed with zero hard failures.
- Froze the H22 point-in-time policy before opening returns: require 85 consecutive closed four-hour bars, rank by trailing 84-bar quote volume, retain the top 18 eligible instruments, exclude USDC and XAUT, and keep the parent 84-bar momentum, 4-in/6-out hysteresis, three staggered cohorts, three-day cadence, 04:00 UTC entry and 0.4 gross exposure unchanged.
- The first attempted run stopped before any return simulation because ETC was not consecutively point-in-time spot-eligible at one date. The fixed survivor baseline was therefore rebuilt from the parent H22 closed 15-minute source while the dynamic panel remained on the validated point-in-time dataset. Over 116,855 overlapping fixed-symbol bars, close prices were effectively identical; isolated open differences reached at most 6.12 bp and cannot explain the final performance gap.
- Opened the frozen audit. The fixed 18-survivor panel remained weakly positive only under base reserves: PF 1.0970, total return +15.81%, maximum drawdown -10.00%; stress PF was 0.9863 with -3.48% return and -16.87% drawdown.
- The point-in-time dynamic 18 panel failed decisively: base PF 0.9247, win rate 48.38%, payoff 0.9867, return -26.74% and drawdown -38.93%; stress PF 0.8730, return -39.75% and drawdown -47.71%. All three historical segments were negative under both cost levels.
- The failure was not a concentration artifact. Maximum positive symbol share was 11.36% and maximum positive month share was 16.28%, both below the frozen 25% ceilings. The dynamic universe used 174 instruments, overlapped the fixed panel by only 7.59 symbols on average, changed on 550 signal days, caused 851 causal decision-time exits and included two terminal delisting exits.
### Evidence and artifacts
- Added `FORTIETH_WAVE_H22_DYNAMIC_UNIVERSE_SURVIVORSHIP_AUDIT_20260628.json` and the matching Chinese report.
- Added the locked protocol, reproducible audit, result, Chinese report and hashes under `h22_dynamic_universe_survivorship_audit_v1`.
- Generated `DATASET_MANIFEST.json`, `DATA_QUALITY_REPORT.json` and `DATA_QUALITY_REPORT_CN.md` under the validated dynamic-universe dataset and corrected its README timezone definition.
- Archived the failed audit in the desktop failed-strategy folder with the decision, metrics and evidence paths.
### Decision
- Status: `SURVIVORSHIP_DEPENDENT_HISTORICAL_SUPPORT_REJECTED_NO_RESCUE`.
- H22 historical support is now rejected as dependent on the current survivor list. Do not rescue it by changing the top-18 count, liquidity window, listing-age rule, stablecoin exclusions, long/short balance, cadence, ranks or dates.
- The already frozen H22 forward shadow may continue only as prospective observation of today's mature fixed pool. It cannot use the old historical evidence for promotion, A-grade status or automation. V357 and H27 remain independent and continue under their own frozen prospective protocols.
### Change-control boundary
- No application strategy, runtime signal configuration, formal signal, A-grade, Feishu, leverage, account, position, order or approved-manifest behavior changed.
- The machine research family registry previously still classified H22 as historically supported, contradicting the completed survivorship audit. That formal research-gate configuration is corrected in v3.56.29.
### Release completion
- Bumped application/package metadata and release documentation to `3.56.29`; approved strategy remains `3.56.15` and strict research identity remains `v3.56-strict`.
- Added a machine regression test requiring H22 to remain `historical_support_rejected_survivorship_dependent_forward_observation_only` with the frozen failure metrics and no-rescue boundary.
- H22 registry and release-safety targeted tests: 42 passed. Full Python suite: 467 collected, 449 passed, 18 skipped because `JIAOYI_DATA_DIR` is not configured, 0 failed.
- `system_check.py source --json` passed for version 3.56.29, 264 unique release files, 21 configured swaps, 23 registered research families and 30 failure fingerprints.
- A real H19 research-gate run correctly passed data/leakage/freedom checks and rejected the already registered duplicate by structural and alias gates.
- Change governance passed; the versioned release ZIP and SHA-256 sidecar were rebuilt and verified.
- Functional release commit `a13f2689` was pushed to `origin/master`, and the required remote-sync governance check confirmed local HEAD and upstream were identical with a clean project directory.
### Current recovery point
- Treat H22 as `FORWARD_OBSERVATION_ONLY_HISTORICAL_SUPPORT_REJECTED_SURVIVORSHIP_DEPENDENT` and continue recording its frozen prospective shadow without early promotion or parameter rescue.
- Continue V357 and H27 under their existing frozen forward protocols and resume the independent mechanism funnel. Do not reuse fixed-survivor historical evidence as proof for another cross-sectional strategy without a point-in-time universe audit.

## 2026-06-28 - Task: v3.56.30 dashboard 5-minute backfill recovery
### What was found
- The 15-minute core closed-candle backfill, 21-symbol WebSocket coverage, signal scanner and shadow ensemble were live. A temporary OKX REST failure left HYPE and BCH one 15-minute bar behind, and the next scheduled cycle automatically repaired both with zero write failures and zero internal gaps.
- The auxiliary 5-minute status file had not changed since 2026-06-18. Most 5-minute runtime caches ended on 2026-06-18 and five ended on 2026-06-21, leaving roughly 2,106 to 2,854 missing tail bars per symbol.
- The primary root cause was runtime wiring: the desktop application uses the GUI path, while the existing 5-minute service was started only by the CLI signal loop. GUI background tasks launched the 15-minute service and outbox but never launched the 5-minute service.
- A secondary scalability issue existed after reconnection: merging the latest 300 bars into a stale file created a multi-day internal gap, which the old code attempted to repair serially for every symbol before writing a new status cycle.
- Initial live recovery proved the GUI wiring and latest-window rebuild worked, but transient OKX REST timeouts could still leave one or two symbols lagging until the next five-minute cycle.
### What was changed
- Added `replace_with_latest_window` support to `ClosedCandleBackfillService`. The 5-minute auxiliary cache now atomically replaces stale content with the latest contiguous confirmed window instead of backfilling thousands of nonessential dashboard-only bars.
- Added `latest_window_rebuilt` and `attempts` to per-symbol status for observable recovery evidence.
- Added bounded per-symbol retry controls. The 5-minute GUI and CLI services use at most three attempts with a one-second delay; the 15-minute core service keeps the default single request per scheduled cycle and its strict internal-gap repair behavior.
- Added `_run_dashboard_5m_backfill_once` and `_dashboard_5m_backfill_loop` to the GUI and included the task in the live background task list. A 5-minute failure is reported as auxiliary degradation and cannot stop a healthy 15-minute core chain.
- Added nonblocking runtime health checks for 5-minute status freshness, completion/write failures and configured 21-symbol coverage.
- Bumped application/package metadata and release documentation to `3.56.30`; approved strategy remains `3.56.15` and strict research identity remains `v3.56-strict`.
### Verification
- Four focused regressions passed: stale latest-window rebuild without long-gap repair, transient single-symbol retry within one cycle, GUI task wiring/configuration and stale-status health warning.
- Full Python suite: 471 collected, 453 passed, 18 skipped because external historical data is not configured, 0 failed.
- Python compilation, source audit and change-governance checks passed. Source audit reports version 3.56.30, 265 unique released files, 21 configured swaps, 23 research families and 30 failure fingerprints.
- Restarted the desktop runtime with a single clean process chain. The final 5-minute cycle completed all 21 symbols with zero write failures and zero internal gaps.
- Directly inspected all 21 runtime-cache Parquet files: each contains 299 unique contiguous closed 5-minute bars, all terminate at `2026-06-28T14:00:00+00:00`, and no file has an internal gap.
- `scripts/system_check.py runtime --json` returned `ok=true`; 5-minute freshness, completeness and symbol coverage all passed, while the 15-minute core backfill, WebSocket, signal status, outbox and 21-symbol coverage remained healthy.
### Safety and scope
- No strategy parameters, formal A-grade gate, approved manifest, Feishu policy, leverage, account, position or order behavior changed.
- Formal multi-year historical data remains read-only and was not modified. Only the writable runtime 5-minute dashboard cache is rebuilt.
- The system remains signal-only with live orders and automatic closing disabled.
### Current recovery point
- Keep the desktop runtime running under v3.56.30. The 5-minute dashboard cache should refresh after each confirmed five-minute close; any stale, incomplete or partial-coverage state is now visible in `system_check.py runtime` instead of being silently hidden.
- Continue H22 only as forward observation with rejected survivor-dependent history, and continue V357/H27 under their frozen prospective protocols.

## 2026-06-28 - Task: V357 point-in-time dynamic-universe survivorship audit and v3.56.31 research-gate correction
### What was done
- Froze the V357 audit before outcomes. Both released members, EMA/Donchian/relative-strength/breadth/volume rules, ATR stops, holding periods, entry timing, cost reserves and 0.5 percent per-trade portfolio-risk normalization remained unchanged.
- Replaced the fixed mature-survivor list with a causal top-18 point-in-time liquidity panel. Eligibility required 180 consecutive closed four-hour bars, all latest 84 quote-volume bars strictly positive, a next four-hour open and exclusion of USDC/XAUT. The top 12 within the panel supplied the original cross-sectional relative-strength and breadth references.
- Built 5,391 dynamic decision panels across 154 historical contracts. Eligible-count range was 144 to 214 with a median of 192. The dynamic top 18 overlapped the fixed 18 by only 7.60 symbols on average and changed on 3,171 four-hour bars.
- Replayed 1,896 comparable dynamic trades against 1,837 fixed-survivor trades over 896 common daily periods. Only three dynamic trades required terminal-data exits, so the result is not driven by missing delisted data.
- The fixed panel had base PF 1.1598, return +59.82 percent and drawdown -51.41 percent; stress PF 1.0050, compounded return -13.94 percent and drawdown -61.54 percent.
- The point-in-time dynamic panel failed: base PF 1.0552, return +6.78 percent and drawdown -35.06 percent; stress PF 0.9386, return -32.36 percent and drawdown -42.47 percent. The middle segment had base/stress PF 0.8534/0.7622.
- Maximum positive symbol and month contribution shares were 20.79 and 18.17 percent, so the failure is not a single-symbol or single-month concentration artifact.
- Verified the vectorized rolling ATR-percentile implementation against the released runtime feature builder on BTC; all audited feature columns matched with zero maximum absolute difference.
### Evidence and artifacts
- Added the locked protocol, reproducible audit script, machine result, Chinese report, concise failure note and SHA-256 manifest under `v357_dynamic_universe_survivorship_audit_v1`.
- Archived `V357_动态币池存活者偏差失败_20260628.md` to the desktop failed-strategy folder.
- The candidate files and their frozen SHA-256 values were not modified.
### Decision
- Status: `V357_SURVIVORSHIP_DEPENDENT_HISTORICAL_SUPPORT_REJECTED_NO_RESCUE`.
- V357 fixed mature-survivor history is no longer valid promotion evidence. The existing frozen runtime may continue only as fully prospective research observation.
- Do not rescue the family by changing either member, panel size, reference size, liquidity window, minimum history, parameters, stops, holding periods, costs, symbols, dates or direction.
### Machine-state correction
- Updated the existing `4h_donchian_volatility_compression` registry entry from `shadow_reference` to `historical_support_rejected_survivorship_dependent_forward_observation_only`.
- Registered both V357 candidate IDs and both member aliases so renamed candidates cannot reuse the rejected fixed-survivor history.
- Added a regression test pinning the rejected status, failure metrics and no-rescue boundary.
- H27 remains a pure prospective diversification observation and may not repackage H22 or V357 fixed-survivor history as combination support.
### Release boundary
- Bumped application/package metadata and release documentation to `3.56.31`; approved strategy remains `3.56.15` and strict research identity remains `v3.56-strict`.
- No frozen V357 rule, formal signal, A-grade strategy, Feishu policy, leverage, account, position or order behavior changed. The system remains signal-only.
### Current recovery point
- Continue H22 and V357 only as frozen fully prospective observations with survivor-dependent historical support rejected.
- Continue H27 only as record-only forward diversification evidence.
- Resume the independent mechanism funnel using the validated point-in-time dynamic universe; every future cross-sectional candidate must pass the same survivorship audit before historical support can be claimed.

## 2026-06-29 - Task: fixed-21 mature-universe mandate and v3.56.32 candidate-scope correction
### User mandate clarified
- The operator explicitly froze the intended future trading scope to the 21 mature OKX USDT swaps already configured in `config/base.yaml`. Other current, historical, delisted or future contracts are outside the strategy mandate.
- The prior 307-contract point-in-time audits therefore answer a different question: whether H22 or V357 generalizes to a broad changing market. They do not directly answer whether either frozen candidate can work inside the operator-selected fixed 21.
### Machine-state correction
- Added `config/research_universe_policy.json` with the exact 21 symbols, no outcome-driven additions/removals, prospective replacement governance and an explicit prohibition on claiming broad-market portability from fixed-21 evidence.
- Reclassified H22 and V357 as `fixed_21_scope_candidate_forward_validation_pending`. Their parameters, directions, costs, dates, entry timing, eligible-subset rules and existing forward ledgers remain unchanged.
- Reclassified the dynamic-universe results as `out_of_scope_generalization_stress_test`. H22 dynamic PF 0.9247/0.8730 and V357 dynamic PF 1.0552/0.9386 remain valid warnings against dynamic-universe deployment or broad-market claims, but no longer disqualify the fixed-21 mandate.
- Kept H27 as `record_only_forward_diversification_observation`. It is not a third independent Alpha and may not search weights or promote the two parent candidates.
### Evidence standard
- This is a scope correction, not a parameter or sample rescue. Because the 21 projects are known mature survivors as of the policy date, their old fixed-history results remain conditional rather than decisive.
- H22 must still complete its original 60-day/50-observation stage and preferred 90-day/80-observation review. V357 must still complete its original frozen time, trade-count, cost, drawdown and concentration gates. Neither is approved or A-grade now.
### Release boundary
- Bumped application/package metadata and documentation to `3.56.32`; approved strategy remains `3.56.15` and strict research identity remains `v3.56-strict`.
- Added machine tests that require the policy symbols to match the runtime 21 exactly and pin H22/V357/H27 to their corrected roles.
- The 44 focused release/research tests passed; the complete Python suite passed with only the expected external-data skips. Source audit and change governance both returned `ok=true`, and the v3.56.32 release ZIP plus SHA-256 sidecar were built successfully.
- No formal signal, Feishu policy, leverage, account, position, order or automatic-trading behavior changed. The system remains `SIGNAL_ONLY`.
### Current recovery point
- Continue H22 and V357 under their unchanged frozen prospective protocols as two fixed-21 conditional candidates.
- Continue H27 only as a record-only diversification observer.
- Use the 307-contract dataset only for portability diagnostics or separately declared broad-market research; do not use it to eliminate a fixed-21 candidate solely because out-of-mandate contracts perform differently.

## 2026-06-29 - Task: prioritize H22 and V357 observation and v3.56.33
### Priority directive
- The operator designated H22 and V357 as the primary observation targets.
- Machine priority is now frozen as `H22 -> V357 -> H27`. H27 remains a dependent record-only observer and may run only after both source ledgers are current.
- The original 14-day momentum reference and fixed three-day refresh track remain available as secondary references. Independent mechanism intake continues in parallel but cannot displace, delay or tune H22/V357.
### Configuration changes
- Added machine-readable observation priority to `config/research_universe_policy.json`, including the primary candidates, update order, H27 source-ledger dependency and a prohibition on using priority to change parameters or rules.
- Reordered `config/parallel_acceptance.yaml` so H22 is updated first and V357 second. Secondary tracks follow afterward.
- Added a regression test requiring the first two configured tracks to remain H22 and V357 and pinning the H27 dependency.
### Current observed H22 sample
- The H22 updater reached closed data through `2026-06-29T04:45:00Z`, with 3 fully prospective rebalances, 2 closed observations and 1 active observation. All protocol, ledger, data-quality and snapshot-chain checks pass.
- The H22 sample remains far below its 60-day/50-observation stage gate, so all displayed PF, return and concentration values remain non-decisive and must not trigger early judgment.
### Release boundary
- Bumped package and documentation to `3.56.33`; approved strategy remains `3.56.15` and strict research identity remains `v3.56-strict`.
- Sixty focused priority/research/release tests passed. The complete Python suite passed with only the expected external-data skips. Source audit and change governance returned `ok=true`, and the v3.56.33 release ZIP plus SHA-256 sidecar were built successfully.
- No H22 or V357 parameter, signal, cost, position, leverage, Feishu, account, order or automatic-trading behavior changed. The system remains `SIGNAL_ONLY`.
### Current recovery point
- Refresh and inspect H22 first, V357 second, and H27 only after both source ledgers are current.
- Do not evaluate either primary candidate before its frozen sample gates are due.
- Keep new strategy research active as a secondary parallel line.

## 2026-06-29 - Task: next-step forward refresh and existing-data mechanism boundary audit
### Forward evidence refresh
- Updated H22 first. Closed data now reaches `2026-06-29T06:00:00Z`; the ledger has 6 fully prospective rebalances, 5 closed observations and 1 active observation for both the original and 4-in-6-out variants. Data quality and the daily snapshot hash chain pass. The sample remains `NOT_EVALUATED_SAMPLE_INCOMPLETE`, with 44 rebalances and 55 closed-data days still required for the minimum gate.
- Updated the frozen V357 runtime and acceptance adapter second. Coverage remains 21/21 symbols, elapsed closed-data time is 7 days and there are 6 fully prospective observations. The Donchian member has 2 closed and 4 active observations; VCB has no eligible forward observation yet. The sample remains incomplete and no automatic promotion is allowed.
- Updated H27 only after both source ledgers. It has 4 common daily rows, 2 usable H22 closed observations and 2 V357 closed trades. It remains `RECORD_ONLY_SAMPLE_INCOMPLETE`; correlations and weight selection are unavailable.
### Existing local numeric-field audit
- Confirmed 21-symbol mark-price and index-price four-hour histories are present through `2026-06-29T04:00:00Z`, with 179,066 and 179,538 rows respectively. These values do not reopen H11/H12, basis convergence, execution-mismatch or liquidation-trigger families.
- Confirmed the long official funding archive still covers 18 mature symbols from 2022-06 through 2026-05; the runtime 21-symbol funding table has 6,295 rows from 2026-03-21 through 2026-06-29. Carry, persistence, crowding reversal and settlement families remain closed without smoothing, threshold or price/OI rescue variants.
- Confirmed the runtime fixed-21 open-interest table contains only 57 hourly points per symbol (`2026-06-24T13:00:00Z` through `2026-06-29T06:00:00Z`). The legacy native-OKX CSV fragments have no complete common 16/18/21-symbol hourly or daily cross-section.
- Clarified that the Stage 4 result showing about 908 common daily OI timestamps was an online coverage probe. It retained timestamp summaries, not the underlying historical OI values, so it is not admissible for an existing-local-data-only signal backtest.
### Recent primary-research screen
- The updated asset-pricing survey continues to identify size, two-week momentum and network value as the core cross-sectional factors. Two-week momentum is already H22; size and network value require point-in-time market-cap and on-chain address histories not present locally.
- Recent hidden-factor work relies on equity-industry factors, sentiment, speculative-rotation indices and security shocks. Recent explainable microstructure work relies on one-second L2/trades, CatBoost and maker/taker execution. These routes violate the local-only, low-complexity or no-continuous-L2 boundaries and map to already closed families.
### Decision and recovery point
- No new independent candidate, PnL protocol or forward shadow was created. The current local dataset has no unused numeric field with sufficient history, unique direction, observable payer and cost capacity.
- Continue H22 and V357 frozen forward evidence and H27 record-only observation. Allow fixed-21 OI and funding values to accumulate naturally without predefining rescue strategies.
- Reopen the independent candidate funnel only after a genuinely new locally stored numeric field has sufficient point-in-time history and passes payer, unique-direction, cost-ceiling and global-duplicate gates.
- This round changed research outputs and documentation only. Application code, runtime configuration and version remain `3.56.33`; no Git commit or GitHub push is required.

## 2026-06-29 - Task: complete fixed-21 OKX daily open-interest history
### Data completion
- Added a resumable research-only downloader under `HISTORY_PACKAGES_20260621/RESEARCH/local_only_hypothesis_discovery_v1/okx_fixed21_daily_oi_history_v1/`.
- Downloaded actual daily OI values for all 21 configured OKX USDT swaps from the official public contract open-interest history endpoint. The data are isolated under `历史数据_保留/imports/okx_open_interest_history` and are not mixed with the legacy Binance/fragmented OI directory.
- Stored per-symbol CSV and Parquet, a unified long panel, USD/contract/coin wide panels, a machine manifest, Chinese coverage report, README, resumable state and SHA-256 manifest.
- Final long table contains 18,711 rows across 21 symbols. The complete data package contains 51 files and occupies about 7.28 MiB.
### Coverage and quality
- The exact fixed-21 common panel contains 491 daily timestamps from `2025-02-21T16:00:00Z` through `2026-06-28T16:00:00Z`, limited by HYPE history.
- The mature-18 common panel contains 910 daily timestamps from `2023-12-31T16:00:00Z` through `2026-06-28T16:00:00Z`.
- HYPE contains 493 daily rows beginning `2025-02-21T16:00:00Z`.
- OP is missing `2025-08-25T16:00:00Z` and ARB is missing `2026-06-26T16:00:00Z`. Targeted official queries skipped those dates directly, so they remain missing with no interpolation or forward fill.
- All per-symbol structural checks, unified-panel schema checks and SHA-256 file checks passed. No cross-exchange OI, synthetic values or pre-listing backfill were used.
### Research and release boundary
- The earlier statement that Stage 4 only had timestamp coverage and no local daily OI values is now superseded. Fixed-21 daily OI is a valid local research data asset.
- No strategy was created and no PnL was opened. The next permitted action is a return-blind causality and global-family deduplication audit against FP12, ordinary momentum, reversal and liquidation-proxy families.
- OI may not be added as a post-hoc filter to H22 or V357. Both frozen candidates remain unchanged.
- Application code, runtime configuration and version remain `3.56.33`; no Git commit or GitHub push is required for this research-data completion.
### Current recovery point
- Continue H22 and V357 forward evidence first, then H27 record-only observation.
- The next independent research step is the fixed-21 daily-OI pre-PnL screen. Do not open returns until payer, unique direction, causal timing, data coverage, turnover, concentration and family-deduplication gates pass.

## 2026-06-29 - Task: fixed-21 daily-OI return-blind causality and deduplication audit
### Frozen audit boundary
- Locked a research-only protocol before structural results: user-fixed 21 OKX USDT swaps as the primary universe, mature 18 symbols as a robustness universe, Sunday 16:00 UTC weekly decisions, a seven-day primary window, three-day/fourteen-day neighbors and equal 4-long/4-short weights for structure only.
- Future returns, PnL, calibration, validation and sealed history remained closed. No H22, V357 or H27 rule could be changed or filtered by OI.
- Enforced strictly prior closed-hour price alignment. The audit used 18,369 causal price points, used zero equal-time/future points and rejected 336 stale end-tail joins. OI-only routes continued through 2026-06-28; price-linked routes stopped at the last complete weekly decision on 2026-06-07.
### Numeraire and route results
- Contract-count and coin-denominated OI changes are effectively identical scalings in the fixed swaps. USD OI is not independent: seven-day `log USD OI change - log coin OI change` correlates `0.99996` with the seven-day underlying price change.
- Coin-denominated OI growth produced 130 weekly structures but had 86.34% one-way turnover, failed three-day/fourteen-day stability and could not identify a signed entrant or unique continuation/reversal direction.
- OI-share migration was an exact ranking duplicate of raw OI growth: weight correlation `1.0` and same-side overlap `100%`.
- Price-confirmed OI expansion had 77.88% turnover and was a hard duplicate of seven-day price momentum (`0.6476` weight correlation, `65.94%` same-side overlap). The mature-18 overlap remained `67.03%`.
- OI-expansion reversal used the same unsigned field but selected the opposite explanation, so direction was not identifiable before outcomes.
- Deleveraging continuation had 79.07% turnover, weak neighbor stability and duplicated seven-day momentum (`0.5541` correlation, `58.17%` overlap); OI contraction cannot distinguish continuing liquidation from completed exhaustion.
- USD-OI growth had 85.37% turnover and duplicated coin-OI growth (`0.7510` correlation, `75.19%` overlap) in addition to its mechanical price contamination.
### Decision and recovery point
- All six routes stopped before PnL. Decision: `NO_INDEPENDENT_OI_CANDIDATE_PASSES_PRE_PNL`; new formal C/H candidates remain zero.
- No desktop failed-strategy folder was created because none of the routes became a formal executable candidate. Their protocol, script, compact result, Chinese report and hashes remain in `fixed21_daily_oi_return_blind_audit_v1/` as negative evidence.
- Continue H22 and V357 under their unchanged frozen forward protocols and keep H27 record-only. Daily OI is diagnostic only; higher-frequency OI may accumulate passively without rescue rules.
- This round changed research utilities, artifacts and documentation only. Application code, runtime configuration and application version remain `3.56.33`; no version bump, Git commit or GitHub push was performed.

## 2026-06-29 - Task: ordered forward refresh and M01 point-in-time market-cap data gate
### Ordered forward refresh
- Refreshed H22 first. The validated frozen ledger now reaches `2026-06-29T07:30:00Z` with exactly 3 fully prospective rebalances, 2 closed observations and 1 active observation; protocol, ledger, data-quality and snapshot-chain integrity all pass. The earlier local summary reporting 6 total and 5 closed observations was incorrect and is superseded by the actual three dated evidence snapshots.
- Refreshed V357 second. The first attempt correctly stopped at `frozen_reference_bar_misaligned` because the LTC 15-minute runtime cache lagged the other frozen reference symbols. Causally backfilled four closed LTC bars through `2026-06-29T08:30:00Z`, then reran successfully with 21/21 coverage, 7 closed-data days and 6 fully prospective observations: 2 closed and 4 active. No strategy rule changed.
- Updated H27 only after both source ledgers. It now has 4 common daily rows, 2 H22 closed observations and 2 V357 closed trades, and remains `RECORD_ONLY_SAMPLE_INCOMPLETE`; correlation and weight-selection decisions remain unavailable.
### M01 data feasibility and provenance
- Froze a data-only protocol before downloading any market-cap history. Future returns, PnL, factor direction, rank counts, rebalance cadence and no-trade bands remained unopened.
- Downloaded a small Coin Metrics Community `CapMrktEstUSD` daily snapshot for the fixed 21. Twenty symbols cover every day from `2023-06-15` through `2026-06-28`; HYPE covers its natural history from `2024-12-13`. The snapshot contains 22,763 valid rows, zero gaps, duplicates, non-midnight timestamps or nonpositive values, occupies about 3.5 MB and has panel SHA-256 `caacbb5b6daccdc8c93e1f32c36e00772f9a5eb2b0a4ed62902676c8bcab7084`.
- Coverage alone was not accepted as point-in-time validity. `CapMrktEstUSD` combines recalculable reference prices with project self-reported circulating-supply estimates sourced through CoinGecko, and no immutable historical vintage/as-of archive was established. The chain-native `CapMrktCurUSD` alternative has only 10 fixed symbols with usable coverage over the required window and is not economically equivalent to circulating/free-float market capitalization.
- Free CoinGecko and CoinPaprika histories are too short; adequate CoinMarketCap, CoinPaprika or Messari bulk history requires payment and still did not establish immutable historical vintages. M01 therefore stopped before factor-structure testing or PnL as `STOP_M01_BEFORE_FACTOR_AUDIT_NO_FREE_STRICT_POINT_IN_TIME_SOURCE`.
### Decision and recovery point
- Formal new-candidate count remains zero. The downloaded market-cap panel is diagnostic and a future revision anchor only; it may not be merged into a causal backtest or added to H22/V357.
- Continue the frozen order `H22 -> V357 -> H27` when new closed data arrives. Reopen M01 only if at least 18 fixed symbols obtain auditable historical circulating/free-float market-cap vintages that match each decision-time information set.
- Application source, formal configuration, signal behavior and version remain `3.56.33`; no version bump, commit or GitHub push was performed.

## 2026-06-29 - Task: fixed-21 free on-chain field coverage audit
### Frozen data-only boundary
- Locked the fixed 21 symbols, canonical chain/token mappings, 2023-06-15 start date, 18-symbol minimum, free-access requirement, same-economic-scope rule, revision/vintage requirement and a prohibition on reading returns or PnL before the full catalog audit.
- Disclosed that small public catalog probes had been used only to verify endpoint syntax and exact project slugs before the full protocol. No OKX future return, strategy outcome or PnL was queried or merged.
- Prevented ticker-only matching: UNI was pinned to the Ethereum contract, ARB and OP to their governance-token contracts, and BTC/ETH ETF or bridged same-ticker projects were excluded.
### Public catalog results
- Queried Coin Metrics Community, Santiment SANAPI FREE and DefiLlama Free catalogs across 21 field routes. Santiment project queries completed for all 21 fixed symbols after retry handling; no unresolved source-query error remains.
- Coin Metrics `AdrActCnt`, `TxCnt` and `TxTfrCnt` each reach only 12 raw symbols, 11 with the required catalog window and 9 economically comparable full-window symbols. DOT community history ends in 2022-06-03. Coin Metrics free exchange inflow/outflow fields reach only BTC and ETH.
- Santiment `active_addresses_24h` reaches 14 raw symbols but only 10 under the frozen comparable-scope rule. `daily_active_addresses` reaches 12. Transaction volume reaches 14 raw symbols, while exchange inflow/outflow reaches 6 and has zero unrestricted full-history symbols on the FREE plan.
- DefiLlama chain catalog is the broadest raw route at 16 symbols, but only 14 are economically comparable. LINK and UNI are token contracts; Arbitrum and Optimism network activity is not equivalent to ARB and OP governance-token demand. Chain TVL, DEX volume and fees therefore cannot form a fixed-21 asset factor.
### Decision and recovery point
- None of the 21 routes reaches the frozen 18-symbol free coverage gate. Decision: `NO_FREE_FIELD_REACHES_FIXED21_COVERAGE_GATE`.
- The audit stopped before bulk historical timeseries download, rank construction, turnover, correlations, PnL, PF, win rate or drawdown. No formal candidate or desktop failed-strategy folder was created because no executable candidate formed.
- Do not reopen active-address, network-growth, transaction-count, transaction-volume, chain-fee, chain-TVL, DEX-volume or exchange-flow routes merely by switching provider names. Reopen only if one source reaches at least 18 comparable fixed symbols with the required history and auditable metric/wallet-label vintages.
- Continue `H22 -> V357 -> H27` under their unchanged frozen protocols. Application source, runtime configuration, signal behavior and version remain `3.56.33`; no version bump, commit or GitHub push was performed.

## 2026-06-29 - Task: post-audit ordered forward refresh
- Ran H22 first and advanced closed-data availability from 07:30 UTC to 09:45 UTC. No new scheduled rebalance was due, so the evidence count remains exactly 3 fully prospective rebalances, 2 closed observations and 1 active observation. Protocol, ledger, data quality and snapshot-chain integrity remain PASS.
- Ran V357 second. It remains at 21/21 eligible symbols, 7 closed-data days and 6 fully prospective observations: 2 closed and 4 active. No rule, member, parameter, stop or holding period changed.
- Recomputed H27 last. It remains record-only with 4 common daily rows, 2 H22 closed observations and 2 V357 closed trades; correlations and weight selection remain unavailable.
- The next scheduled H22 refresh and entry is `2026-06-30T04:00:00Z` (`2026-06-30 13:00` Japan time). Until that point, repeated refreshes can update data freshness but cannot create a new H22 rebalance sample.
- Application source, formal configuration, signal behavior and version remain `3.56.33`; no version bump, commit or GitHub push was performed.

## 2026-06-29 - Task: allow profitable candidate-specific subsets and v3.56.34
### Policy correction
- Replaced the mandatory-breadth interpretation with an allowed-pool interpretation: the declared 21 mature OKX USDT swaps remain the maximum permitted trading pool, but a new strategy may prospectively freeze any subset from one to 21 symbols. Profitability and robustness are objectives; symbol count is not.
- Outcome-driven membership remains forbidden. A candidate must list its exact symbols and selection basis before PnL, may not test all 21 and retain winners, may not delete losing members after results, and may not use the new policy to redesign H22 or V357.
### Machine gates
- Upgraded `config/research_universe_policy.json` to v2 and the pre-PnL template to require an explicit candidate subset, full data coverage for that subset, a pre-PnL selection basis and declarations that no legacy outcomes selected the symbols.
- Extended `scripts/system_check.py` to reject empty, duplicate, out-of-pool or outcome-selected subsets and to reject sample trades in undeclared symbols.
- Cross-symbol concentration now depends on frozen subset size. One-to-five-symbol candidates no longer fail mechanically because one symbol exceeds 25% of positive contribution; six-to-21-symbol candidates retain the 25% gate. Time splits, market regimes, month/trade concentration, effective positive trades, costs, future leakage, family deduplication and forward evidence remain mandatory for every size.
- Added focused tests proving that a BTC-only candidate can pass the subset gate before PnL, while the 25% cross-symbol contribution gate reactivates at six symbols. All focused research-automation tests pass.
### Historical evidence interpretation
- Preserved the original free on-chain catalog audit. Its `NO_FREE_FIELD_REACHES_FIXED21_COVERAGE_GATE` result still rejects a uniform 18-plus-symbol on-chain cross-section, but no longer globally rejects BTC-only, BTC/ETH or naturally defined small subsets.
- Added `SUPERSESSION_20260629_CANDIDATE_SUBSET_POLICY.json`. Reopening any narrow route requires a new return-blind protocol and cannot use historical outcomes to choose members; wallet-label vintages, field revisions and chain-versus-token semantics remain binding.
### Release boundary
- Bumped package and release documentation to `3.56.34`. Approved strategy identity remains `3.56.15`, strict research identity remains `v3.56-strict`, and the application remains signal-only with no automatic ordering or promotion.
- Focused release/research tests passed, the complete Python suite passed with only expected data-dependent skips, unified source audit passed, and change governance returned `ok=true`.
- Built `dist/okx-contract-signal-system-v3.56.34.zip` with its SHA-256 sidecar. Repository synchronization is verified separately by the Git governance check.
