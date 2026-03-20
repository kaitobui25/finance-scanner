# IMFVG Position Monitor — Implementation Plan v2 (Patch Round 8 — Final)
> Reviewer feedback round 8: expectancy fix + MDD + robustness
> Updated: 2026-03-20

---

## 1. `expectancy` — fix công thức sai

### Tại sao `win_rate × avg_rr` sai

```
Ví dụ: 5 trades, 3 win (+2.0 RR), 2 loss (−1.0 RR)

avg_rr    = (3×2.0 + 2×(−1.0)) / 5 = 4/5 = +0.8   ← đây đã là expectancy
win_rate  = 3/5 = 0.6

Công thức cũ: 0.6 × 0.8 = 0.48  ← không có ý nghĩa (double-weight win)

Classic trading expectancy:
  E = win_rate × avg_win_rr + (1 − win_rate) × avg_loss_rr
  E = 0.6 × 2.0 + 0.4 × (−1.0) = 1.2 − 0.4 = 0.8  ← bằng avg_rr

→ avg_rr (signed) = expectancy by definition khi rr đã signed.
  Không cần nhân thêm win_rate.
```

### Fix trong `TradesSummary`

```python
@property
def expectancy(self) -> float:
    """
    Trading expectancy: avg P&L per trade, normalized by ATR at entry.

    = avg_rr (signed)

    Tại sao không phải win_rate × avg_rr:
        avg_rr đã là weighted mean của win (+) và loss (−) trades.
        Nhân thêm win_rate → double-weight phần win → sai công thức.

    Chuẩn classic:
        E = win_rate × avg_win_rr + (1 − win_rate) × avg_loss_rr
        E = avg_rr  (tương đương khi rr đã có dấu)

    Interpretation:
        > 0: strategy có positive expectancy (profitable theo ATR basis)
        < 0: strategy thua dài hạn
        = 0: breakeven (sau khi tính fee nếu net_pnl dùng)
    """
    return self.avg_rr
```

**Fix tương tự trong `backtest_portfolio()`:**
```python
# TRƯỚC (sai):
"portfolio_expectancy": (totals["n_wins"] / n) * (totals["total_rr"] / n),

# SAU (đúng):
"portfolio_expectancy": totals["total_rr"] / n,   # = avg_rr = expectancy
```

---

## 2. `_accumulate` — explicit if/elif thay vì string parsing

### Vấn đề

```python
# TRƯỚC — fragile:
summary_acc[f"n_{close_reason.split('_')[0].lower()}"] += 1
# "TP_HIT"   → "n_tp"        ✓
# "SL_HIT"   → "n_sl"        ✓
# "TS_HIT"   → "n_ts"        ✓
# "REVERSED" → "n_reversed"  ✓
# "STOP_LOSS_HIT" → "n_stop" → KeyError  ← future-proof fail
```

### Fix

```python
def _accumulate_reason(summary_acc: dict, close_reason: str) -> None:
    """
    Update reason counter. Explicit mapping — không dùng string parsing.
    Thêm reason mới ở đây khi cần, không sợ break silent.
    """
    if close_reason == "TP_HIT":
        summary_acc["n_tp"]       += 1
    elif close_reason == "SL_HIT":
        summary_acc["n_sl"]       += 1
    elif close_reason == "TS_HIT":
        summary_acc["n_ts"]       += 1
    elif close_reason == "REVERSED":
        summary_acc["n_reversed"] += 1
    else:
        # Unknown reason — log warning, không crash
        # Để phát hiện khi thêm reason mới mà quên update đây
        log.warning(f"_accumulate_reason: unknown close_reason={close_reason!r}")
```

**Tách thành hàm riêng** (`_accumulate_reason`) vì sẽ gọi từ `_accumulate()` — dễ test riêng.

---

## 3. `Trade` — docstring clarify + memory warning

