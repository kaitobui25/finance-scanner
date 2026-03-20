# IMFVG Position Monitor v2 — Implementation Checklist (Final)
> Plan v2 Patch Rounds 1–11 + DI Upgrade + Phase 0 Contracts
> Conflicts resolved: SignalContext, entry_price, strategy_name, DB schema
> Updated: 2026-03-20

---

## Quy tắc làm việc

- **Test ngay sau mỗi task implement** — không gộp test lại cuối phase
- **Verify với data thực** ở mỗi milestone (✦)
- **Không sửa file hiện tại** ngoại trừ `indicators/fvg.py` (Phase 1)
- **Không hardcode** trong engine — mọi thứ từ `cfg.xxx` hoặc `signal_fn`

---

## Phase 0 — Contracts (không code, đọc và hiểu)

Tất cả quyết định thiết kế đã chốt. Không có gì để code ở đây.

### Contracts cần nhớ khi code các phase sau

```python
# 1. SignalContext — context engine truyền vào signal_fn
class SignalContext(TypedDict, total=False):
    atr: float | None     # engine always provides, value có thể None
    # v3: volume, rsi, ema_fast, ...

# 2. SignalFn — type alias
SignalFn = Callable[
    [pd.DataFrame, int, SignalContext],
    tuple[str | None, dict]
]

# 3. meta contract
meta["entry_price"]       # float — REQUIRED (không phải "entry_close")
meta.get("gap_top")       # float | None — OPTIONAL
meta.get("gap_bottom")    # float | None — OPTIONAL

# 4. Error policy
# Expected data issue (NaN, thiếu bar) → return (None, {})
# Programming error (impossible state) → raise

# 5. Strategy naming
def _resolve_strategy_name(signal_fn, override: str | None) -> str:
    if override:
        return override
    name = getattr(signal_fn, "__name__", None)
    if name and name not in ("<lambda>", "functools.partial"):
        return name
    wrapped = getattr(signal_fn, "func", None)
    if wrapped:
        return getattr(wrapped, "__name__", "unknown")
    return "unknown"

# 6. Factory sets __name__
def make_imfvg_detector(cfg) -> SignalFn:
    def _fn(df, i, ctx): ...
    _fn.__name__ = f"imfvg_fw{cfg.filter_width}"
    return _fn

# 7. Engine signatures — strategy_name param ở mọi nơi
def scan_full_history(df, cfg,
    return_trades=False, summarize_trades=False,
    atr_series=None, signal_fn=None,
    strategy_name: str | None = None): ...

def check_latest_bar(df, position_row, cfg,
    signal_fn=None,
    strategy_name: str | None = None): ...

def backtest_portfolio(symbols, cfg,
    timeframe="1MO", weight_by="trades",
    signal_fn=None,
    strategy_name: str | None = None): ...

# 8. signal_fn=None → auto make_imfvg_detector(cfg) — backward compat
```

---

## Phase 1 — Foundation: Single Source of Truth

> Mục tiêu: tách logic FVG ra file riêng. Test xong mới sửa `fvg.py`.

### 1.1 Tạo `indicators/fvg_core.py`

- [x] **T01** Khai báo `BULL = "BULL"`, `BEAR = "BEAR"`
- [x] **T02** Khai báo `FVGResult` TypedDict: `signal`, `gap_top`, `gap_bottom`
- [x] **T03** Implement `detect_imfvg_from_bars(b3_low, b3_high, b3_close, b2_close, b1_low, b1_high, b0_close, filter_width=0.0, atr=None)`:
  - [x] Guard: `filter_width > 0` và (`atr is None` hoặc `atr <= 0`) → `raise ValueError`
  - [x] Bullish: `b3_low > b1_high AND b2_close < b3_low AND b0_close > b3_low`
  - [x] Gap filter bull: `filter_width > 0` → `(b3_low − b1_high) > atr * filter_width`
  - [x] Bearish: `b1_low > b3_high AND b2_close > b3_high AND b0_close < b3_high`
  - [x] Gap filter bear: `filter_width > 0` → `(b1_low − b3_high) > atr * filter_width`
  - [x] Bear override Bull
  - [x] Bull meta: `gap_top=b3_low`, `gap_bottom=b1_high`
  - [x] Bear meta: `gap_top=b1_low`, `gap_bottom=b3_high`

### 1.2 Tests `tests/test_fvg_core.py`

