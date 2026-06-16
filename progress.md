## 2026-06-16 - Task: Add Correlation-Aware Signal Tiers
### What was done
- Added Phase 3 correlation-aware tiering so highly correlated ready candidates cannot occupy multiple A-tier slots in the same closed-candle cycle.
- Kept the total A-tier cap at two and retained demoted ready candidates as B-tier.
- Connected realtime and GUI scans to pass current closed K-line history into tier selection.
- Bumped version metadata to v3.31.
### Testing
- `D:\JIAOYI-CX\LOCAL_DEPS\venv\Scripts\python.exe -m compileall gui.py main.py src` passed.
- `D:\JIAOYI-CX\LOCAL_DEPS\venv\Scripts\python.exe -m pytest tests/test_signal_quality.py` passed, 4 tests.
### Notes
- `1_CODE_代码/okx-contract-signal-system/src/okx_signal_system/signal_quality/correlation.py`: added rolling return correlation grouping.
- `1_CODE_代码/okx-contract-signal-system/src/okx_signal_system/signal_quality/selector.py`: enforced one A-tier candidate per correlation group.
- `1_CODE_代码/okx-contract-signal-system/src/okx_signal_system/signal_quality/candidate.py`: added correlation group metadata to candidates.
- `1_CODE_代码/okx-contract-signal-system/src/okx_signal_system/signal_quality/__init__.py`: exported the correlation grouping helper.
- `1_CODE_代码/okx-contract-signal-system/src/okx_signal_system/exchange/realtime.py`: passed closed K-line history into tier selection and exposed group metadata in health status.
- `1_CODE_代码/okx-contract-signal-system/gui.py`: applied the same correlation-aware tier selection in desktop scans.
- `1_CODE_代码/okx-contract-signal-system/tests/test_signal_quality.py`: covered same-group demotion, different-group A-tier eligibility, and candidate retention.
- `1_CODE_代码/okx-contract-signal-system/main.py`: bumped app version to v3.31.
- `1_CODE_代码/okx-contract-signal-system/pyproject.toml`: bumped package version to 3.31.0.
- `1_CODE_代码/okx-contract-signal-system/src/okx_signal_system/__init__.py`: bumped package runtime version to 3.31.0.
- `1_CODE_代码/okx-contract-signal-system/start.bat`: bumped launcher version text to v3.31.
- `docs/okx-runtime-health-v3.31.md`: added the v3.31 runtime health note.
- Rollback method: revert the upcoming commit `feat: add correlation-aware signal tiers`.

## 2026-06-16 - Task: Complete v3.31 Verification And Handoff Sync
### What was done
- Ran the full backend and dashboard verification suite after the Phase 3 implementation.
- Updated the next-step handoff so v3.31 is the current completed version and Phase 4 is the next remaining phase.
- Updated the v3.31 runtime health note with complete verification evidence.
### Testing
- `D:\JIAOYI-CX\LOCAL_DEPS\venv\Scripts\python.exe -m compileall gui.py main.py src` passed.
- `D:\JIAOYI-CX\LOCAL_DEPS\venv\Scripts\python.exe -m pytest` passed, 106 tests.
- `npm.cmd run lint` passed from `D:\JIAOYI-CX\1_CODE_代码\okx-contract-signal-system\dashboard`.
- `npm.cmd run build` passed from `D:\JIAOYI-CX\1_CODE_代码\okx-contract-signal-system\dashboard`.
### Notes
- `docs/okx-signal-quality-next-steps.md`: moved Phase 3 into completed v3.31 work and left Phase 4 as the next execution phase.
- `docs/okx-runtime-health-v3.31.md`: added full verification evidence and included handoff/progress files in the changed-file note.
- `progress.md`: appended this verification and handoff synchronization record.
- Rollback method: revert the upcoming commit `feat: add correlation-aware signal tiers`.