```python
@dataclass
class Trade:
    """
    In-memory record của 1 completed trade.

    Price fields:
        entry_price:       Close tại bar entry. Chưa apply slippage.
                           (Slippage apply khi exit, không khi entry trong model này)
        exit_price:        Fill price lý thuyết trước slippage.
                           TP_HIT  → tp_level
                           SL_HIT  → sl_level
                           TS_HIT (ts_on_close=True)  → bar["close"]
                           TS_HIT (ts_on_close=False) → ts_value (stop fill level)
                           REVERSED → bar["close"]
        actual_exit_price: exit_price ± slippage. Đây là giá thực sự dùng để tính P&L.
                           BULL: actual = exit × (1 − slippage)  [bán được thấp hơn]
                           BEAR: actual = exit × (1 + slippage)  [mua đắt hơn]

    P&L calculation:
        Tất cả P&L dùng actual_exit_price (sau slippage), KHÔNG phải exit_price.
        signed_pnl, pnl_pct, net_pnl_pct đều từ actual_exit_price.

    Memory note:
        Mỗi Trade ~300 bytes. 500 symbols × 50 trades = 7.5 MB (OK).
        Nhưng 500 symbols × 1000 configs × 50 trades = 7.5 GB (OOM risk).
        Khi chạy mass optimization: dùng summarize_trades=True thay vì return_trades=True.
    """
    entry_date:         str
    exit_date:          str
    direction:          str
    entry_price:        float
    exit_price:         float         # before slippage
    actual_exit_price:  float         # after slippage — dùng cho P&L
    fee_per_trade:      float         # from cfg.fee_per_trade at creation time
    close_reason:       str
    bars_held:          int
    gap_top:            float
    gap_bottom:         float
    atr_at_entry:       float
    tp_level:           float
    sl_level:           float

    @property
    def signed_pnl(self) -> float:
        """
        P&L tuyệt đối CÓ DẤU, sau slippage, trước fee.

        Dùng actual_exit_price (not exit_price) để reflect slippage impact.
        BULL: actual_exit_price − entry_price  (+win, −loss)
        BEAR: entry_price − actual_exit_price  (+win, −loss)
        """
        if self.direction == BULL:
            return self.actual_exit_price - self.entry_price
        else:
            return self.entry_price - self.actual_exit_price

    @property
    def pnl_pct(self) -> float:
        """P&L % sau slippage, trước fee. = signed_pnl / entry_price."""
        return self.signed_pnl / self.entry_price if self.entry_price != 0 else 0.0

    @property
    def net_pnl_pct(self) -> float:
        """
        P&L % sau slippage VÀ sau fee (round-trip).
        Đây là số thực tế trader nhận — dùng cho AI fitness.
        """
        return self.pnl_pct - self.fee_per_trade

    @property
    def rr_ratio(self) -> float:
        """
        Risk-Reward ratio CÓ DẤU. = signed_pnl / atr_at_entry.
        + = win trade, − = loss trade.
        avg_rr across trades = expectancy của strategy.
        """
        return self.signed_pnl / self.atr_at_entry if self.atr_at_entry > 0 else 0.0

    @property
    def is_win(self) -> bool:
        """True nếu net_pnl_pct > 0 (sau cả slippage và fee)."""
        return self.net_pnl_pct > 0
```

---

## 4. `TradesSummary` — thêm MDD + Calmar

### MDD accumulation on-the-fly

```python
# Thêm vào summary_acc trong scan_full_history khi summarize_trades=True:
summary_acc = {
    "n_trades":      0,
    "n_wins":        0,
    "total_pnl_pct": 0.0,
    "total_net_pnl": 0.0,
    "total_rr":      0.0,
    "n_tp":          0,
    "n_sl":          0,
    "n_ts":          0,
    "n_reversed":    0,
    "total_bars":    0,
    # MDD tracking
    "equity":        0.0,   # running cumulative net_pnl_pct
    "peak_equity":   0.0,   # high-water mark
    "max_drawdown":  0.0,   # most negative drawdown (âm hoặc 0)
}

def _accumulate(summary_acc, exit_price, close_reason, bars_held,
                direction, entry_price, atr_at_entry, cfg):
    """Accumulate metrics. Không tạo Trade object."""
    actual_exit = _apply_slippage(exit_price, direction, close_reason, cfg)
    signed_pnl  = (actual_exit - entry_price) if direction == BULL \
                  else (entry_price - actual_exit)
    pnl_pct     = signed_pnl / entry_price if entry_price != 0 else 0.0
    net_pnl     = pnl_pct - cfg.fee_per_trade
    rr          = signed_pnl / atr_at_entry if atr_at_entry > 0 else 0.0

    summary_acc["n_trades"]      += 1
    summary_acc["n_wins"]        += 1 if net_pnl > 0 else 0
    summary_acc["total_pnl_pct"] += pnl_pct
    summary_acc["total_net_pnl"] += net_pnl
    summary_acc["total_rr"]      += rr
    summary_acc["total_bars"]    += bars_held
    _accumulate_reason(summary_acc, close_reason)

    # MDD tracking — accumulate equity curve on-the-fly
    summary_acc["equity"]       += net_pnl
    summary_acc["peak_equity"]   = max(summary_acc["peak_equity"],
                                       summary_acc["equity"])
    current_dd = summary_acc["equity"] - summary_acc["peak_equity"]  # ≤ 0
    summary_acc["max_drawdown"]  = min(summary_acc["max_drawdown"], current_dd)
```

