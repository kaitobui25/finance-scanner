# IMFVG Position Monitor — Implementation Plan
> Reviewer feedback round 11: API clarity + statistical completeness
> Updated: 2026-03-20

---

## 1. `portfolio_total_net_pnl_pct` — rename để tránh nhầm

```python
# TRƯỚC (ambiguous):
"portfolio_net_pnl": totals["total_net_pnl"]
# User có thể hiểu: "portfolio return = X%"
# Thực tế: "sum of per-trade net_pnl_pct"

# SAU (explicit):
"portfolio_total_net_pnl_pct": totals["total_net_pnl"]
# Rõ ràng: sum (not avg), per-trade (not capital), pct (fraction)
```

Tương tự: giữ `total_net_pnl` trong `TradesSummary` (internal field, context rõ trong dataclass).
Chỉ rename key trong `backtest_portfolio()` return dict (API-facing, cần self-documenting).

---

## 2. `portfolio_expectancy_rr` — explicit unit trong key name

### Vấn đề

"Expectancy" trong trading community có nhiều nghĩa:
- **Dollar expectancy**: `win_rate × avg_win_$ − loss_rate × avg_loss_$` (money per trade)
- **RR expectancy**: `avg_rr` (ATR-normalized, dimensionless)

Nếu user integrate với tool khác và expect dollar-based → bug không rõ nguồn.

### Fix trong `backtest_portfolio()` return dict

```python
# TRƯỚC (ambiguous):
"portfolio_expectancy": avg_rr

# SAU (explicit unit):
"portfolio_avg_rr":         avg_rr,    # ATR-normalized signed avg RR
"portfolio_expectancy_rr":  avg_rr,    # alias, same value, explicit "rr" suffix
```

### `TradesSummary.expectancy` — giữ tên, strengthen docstring

```python
@property
def expectancy(self) -> float:
    """
    Trading expectancy (ATR-normalized) = avg_rr.

    UNIT: ATR per trade (dimensionless ratio), NOT dollar per trade.
    This is RR-based expectancy, not dollar-based expectancy.

    Dollar expectancy formula (for reference):
        E_$ = win_rate × avg_win_$ − loss_rate × avg_loss_$
    RR expectancy (this property):
        E_rr = avg(signed rr_ratio across all trades) = avg_rr

    Both formulas give the same sign (positive = profitable strategy),
    but different magnitudes. Use avg_rr for ATR-normalized comparison
    across symbols and time periods.
    """
    return self.avg_rr
```

---

## 3. `REASON_MAP` — dynamic mapping thay cho if/elif

```python
# core/position_tracker.py — module level constant

# Map close_reason → accumulator key
# Thêm reason mới: chỉ cần thêm 1 entry vào đây.
# TradesSummary cũng cần update field tương ứng.
REASON_COUNTER_MAP: dict[str, str] = {
    "TP_HIT":   "n_tp",
    "SL_HIT":   "n_sl",
    "TS_HIT":   "n_ts",
    "REVERSED": "n_reversed",
    # v3 additions (khi implement):
    # "PYRAMID_EXIT": "n_pyramid",
    # "PARTIAL_EXIT": "n_partial",
}


def _accumulate_reason(
    summary_acc:  dict,
    close_reason: str,
    strict:       bool = True,
) -> None:
    """
    Update reason counter using REASON_COUNTER_MAP.

    Adding a new exit reason (v3+):
        1. Add entry to REASON_COUNTER_MAP above
        2. Add corresponding field to TradesSummary
        3. Initialize in summary_acc dict
        4. Run tests with strict=True to verify

    Args:
        strict: True  → raise ValueError on unknown reason (dev/test default)
                False → increment n_unknown, log warning (resilient batch)

    Raises:
        ValueError: when strict=True and close_reason not in REASON_COUNTER_MAP.
    """
    counter_key = REASON_COUNTER_MAP.get(close_reason)

    if counter_key is not None:
        summary_acc[counter_key] += 1
    elif strict:
        raise ValueError(
            f"_accumulate_reason: unknown close_reason={close_reason!r}. "
            f"Known reasons: {list(REASON_COUNTER_MAP)}. "
            f"Add to REASON_COUNTER_MAP and TradesSummary when adding new exit reasons."
        )
    else:
        summary_acc["n_unknown"] = summary_acc.get("n_unknown", 0) + 1
        log.warning(
            f"_accumulate_reason: unknown reason={close_reason!r}, "
            f"counted in n_unknown (strict=False)"
        )
```

