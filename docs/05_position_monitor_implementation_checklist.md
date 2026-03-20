# IMFVG Position Monitor v2 — Implementation Checklist
> Derived from Plan v2 Patch Rounds 1–11
> Build order: phase phụ thuộc phase trước. Test ngay sau implement, không để cuối.

---

## Quy tắc làm việc

- **Test ngay sau mỗi task implement** — không gộp test lại cuối phase
- **Verify với data thực** ở mỗi milestone (✦)
- **Không sửa file hiện tại** ngoại trừ `indicators/fvg.py` (Phase 1, 1 chỗ duy nhất)
- **Không hardcode** trong engine — mọi thứ phải từ `cfg.xxx`

---

## Phase 1 — Foundation: Single Source of Truth

> Mục tiêu: tách logic FVG ra file riêng, đảm bảo scanner không regression.
> Không phụ thuộc gì. Làm trước tiên.

### 1.1 Tạo `indicators/fvg_core.py`

- [x] **T01** Khai báo constants `BULL = "BULL"`, `BEAR = "BEAR"`
- [x] **T02** Khai báo `FVGResult` TypedDict với 3 fields: `signal`, `gap_top`, `gap_bottom`
- [x] **T03** Implement `detect_imfvg_from_bars(b3_low, b3_high, b3_close, b2_close, b1_low, b1_high, b0_close, filter_width=0.0, atr=None)`:
  - [x] Guard: `filter_width > 0` và `atr=None` hoặc `atr <= 0` → `raise ValueError`
  - [x] Logic Bullish: `b3_low > b1_high AND b2_close < b3_low AND b0_close > b3_low`
  - [x] Gap filter Bullish: nếu `filter_width > 0` → `(b3_low − b1_high) > atr * filter_width`
  - [x] Logic Bearish: `b1_low > b3_high AND b2_close > b3_high AND b0_close < b3_high`
  - [x] Gap filter Bearish: nếu `filter_width > 0` → `(b1_low − b3_high) > atr * filter_width`
  - [x] Bear override Bull nếu cả hai True
  - [x] Return đúng `gap_top`, `gap_bottom` theo từng direction

### 1.2 Tests `tests/test_fvg_core.py`

- [x] **T39** `test_bull_detected` — giá trị cụ thể, verify `signal == BULL`, `gap_top`, `gap_bottom`
- [x] **T40** `test_bear_detected` — verify `signal == BEAR`
- [x] **T41** `test_bear_overrides_bull` — construct case cả hai True → phải là `BEAR`
- [x] **T42** `test_no_signal` — không có gap → `signal == None`
- [x] **T43** `test_filter_width_zero_no_atr_needed` — default không cần truyền `atr`
- [x] **T44** `test_filter_width_nonzero_requires_atr` → `ValueError` khi `atr=None`
- [x] **T45** `test_filter_width_filters_small_gap` — gap < threshold → `None`
- [x] **T46** `test_filter_width_passes_large_gap` — gap ≥ threshold → signal

### 1.3 Sửa `indicators/fvg.py`

- [x] **T05** Thêm `from indicators.fvg_core import detect_imfvg_from_bars, BULL, BEAR`
- [x] **T06** Thay ~10 dòng bull/bear detection bằng 1 lời gọi `detect_imfvg_from_bars(...)` — không truyền `filter_width` (default 0.0)
- [x] **T07** ✦ **Verify regression**: chạy `python scanner.py --timeframe 1MO --dry-run` với ít nhất 1 symbol thực → kết quả phải khớp với trước khi sửa

---

## Phase 2 — Dataclasses & Constants

> Mục tiêu: định nghĩa tất cả data structures trong `core/position_tracker.py`.
> Không phụ thuộc Phase 1. Có thể làm song song nhưng cần xong trước Phase 3.

### 2.1 `PositionConfig`

- [x] **T08** Implement dataclass với 10 params và defaults:
  - `filter_width: float = 0.0`
  - `atr_period: int = 14`
  - `tp_mult: float = 4.0`
  - `sl_mult: float = 2.0`
  - `ts_mult: float = 3.0`
  - `exit_on_wick: bool = True`
  - `ts_on_close: bool = True`
  - `exit_priority: str = "TP_FIRST"`
  - `slippage: float = 0.0`
  - `fee_per_trade: float = 0.0`
  - Docstring: ghi rõ coupling note (filter_width × atr_period)

