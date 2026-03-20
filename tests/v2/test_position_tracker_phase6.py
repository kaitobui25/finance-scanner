"""
tests/test_position_tracker_phase6.py
Phase 6 Tests — backtest_symbol, backtest_portfolio, summary metrics
"""
import pytest
import numpy as np
import pandas as pd
from unittest.mock import patch

from core.position_tracker import (
    PositionConfig,
    scan_full_history,
    backtest_symbol,
    backtest_portfolio,
    TradesSummary,
    Trade,
    _make_accumulator,
    _accumulate
)
from indicators.fvg_core import BULL, BEAR

def base_cfg():
    return PositionConfig(
        filter_width=0.0,
        atr_period=1,
        tp_mult=2.0,
        sl_mult=1.0,
        ts_mult=10.0,
        exit_on_wick=True,
        ts_on_close=True,
        exit_priority="TP_FIRST"
    )

def build_df(data: list[dict], base_date="2024-01-01"):
    dates = pd.date_range(base_date, periods=len(data), tz="Asia/Tokyo")
    df = pd.DataFrame(data, index=dates)
    for col in ["open", "high", "low", "close"]:
        if col not in df.columns:
            if col == "open": df["open"] = df["close"] * 0.9
            if col == "high": df["high"] = df["close"] * 1.1
            if col == "low": df["low"] = df["close"] * 0.9
    df["volume"] = 1000
    return df

def mock_signal_factory(signals: dict[int, tuple[str, float]]):
    def _fn(df, i, ctx):
        if i in signals:
            sig, price = signals[i]
            return sig, {"entry_price": price}
        return None, {}
    _fn.__name__ = "mock_strat"
    return _fn

def test_summarize_matches_trade_list():
    cfg = base_cfg()
    cfg.tp_mult = 1.0
    cfg.sl_mult = 1.0
    df = build_df([
        {"close": 100}, {"close": 100}, {"close": 100}, {"close": 100},
        {"close": 100}, 
        {"close": 110, "high": 120}, 
        {"close": 100}, 
        {"close": 110, "high": 120}, 
    ])
    atr_series = pd.Series([10.0]*len(df), index=df.index)
    sig_fn = mock_signal_factory({4: (BULL, 100.0), 6: (BEAR, 100.0)})
    
    state1, trades = scan_full_history(df, cfg, return_trades=True, atr_series=atr_series, signal_fn=sig_fn)
    wins = sum(1 for t in trades if t.is_win)
    rrs = [t.rr_ratio for t in trades]
    pnls = [t.net_pnl_pct for t in trades]
    total_bars = sum(t.bars_held for t in trades)
    
    state2, summary = scan_full_history(df, cfg, summarize_trades=True, atr_series=atr_series, signal_fn=sig_fn)
    
    assert summary.n_trades == len(trades)
    assert summary.n_wins == wins
    assert summary.avg_rr == pytest.approx(np.mean(rrs))
    assert summary.avg_net_pnl == pytest.approx(np.mean(pnls))
    assert summary.total_bars == total_bars
    if len(rrs) >= 2:
        assert summary.std_rr == pytest.approx(np.std(rrs))
        assert summary.std_pnl == pytest.approx(np.std(pnls))

def test_return_trades_and_summarize_mutually_exclusive():
    cfg = base_cfg()
    df = build_df([{"close": 100}] * 5)
    with pytest.raises(ValueError):
        scan_full_history(df, cfg, return_trades=True, summarize_trades=True)

def test_atr_cache_same_result():
    cfg = base_cfg()
    cfg.tp_mult = 1.0
    df = build_df([{"close": 100}] * 4 + [{"close": 100}, {"close": 120, "high": 120}])
    s1, summ1 = scan_full_history(df, cfg, summarize_trades=True, signal_fn=mock_signal_factory({4: (BULL, 100.0)}))
    
    from core.position_tracker import compute_atr
    atr_series = compute_atr(df["high"], df["low"], df["close"], cfg.atr_period)
    s2, summ2 = scan_full_history(df, cfg, summarize_trades=True, atr_series=atr_series, signal_fn=mock_signal_factory({4: (BULL, 100.0)}))
    
    assert summ1.n_trades == summ2.n_trades
    assert summ1.total_net_pnl == summ2.total_net_pnl