## 2026-06-16 - Task: Summarize B-Tier Signal Candidates
### What was done
- Added Phase 4 B-tier summary pushes so B-tier candidates are summarized once per closed-candle cycle.
- Kept A-tier individual push behavior unchanged.
- Added a separate B-tier summary SQLite de-duplication store from the A-tier pushed signal store.
- Connected realtime and GUI scans to send B-tier summaries after A-tier processing.
- Bumped version metadata to v3.32.
### Testing
- `D:\JIAOYI-CX\LOCAL_DEPS\venv\Scripts\python.exe -m compileall gui.py main.py src` passed.
- `D:\JIAOYI-CX\LOCAL_DEPS\venv\Scripts\python.exe -m pytest tests/test_feishu_notify.py` passed, 10 tests.
- `D:\JIAOYI-CX\LOCAL_DEPS\venv\Scripts\python.exe -m pytest` passed, 109 tests.
- `npm.cmd run lint` passed from `D:\JIAOYI-CX\1_CODE_代码\okx-contract-signal-system\dashboard`.
- `npm.cmd run build` passed from `D:\JIAOYI-CX\1_CODE_代码\okx-contract-signal-system\dashboard`.
### Notes
- `1_CODE_代码/okx-contract-signal-system/src/okx_signal_system/notify/feishu.py`: added B-tier summary text formatting and send helper.
- `1_CODE_代码/okx-contract-signal-system/src/okx_signal_system/notify/signal_dedupe.py`: added B-tier summary keys and a separate SQLite summary store.
- `1_CODE_代码/okx-contract-signal-system/src/okx_signal_system/notify/__init__.py`: exported the B-tier summary helper.
- `1_CODE_代码/okx-contract-signal-system/src/okx_signal_system/exchange/realtime.py`: sends one B-tier summary per candle cycle with separate de-duplication.
- `1_CODE_代码/okx-contract-signal-system/gui.py`: applies the same B-tier summary behavior in desktop scans.
- `1_CODE_代码/okx-contract-signal-system/tests/test_feishu_notify.py`: covered summary text, empty summary suppression, and separate summary de-duplication.
- `1_CODE_代码/okx-contract-signal-system/main.py`: bumped app version to v3.32.
- `1_CODE_代码/okx-contract-signal-system/pyproject.toml`: bumped package version to 3.32.0.
- `1_CODE_代码/okx-contract-signal-system/src/okx_signal_system/__init__.py`: bumped package runtime version to 3.32.0.
- `1_CODE_代码/okx-contract-signal-system/start.bat`: bumped launcher version text to v3.32.
- `docs/okx-runtime-health-v3.32.md`: added the v3.32 runtime health note.
- `docs/okx-signal-quality-next-steps.md`: moved Phase 4 into completed v3.32 work and left Phase 5 as the next execution phase.
- `progress.md`: appended this task record.
- Rollback method: revert the upcoming commit `feat: summarize b tier signal candidates`.