### 2.2 `PositionState`

- [x] **T09** Implement dataclass với 17 fields:
  - `new_signal_detected: bool`
  - `signal_action: str | None` — `"OPEN" | "REVERSE" | "IGNORE" | None`
  - `is_holding: bool`
  - `direction: str | None`
  - `entry_date: str | None`
  - `gap_top, gap_bottom: float | None`
  - `entry_close: float | None`
  - `tp_level, sl_level: float | None`
  - `trailing_stop: float | None`
  - `atr_at_entry: float | None`
  - `bars_held: int`
  - `close_reason: str | None`
  - `close_price_at_exit: float | None`
  - `last_signal_type: str | None`
  - `last_signal_date: str | None`
  - `last_checked_bar_date: str | None`
  - Docstring: giải thích `bars_held` convention (entry bar = 0)

### 2.3 `Trade`

- [x] **T10** Implement dataclass với 14 fields + 6 properties:
  - Fields: `entry_date, exit_date, direction, entry_price, exit_price, actual_exit_price, fee_per_trade, close_reason, bars_held, gap_top, gap_bottom, atr_at_entry, tp_level, sl_level`
  - `signed_pnl` property — BULL: `actual_exit − entry`, BEAR: `entry − actual_exit` (dùng `actual_exit_price`, không phải `exit_price`)
  - `pnl_pct` property — `signed_pnl / entry_price`
  - `net_pnl_pct` property — `pnl_pct − fee_per_trade`
  - `rr_ratio` property — `signed_pnl / atr_at_entry` (signed, + win, − loss)
  - `is_win` property — `net_pnl_pct > 0`
  - `is_tp_hit` property — `close_reason == "TP_HIT"`
  - Docstring: clarify `actual_exit_price` cho P&L; `is_win` vs `is_tp_hit` distinction; memory warning khi mass optimization

### 2.4 `TradesSummary`

- [x] **T11** Implement dataclass với 13 fields + 8 properties:
  - Fields: `n_trades, n_wins, n_tp, n_sl, n_ts, n_reversed, total_bars: int`; `total_pnl_pct, total_net_pnl, total_rr, max_drawdown, sum_sq_rr, sum_sq_pnl: float`
  - `win_rate` — `n_wins / n_trades`
  - `avg_rr` — `total_rr / n_trades` (signed)
  - `avg_net_pnl` — `total_net_pnl / n_trades`
  - `avg_bars` — `total_bars / n_trades`
  - `expectancy` — `avg_rr` (NOT `win_rate × avg_rr`; docstring explain why + ATR-normalized unit)
  - `calmar` — `avg_net_pnl / abs(max_drawdown)` (0.0 nếu no drawdown; docstring: per-trade proxy, not annualized)
  - `std_rr` — `sqrt(sum_sq_rr/n − avg_rr²)`, 0.0 nếu `n < 2`
  - `std_pnl` — `sqrt(sum_sq_pnl/n − avg_net_pnl²)`, 0.0 nếu `n < 2`
  - `sharpe` — `avg_net_pnl / std_pnl` (0.0 nếu `std_pnl == 0`; docstring: per-trade proxy)
  - `from_accumulator(cls, acc)` classmethod
  - Docstring: equity model additive (WARNING về risk-of-ruin); MDD per-symbol only (không dùng cho portfolio); `max_drawdown ≤ 0` convention

### 2.5 `REASON_COUNTER_MAP`

- [x] **T12** Khai báo module-level dict:
  ```python
  REASON_COUNTER_MAP = {
      "TP_HIT": "n_tp",
      "SL_HIT": "n_sl",
      "TS_HIT": "n_ts",
      "REVERSED": "n_reversed",
  }
  ```
  - Comment: v3 additions sẽ thêm vào đây

---

## Phase 3 — Helper Functions

> Mục tiêu: tất cả building blocks của engine.
> Phụ thuộc Phase 2 (dataclasses). Test ngay sau mỗi hàm.

### 3.1 ATR & Signal Detection