- [x] **T39** `test_bull_detected` — verify `signal==BULL`, `gap_top`, `gap_bottom`
- [x] **T40** `test_bear_detected`
- [x] **T41** `test_bear_overrides_bull`
- [x] **T42** `test_no_signal`
- [x] **T43** `test_filter_width_zero_no_atr_needed` — default, không truyền `atr`
- [x] **T44** `test_filter_width_nonzero_requires_atr` → `ValueError`
- [x] **T45** `test_filter_width_filters_small_gap`
- [x] **T46** `test_filter_width_passes_large_gap`

### 1.3 Sửa `indicators/fvg.py`

- [x] **T05** `from indicators.fvg_core import detect_imfvg_from_bars, BULL, BEAR`
- [x] **T06** Thay phần bull/bear detection bằng `detect_imfvg_from_bars(...)` — `filter_width` mặc định 0.0, không truyền `atr`
- [x] **T07** ✦ **Verify regression**: `python scanner.py --timeframe 1MO --dry-run` ≥1 symbol thực → kết quả giống trước

---

## Phase 2 — Dataclasses, Constants & Type Aliases

> Tất cả trong `core/position_tracker.py`. Test sau Phase 3.

### 2.1 Type definitions — **PHẢI làm đầu tiên, các phase sau phụ thuộc**

- [ ] **T00a** Import cần thiết ở đầu file:
  ```python
  from __future__ import annotations
  from typing import Callable, TypedDict
  import pandas as pd
  ```

- [ ] **T00b** Khai báo `SignalContext`:
  ```python
  class SignalContext(TypedDict, total=False):
      atr: float | None
      # v3: volume: float | None, rsi: float | None, ...
  ```
  - Docstring: `"atr" key luôn có trong mọi context engine truyền, value có thể None`

- [ ] **T00c** Khai báo `SignalFn`:
  ```python
  SignalFn = Callable[
      [pd.DataFrame, int, SignalContext],
      tuple[str | None, dict]
  ]
  ```
  - Docstring contract:
    - `df`: full DataFrame tz-aware JST, sorted ascending
    - `i`: bar index hiện tại, `i >= 3` với IMFVG
    - `context`: dict từ engine, luôn có `"atr"` key
    - Returns `(signal, meta)`: signal = `BULL|BEAR|None`, meta phải có `"entry_price"` khi signal != None
    - Expected data issue → `return (None, {})`; programming error → `raise`

### 2.2 `PositionConfig`

- [ ] **T08** Implement dataclass 10 params:
  - `filter_width: float = 0.0` — note: default param cho `make_imfvg_detector`; custom `signal_fn` ignore
  - `atr_period: int = 14`
  - `tp_mult: float = 4.0`, `sl_mult: float = 2.0`, `ts_mult: float = 3.0`
  - `exit_on_wick: bool = True`, `ts_on_close: bool = True`
  - `exit_priority: str = "TP_FIRST"`
  - `slippage: float = 0.0`, `fee_per_trade: float = 0.0`
  - Docstring: coupling note `filter_width × atr_period`

### 2.3 `PositionState`

- [ ] **T09** Implement dataclass 17 fields — không thay đổi so với plan trước

### 2.4 `Trade`

- [ ] **T10** Implement dataclass 14 fields + 6 properties:
  - `entry_price: float` — giá thực tế vào lệnh (từ `meta["entry_price"]`)
  - `signed_pnl`, `pnl_pct`, `net_pnl_pct`, `rr_ratio` (signed), `is_win`, `is_tp_hit`

### 2.5 `TradesSummary`

- [ ] **T11** Implement dataclass 13 fields + 8 properties:
  - `expectancy = avg_rr` (NOT `win_rate × avg_rr`)
  - `calmar = avg_net_pnl / abs(max_drawdown)`
  - `std_rr`, `std_pnl`, `sharpe`
  - `from_accumulator(cls, acc)` classmethod

### 2.6 `REASON_COUNTER_MAP`

- [ ] **T12** Module-level dict: `TP_HIT→n_tp, SL_HIT→n_sl, TS_HIT→n_ts, REVERSED→n_reversed`

---

## Phase 3 — Helper Functions

> Phụ thuộc Phase 2. Test ngay sau mỗi hàm.

### 3.1 ATR

- [ ] **T13** `compute_atr(high, low, close, period: int) → pd.Series` — True Range SMA
- [ ] **T47** `test_compute_atr_correct`
- [ ] **T48** `test_compute_atr_insufficient_bars`

### 3.2 Signal detection — **đã update theo Phase 0**