### `TradesSummary` dataclass — version cuối

```python
@dataclass
class TradesSummary:
    """
    Pre-computed metrics từ trade sequence. Memory-efficient alternative to List[Trade].

    MDD convention:
        max_drawdown là số âm (e.g., −0.15 = drawdown 15%).
        abs(max_drawdown) để so sánh hoặc display.
        max_drawdown = 0.0 khi equity chưa bao giờ dưới peak (no drawdown).

    Calmar ratio:
        avg_rr / abs(max_drawdown).
        Higher = better risk-adjusted return.
        0.0 nếu max_drawdown = 0 (không có drawdown → không tính được).
    """
    n_trades:      int
    n_wins:        int
    n_tp:          int
    n_sl:          int
    n_ts:          int
    n_reversed:    int
    total_bars:    int
    total_pnl_pct: float
    total_net_pnl: float
    total_rr:      float
    max_drawdown:  float   # ≤ 0, e.g. −0.15

    # --- Computed properties ---

    @property
    def win_rate(self) -> float:
        return self.n_wins / self.n_trades if self.n_trades > 0 else 0.0

    @property
    def avg_rr(self) -> float:
        """Signed avg RR. > 0 = positive expectancy per ATR."""
        return self.total_rr / self.n_trades if self.n_trades > 0 else 0.0

    @property
    def avg_net_pnl(self) -> float:
        return self.total_net_pnl / self.n_trades if self.n_trades > 0 else 0.0

    @property
    def avg_bars(self) -> float:
        return self.total_bars / self.n_trades if self.n_trades > 0 else 0.0

    @property
    def expectancy(self) -> float:
        """
        Trading expectancy = avg_rr (signed).

        avg_rr already weights wins (+) and losses (−) proportionally.
        Multiplying by win_rate again would double-weight wins → wrong.

        Classic formula for verification:
            E = win_rate × avg_win_rr + (1 − win_rate) × avg_loss_rr
            E = avg_rr  (equivalent when rr is signed)
        """
        return self.avg_rr   # NOT win_rate × avg_rr

    @property
    def calmar(self) -> float:
        """
        Calmar ratio = expectancy / abs(max_drawdown).
        Risk-adjusted return: higher = better.
        0.0 nếu không có drawdown (undefined, không phải infinity).
        """
        if self.max_drawdown == 0.0:
            return 0.0
        return self.expectancy / abs(self.max_drawdown)

    @classmethod
    def from_accumulator(cls, acc: dict) -> "TradesSummary":
        """Build từ accumulator dict sau khi scan xong."""
        return cls(
            n_trades      = acc["n_trades"],
            n_wins        = acc["n_wins"],
            n_tp          = acc["n_tp"],
            n_sl          = acc["n_sl"],
            n_ts          = acc["n_ts"],
            n_reversed    = acc["n_reversed"],
            total_bars    = acc["total_bars"],
            total_pnl_pct = acc["total_pnl_pct"],
            total_net_pnl = acc["total_net_pnl"],
            total_rr      = acc["total_rr"],
            max_drawdown  = acc["max_drawdown"],
        )
```

---

## 5. `backtest_portfolio` — fix expectancy + thêm MDD portfolio