- [x] **T13** `compute_atr(high, low, close, period: int) → pd.Series`:
  - True Range = `max(H−L, |H−prev_C|, |L−prev_C|)`
  - Rolling SMA của TR với `period`
  - Đầu tiên `period−1` bars là NaN
  - Period luôn từ param, không hardcode

- [x] **T47** Test: `test_compute_atr_correct` — verify với giá trị tính tay
- [x] **T48** Test: `test_compute_atr_insufficient_bars` — ít hơn `period` bars → toàn NaN, không crash

- [x] **T14** `_detect_imfvg_at(df, i, cfg) → tuple[str|None, dict]`:
  - Guard: `i < 3` → `(None, {})`
  - NaN guard cho `high, low, close` của 4 bars
  - Gọi `detect_imfvg_from_bars()` với `filter_width=cfg.filter_width`, truyền `atr` nếu `filter_width > 0`
  - Return `(signal, {"gap_top": ..., "gap_bottom": ..., "entry_close": ...})`

- [x] **T49** Test: `test_fvg_py_and_tracker_same_result` — regression: `fvg.analyze(df)` vs `_detect_imfvg_at(df, -1, cfg)` phải cho cùng signal trên cùng DataFrame

### 3.2 Exit Logic

- [x] **T15** `_ratchet_ts(bar, direction, ts, atr, cfg) → float`:
  - BULL: `new_ts = close − atr * cfg.ts_mult`; trả `max(ts, new_ts)` (chỉ tăng)
  - BEAR: `new_ts = close + atr * cfg.ts_mult`; trả `min(ts, new_ts)` (chỉ giảm)
  - TS ratchet luôn dùng `close` làm base (Pine behavior)

- [x] **T58** Test: `test_ratchet_ts_bull_only_increases` — qua 5 bars, assert TS monotonic non-decreasing
- [x] **T59** Test: `test_ratchet_ts_bear_only_decreases` — qua 5 bars, assert TS monotonic non-increasing

- [x] **T16** `_check_exit(bar, direction, tp_level, sl_level, ts, cfg) → tuple[str|None, float|None]`:
  - Tính `check_high`, `check_low` theo `cfg.exit_on_wick`
  - Tính `ts_trigger` và `ts_exit_price` theo `cfg.ts_on_close`:
    - `ts_on_close=True`: trigger = close, exit = close
    - `ts_on_close=False`: trigger = low/high (wick), exit = `ts` level (stop fill)
  - Áp dụng `cfg.exit_priority` (`"TP_FIRST"` hoặc `"SL_FIRST"`)
  - Priority luôn: `selected_first → other TP/SL → TS`
  - Return `(reason, price)` hoặc `(None, None)`

- [x] **T55** Test: `test_ts_exit_price_at_ts_level_when_wick` — `ts_on_close=False`, wick trigger → exit = ts level (không phải close)
- [x] **T56** Test: `test_exit_priority_tp_first` — cùng bar high ≥ TP và low ≤ SL → `TP_HIT`
- [x] **T57** Test: `test_exit_priority_sl_first` — `exit_priority="SL_FIRST"` → `SL_HIT` wins

### 3.3 Position Open & Cost

- [x] **T17** `_open_position(sig, meta, atr_i, bar_date_str, cfg) → dict`:
  - Tính `tp_level`, `sl_level`, `ts` theo direction và `cfg.xxx_mult`
  - Return dict với tất cả fields cần thiết để INSERT vào DB và mở state

- [x] **T18** `_apply_slippage(exit_price, direction, close_reason, cfg) → float`:
  - BULL: `exit * (1 − slippage)` (bán được giá thấp hơn)
  - BEAR: `exit * (1 + slippage)` (mua phải giá cao hơn)
  - Return `exit_price` nếu `cfg.slippage == 0.0`

### 3.4 Accumulator

- [x] **T19** `_accumulate_reason(summary_acc, close_reason, strict=True)`:
  - Lookup `close_reason` trong `REASON_COUNTER_MAP`
  - Found → `summary_acc[counter_key] += 1`
  - Not found + `strict=True` → `raise ValueError` (message bao gồm list known reasons)
  - Not found + `strict=False` → `summary_acc["n_unknown"] = get(..., 0) + 1` + `log.warning`