---

## 4. `backtest_portfolio(weight_by)` — configurable weighting

```python
def backtest_portfolio(
    symbols:   list[str],
    cfg:       PositionConfig,
    timeframe: str = "1MO",
    weight_by: str = "trades",   # "trades" | "symbol"
) -> dict:
    """
    Args:
        weight_by:
            "trades" (default): Metrics weighted by n_trades per symbol.
                Statistical rationale: more trades = stronger evidence.
                Risk: high-frequency symbols dominate.
                Use when: maximizing statistical confidence.

            "symbol": Equal weight per symbol (mean of per-symbol summaries).
                Equal diversification rationale: each market gets same voice.
                Risk: low-evidence symbols (few trades) have same weight as high-evidence.
                Use when: simulating equal-weight portfolio allocation.

    Note on "symbol" mode:
        When weight_by="symbol", metrics are arithmetic mean of per-symbol metrics.
        Example: win_rate = mean([symbol_A_win_rate, symbol_B_win_rate, ...])
        This differs from "trades" mode: mean([0.6, 0.0]) = 0.3 vs 30/52 = 0.577
    """
    if weight_by not in ("trades", "symbol"):
        raise ValueError(f"weight_by must be 'trades' or 'symbol', got {weight_by!r}")

    atr_cache    = {}
    summaries    = []    # collect per-symbol TradesSummary for "symbol" mode
    totals       = {     # for "trades" mode
        "n_trades": 0, "n_wins": 0,
        "total_rr": 0.0, "total_net_pnl": 0.0, "total_bars": 0,
        "n_tp": 0, "n_sl": 0, "n_ts": 0, "n_reversed": 0,
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

        n_with_data += 1

        if weight_by == "trades":
            totals["n_trades"]      += summary.n_trades
            totals["n_wins"]        += summary.n_wins
            totals["total_rr"]      += summary.total_rr
            totals["total_net_pnl"] += summary.total_net_pnl
            totals["total_bars"]    += summary.total_bars
            totals["n_tp"]          += summary.n_tp
            totals["n_sl"]          += summary.n_sl
            totals["n_ts"]          += summary.n_ts
            totals["n_reversed"]    += summary.n_reversed
        else:  # "symbol"
            summaries.append(summary)

    # Build result
    meta = {
        "total_trades":          0,
        "n_symbols_with_data":   n_with_data,
        "n_symbols_no_data":     n_no_data,
        "weight_by":             weight_by,
    }

    if weight_by == "trades":
        n = totals["n_trades"]
        if n == 0:
            return {**meta, "portfolio_win_rate": 0.0, "portfolio_avg_rr": 0.0,
                    "portfolio_expectancy_rr": 0.0}
        meta["total_trades"] = n
        avg_rr = totals["total_rr"] / n
        return {
            **meta,
            "portfolio_win_rate":          totals["n_wins"]        / n,
            "portfolio_avg_rr":            avg_rr,
            "portfolio_expectancy_rr":     avg_rr,              # explicit unit in key
            "portfolio_avg_net_pnl":       totals["total_net_pnl"] / n,
            "portfolio_total_net_pnl_pct": totals["total_net_pnl"],   # renamed
            "portfolio_avg_bars":          totals["total_bars"]     / n,
            "pct_tp":                      totals["n_tp"]           / n,
            "pct_sl":                      totals["n_sl"]           / n,
            "pct_ts":                      totals["n_ts"]           / n,
            "pct_reversed":                totals["n_reversed"]     / n,
        }

    else:  # "symbol" — equal weight
        if not summaries:
            return {**meta, "portfolio_win_rate": 0.0, "portfolio_avg_rr": 0.0,
                    "portfolio_expectancy_rr": 0.0}
        s = len(summaries)
        meta["total_trades"] = sum(sm.n_trades for sm in summaries)
        avg_rr = sum(sm.avg_rr for sm in summaries) / s
        return {
            **meta,
            "portfolio_win_rate":          sum(sm.win_rate     for sm in summaries) / s,
            "portfolio_avg_rr":            avg_rr,
            "portfolio_expectancy_rr":     avg_rr,
            "portfolio_avg_net_pnl":       sum(sm.avg_net_pnl  for sm in summaries) / s,
            "portfolio_total_net_pnl_pct": sum(sm.total_net_pnl for sm in summaries),
            "portfolio_avg_bars":          sum(sm.avg_bars      for sm in summaries) / s,
            "pct_tp":     sum(sm.n_tp       / sm.n_trades for sm in summaries) / s,
            "pct_sl":     sum(sm.n_sl       / sm.n_trades for sm in summaries) / s,
            "pct_ts":     sum(sm.n_ts       / sm.n_trades for sm in summaries) / s,
            "pct_reversed": sum(sm.n_reversed / sm.n_trades for sm in summaries) / s,
        }
```