- [ ] **T14** `_detect_imfvg_at(df, i, cfg, context: SignalContext) → tuple[str|None, dict]`:
  - Guard: `i < 3` → `return (None, {})` ← expected data issue
  - NaN guard → `return (None, {})`
  - Extract: `atr_i = context.get("atr")`
  - Gọi `detect_imfvg_from_bars(...)` với `filter_width=cfg.filter_width`, `atr=atr_i`
  - Return meta với key `"entry_price"` (không phải `"entry_close"`):
    ```python
    return signal, {
        "entry_price": float(b0["close"]),   # ← "entry_price"
        "gap_top":     result["gap_top"],    # optional, IMFVG-specific
        "gap_bottom":  result["gap_bottom"], # optional
    }
    ```

- [ ] **T14b** `make_imfvg_detector(cfg: PositionConfig) → SignalFn` — factory:
  ```python
  def make_imfvg_detector(cfg: PositionConfig) -> SignalFn:
      def _imfvg_detector(df: pd.DataFrame, i: int, context: SignalContext):
          return _detect_imfvg_at(df, i, cfg, context)
      _imfvg_detector.__name__ = f"imfvg_fw{cfg.filter_width}"
      return _imfvg_detector
  ```

- [ ] **T14c** `_resolve_strategy_name(signal_fn, override: str | None) → str`:
  ```python
  def _resolve_strategy_name(signal_fn, override):
      if override:
          return override
      name = getattr(signal_fn, "__name__", None)
      if name and name not in ("<lambda>", "functools.partial"):
          return name
      wrapped = getattr(signal_fn, "func", None)  # functools.partial
      if wrapped:
          return getattr(wrapped, "__name__", "unknown")
      return "unknown"
  ```

- [ ] **T49** `test_fvg_py_and_tracker_same_result` — regression:
  ```python
  context: SignalContext = {"atr": atr_i}   # ← dict, không phải float
  detector = make_imfvg_detector(cfg)
  tracker_sig, tracker_meta = detector(df, len(df)-1, context)
  ```
  Verify `tracker_meta["entry_price"]` (không phải `"entry_close"`)

- [ ] **T_DI1** `test_make_imfvg_detector_name` — `detector.__name__ == "imfvg_fw0.0"`
- [ ] **T_DI2** `test_resolve_strategy_name_factory` — factory fn → tên đúng
- [ ] **T_DI3** `test_resolve_strategy_name_lambda` → `"unknown"`
- [ ] **T_DI4** `test_resolve_strategy_name_partial` → tên của wrapped fn
- [ ] **T_DI5** `test_resolve_strategy_name_override` → override wins

### 3.3 Exit logic

- [ ] **T15** `_ratchet_ts(bar, direction, ts, atr, cfg) → float`
- [ ] **T58** `test_ratchet_ts_bull_only_increases`
- [ ] **T59** `test_ratchet_ts_bear_only_decreases`

- [ ] **T16** `_check_exit(bar, direction, tp_level, sl_level, ts, cfg) → tuple[str|None, float|None]`:
  - `exit_on_wick` → `check_high/low`
  - `ts_on_close` → trigger + exit price (wick → exit = ts level)
  - `exit_priority` → TP_FIRST hoặc SL_FIRST
- [ ] **T55** `test_ts_exit_price_at_ts_level_when_wick`
- [ ] **T56** `test_exit_priority_tp_first`
- [ ] **T57** `test_exit_priority_sl_first`

### 3.4 Position open & cost

- [ ] **T17** `_open_position(sig, meta, atr_i, bar_date_str, cfg) → dict`:
  - Lấy `entry_price = meta["entry_price"]` ← key mới
  - Guard: `"entry_price" not in meta` → `raise KeyError("meta must contain 'entry_price'")`

- [ ] **T18** `_apply_slippage(exit_price, direction, close_reason, cfg) → float`

### 3.5 Accumulator

- [ ] **T19** `_accumulate_reason(summary_acc, close_reason, strict=True)` — dùng `REASON_COUNTER_MAP`
- [ ] **T86–T90** Tests accumulate reason

- [ ] **T20** `_accumulate(summary_acc, exit_price, close_reason, bars_held, direction, entry_price, atr_at_entry, cfg)` — update counters + `sum_sq` + MDD

### 3.6 State utilities

- [ ] **T21** `_no_update_state(position_row, reason) → PositionState`

---

## Phase 4 — `scan_full_history`

> Phụ thuộc Phase 3 hoàn toàn.