- [x] **T86** Test: `test_accumulate_reason_all_known` — 4 reasons đều map đúng
- [x] **T87** Test: `test_accumulate_reason_strict_raises` — unknown + strict → ValueError
- [x] **T88** Test: `test_accumulate_reason_strict_false_n_unknown` — unknown + non-strict → n_unknown tăng, không crash
- [x] **T89** Test: `test_reason_map_covers_all_known_reasons` — verify dict có đủ 4 keys
- [x] **T90** Test: `test_reason_map_dynamic_lookup` — thêm test reason vào map → hàm nhận ra

- [x] **T20** `_accumulate(summary_acc, exit_price, close_reason, bars_held, direction, entry_price, atr_at_entry, cfg)`:
  - Tính `actual_exit`, `signed_pnl`, `pnl_pct`, `net_pnl`, `rr`
  - Update: `n_trades, n_wins, total_pnl_pct, total_net_pnl, total_rr, total_bars`
  - Update: `sum_sq_rr += rr²`, `sum_sq_pnl += net_pnl²`
  - Update MDD: `equity += net_pnl`, `peak_equity = max(...)`, `max_drawdown = min(...)`
  - Gọi `_accumulate_reason(..., strict=True)`

### 3.5 State Utilities

- [x] **T21** `_no_update_state(position_row, reason) → PositionState`:
  - Trả `PositionState` với `close_reason=reason`, `is_holding=True` (vị thế không thay đổi)
  - Dùng bởi `check_latest_bar` khi không cần update

---

## Phase 4 — `scan_full_history`

> Mục tiêu: engine chính, bar-by-bar simulation.
> Phụ thuộc hoàn toàn Phase 3. Test từng sub-feature theo thứ tự.

- [ ] **T23** Implement `scan_full_history(df, cfg, return_trades=False, summarize_trades=False, atr_series=None)`:

  **Skeleton:**
  - [ ] Guard: `return_trades and summarize_trades` → `raise ValueError`
  - [ ] Guard: `len(df) < cfg.atr_period + 4` → return `None` hoặc `(None, [])` / `(None, empty_summary)`
  - [ ] Compute ATR: dùng `atr_series` nếu có, else `compute_atr(df, period=cfg.atr_period)`
  - [ ] Initialize state variables (direction, ts, tp_level, sl_level, entry_date, ...)
  - [ ] Initialize accumulator dict (nếu `summarize_trades`)

  **Main loop (thứ tự bắt buộc):**
  - [ ] `bars_held += 1` chỉ khi `direction is not None` (entry bar = 0)
  - [ ] Gọi `_ratchet_ts()` để cập nhật TS
  - [ ] **STEP 1**: `_check_exit()` nếu đang HOLDING → nếu exit: `_record_trade()` hoặc `_accumulate()` + `_clean_exit_state()`
  - [ ] **STEP 2**: `_detect_imfvg_at()` → set `signal_action`:
    - `direction is not None` + `sig == direction` → `signal_action = "IGNORE"`
    - `direction is not None` + `sig != direction` → `signal_action = "REVERSE"` + record REVERSED + clean + open mới
    - `direction is None` (no holding hoặc vừa exit step 1) → `signal_action = "OPEN"` + open
  - [ ] Sau khi open: `bars_held = 0`, reset `close_reason, exit_price`

  **Return:**
  - [ ] Build `PositionState` từ state cuối
  - [ ] `return_trades=False` → `return state`
  - [ ] `return_trades=True` → `return (state, trades_list)`
  - [ ] `summarize_trades=True` → `return (state, TradesSummary.from_accumulator(acc))`

**Tests — thêm vào `tests/test_position_tracker.py` sau từng sub-feature:**