---

## 5. Std deviation + Sharpe trong `TradesSummary`

### Algorithm: population variance từ sum of squares

```
Variance = E[X²] − (E[X])²
         = (sum_sq / n) − mean²

std = sqrt(variance)

Numerically stable cho n_trades < 10000 (đủ cho use case này).
Welford's method cho n >> 1M — không cần ở đây.
```

### Thêm fields vào `TradesSummary`

```python
@dataclass
class TradesSummary:
    """..."""
    # Existing fields (unchanged)
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
    max_drawdown:  float

    # NEW: for std computation
    sum_sq_rr:     float    # sum of rr_ratio² across all trades
    sum_sq_pnl:    float    # sum of net_pnl_pct² across all trades

    # --- Existing properties (unchanged) ---
    @property win_rate, avg_rr, avg_net_pnl, avg_bars, expectancy, calmar: ...

    # --- NEW: dispersion metrics ---

    @property
    def var_rr(self) -> float:
        """Population variance of rr_ratio. 0.0 if n_trades < 2."""
        if self.n_trades < 2:
            return 0.0
        return max(0.0, self.sum_sq_rr / self.n_trades - self.avg_rr ** 2)

    @property
    def std_rr(self) -> float:
        """Population std of rr_ratio. 0.0 if n_trades < 2."""
        return self.var_rr ** 0.5

    @property
    def var_pnl(self) -> float:
        """Population variance of net_pnl_pct. 0.0 if n_trades < 2."""
        if self.n_trades < 2:
            return 0.0
        return max(0.0, self.sum_sq_pnl / self.n_trades - self.avg_net_pnl ** 2)

    @property
    def std_pnl(self) -> float:
        """Population std of net_pnl_pct. 0.0 if n_trades < 2."""
        return self.var_pnl ** 0.5

    @property
    def sharpe(self) -> float:
        """
        Sharpe proxy = avg_net_pnl / std_pnl.

        Per-trade Sharpe proxy (not annualized).
        Suitable for config comparison, not absolute benchmark comparison.

        0.0 if std_pnl = 0 (all trades have same P&L, undefined).
        Negative if avg_net_pnl < 0 (losing strategy, penalized by volatility).
        """
        if self.std_pnl == 0.0:
            return 0.0
        return self.avg_net_pnl / self.std_pnl

    @classmethod
    def from_accumulator(cls, acc: dict) -> "TradesSummary":
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
            sum_sq_rr     = acc["sum_sq_rr"],     # NEW
            sum_sq_pnl    = acc["sum_sq_pnl"],    # NEW
        )
```

### Update `_accumulate()` để tính sum of squares