def test_std_rr_and_pnl_correct():
    cfg = base_cfg()
    acc = _make_accumulator()
    _accumulate(acc, exit_price=110, close_reason="TP_HIT", bars_held=1, direction=BULL, entry_price=100.0, atr_at_entry=10.0, cfg=cfg)
    _accumulate(acc, exit_price=90, close_reason="SL_HIT", bars_held=1, direction=BULL, entry_price=100.0, atr_at_entry=10.0, cfg=cfg)
    
    summ = TradesSummary.from_accumulator(acc)
    assert summ.std_rr == pytest.approx(1.0)
    assert summ.std_pnl == pytest.approx(0.1)

def test_sharpe_positive():
    acc = _make_accumulator()
    cfg = base_cfg()
    _accumulate(acc, exit_price=110, close_reason="TP_HIT", bars_held=1, direction=BULL, entry_price=100.0, atr_at_entry=10.0, cfg=cfg)
    _accumulate(acc, exit_price=105, close_reason="TP_HIT", bars_held=1, direction=BULL, entry_price=100.0, atr_at_entry=10.0, cfg=cfg)
    summ = TradesSummary.from_accumulator(acc)
    assert summ.sharpe > 0

def test_sharpe_zero_when_std_zero():
    acc = _make_accumulator()
    cfg = base_cfg()
    _accumulate(acc, exit_price=110, close_reason="TP_HIT", bars_held=1, direction=BULL, entry_price=100.0, atr_at_entry=10.0, cfg=cfg)
    _accumulate(acc, exit_price=110, close_reason="TP_HIT", bars_held=1, direction=BULL, entry_price=100.0, atr_at_entry=10.0, cfg=cfg)
    summ = TradesSummary.from_accumulator(acc)
    assert summ.std_pnl == 0.0
    assert summ.sharpe == 0.0

def test_monotonic_tp_mult():
    df = build_df([
        {"close": 100}, {"close": 100}, {"close": 100}, {"close": 100},
        {"close": 100}, 
        {"close": 105, "high": 110}, 
        {"close": 105, "high": 120}, 
    ])
    atr_series = pd.Series([10.0]*len(df), index=df.index)
    sig_fn = mock_signal_factory({4: (BULL, 100.0)})
    
    cfg1 = base_cfg()
    cfg1.tp_mult = 1.0 
    _, summ1 = scan_full_history(df, cfg1, summarize_trades=True, atr_series=atr_series, signal_fn=sig_fn)
    
    cfg2 = base_cfg()
    cfg2.tp_mult = 2.0 
    _, summ2 = scan_full_history(df, cfg2, summarize_trades=True, atr_series=atr_series, signal_fn=sig_fn)
    
    assert summ2.avg_bars > summ1.avg_bars

def test_monotonic_filter_width_reduces_trades():
    df = pd.DataFrame({
        "open":  [100, 100, 100, 105,  95,  85,  100],
        "high":  [100, 100, 100, 110, 100,  90,  110],
        "low":   [100, 100, 100, 100,  85,  80,   95],
        "close": [100, 100, 100, 105,  90,  85,  105],
    }, index=pd.date_range("2024-01-01", periods=7, tz="Asia/Tokyo"))
    atr_series = pd.Series([10.0]*len(df), index=df.index)
    
    cfg1 = base_cfg()
    cfg1.filter_width = 0.5
    s1 = scan_full_history(df, cfg1, summarize_trades=True, atr_series=atr_series) 
    state1 = s1[0] if s1 else None
    
    cfg2 = base_cfg()
    cfg2.filter_width = 1.5 
    s2 = scan_full_history(df, cfg2, summarize_trades=True, atr_series=atr_series)
    state2 = s2[0] if s2 else None
    
    assert state1.is_holding is True
    assert state2.is_holding is False