- [ ] **T60** `test_bull_signal_detected` — DataFrame có BULL signal → `is_holding=True`, `direction=BULL`
- [ ] **T61** `test_bear_signal_detected` — tương tự BEAR
- [ ] **T62** `test_no_signal_no_position` — không có gap → `is_holding=False`
- [ ] **T63** `test_tp_hit` — BULL position, bar có `high >= tp_level` → `close_reason="TP_HIT"`
- [ ] **T64** `test_sl_hit` — BULL position, bar có `low <= sl_level` → `close_reason="SL_HIT"`
- [ ] **T65** `test_ts_hit` — TS ratchet lên, close vượt → `close_reason="TS_HIT"`
- [ ] **T66** `test_reversed` — BULL đang hold, BEAR signal mới → `close_reason="REVERSED"`, `direction=BEAR`
- [ ] **T67** ⚠️ `test_tp_and_new_signal_same_bar` — **case critical nhất**: cùng bar high ≥ TP VÀ signal mới → `close_reason="TP_HIT"`, `is_holding=True` (position mới mở), `signal_action="OPEN"`
- [ ] **T68** `test_exit_state_fully_reset` — sau SL_HIT, tất cả state fields phải là `None`
- [ ] **T69** `test_bars_held_entry_bar_is_zero` — bar entry: `bars_held=0`; sau 3 bars: `bars_held=3`
- [ ] **T70** `test_signal_action_ignore_same_direction`
- [ ] **T71** `test_signal_action_open`
- [ ] **T72** `test_signal_action_reverse`
- [ ] **T73** `test_return_trades_backward_compat` — `return_trades=False` → trả `PositionState`, không phải tuple
- [ ] **T85** `test_no_literal_in_engine` — chạy 2 configs khác nhau (`tp_mult=1.0` vs `4.0`) → kết quả phải khác nhau

---

## Phase 5 — `check_latest_bar`

> Phụ thuộc Phase 3 helpers. Test ngay.

- [ ] **T24** Implement `check_latest_bar(df, position_row, cfg) → PositionState`:

  **Guards (trả `_no_update_state`):**
  - [ ] `df is None or df.empty` → reason `"cache_unavailable"`
  - [ ] `last_bar_date = df.index[-1].tz_convert("Asia/Tokyo").date()` ← timezone-safe
  - [ ] `last_checked_at` từ DB là `"YYYY-MM-DD"` JST string → `date.fromisoformat(...)`
  - [ ] `last_bar_date <= last_checked` → reason `"no_new_bar"`
  - [ ] `len(df) < cfg.atr_period + 1` → reason `"insufficient_bars_for_atr"`
  - [ ] `atr_now = atr_series.iloc[-1]`; `isna(atr_now) or atr_now <= 0` → reason `"atr_not_ready"`

  **Logic (dùng `i = len(df) − 1`, bar = `df.iloc[-1]`):**
  - [ ] **STEP 1**: TP/SL/TS check (nếu `position_row["direction"]` != None)
  - [ ] **STEP 2**: `_detect_imfvg_at(df, i, cfg)` → set `signal_action`, `new_signal_detected`
  - [ ] Xử lý REVERSE, OPEN, IGNORE
  - [ ] Return `PositionState` với `last_checked_bar_date = last_bar_date.isoformat()`

**Tests:**

- [ ] **T50** `test_rr_ratio_signed_positive` — BULL win trade → `rr > 0`
- [ ] **T51** `test_rr_ratio_signed_negative` — BULL loss trade → `rr < 0`
- [ ] **T52** `test_net_pnl_includes_fee`
- [ ] **T53** `test_is_win_tp_hit_but_high_fee` — TP_HIT nhưng `net_pnl_pct < 0` → `is_win=False`
- [ ] **T54** `test_is_tp_hit_property` — `close_reason="TP_HIT"` → `is_tp_hit=True`; `close_reason="TS_HIT"` → `is_tp_hit=False`
- [ ] Test `check_latest_bar` guards: cache_unavailable, no_new_bar, insufficient_bars
- [ ] Test `check_latest_bar` với signal detection

---

## Phase 6 — Backtest Functions

> Phụ thuộc `scan_full_history`. Test metrics tính toán đúng.

- [ ] **T25** `backtest_symbol(symbol, cfg, atr_cache=None) → dict`:
  - `read_cache(symbol, timeframe)` → nếu None → `{}`
  - ATR cache theo `cfg.atr_period`
  - Gọi `scan_full_history(..., summarize_trades=True, atr_series=...)`
  - Tính và return metrics dict

- [ ] **T26** `backtest_portfolio(symbols, cfg, timeframe="1MO", weight_by="trades") → dict`:
  - Guard: `weight_by not in ("trades", "symbol")` → `raise ValueError`
  - `"trades"` mode: accumulate totals → weighted metrics
  - `"symbol"` mode: collect per-symbol summaries → equal-weight mean
  - **KHÔNG** trả `portfolio_max_drawdown` hay `portfolio_calmar`
  - Return keys: `portfolio_win_rate`, `portfolio_avg_rr`, `portfolio_expectancy_rr`, `portfolio_avg_net_pnl`, `portfolio_total_net_pnl_pct`, `portfolio_avg_bars`, `pct_tp/sl/ts/reversed`, `total_trades`, `n_symbols_with_data`, `n_symbols_no_data`, `weight_by`