## 2026-06-16 - Task: Track Signal Lifecycle States
### What was done
- Added Phase 5 lifecycle tracking so push-eligible signals are recorded as `TRIGGERED`.
- Added invalidation price and lifecycle status to candidate payloads, health output, and Feishu signal summaries.
- Updated realtime and GUI scans to refresh lifecycle state from closed K-line history only.
- Added persistent lifecycle JSON output under `outputs/signal_lifecycle.json`.
- Bumped version metadata to v3.33.
### Testing
- `D:\JIAOYI-CX\LOCAL_DEPS\venv\Scripts\python.exe -m compileall gui.py main.py src` passed.
- `D:\JIAOYI-CX\LOCAL_DEPS\venv\Scripts\python.exe -m pytest` passed, 115 tests.
- `npm.cmd run lint` passed from `D:\JIAOYI-CX\1_CODE_代码\okx-contract-signal-system\dashboard`.
- `npm.cmd run build` passed from `D:\JIAOYI-CX\1_CODE_代码\okx-contract-signal-system\dashboard`.
### Notes
- `1_CODE_代码/okx-contract-signal-system/src/okx_signal_system/signal_quality/lifecycle.py`: added persistent signal lifecycle records and closed-candle state transitions.
- `1_CODE_代码/okx-contract-signal-system/src/okx_signal_system/signal_quality/candidate.py`: exposed candidate invalidation price.
- `1_CODE_代码/okx-contract-signal-system/src/okx_signal_system/signal_quality/__init__.py`: exported lifecycle helpers.
- `1_CODE_代码/okx-contract-signal-system/src/okx_signal_system/exchange/realtime.py`: updates lifecycle records and includes lifecycle summary in scan status.
- `1_CODE_代码/okx-contract-signal-system/gui.py`: applies the same lifecycle handling in desktop scans.
- `1_CODE_代码/okx-contract-signal-system/src/okx_signal_system/notify/feishu.py`: includes lifecycle status and invalidation price in alert text.
- `1_CODE_代码/okx-contract-signal-system/tests/test_signal_lifecycle.py`: covers trigger, confirm, invalidation, expiry, closed-candle filtering, and persistence.
- `1_CODE_代码/okx-contract-signal-system/tests/test_feishu_notify.py`: covers lifecycle fields in signal alerts.
- `1_CODE_代码/okx-contract-signal-system/main.py`: bumped app version to v3.33.
- `1_CODE_代码/okx-contract-signal-system/pyproject.toml`: bumped package version to 3.33.0.
- `1_CODE_代码/okx-contract-signal-system/src/okx_signal_system/__init__.py`: bumped package runtime version to 3.33.0.
- `1_CODE_代码/okx-contract-signal-system/start.bat`: bumped launcher version text to v3.33.
- `docs/okx-runtime-health-v3.33.md`: added the v3.33 runtime health note.
- `docs/okx-signal-quality-next-steps.md`: moved Phase 5 into completed v3.33 work and left Phase 6 as the next execution phase.
- `progress.md`: appended this task record.
- Rollback method: revert the upcoming commit `feat: track signal lifecycle states`.

## 2026-06-16 - Task: Label Historical Signal Outcomes
### What was done
- Added Phase 6 historical outcome labeling for closed-candle signal analysis.
- Labels now report `TP`, `SL`, `TIMEOUT`, net R after existing costs, MAE, MFE, holding bars, exit time, and exit price.
- Same-candle TP/SL conflicts are handled conservatively as `SL`.
- Labeling ignores bars at or before the signal time and filters to closed K-lines only.
- Bumped version metadata to v3.34.
### Testing
- `D:\JIAOYI-CX\LOCAL_DEPS\venv\Scripts\python.exe -m compileall gui.py main.py src` passed.
- `D:\JIAOYI-CX\LOCAL_DEPS\venv\Scripts\python.exe -m pytest` passed, 120 tests.
- `npm.cmd run lint` passed from `D:\JIAOYI-CX\1_CODE_代码\okx-contract-signal-system\dashboard`.
- `npm.cmd run build` passed from `D:\JIAOYI-CX\1_CODE_代码\okx-contract-signal-system\dashboard`.
### Notes
- `1_CODE_代码/okx-contract-signal-system/src/okx_signal_system/signal_quality/labeler.py`: added historical labeling and cost-adjusted net R output.
- `1_CODE_代码/okx-contract-signal-system/src/okx_signal_system/signal_quality/__init__.py`: exported labeler interfaces.
- `1_CODE_代码/okx-contract-signal-system/tests/test_signal_quality_labeler.py`: covers TP, SL, TIMEOUT, same-candle stop-loss priority, and closed-candle boundaries.
- `1_CODE_代码/okx-contract-signal-system/main.py`: bumped app version to v3.34.
- `1_CODE_代码/okx-contract-signal-system/pyproject.toml`: bumped package version to 3.34.0.
- `1_CODE_代码/okx-contract-signal-system/src/okx_signal_system/__init__.py`: bumped package runtime version to 3.34.0.
- `1_CODE_代码/okx-contract-signal-system/start.bat`: bumped launcher version text to v3.34.
- `docs/okx-runtime-health-v3.34.md`: added the v3.34 runtime health note.
- `docs/okx-signal-quality-next-steps.md`: moved Phase 6 into completed v3.34 work and left Phase 7 as the next execution phase.
- `progress.md`: appended this task record.
- Rollback method: revert the upcoming commit `feat: label historical signal outcomes`.

