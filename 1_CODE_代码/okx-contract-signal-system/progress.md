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