**Tests:**

- [ ] **T74** `test_summarize_matches_trade_list` — `return_trades=True` và `summarize_trades=True` trên cùng df → mọi metric phải khớp (MDD, std_rr, std_pnl, n_wins, total_rr...)
- [ ] **T75** `test_return_trades_and_summarize_mutually_exclusive` → `ValueError`
- [ ] **T76** `test_atr_cache_same_result` — precomputed vs inline → cùng kết quả
- [ ] **T77** `test_std_rr_correct` — verify vs `numpy.std(rr_values)`
- [ ] **T78** `test_std_pnl_correct`
- [ ] **T79** `test_sharpe_positive` — profitable strategy → `sharpe > 0`
- [ ] **T80** `test_sharpe_zero_when_std_zero` — tất cả trades cùng P&L → `std=0`, `sharpe=0`
- [ ] **T81** `test_monotonic_tp_mult` — `tp_mult` tăng → `avg_bars` tăng (hoặc không giảm)
- [ ] **T82** `test_monotonic_filter_width_reduces_trades` — `filter_width` tăng → số trades ≤ trước
- [ ] **T83** `test_sl_first_priority_changes_results` — construct bar có cả TP và SL cùng hit
- [ ] **T84** `test_slippage_reduces_pnl` — `slippage > 0` → `avg_net_pnl < 0 slippage`
- [ ] **T91** `test_portfolio_win_rate_weighted` — symbol A 50 trades win 60%, B 2 trades win 0% → weighted ≈ 57.7% (không phải 30%)
- [ ] **T92** `test_portfolio_weight_by_symbol` — same setup → `"symbol"` mode = 30%
- [ ] **T93** `test_portfolio_no_max_drawdown_key` — key không được có trong result
- [ ] **T95** `test_portfolio_total_net_pnl_pct_naming` — key mới có, key cũ không có
- [ ] **T96** `test_portfolio_expectancy_rr_naming` — `portfolio_expectancy_rr` có, `portfolio_expectancy` không có

---

## Phase 7 — Database Layer (`position_monitor.py`)

> Mục tiêu: schema DB + CRUD helpers.
> Không phụ thuộc engine. Có thể làm song song Phase 4–6.

### 7.1 Schema & Init

- [ ] **T27** `init_positions_db(timeframe, conn)` — tạo 2 bảng + indexes:

  **`positions_{tf}`:**
  - `id INTEGER PRIMARY KEY AUTOINCREMENT`
  - `symbol, direction, entry_date, entry_close, gap_top, gap_bottom`: NOT NULL
  - `tp_level, sl_level, trailing_stop, atr_at_entry`: NOT NULL
  - `status TEXT DEFAULT 'HOLDING'`
  - `bars_held INTEGER DEFAULT 0`
  - `close_price_at_exit, last_signal_type, last_signal_date`: nullable
  - `last_checked_at TEXT` — "YYYY-MM-DD" JST (DATE string, KHÔNG phải UTC DATETIME)
  - `created_at DATETIME NOT NULL` — UTC ISO8601
  - `closed_at DATETIME` — UTC ISO8601
  - **Partial unique index**: `CREATE UNIQUE INDEX ... ON positions_{tf}(symbol) WHERE status='HOLDING'`

  **`position_history_{tf}`:**
  - `id INTEGER PRIMARY KEY AUTOINCREMENT`
  - `symbol, direction, entry_date, exit_date`: NOT NULL
  - `entry_price, exit_price, close_reason, bars_held, atr_at_entry, tp_level, sl_level`: NOT NULL
  - `created_at DATETIME NOT NULL`

  **Regular indexes:**
  - `idx_positions_{tf}_symbol`
  - `idx_positions_{tf}_sym_status` — `(symbol, status)` composite
  - `idx_positions_{tf}_entry_date`
  - `idx_pos_history_{tf}_symbol`
  - `idx_pos_history_{tf}_exit_date`
  - `idx_pos_history_{tf}_reason`

