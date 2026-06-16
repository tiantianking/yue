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
