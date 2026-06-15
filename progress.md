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