```python
def _accumulate(summary_acc, exit_price, close_reason, bars_held,
                direction, entry_price, atr_at_entry, cfg):
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
    summary_acc["sum_sq_rr"]     += rr * rr       # NEW: for std_rr
    summary_acc["sum_sq_pnl"]    += net_pnl * net_pnl  # NEW: for std_pnl

    _accumulate_reason(summary_acc, close_reason, strict=True)

    # MDD tracking
    summary_acc["equity"]      += net_pnl
    summary_acc["peak_equity"]  = max(summary_acc["peak_equity"],
                                      summary_acc["equity"])
    summary_acc["max_drawdown"] = min(summary_acc["max_drawdown"],
                                      summary_acc["equity"] - summary_acc["peak_equity"])
```

### Initial accumulator dict

```python
summary_acc = {
    "n_trades": 0, "n_wins": 0, "n_tp": 0, "n_sl": 0, "n_ts": 0, "n_reversed": 0,
    "total_bars": 0,
    "total_pnl_pct": 0.0, "total_net_pnl": 0.0, "total_rr": 0.0,
    "sum_sq_rr": 0.0,    # NEW
    "sum_sq_pnl": 0.0,   # NEW
    "equity": 0.0, "peak_equity": 0.0, "max_drawdown": 0.0,
}
```

---

## 6. AI fitness function — updated với Sharpe

```python
# Fitness options sau khi có Sharpe:
fitness_options = {
    "expectancy":  lambda s: s.expectancy,          # avg_rr
    "calmar":      lambda s: s.calmar,               # avg_net_pnl / abs(MDD)
    "sharpe":      lambda s: s.sharpe,               # avg_net_pnl / std_pnl
    "composite":   lambda s: s.sharpe * s.win_rate,  # balanced
    "pnl":         lambda s: s.avg_net_pnl,          # raw return
}

# Recommended for AI optimization:
# - sharpe: good when std matters (risk-conscious)
# - calmar: good when drawdown matters (conservative)
# - expectancy: good when pure return/ATR matters (aggressive)
```

---

## 7. Test cases bổ sung round 11