- [ ] **T23** Implement với signature đầy đủ:
  ```python
  def scan_full_history(
      df:               pd.DataFrame,
      cfg:              PositionConfig,
      return_trades:    bool = False,
      summarize_trades: bool = False,
      atr_series:       pd.Series | None = None,
      signal_fn:        SignalFn | None = None,
      strategy_name:    str | None = None,
  ):
  ```

  **Checklist sub-tasks:**
  - [ ] Guard: `return_trades and summarize_trades` → `raise ValueError`
  - [ ] Guard: `len(df) < cfg.atr_period + 4` → return None/empty theo mode
  - [ ] Compute ATR từ `atr_series` hoặc `cfg.atr_period`
  - [ ] Resolve signal_fn: `if signal_fn is None: signal_fn = make_imfvg_detector(cfg)`
  - [ ] Resolve name: `name = _resolve_strategy_name(signal_fn, strategy_name)`
  - [ ] Log: `log.debug(f"scan_full_history: strategy={name}")`
  - [ ] Init state variables + accumulator

  **Trong main loop — context dict, không phải float:**
  ```python
  # Build context — engine cung cấp atr
  context: SignalContext = {
      "atr": float(atr_i) if not pd.isna(atr_i) else None
  }

  # STEP 1: TP/SL/TS check (nếu HOLDING)
  # STEP 2: signal_fn call
  sig, meta = signal_fn(df, i, context)   # ← context dict
  ```

  - [ ] `bars_held += 1` chỉ khi `direction is not None` (entry bar = 0)
  - [ ] STEP 1: `_check_exit()` → exit handling + `_record_trade()`/`_accumulate()`
  - [ ] STEP 2: signal handling → `signal_action` + `_open_position(sig, meta, ...)`
  - [ ] `_open_position` nhận `meta` → extract `meta["entry_price"]`
  - [ ] Return 3 modes (state / trades / summary)

**Tests signal logic:**
- [ ] **T60–T72** (signal, TP, SL, TS, reversed, bars_held, signal_action...)
- [ ] **T67** ⚠️ `test_tp_and_new_signal_same_bar` — critical
- [ ] **T73** `test_return_trades_backward_compat`
- [ ] **T85** `test_no_literal_in_engine`

**Tests DI — updated signatures:**
- [ ] **T_DI6** `test_signal_fn_default_imfvg` — `signal_fn=None` → behavior = `make_imfvg_detector(cfg)`
- [ ] **T_DI7** `test_signal_fn_always_none` — inject `lambda df,i,ctx: (None, {})` → không có trade
- [ ] **T_DI8** `test_signal_fn_always_bull`:
  ```python
  def always_bull(df, i, ctx: SignalContext):   # ← SignalContext
      return "BULL", {"entry_price": float(df.iloc[i]["close"])}
  scan_full_history(df, cfg, signal_fn=always_bull)
  ```
- [ ] **T_DI9** `test_two_strategies_different_results` — cùng df, cùng cfg, khác `signal_fn` → metrics khác nhau
- [ ] **T_DI10** `test_strategy_name_in_result`:
  ```python
  _, summary = scan_full_history(df, cfg,
      summarize_trades=True, strategy_name="my_test")
  # Verify name resolved correctly (trong log hoặc qua backtest_portfolio)
  ```

---

## Phase 5 — `check_latest_bar`

- [ ] **T24** Implement với signature đầy đủ:
  ```python
  def check_latest_bar(
      df:            pd.DataFrame,
      position_row:  dict,
      cfg:           PositionConfig,
      signal_fn:     SignalFn | None = None,
      strategy_name: str | None = None,
  ) -> PositionState:
  ```

  - [ ] Guards: cache_unavailable, no_new_bar, insufficient_atr, atr_not_ready
  - [ ] Timezone-safe: `df.index[-1].tz_convert("Asia/Tokyo").date()`
  - [ ] Resolve: `if signal_fn is None: signal_fn = make_imfvg_detector(cfg)`
  - [ ] Build context và call:
    ```python
    context: SignalContext = {
        "atr": float(atr_now) if not pd.isna(atr_now) else None
    }
    sig, meta = signal_fn(df, i, context)   # ← context dict
    ```
  - [ ] STEP 1: TP/SL/TS
  - [ ] STEP 2: signal → signal_action
  - [ ] Return `PositionState`

