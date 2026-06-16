# OKX Signal Quality Next Steps

## Current State

- Repository root: `D:\JIAOYI-CX`
- Project path: `D:\JIAOYI-CX\1_CODE_代码\okx-contract-signal-system`
- Current completed version: v3.39
- Latest completed commits:
  - `a26f0d9 feat: batch rank and tier signal pushes`
  - `feat: add correlation-aware signal tiers`
  - `feat: summarize b tier signal candidates`
  - `feat: track signal lifecycle states`
  - `feat: label historical signal outcomes`
  - `feat: build baseline signal quality model`
  - `feat: score candidates with quality model shadow mode`
  - `fix: correct trend resample boundary`
  - `feat: unify signal scan service`
  - `fix: tighten scan data gates`

## Completed Work

### v3.27
- Startup closed K-line gate is enforced before desktop/CLI monitoring starts.
- Missing latest closed K-lines are backfilled or monitoring is blocked.
- Weighted strategy vote threshold is configurable through `strategy.min_vote_approval_rate`.
- Vote threshold is applied consistently to live scan, backtest, startup quality gate, daily learning review, and candidate search.

### v3.28
- Removed stale historical signal recovery from `TradingBrain` and scheduler paths.
- Signal generation checks only the latest closed K-line.
- Signals older than the allowed closed-candle delay are blocked as `stale_signal_bar`.
- Persistent push de-duplication now uses SQLite `pushed_signals` by default.
- Signal ID includes symbol, candle time, side, strategy version, and parameter hash.

### v3.29
- Added `signal_quality` candidate, ranker, and selector modules.
- Realtime and GUI scans now collect all ready candidates before pushing.
- A-tier immediate pushes are capped at two per closed-candle cycle.
- B-tier candidates are retained in health/dashboard status instead of being dropped.
- Feishu signal text includes tier and cross-symbol rank.

### v3.30
- Added this next-step handoff document for a new conversation window.
- No trading logic changed.
- Version metadata was bumped to v3.30.

### v3.31
- Added rolling return correlation grouping for ready signal candidates.
- Realtime and GUI scans pass current closed 15m candle history into tier selection.
- A-tier still caps at two candidates, with at most one A-tier candidate per correlation group.
- Correlated but otherwise ready candidates remain B-tier and stay visible in status output.
- Version metadata was bumped to v3.31.

### v3.32
- Added B-tier summary text push for ready candidates that are not immediate A-tier alerts.
- Realtime and GUI scans send at most one B-tier summary per closed-candle cycle.
- B-tier summary de-duplication uses a separate SQLite store from A-tier signal push de-duplication.
- A-tier individual push behavior remains unchanged.
- Version metadata was bumped to v3.32.

### v3.33
- Added persistent lifecycle tracking for ready signal candidates.
- Ready candidates are recorded as `TRIGGERED` with an invalidation price.
- Later closed K-lines update lifecycle state to `CONFIRMED`, `INVALIDATED`, or `EXPIRED`.
- Realtime and GUI status output include lifecycle status and lifecycle summary counts.
- Version metadata was bumped to v3.33.

### v3.34
- Added historical outcome labeling for signal candidates.
- Labels include `TP`, `SL`, `TIMEOUT`, final net R, MAE, MFE, holding bars, exit time, and exit price.
- Labeling uses only later closed K-lines and treats same-candle TP/SL as `SL`.
- Existing fee, slippage, and funding cost rules are applied to final net R.
- Version metadata was bumped to v3.34.

### v3.35
- Added leakage-safe signal quality feature building for historical and candidate analysis.
- Feature generation uses only signal-time and earlier closed K-lines.
- Added a baseline signal quality ranking model with walk-forward purged validation.
- Model output is ranking-only and includes `p_tp`, `p_sl`, `p_timeout`, `expected_net_r`, and uncertainty.
- Version metadata was bumped to v3.35.

### v3.36
- Connected the v3.35 quality model as shadow scoring in realtime and GUI scans.
- Shadow scores are written to candidate health output, ready-signal payloads, and dashboard status.
- Model artifacts are optional and loaded from `outputs/signal_quality_model.json`.
- Existing A/B-tier selection and push decisions remain unchanged.
- Version metadata was bumped to v3.36.