## 2026-06-16 - Task: Build Baseline Signal Quality Model
### What was done
- Added Phase 7 leakage-safe signal quality features from signal-time and earlier closed K-lines.
- Added Phase 8 baseline quality ranking for historical labels and candidate ordering research.
- Added purged walk-forward validation for ordered time-series evaluation.
- Kept model output as ranking-only; it is not wired into live reject or promotion decisions.
- Bumped version metadata to v3.35.
### Testing
- `D:\JIAOYI-CX\LOCAL_DEPS\venv\Scripts\python.exe -m compileall gui.py main.py src` passed.
- `D:\JIAOYI-CX\LOCAL_DEPS\venv\Scripts\python.exe -m pytest` passed, 128 tests.
- `npm.cmd run lint` passed from `D:\JIAOYI-CX\1_CODE_代码\okx-contract-signal-system\dashboard`.
- `npm.cmd run build` passed from `D:\JIAOYI-CX\1_CODE_代码\okx-contract-signal-system\dashboard`.
### Notes
- `1_CODE_代码/okx-contract-signal-system/src/okx_signal_system/signal_quality/feature_builder.py`: added signal-time quality feature construction.
- `1_CODE_代码/okx-contract-signal-system/src/okx_signal_system/signal_quality/model.py`: added the baseline ranking model and purged walk-forward validation.
- `1_CODE_代码/okx-contract-signal-system/src/okx_signal_system/signal_quality/__init__.py`: exported feature and model interfaces.
- `1_CODE_代码/okx-contract-signal-system/tests/test_signal_quality_features.py`: covered future leakage, prefix invariance, and missing optional columns.
- `1_CODE_代码/okx-contract-signal-system/tests/test_signal_quality_model.py`: covered ranking outputs, missing-feature fallback, no hard reject gate, and purged validation.
- `1_CODE_代码/okx-contract-signal-system/gui.py`: bumped app version to v3.35.
- `1_CODE_代码/okx-contract-signal-system/main.py`: bumped app version to v3.35.
- `1_CODE_代码/okx-contract-signal-system/pyproject.toml`: bumped package version to 3.35.0.
- `1_CODE_代码/okx-contract-signal-system/src/okx_signal_system/__init__.py`: bumped package runtime version to 3.35.0.
- `1_CODE_代码/okx-contract-signal-system/start.bat`: bumped launcher version text to v3.35.
- `docs/okx-runtime-health-v3.35.md`: added the v3.35 runtime health note.
- `docs/okx-signal-quality-next-steps.md`: moved Phase 7 and Phase 8 into completed v3.35 work and left Phase 9 as the next execution phase.
- `progress.md`: appended this task record.
- Rollback method: revert the upcoming commit `feat: build baseline signal quality model`.

