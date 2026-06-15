# OKX Signal Quality Next Steps

## Current State

- Repository root: `D:\JIAOYI-CX`
- Project path: `D:\JIAOYI-CX\1_CODE_代码\okx-contract-signal-system`
- Current completed version: v3.31
- Latest completed commits:
  - `62d7891 fix: harden signal push correctness`
  - `a26f0d9 feat: batch rank and tier signal pushes`
  - `feat: add correlation-aware signal tiers`

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

## Remaining Execution Plan

### Phase 4: B-Tier Summary Push

Goal: B-tier signals are not silently hidden; they are summarized without alert spam.

Implement:
- Add a Feishu summary helper for B-tier candidates.
- Send at most one B-tier summary per closed-candle cycle.
- Summary should include:
  - candle time;
  - number of B-tier candidates;
  - top 5 B-tier symbols;
  - rank, side, score, risk/reward, reason;
  - note that these are not immediate A-tier alerts.
- Keep SQLite de-duplication separate for A-tier signal pushes and B-tier summary messages.

Suggested tests:
- B-tier summary text is understandable.
- Summary is not sent when there are no B-tier candidates.
- A-tier individual push behavior remains unchanged.

Primary files:
- `src/okx_signal_system/notify/feishu.py`
- `src/okx_signal_system/notify/signal_dedupe.py`
- `src/okx_signal_system/exchange/realtime.py`
- `gui.py`
- `tests/test_feishu_notify.py`

### Phase 5: Signal Lifecycle

Goal: make each signal status clear after trigger.

Lifecycle states:
- `TRIGGERED`: latest closed candle formally triggered.
- `CONFIRMED`: later closed candle still supports signal direction.
- `INVALIDATED`: later closed candle closes back through invalidation level.
- `EXPIRED`: signal no longer valid after configured candle count.

Implement:
- Add `src/okx_signal_system/notification/lifecycle.py` or `src/okx_signal_system/signal_quality/lifecycle.py`.
- Persist lifecycle records in SQLite or JSON under `outputs`.
- Add invalidation price to candidate payload.
- Only use closed candles for confirmation/invalidation.

Suggested tests:
- Triggered signal becomes confirmed only after a later closed candle.
- Immediate reversal marks invalidated.
- Old untouched signal expires.

Primary files:
- `src/okx_signal_system/signal_quality/candidate.py`
- `src/okx_signal_system/notify/feishu.py`
- `src/okx_signal_system/exchange/realtime.py`
- `gui.py`
- new lifecycle tests.

### Phase 6: Historical Candidate Labeling

Goal: create training data for later quality ranking without future leakage.

Implement:
- Add `src/okx_signal_system/signal_quality/labeler.py`.
- For every historical candidate signal, label:
  - `TP`
  - `SL`
  - `TIMEOUT`
  - final net R
  - MAE
  - MFE
  - holding bars
- Label using the same stop loss, take profit, max hold, fee, slippage rules as real strategy/backtest.
- Same-candle TP and SL must be conservative: SL first.

Suggested tests:
- TP label.
- SL label.
- TIMEOUT label.
- Same-candle TP/SL resolves to SL.
- Features/labels never use bars before the signal time incorrectly.

Primary files:
- `src/okx_signal_system/signal_quality/labeler.py`
- `src/okx_signal_system/backtest/runner.py`
- `tests/test_signal_quality_labeler.py`

### Phase 7: Safe Feature Builder

Goal: build quality-model features that only use data available at signal time.

Implement:
- Add `src/okx_signal_system/signal_quality/feature_builder.py`.
- Include only signal-time or prior rolling features:
  - trend spread;
  - trend slope;
  - 15m/1h trend alignment;
  - breakout distance over ATR;
  - candle close location value;
  - volume percentile;
  - ATR percentile;
  - stop distance percent;
  - breakout range compression.
- Add prefix-invariance tests: features for a timestamp must not change when future rows are appended.

Suggested tests:
- Feature frame has no future leakage.
- Prefix-invariance passes.
- Missing optional columns degrade to null/default, not crash.

Primary files:
- `src/okx_signal_system/signal_quality/feature_builder.py`
- `tests/test_signal_quality_features.py`

### Phase 8: Baseline Quality Model

Goal: train a simple ranking model for ordering only, not rejecting signals.

Implement:
- Add `src/okx_signal_system/signal_quality/model.py`.
- First model: scikit-learn logistic regression pipeline.
- Use Purged Walk-forward validation.
- Output:
  - `p_tp`
  - `p_sl`
  - `p_timeout`
  - `expected_net_r`
  - uncertainty placeholder if ensemble/folds are available.
- Model is only allowed to rank candidates initially.
- Do not use model output as a hard reject gate.

Suggested metrics:
- Precision@1
- Precision@3
- Mean net R@K
- PF@K
- Lift@K
- Brier Score
- calibration error

Primary files:
- `src/okx_signal_system/signal_quality/model.py`
- `src/okx_signal_system/signal_quality/calibration.py`
- `tests/test_signal_quality_model.py`

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
  - `feat: build leakage-safe quality features`
  - `feat: train baseline quality ranking model`