### 7.2 CRUD Helpers

- [ ] **T28** `_get_holding_position(conn, timeframe, symbol) → dict | None` — `SELECT ... WHERE symbol=? AND status='HOLDING' ORDER BY id DESC LIMIT 1`

- [ ] **T29** `_close_and_log(conn, timeframe, position_id, position_row, exit_date, close_reason, exit_price, bars_held)`:
  - `UPDATE positions_{tf} SET status=?, close_price_at_exit=?, closed_at=? WHERE id=?`
  - `INSERT INTO position_history_{tf} (...)` — tất cả entry fields từ `position_row` (DB row), không từ state
  - Caller phải gọi trong `with conn:` block

- [ ] **T30** `_insert_position(conn, timeframe, symbol, state)`:
  - `INSERT INTO positions_{tf} (...)`
  - Catch `sqlite3.IntegrityError` (partial index violation) → `log.error`, không crash

- [ ] **T31** `_update_position(conn, timeframe, position_id, state)`:
  - `UPDATE positions_{tf} SET trailing_stop=?, bars_held=?, last_checked_at=?, last_signal_type=?, last_signal_date=? WHERE id=?`

- [ ] **T32** `_process_symbol(conn, timeframe, symbol, state, bar_date)`:
  - Toàn bộ trong 1 `with conn:` block (atomic)
  - Nếu `state.close_reason` → `_close_and_log(...)` dùng `position_row` từ DB
  - Nếu `state.signal_action in ("OPEN", "REVERSE")` → `_insert_position(...)`
  - Nếu `is_holding` và không có action → `_update_position(...)`

**Tests (trong `tests/test_position_monitor.py`):**

- [ ] **T97** `test_partial_index_prevents_duplicate_holding` — INSERT 2 HOLDING cùng symbol → `IntegrityError`; 2 CLOSED cùng symbol → OK
- [ ] **T98** `test_close_and_log_uses_db_row_not_state` — REVERSED: history phải có direction của position cũ (BULL), không phải state mới (BEAR)
- [ ] **T99** `test_atomic_transaction` — close + insert new phải cùng commit

---

## Phase 8 — CLI (`position_monitor.py`)

> Phụ thuộc Phase 4–7 hoàn thành.

### 8.1 Logging

- [ ] **T33** `setup_logging(timeframe)` — tái sử dụng pattern từ `scanner.py` (`JSTFormatter`, file + stdout)

### 8.2 CLI Parse

- [ ] **T34** `parse_args()`:
  - `--timeframe` — "1MO" default
  - `--full-scan` — force scan toàn bộ cache
  - `--dry-run` — không ghi DB, không gửi Telegram
  - `--report` — in danh sách HOLDING và thoát

### 8.3 Full Scan Mode

- [ ] **T35** `run_full_scan(timeframe, cfg, dry_run)`:
  - Scan `cache/` → list tất cả `*_{timeframe}.parquet`
  - `read_cache(symbol, timeframe)` → nếu None: skip
  - `scan_full_history(df, cfg)` → nếu `is_holding=False`: skip
  - Nếu không `dry_run`: `_process_symbol(...)` với `_insert_position`
  - Log: "Full scan: N symbols, M holding"
- [ ] ✦ **Verify**: `python position_monitor.py --timeframe 1MO --dry-run --full-scan` với 5 symbols thực → output hợp lý, không crash

### 8.4 Normal Mode

- [ ] **T36** `run_normal(timeframe, cfg, dry_run)`:
  - `SELECT * FROM positions_{tf} WHERE status='HOLDING'`
  - Với mỗi HOLDING: `read_cache(symbol, timeframe)` → `check_latest_bar(df, pos_row, cfg)` → `_process_symbol(...)`
  - Log: "N positions checked, M closed, K updated"

### 8.5 Report & Notify

- [ ] **T37** `run_report(timeframe)`:
  - `SELECT * FROM positions_{tf} WHERE status='HOLDING' ORDER BY entry_date`
  - Print table: symbol | direction | entry_date | tp | sl | ts | bars_held