## 2026-06-16 - Task: Score Candidates With Quality Model Shadow Mode
### What was done
- Added optional Phase 9 quality model shadow scoring for realtime and GUI scans.
- Wrote model scores into candidate health output, ready-signal payloads, and dashboard-visible status.
- Kept A/B-tier selection, push decisions, and fixed strategy parameters unchanged.
- Added model artifact save/load helpers and a disabled fallback when `outputs/signal_quality_model.json` is missing.
- Bumped version metadata to v3.36.
### Testing
- `D:\JIAOYI-CX\LOCAL_DEPS\venv\Scripts\python.exe -m compileall gui.py main.py src` passed.
- `D:\JIAOYI-CX\LOCAL_DEPS\venv\Scripts\python.exe -m pytest` passed, 130 tests.
- `npm.cmd run lint` passed from `D:\JIAOYI-CX\1_CODE_代码\okx-contract-signal-system\dashboard`.
- `npm.cmd run build` passed from `D:\JIAOYI-CX\1_CODE_代码\okx-contract-signal-system\dashboard`.
### Notes
- `1_CODE_代码/okx-contract-signal-system/src/okx_signal_system/signal_quality/model.py`: added quality model artifact save/load support.
- `1_CODE_代码/okx-contract-signal-system/src/okx_signal_system/signal_quality/quality_shadow.py`: added optional shadow scoring from `outputs/signal_quality_model.json`.
- `1_CODE_代码/okx-contract-signal-system/src/okx_signal_system/signal_quality/__init__.py`: exported shadow scoring interfaces.
- `1_CODE_代码/okx-contract-signal-system/src/okx_signal_system/exchange/realtime.py`: added shadow model output to realtime scan health and ready-signal payloads without changing decisions.
- `1_CODE_代码/okx-contract-signal-system/gui.py`: added the same shadow model output for desktop scans.
- `1_CODE_代码/okx-contract-signal-system/dashboard/src/lib/types.ts`: added dashboard types for quality model shadow output.
- `1_CODE_代码/okx-contract-signal-system/dashboard/src/components/dashboard.tsx`: displayed model TP probability and expected R in shadow-only status.
- `1_CODE_代码/okx-contract-signal-system/tests/test_signal_quality_shadow.py`: covered missing artifact fallback and optional artifact scoring.
- `1_CODE_代码/okx-contract-signal-system/gui.py`: bumped app version to v3.36.
- `1_CODE_代码/okx-contract-signal-system/main.py`: bumped app version to v3.36.
- `1_CODE_代码/okx-contract-signal-system/pyproject.toml`: bumped package version to 3.36.0.
- `1_CODE_代码/okx-contract-signal-system/src/okx_signal_system/__init__.py`: bumped package runtime version to 3.36.0.
- `1_CODE_代码/okx-contract-signal-system/start.bat`: bumped launcher version text to v3.36.
- `docs/okx-runtime-health-v3.36.md`: added the v3.36 runtime health note.
- `docs/okx-signal-quality-next-steps.md`: moved Phase 9 into completed v3.36 work and left Phase 10 as an explicit decision point.
- `progress.md`: appended this task record.
- Rollback method: revert the upcoming commit `feat: score candidates with quality model shadow mode`.

## 2026-06-16 - Task: Fix Trend Resample Boundary
### What was done
- Corrected the 15m to 1h trend resample boundary so the right-edge newly started 15m bar is not included in the completed 1h trend bar.
- Extended startup anti-future checks to verify that trend resampling excludes the right edge.
- Added a focused regression test for the 15m to 1h boundary.
- Bumped version metadata to v3.37.
### Testing
- `D:\JIAOYI-CX\LOCAL_DEPS\venv\Scripts\python.exe -m compileall gui.py main.py src` passed.
- `D:\JIAOYI-CX\LOCAL_DEPS\venv\Scripts\python.exe -m pytest` passed, 131 tests.
- `npm.cmd run lint` passed from `D:\JIAOYI-CX\1_CODE_代码\okx-contract-signal-system\dashboard`.
- `npm.cmd run build` passed from `D:\JIAOYI-CX\1_CODE_代码\okx-contract-signal-system\dashboard`.
### Notes
- `1_CODE_代码/okx-contract-signal-system/src/okx_signal_system/features/indicators.py`: changed trend resampling to left-closed, right-labeled bins.
- `1_CODE_代码/okx-contract-signal-system/src/okx_signal_system/training/startup_quality.py`: added a startup anti-future check for right-edge trend leakage.
- `1_CODE_代码/okx-contract-signal-system/tests/test_features.py`: added the 15m to 1h boundary regression test.
- `1_CODE_代码/okx-contract-signal-system/gui.py`: bumped app version to v3.37.
- `1_CODE_代码/okx-contract-signal-system/main.py`: bumped app version to v3.37.
- `1_CODE_代码/okx-contract-signal-system/pyproject.toml`: bumped package version to 3.37.0.
- `1_CODE_代码/okx-contract-signal-system/src/okx_signal_system/__init__.py`: bumped package runtime version to 3.37.0.
- `1_CODE_代码/okx-contract-signal-system/start.bat`: bumped launcher version text to v3.37.
- `docs/okx-runtime-health-v3.37.md`: added the v3.37 runtime health note.
- `docs/okx-signal-quality-next-steps.md`: moved the trend resample boundary fix into completed v3.37 work.
- `progress.md`: appended this task record.
- Rollback method: revert the upcoming commit `fix: correct trend resample boundary`.

