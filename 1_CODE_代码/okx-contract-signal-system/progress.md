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