**Tests:**
- [ ] **T50–T54** (rr_ratio, net_pnl, is_win, is_tp_hit)
- [ ] Guards tests
- [ ] **T_DI11** `test_check_latest_bar_custom_signal_fn`:
  ```python
  def bear_detector(df, i, ctx: SignalContext):
      return "BEAR", {"entry_price": float(df.iloc[i]["close"])}
  state = check_latest_bar(df, pos_row, cfg, signal_fn=bear_detector)
  ```

---

## Phase 6 — Backtest Functions

- [ ] **T25** `backtest_symbol(symbol, cfg, atr_cache=None, signal_fn=None, strategy_name=None) → dict`

- [ ] **T26** `backtest_portfolio(symbols, cfg, timeframe="1MO", weight_by="trades", signal_fn=None, strategy_name=None) → dict`:
  - Guard: `weight_by not in ("trades", "symbol")` → `raise ValueError`
  - Resolve name: `name = _resolve_strategy_name(signal_fn or make_imfvg_detector(cfg), strategy_name)`
  - Trả thêm `"strategy": name` trong result dict
  - **Không** trả `portfolio_max_drawdown`, `portfolio_calmar`
  - Keys: `portfolio_win_rate`, `portfolio_avg_rr`, `portfolio_expectancy_rr`, `portfolio_avg_net_pnl`, `portfolio_total_net_pnl_pct`, `portfolio_avg_bars`, `pct_tp/sl/ts/reversed`, `total_trades`, `n_symbols_with_data`, `n_symbols_no_data`, `weight_by`, `strategy`

**Tests:**
- [ ] **T74–T96** (summarize matches, std, sharpe, portfolio metrics, naming...)
- [ ] **T_DI12** `test_portfolio_strategy_name_in_result`

---

## Phase 7 — Database Layer

> Có thể làm song song Phase 4–6.

### 7.1 Schema & Init — **đã update: thêm `strategy_name`**

- [ ] **T27** `init_positions_db(timeframe, conn)`:

  **`positions_{tf}`** — thêm column mới so với plan trước:
  ```sql
  CREATE TABLE IF NOT EXISTS positions_{tf} (
      id                  INTEGER PRIMARY KEY AUTOINCREMENT,
      symbol              TEXT    NOT NULL,
      strategy_name       TEXT,               -- ← MỚI: nullable, backward compat
      direction           TEXT    NOT NULL,
      entry_date          TEXT    NOT NULL,    -- "YYYY-MM-DD" JST
      gap_top             REAL,               -- nullable (optional, IMFVG-specific)
      gap_bottom          REAL,               -- nullable
      entry_close         REAL    NOT NULL,   -- giữ tên "entry_close" trong DB
      tp_level            REAL    NOT NULL,
      sl_level            REAL    NOT NULL,
      trailing_stop       REAL    NOT NULL,
      atr_at_entry        REAL    NOT NULL,
      status              TEXT    NOT NULL DEFAULT 'HOLDING',
      bars_held           INTEGER NOT NULL DEFAULT 0,
      close_price_at_exit REAL,
      last_signal_type    TEXT,
      last_signal_date    TEXT,
      last_checked_at     TEXT,               -- "YYYY-MM-DD" JST
      created_at          DATETIME NOT NULL,  -- UTC
      closed_at           DATETIME
  );
  ```

  **Lưu ý quan trọng:** DB column tên `entry_close` (không đổi để tránh migration). `meta["entry_price"]` là key trong Python dict — tách biệt hoàn toàn với DB column name. Map khi INSERT:
  ```python
  # Python: meta["entry_price"] → DB column: entry_close
  conn.execute("INSERT INTO positions_{tf} (entry_close, ...) VALUES (?, ...)",
               (meta["entry_price"], ...))
  ```

  - **Partial unique index**: `CREATE UNIQUE INDEX IF NOT EXISTS idx_unique_holding ... WHERE status='HOLDING'`
  - Tất cả indexes như plan trước

  **`position_history_{tf}`** — giữ nguyên, không thay đổi

### 7.2 CRUD Helpers

- [ ] **T28** `_get_holding_position(conn, tf, symbol) → dict | None`
- [ ] **T29** `_close_and_log(conn, tf, position_id, position_row, exit_date, close_reason, exit_price, bars_held)` — source: DB row
- [ ] **T30** `_insert_position(conn, tf, symbol, state, strategy_name: str)`:
  - Ghi `strategy_name` vào column
  - `entry_close` column ← từ `state.entry_close` (engine đã lưu vào state)