## 2026-06-16 - Task: Unify GUI and CLI Signal Scan Core
### What was done
- Added a shared signal scan service for GUI and CLI candidate decision logic.
- Unified closed-bar checks, stale-signal blocking, feature generation, signal building, ensemble voting, risk validation, quality shadow scoring, lifecycle recording, and A/B tier selection behind one scan result.
- Kept notifications, status files, data persistence, GUI display, and position management at the caller boundary.
- Aligned GUI and CLI context inputs for position-symbol skipping and shadow-score minimum closed-signal support.
- Bumped version metadata to v3.38.
### Testing
- `D:\JIAOYI-CX\LOCAL_DEPS\venv\Scripts\python.exe -m compileall gui.py main.py src` passed.
- `D:\JIAOYI-CX\LOCAL_DEPS\venv\Scripts\python.exe -m pytest tests/test_signal_scan_service.py tests/test_desktop_runtime.py tests/test_shadow_trading.py tests/test_signal_quality_shadow.py tests/test_signal_runtime.py tests/test_feishu_notify.py` passed, 32 tests.
- `D:\JIAOYI-CX\LOCAL_DEPS\venv\Scripts\python.exe -m pytest` passed, 133 tests.
- `npm.cmd run lint` passed from `D:\JIAOYI-CX\1_CODE_浠ｇ爜\okx-contract-signal-system\dashboard`.
- `npm.cmd run build` passed from `D:\JIAOYI-CX\1_CODE_浠ｇ爜\okx-contract-signal-system\dashboard`.
- `git diff --check` passed.
### Notes
- `1_CODE_浠ｇ爜/okx-contract-signal-system/src/okx_signal_system/signal_service/scan.py`: added the shared scan decision service and result/context structures.
- `1_CODE_浠ｇ爜/okx-contract-signal-system/src/okx_signal_system/signal_service/__init__.py`: exported the scan service interfaces.
- `1_CODE_浠ｇ爜/okx-contract-signal-system/src/okx_signal_system/exchange/realtime.py`: routed CLI monitoring through the shared scan service while keeping side effects outside it.
- `1_CODE_浠ｇ爜/okx-contract-signal-system/gui.py`: routed desktop scans through the shared scan service and preserved GUI notification/status behavior.
- `1_CODE_浠ｇ爜/okx-contract-signal-system/tests/test_signal_scan_service.py`: covered ready-candidate output, checked-bar gating, and shadow-score support propagation.
- `1_CODE_浠ｇ爜/okx-contract-signal-system/main.py`: bumped app version to v3.38.
- `1_CODE_浠ｇ爜/okx-contract-signal-system/pyproject.toml`: bumped package version to 3.38.0.
- `1_CODE_浠ｇ爜/okx-contract-signal-system/src/okx_signal_system/__init__.py`: bumped package runtime version to 3.38.0.
- `1_CODE_浠ｇ爜/okx-contract-signal-system/start.bat`: bumped launcher version text to v3.38.
- `docs/okx-runtime-health-v3.38.md`: added the v3.38 runtime health note.
- `docs/okx-signal-quality-next-steps.md`: moved scan-service unification into completed v3.38 work and left Phase 10 as an explicit decision point.
- `progress.md`: appended this task record.
- Runtime outputs such as `outputs/pushed_signals.sqlite3` and `outputs/signal_lifecycle.json` were left uncommitted.
- Rollback method: revert the upcoming commit `feat: unify signal scan service`.