- [ ] **T38** `notify_positions(timeframe)`:
  - Format: `[1MO | POSITION MONITOR — YYYY-MM-DD]`
  - Group: HOLDING list + closed this run
  - Chunk 50 per message (reuse pattern từ `notifier.py`)
  - Dùng `send_telegram()` từ `core/notifier.py`

### 8.6 Integration Tests

- [ ] **T100** `test_tp_and_new_signal_same_bar_db` — integration: TP_HIT + signal mới cùng bar → chỉ 1 HOLDING row sau khi chạy
- [ ] **T101** `test_full_scan_mode` — mock cache với 3 symbols → verify 3 positions inserted
- [ ] **T102** `test_normal_mode_update_trailing_stop` — HOLDING position, bar mới → trailing_stop được update
- [ ] **T103** `test_no_update_when_bar_date_same_as_last_checked` — `last_checked_at` = ngày bar cuối → không update

---

## Milestone Checkpoints ✦

- [x] ✦ **M1** (Sau Phase 1): `python scanner.py --timeframe 1MO --dry-run` → kết quả giống trước khi sửa `fvg.py`
- [ ] ✦ **M2** (Sau Phase 4): `scan_full_history(df, cfg)` trên 5 symbols thực → kết quả hợp lý, T67 (`test_tp_and_new_signal_same_bar`) pass
- [ ] ✦ **M3** (Sau Phase 6): `backtest_portfolio([...], cfg, weight_by="trades")` trên 10 symbols thực → metrics có ý nghĩa, `portfolio_max_drawdown` không có trong result
- [ ] ✦ **M4** (Sau Phase 7): `init_positions_db` + `_process_symbol` với mock data → partial index hoạt động đúng (T97 pass)
- [ ] ✦ **M5** (Sau Phase 8 full-scan): `python position_monitor.py --timeframe 1MO --dry-run --full-scan` → scan 50+ symbols thực, không crash, log hợp lý
- [ ] ✦ **M6** (Sau Phase 8 normal): `python position_monitor.py --timeframe 1MO --dry-run` → check HOLDING positions, update TS log hiển thị
- [ ] ✦ **M7** (Final): Tất cả 103 tests pass. `python position_monitor.py --timeframe 1MO` → Telegram nhận message.

---

## Không làm ở v2 (defer sang v3)

- TS reset khi signal cùng hướng (Pine-exact `ts_reset='Every Signals'`)
- Portfolio MDD đúng (cần time-sorted equity curve cross-symbol)
- Multiplicative equity model (`equity *= 1 + net_pnl_pct`)
- Annual Calmar ratio
- `backtest_portfolio` parallel (`multiprocessing.Pool`)
- AI optimizer integration (Bayesian/genetic)
- Pyramiding
- `weight_by="capital"` (market cap weighted)
- Sharpe annualized

---

## Quick Reference — Thiết kế cốt lõi

| Quyết định | Giá trị |
|-----------|---------|
| Source of truth FVG | `fvg_core.py` — không duplicate |
| Thứ tự logic trong loop | **TP/SL/TS → signal** (không đảo) |
| `bars_held` tại entry bar | = 0 |
| TP/SL trigger | `high`/`low` (configurable `exit_on_wick`) |
| TS trigger | `close` (configurable `ts_on_close`) |
| TS exit price khi wick trigger | TS level (stop fill), không phải close |
| Exit priority | `"TP_FIRST"` default (configurable) |
| HOLDING constraint | Partial unique index SQLite |
| `_close_and_log` source | DB row (không phải runtime state) |
| Transaction scope | Atomic: SELECT + close + insert new |
| `expectancy` | = `avg_rr` (NOT `win_rate × avg_rr`) |
| `calmar` | = `avg_net_pnl / abs(max_drawdown)` (unit-consistent) |
| Portfolio MDD | Không tính (trades overlap time) |
| Portfolio weighting | `"trades"` default, `"symbol"` option |
| `portfolio_net_pnl` key | → `portfolio_total_net_pnl_pct` |
| `portfolio_expectancy` key | → `portfolio_expectancy_rr` |
| `REASON_COUNTER_MAP` | Dynamic dict — thêm reason: 1 dòng |
| Equity model | Additive (fixed-size per trade) |
| std computation | Population variance từ sum of squares (1 pass) |
| Memory (mass optimization) | `summarize_trades=True` — không tạo Trade objects |