def test_sl_first_priority_changes_results():
    df = build_df([
        {"close": 100}, {"close": 100}, {"close": 100}, {"close": 100},
        {"close": 100}, 
        {"close": 100, "high": 120, "low": 80}, 
    ])
    atr_series = pd.Series([20.0]*len(df), index=df.index)
    sig_fn = mock_signal_factory({4: (BULL, 100.0)})
    
    cfg1 = base_cfg()
    cfg1.tp_mult = 1.0 # TP=120
    cfg1.exit_priority = "TP_FIRST"
    _, summ1 = scan_full_history(df, cfg1, summarize_trades=True, atr_series=atr_series, signal_fn=sig_fn)
    
    cfg2 = base_cfg()
    cfg2.tp_mult = 1.0 # TP=120
    cfg2.exit_priority = "SL_FIRST"
    _, summ2 = scan_full_history(df, cfg2, summarize_trades=True, atr_series=atr_series, signal_fn=sig_fn)
    
    assert summ1.n_tp == 1
    assert summ2.n_sl == 1

def test_slippage_reduces_pnl():
    df = build_df([
        {"close": 100}, {"close": 100}, {"close": 100}, {"close": 100},
        {"close": 100}, 
        {"close": 110, "high": 120}, 
    ])
    atr_series = pd.Series([10.0]*len(df), index=df.index)
    sig_fn = mock_signal_factory({4: (BULL, 100.0)})
    
    cfg1 = base_cfg()
    cfg1.tp_mult = 1.0
    cfg1.slippage = 0.0
    _, summ1 = scan_full_history(df, cfg1, summarize_trades=True, atr_series=atr_series, signal_fn=sig_fn)
    
    cfg2 = base_cfg()
    cfg2.tp_mult = 1.0
    cfg2.slippage = 0.05 
    _, summ2 = scan_full_history(df, cfg2, summarize_trades=True, atr_series=atr_series, signal_fn=sig_fn)
    
    assert summ2.avg_net_pnl < summ1.avg_net_pnl

def test_portfolio_weighting():
    with patch("core.position_tracker.backtest_symbol") as mock_bs:
        mock_bs.side_effect = [
            {"symbol": "A", "n_trades": 50, "win_rate": 0.60, "avg_rr": 1.0, "expectancy_rr": 1.0, "avg_net_pnl": 10.0, "avg_bars": 5, "pct_tp": 0.6, "pct_sl": 0.4, "pct_ts": 0, "pct_reversed": 0, "sharpe": 1.0, "calmar": 1.0},
            {"symbol": "B", "n_trades": 2,  "win_rate": 0.00, "avg_rr": -1.0, "expectancy_rr": -1.0, "avg_net_pnl": -10.0, "avg_bars": 2, "pct_tp": 0.0, "pct_sl": 1.0, "pct_ts": 0, "pct_reversed": 0, "sharpe": -1.0, "calmar": -1.0}
        ]
        
        cfg = base_cfg()
        p_trades = backtest_portfolio(["A", "B"], cfg, weight_by="trades")
        
        assert p_trades["portfolio_win_rate"] == pytest.approx(30 / 52)
        assert p_trades["portfolio_avg_rr"] == pytest.approx((50*1.0 - 2*1.0)/52)
        
        mock_bs.side_effect = [
            {"symbol": "A", "n_trades": 50, "win_rate": 0.60, "avg_rr": 1.0, "expectancy_rr": 1.0, "avg_net_pnl": 10.0, "avg_bars": 5, "pct_tp": 0.6, "pct_sl": 0.4, "pct_ts": 0, "pct_reversed": 0, "sharpe": 1.0, "calmar": 1.0},
            {"symbol": "B", "n_trades": 2,  "win_rate": 0.00, "avg_rr": -1.0, "expectancy_rr": -1.0, "avg_net_pnl": -10.0, "avg_bars": 2, "pct_tp": 0.0, "pct_sl": 1.0, "pct_ts": 0, "pct_reversed": 0, "sharpe": -1.0, "calmar": -1.0}
        ]
        p_sym = backtest_portfolio(["A", "B"], cfg, weight_by="symbol")
        
        assert p_sym["portfolio_win_rate"] == pytest.approx((0.6 + 0.0)/2)
        assert p_sym["portfolio_avg_rr"] == pytest.approx(0.0)
        
        assert "portfolio_max_drawdown" not in p_trades
        assert "portfolio_total_net_pnl" in p_trades
        assert "portfolio_expectancy_rr" in p_trades
        assert "portfolio_expectancy" not in p_trades