### v3.37
- Corrected the signal-to-trend resample boundary so a 1h trend bar labeled `01:00` is built from `00:00`, `00:15`, `00:30`, and `00:45`, excluding the newly started `01:00` 15m bar.
- Extended startup anti-future checks to detect right-edge inclusion in trend resampling.
- Added a focused regression test for the 15m to 1h resample boundary.
- Version metadata was bumped to v3.37.

### v3.38
- Added `SignalScanService` as the shared GUI and CLI scan decision core.
- Unified closed-bar checks, stale-signal blocking, feature generation, signal building, ensemble vote, risk validation, shadow quality scoring, lifecycle recording, and A/B tier selection behind one service result.
- Kept caller-owned side effects outside the service: GUI display, Feishu pushes, status files, persistence, and position management remain at their existing boundaries.
- Preserved manual-confirmation mode, `min_signal_score=6.0`, fixed strategy parameters, and no-real-order posture.
- Version metadata was bumped to v3.38.

### v3.39
- Fixed string closed-candle parsing so `"False"`, `"0"`, and `"no"` are not treated as closed bars.
- Shared scan logic now rejects future-dated closed candles and retries the same bar after feature construction or validation failures.
- Realtime publishing now uses the `SignalScanService` tier selection directly instead of running `assign_tiers()` a second time.
- External history lookup can use `JIAOYI_DATA_DIR`, and dashboard history paths no longer fall back to a hard-coded local history directory.
- Trading-entry audit items were not changed in this release; real orders remain disabled and live-order path changes require explicit approval.
- Version metadata was bumped to v3.39.

## Absolute Constraints

- Do not enable real orders.
- Do not use API keys with trading permission.
- Do not change fixed strategy parameters unless the user explicitly asks:
  - `fast_ema=120`
  - `slow_ema=720`
  - `breakout_window=384`
  - `atr_stop_mult=4.0`
  - `take_profit_mult=6.0`
  - `max_hold_bars=768`
- Do not raise `min_signal_score=6.0`.
- Do not read unfinished K-lines for signal decisions.
- Do not use random train/test splits for time series.
- Do not claim completion while tests fail.
- Every code update must bump version and commit to git.

## Remaining Decision Point

The v3.39 scan consistency cleanup does not approve model-assisted ranking or live-order behavior changes. Phase 10 still requires an explicit decision before any behavior change.

### Phase 10: Optional Model-Assisted Ranking

Goal: decide whether the shadow model has enough evidence to affect ordering. This is a behavior change and should not be done automatically.

Possible implementation after explicit approval:
- Use shadow score as a small ranking adjustment only after enough closed shadow outcomes exist.
- Keep `min_signal_score=6.0` unchanged.
- Do not use model output as a hard reject gate.
- Add before/after ordering evidence to docs before changing push behavior.

Primary files:
- `src/okx_signal_system/exchange/realtime.py`
- `gui.py`
- dashboard status surfaces
- focused ranking behavior tests

## Required Verification For Every Phase

Run from `D:\JIAOYI-CX\1_CODE_代码\okx-contract-signal-system`:

```powershell
D:\JIAOYI-CX\LOCAL_DEPS\venv\Scripts\python.exe -m compileall gui.py main.py src
D:\JIAOYI-CX\LOCAL_DEPS\venv\Scripts\python.exe -m pytest
cd dashboard
npm.cmd run lint
npm.cmd run build
```

Before commit:

```powershell
git diff --check
git status --short
git diff --stat
```

## Version And Commit Rules

- Bump all version entry points for every code/document update:
  - `gui.py`
  - `main.py`
  - `pyproject.toml`
  - `src/okx_signal_system/__init__.py`
  - `start.bat`
- Add/update a version note under `docs/`.
- Commit after tests pass.
- Suggested future commit messages:
  - `feat: add correlation-aware signal tiers`
  - `feat: summarize b tier signal candidates`
  - `feat: track signal lifecycle states`
  - `feat: label historical signal outcomes`
  - `feat: score candidates with quality model shadow mode`