```python
# tests/test_position_tracker.py

def test_reason_map_covers_all_known_reasons():
    """REASON_COUNTER_MAP phải chứa tất cả known reasons."""
    for reason in ["TP_HIT", "SL_HIT", "TS_HIT", "REVERSED"]:
        assert reason in REASON_COUNTER_MAP, \
            f"Missing {reason!r} in REASON_COUNTER_MAP"


def test_reason_map_dynamic_lookup():
    """Thêm reason mới vào REASON_COUNTER_MAP → _accumulate_reason nhận ra."""
    acc = {"n_tp": 0, "n_sl": 0, "n_ts": 0, "n_reversed": 0, "n_test": 0}
    # Temporarily add test reason
    REASON_COUNTER_MAP["TEST_HIT"] = "n_test"
    try:
        _accumulate_reason(acc, "TEST_HIT", strict=True)
        assert acc["n_test"] == 1
    finally:
        del REASON_COUNTER_MAP["TEST_HIT"]   # cleanup


def test_std_rr_zero_single_trade():
    """std_rr = 0 khi chỉ có 1 trade."""
    summary = _make_summary_with(n_trades=1, sum_sq_rr=4.0, total_rr=2.0)
    assert summary.std_rr == pytest.approx(0.0)


def test_std_rr_correct():
    """std_rr phải khớp với numpy std."""
    rr_values = [1.0, -0.5, 2.0, -1.5, 0.8]
    import numpy as np
    expected_std = np.std(rr_values)   # population std
    sum_sq_rr = sum(r**2 for r in rr_values)
    total_rr  = sum(rr_values)
    n = len(rr_values)

    summary = _make_summary_with(n_trades=n, sum_sq_rr=sum_sq_rr, total_rr=total_rr)
    assert summary.std_rr == pytest.approx(expected_std, rel=1e-6)


def test_sharpe_positive_for_profitable_strategy():
    """avg_net_pnl > 0 và std > 0 → sharpe > 0."""
    summary = _make_summary_with(
        n_trades=10, total_net_pnl=0.30,   # avg = 0.03 > 0
        sum_sq_pnl=0.10,                   # std > 0
    )
    assert summary.sharpe > 0


def test_sharpe_zero_when_std_zero():
    """Tất cả trades cùng P&L → std = 0 → sharpe = 0 (undefined, not infinity)."""
    # 5 trades mỗi cái net_pnl = 0.05: sum_sq = 5 × 0.0025 = 0.0125
    # var = 0.0125/5 - 0.05^2 = 0.0025 - 0.0025 = 0
    summary = _make_summary_with(
        n_trades=5, total_net_pnl=0.25, sum_sq_pnl=0.0125
    )
    assert summary.std_pnl  == pytest.approx(0.0)
    assert summary.sharpe   == pytest.approx(0.0)


def test_portfolio_weight_by_symbol_vs_trades():
    """
    weight_by="symbol" và "trades" cho kết quả khác nhau.
    Verify với setup có symbol unequal trade counts.
    """
    # Mock: 2 symbols, A=50 trades win_rate 60%, B=2 trades win_rate 0%
    result_trades = backtest_portfolio(..., weight_by="trades")
    result_symbol = backtest_portfolio(..., weight_by="symbol")

    # trades: (50×0.6 + 2×0) / 52 ≈ 0.577
    # symbol: (0.6 + 0.0) / 2 = 0.3
    assert result_trades["portfolio_win_rate"] > result_symbol["portfolio_win_rate"]
    assert result_symbol["weight_by"] == "symbol"


def test_portfolio_total_net_pnl_pct_naming():
    """Verify tên key mới (không còn portfolio_net_pnl)."""
    result = backtest_portfolio(["7203.T"], cfg)
    assert "portfolio_total_net_pnl_pct" in result
    assert "portfolio_net_pnl"           not in result, \
        "Old ambiguous key should be removed"


def test_portfolio_expectancy_rr_naming():
    """Verify key portfolio_expectancy_rr (không còn portfolio_expectancy)."""
    result = backtest_portfolio(["7203.T"], cfg)
    assert "portfolio_expectancy_rr" in result
    assert result.get("portfolio_expectancy") is None, \
        "Old ambiguous key should be removed or not present"
```

---

## 8. Tổng kết `TradesSummary` — version cuối (13 fields + 8 properties)

```python
@dataclass
class TradesSummary:
    # Accumulators (13 fields)
    n_trades, n_wins, n_tp, n_sl, n_ts, n_reversed, total_bars: int
    total_pnl_pct, total_net_pnl, total_rr:                     float
    max_drawdown:                                                float   # ≤ 0
    sum_sq_rr, sum_sq_pnl:                                      float   # NEW

    # Computed properties (8)
    @property win_rate    → n_wins / n_trades
    @property avg_rr      → total_rr / n_trades         (signed)
    @property avg_net_pnl → total_net_pnl / n_trades
    @property avg_bars    → total_bars / n_trades
    @property expectancy  → avg_rr                       (ATR-normalized)
    @property calmar      → avg_net_pnl / abs(max_drawdown)
    @property std_rr      → sqrt(sum_sq_rr/n - avg_rr²)    NEW
    @property std_pnl     → sqrt(sum_sq_pnl/n - avg_pnl²)  NEW
    @property sharpe      → avg_net_pnl / std_pnl           NEW
```

---

## 10. v3 TODO

```python
# v3:
# 1. Portfolio MDD đúng: collect all trades → sort by exit_date → real MDD
# 2. Multiplicative equity option
# 3. Annual Calmar = annual_return / abs(MDD)
# 4. TS reset khi signal cùng hướng (Pine-exact)
# 5. backtest_portfolio parallel (scan_full_history là pure)
# 6. AI optimizer: Bayesian (skopt) / genetic (DEAP)
# 7. Pyramiding
# 8. weight_by="capital": weight proportional to symbol market cap
```