```python
def backtest_portfolio(symbols, cfg, timeframe="1MO") -> dict:
    atr_cache = {}
    totals = {
        "n_trades":     0, "n_wins":       0,
        "total_rr":     0.0, "total_net_pnl": 0.0,
        "total_bars":   0,
        "n_tp":         0, "n_sl":          0,
        "n_ts":         0, "n_reversed":    0,
        # Portfolio-level MDD (equity across all symbols, sequential)
        "equity":       0.0, "peak_equity":  0.0,
        "max_drawdown": 0.0,
    }
    n_with_data = n_no_data = 0

    for symbol in symbols:
        df = read_cache(symbol, timeframe)
        if df is None or len(df) < cfg.atr_period + 4:
            n_no_data += 1
            continue

        if cfg.atr_period not in atr_cache:
            atr_cache[cfg.atr_period] = compute_atr(
                df["high"], df["low"], df["close"], period=cfg.atr_period
            )

        _, summary = scan_full_history(
            df, cfg, summarize_trades=True,
            atr_series=atr_cache[cfg.atr_period],
        )

        if summary.n_trades == 0:
            continue

        n_with_data          += 1
        totals["n_trades"]   += summary.n_trades
        totals["n_wins"]     += summary.n_wins
        totals["total_rr"]   += summary.total_rr
        totals["total_net_pnl"] += summary.total_net_pnl
        totals["total_bars"] += summary.total_bars
        totals["n_tp"]       += summary.n_tp
        totals["n_sl"]       += summary.n_sl
        totals["n_ts"]       += summary.n_ts
        totals["n_reversed"] += summary.n_reversed

        # Portfolio equity: aggregate sequentially
        totals["equity"]     += summary.total_net_pnl
        totals["peak_equity"] = max(totals["peak_equity"], totals["equity"])
        dd = totals["equity"] - totals["peak_equity"]
        totals["max_drawdown"] = min(totals["max_drawdown"], dd)

    n = totals["n_trades"]
    if n == 0:
        return {"portfolio_win_rate": 0, "portfolio_expectancy": 0,
                "total_trades": 0, "n_symbols_with_data": n_with_data}

    avg_rr = totals["total_rr"] / n   # = expectancy

    return {
        "portfolio_win_rate":   totals["n_wins"] / n,        # weighted ✓
        "portfolio_avg_rr":     avg_rr,
        "portfolio_expectancy": avg_rr,                       # = avg_rr, NOT win_rate × avg_rr ✓
        "portfolio_avg_bars":   totals["total_bars"] / n,
        "portfolio_net_pnl":    totals["total_net_pnl"],
        "portfolio_max_drawdown": totals["max_drawdown"],     # ≤ 0
        "portfolio_calmar":     avg_rr / abs(totals["max_drawdown"])
                                if totals["max_drawdown"] != 0 else 0.0,
        "pct_tp":               totals["n_tp"] / n,
        "pct_sl":               totals["n_sl"] / n,
        "pct_ts":               totals["n_ts"] / n,
        "pct_reversed":         totals["n_reversed"] / n,
        "total_trades":         n,
        "n_symbols_with_data":  n_with_data,
        "n_symbols_no_data":    n_no_data,
    }
```

---

## 6. Test cases bổ sung round 8