- [ ] **T31** `_update_position(conn, tf, position_id, state)`
- [ ] **T32** `_process_symbol(conn, tf, symbol, state, bar_date, strategy_name: str)` — atomic

**Tests:**
- [ ] **T97** `test_partial_index_prevents_duplicate_holding`
- [ ] **T98** `test_close_and_log_uses_db_row_not_state`
- [ ] **T99** `test_atomic_transaction`
- [ ] **T_DB1** `test_strategy_name_stored_in_db` — verify column được ghi đúng

---

## Phase 8 — CLI (`position_monitor.py`)

- [ ] **T33** `setup_logging(timeframe)`
- [ ] **T34** `parse_args()` — `--timeframe`, `--full-scan`, `--dry-run`, `--report`, `--strategy` (optional, tên override)

- [ ] **T35** `run_full_scan(timeframe, cfg, dry_run, signal_fn=None, strategy_name=None)`:
  - Resolve: `name = _resolve_strategy_name(signal_fn or make_imfvg_detector(cfg), strategy_name)`
  - `scan_full_history(df, cfg, signal_fn=signal_fn, strategy_name=name)`
  - `_process_symbol(..., strategy_name=name)`
- [ ] ✦ `python position_monitor.py --timeframe 1MO --dry-run --full-scan` → 5+ symbols thực

- [ ] **T36** `run_normal(timeframe, cfg, dry_run, signal_fn=None, strategy_name=None)`:
  - Resolve name
  - `check_latest_bar(df, pos_row, cfg, signal_fn=signal_fn, strategy_name=name)`
  - `_process_symbol(..., strategy_name=name)`

- [ ] **T37** `run_report(timeframe)` — print HOLDING list (thêm cột `strategy_name`)
- [ ] **T38** `notify_positions(timeframe)`

**Integration tests:**
- [ ] **T100–T103** như plan trước
- [ ] **T_INT1** `test_strategy_name_flows_through_pipeline` — từ CLI → DB, verify end-to-end

---

## Milestone Checkpoints ✦

- [ ] ✦ **M1** Scanner regression (sau Phase 1)
- [ ] ✦ **M2** Engine: T67 pass + T_DI9 pass (custom fn cho kết quả khác IMFVG)
- [ ] ✦ **M3** Backtest: `backtest_portfolio` trả `"strategy"` key đúng
- [ ] ✦ **M4** DB: partial index + `strategy_name` column hoạt động
- [ ] ✦ **M5** Full scan dry-run: 50+ symbols, log có `strategy: imfvg_fw0.0`
- [ ] ✦ **M6** Normal mode dry-run: HOLDING check, TS update
- [ ] ✦ **M7** Final: tất cả tests pass, Telegram nhận message

---

## Không làm ở v2

- TS reset khi signal cùng hướng
- Portfolio MDD đúng (time-sorted equity curve)
- Multiplicative equity model
- `backtest_portfolio` parallel
- AI optimizer (Bayesian/genetic)
- Pyramiding
- `weight_by="capital"`

---

## Quick Reference — Contracts cốt lõi

| Contract | Giá trị |
|---------|---------|
| `SignalFn` signature | `(df, i, SignalContext) → (signal, meta)` |
| `SignalContext` key | `"atr": float\|None` — engine always provides |
| `meta` required key | `"entry_price"` (không phải `"entry_close"`) |
| `meta` optional keys | `gap_top`, `gap_bottom`, bất kỳ |
| Error policy | Expected data → `return (None, {})` · Bug → `raise` |
| Strategy name | `_resolve_strategy_name(fn, override)` |
| Factory `__name__` | `f"imfvg_fw{cfg.filter_width}"` |
| `signal_fn=None` | Auto → `make_imfvg_detector(cfg)` |
| DB column | `entry_close` (giữ nguyên) ← map từ `meta["entry_price"]` |
| `strategy_name` | Nullable TEXT trong DB, param ở mọi engine function |
| Thứ tự logic | TP/SL/TS → signal |
| `bars_held` entry | = 0 |
| Exit priority | `"TP_FIRST"` default |
| `expectancy` | = `avg_rr` (NOT `win_rate × avg_rr`) |
| `calmar` | = `avg_net_pnl / abs(MDD)` |
| Portfolio MDD | Không tính — trades overlap time |
| Portfolio key | `portfolio_total_net_pnl_pct`, `portfolio_expectancy_rr` |
| `REASON_COUNTER_MAP` | Dynamic dict |
| Memory | `summarize_trades=True` khi mass optimization |