```python
# tests/test_position_tracker.py

def test_expectancy_equals_avg_rr():
    """
    expectancy phải bằng avg_rr, không phải win_rate × avg_rr.
    Verify với ví dụ số cụ thể.
    """
    summary = TradesSummary(
        n_trades=5, n_wins=3,
        total_rr=4.0,   # 3×(+2.0) + 2×(-1.0) = 4.0
        # ... other fields
    )
    assert summary.avg_rr      == pytest.approx(0.8)
    assert summary.expectancy  == pytest.approx(0.8)   # không phải 0.6 × 0.8 = 0.48


def test_expectancy_not_double_weighted():
    """
    win_rate × avg_rr ≠ avg_rr → verify expectancy dùng avg_rr.
    """
    summary = _make_summary(n_trades=5, n_wins=3, total_rr=4.0)
    wrong_formula = summary.win_rate * summary.avg_rr   # = 0.6 × 0.8 = 0.48
    assert summary.expectancy != pytest.approx(wrong_formula), \
        "expectancy should NOT be win_rate × avg_rr"
    assert summary.expectancy == pytest.approx(summary.avg_rr)


def test_accumulate_reason_unknown_logs_warning(caplog):
    """Unknown reason → log warning, không crash."""
    acc = {"n_tp": 0, "n_sl": 0, "n_ts": 0, "n_reversed": 0}
    with caplog.at_level(logging.WARNING):
        _accumulate_reason(acc, "UNKNOWN_REASON")
    assert "unknown close_reason" in caplog.text
    # Không raise exception


def test_accumulate_reason_explicit_mapping():
    """Tất cả known reasons phải map đúng."""
    acc = {"n_tp": 0, "n_sl": 0, "n_ts": 0, "n_reversed": 0}
    for reason in ["TP_HIT", "SL_HIT", "TS_HIT", "REVERSED"]:
        _accumulate_reason(acc, reason)
    assert acc["n_tp"]       == 1
    assert acc["n_sl"]       == 1
    assert acc["n_ts"]       == 1
    assert acc["n_reversed"] == 1


def test_max_drawdown_negative():
    """MDD phải ≤ 0."""
    df  = _load_real_or_mock_df(n_bars=60)
    cfg = PositionConfig(fee_per_trade=0.001)
    _, summary = scan_full_history(df, cfg, summarize_trades=True)
    assert summary.max_drawdown <= 0.0, \
        f"max_drawdown should be ≤ 0, got {summary.max_drawdown}"


def test_max_drawdown_zero_when_equity_monotonic():
    """
    Nếu mọi trade đều win (equity chỉ tăng) → MDD = 0.
    Hiếm trong thực tế nhưng cần handle đúng.
    """
    df = _build_all_win_trades_df()   # mock: mọi trade đều TP_HIT
    cfg = PositionConfig(slippage=0.0, fee_per_trade=0.0)
    _, summary = scan_full_history(df, cfg, summarize_trades=True)
    assert summary.max_drawdown == pytest.approx(0.0)
    assert summary.calmar       == pytest.approx(0.0)   # undefined → 0


def test_calmar_higher_is_better():
    """
    Strategy A: expectancy=1.0, MDD=-0.5 → calmar=2.0
    Strategy B: expectancy=1.0, MDD=-2.0 → calmar=0.5
    A tốt hơn B về risk-adjusted → calmar_A > calmar_B.
    """
    summary_a = _make_summary_with(expectancy=1.0, max_drawdown=-0.5)
    summary_b = _make_summary_with(expectancy=1.0, max_drawdown=-2.0)
    assert summary_a.calmar > summary_b.calmar


def test_summarize_mdd_matches_trade_list_mdd():
    """
    MDD từ summarize_trades phải khớp với MDD tính từ List[Trade].
    """
    df  = _load_real_or_mock_df(n_bars=60)
    cfg = PositionConfig(fee_per_trade=0.002)

    _, trades  = scan_full_history(df, cfg, return_trades=True)
    _, summary = scan_full_history(df, cfg, summarize_trades=True)

    # Tính MDD từ trade list
    equity = peak = 0.0
    mdd = 0.0
    for t in trades:
        equity += t.net_pnl_pct
        peak = max(peak, equity)
        mdd  = min(mdd, equity - peak)

    assert summary.max_drawdown == pytest.approx(mdd, rel=1e-6)
```

---

## 7. Tổng kết `TradesSummary` — version cuối (11 fields + 5 properties)

```python
@dataclass
class TradesSummary:
    # Raw accumulators (11 fields)
    n_trades, n_wins, n_tp, n_sl, n_ts, n_reversed, total_bars: int
    total_pnl_pct, total_net_pnl, total_rr: float
    max_drawdown: float   # ≤ 0

    # Computed properties (5)
    @property win_rate    → n_wins / n_trades
    @property avg_rr      → total_rr / n_trades         (signed)
    @property avg_net_pnl → total_net_pnl / n_trades
    @property avg_bars    → total_bars / n_trades
    @property expectancy  → avg_rr                       # NOT win_rate × avg_rr ✓
    @property calmar      → expectancy / abs(max_drawdown)

    @classmethod from_accumulator(cls, acc) → TradesSummary
```

**AI fitness function candidates:**
```python
# Option 1: maximize expectancy (return per ATR)
fitness = summary.expectancy

# Option 2: maximize Calmar (risk-adjusted)
fitness = summary.calmar

# Option 3: maximize net_pnl weighted by drawdown
fitness = summary.total_net_pnl / (1 + abs(summary.max_drawdown))

# Option 4: composite
fitness = summary.win_rate * summary.avg_rr / (1 + abs(summary.max_drawdown))
```

## 9. v3 TODO

```python
# v3:
# 1. TS reset khi signal cùng hướng (Pine-exact)
# 2. backtest_portfolio parallel (multiprocessing.Pool) — scan_full_history là pure
# 3. AI optimizer: Bayesian (skopt) / genetic (DEAP)
# 4. equity_curve: list[float] trong TradesSummary (nếu cần plot)
# 5. Sharpe ratio: avg_net_pnl / std(net_pnl per trade) — cần store per-trade pnl
# 6. Pyramiding
# 7. P&L dashboard từ position_history DB
```