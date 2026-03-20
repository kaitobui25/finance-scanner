"""
tests/test_position_tracker_phase4.py
Phase 4 Tests — scan_full_history
"""
import pytest
import pandas as pd

from core.position_tracker import (
    PositionConfig,
    scan_full_history,
    PositionState,
    Trade,
    TradesSummary,
    SignalContext
)
from indicators.fvg_core import BULL, BEAR

def build_df(data: list[dict], base_date="2024-01-01"):
    dates = pd.date_range(base_date, periods=len(data), tz="Asia/Tokyo")
    df = pd.DataFrame(data, index=dates)
    # Ensure open, high, low, close exist
    for col in ["open", "high", "low", "close"]:
        if col not in df.columns:
            if col == "open": df["open"] = df["close"] * 0.9
            if col == "high": df["high"] = df["close"] * 1.1
            if col == "low": df["low"] = df["close"] * 0.9
    return df

def mock_signal_factory(signals: dict[int, tuple[str, float]]):
    def _fn(df, i, ctx):
        if i in signals:
            sig, price = signals[i]
            return sig, {"entry_price": price}
        return None, {}
    _fn.__name__ = "mock_strat"
    return _fn

def base_cfg():
    return PositionConfig(
        filter_width=0.0,
        atr_period=1,
        tp_mult=10.0,
        sl_mult=10.0,
        ts_mult=10.0,
        exit_on_wick=True,
        ts_on_close=True,
        exit_priority="TP_FIRST"
    )

def test_bull_signal_detected():
    cfg = base_cfg()
    df = build_df([
        {"close": 10}, {"close": 10}, {"close": 10}, {"close": 10},
        {"close": 100}
    ])
    sig_fn = mock_signal_factory({4: (BULL, 100.0)})
    
    state = scan_full_history(df, cfg, signal_fn=sig_fn)
    assert state.is_holding is True
    assert state.direction == BULL
    assert state.signal_action == "OPEN"
    assert state.bars_held == 0

def test_bear_signal_detected():
    cfg = base_cfg()
    df = build_df([{"close": 10}] * 4 + [{"close": 100}])
    sig_fn = mock_signal_factory({4: (BEAR, 100.0)})
    
    state = scan_full_history(df, cfg, signal_fn=sig_fn)
    assert state.is_holding is True
    assert state.direction == BEAR

def test_no_signal_no_position():
    cfg = base_cfg()
    df = build_df([{"close": 10}] * 5)
    sig_fn = mock_signal_factory({})
    
    state = scan_full_history(df, cfg, signal_fn=sig_fn)
    assert state.is_holding is False
    assert state.direction is None

def test_tp_hit():
    cfg = base_cfg()
    cfg.tp_mult = 1.0 
    df = build_df([
        {"close": 100}, {"close": 100}, {"close": 100}, {"close": 100},
        {"close": 100, "high": 100},
        {"close": 110, "high": 115}
    ])
    atr_series = pd.Series([10.0]*6, index=df.index)
    sig_fn = mock_signal_factory({4: (BULL, 100.0)})
    
    state, trades = scan_full_history(df, cfg, return_trades=True, atr_series=atr_series, signal_fn=sig_fn)
    assert state.is_holding is False
    assert state.close_reason == "TP_HIT"
    assert len(trades) == 1
    assert trades[0].close_reason == "TP_HIT"

def test_sl_hit():
    cfg = base_cfg()
    cfg.sl_mult = 1.0
    df = build_df([
        {"close": 100}, {"close": 100}, {"close": 100}, {"close": 100},
        {"close": 100, "low": 100},
        {"close": 95, "low": 85}
    ])
    atr_series = pd.Series([10.0]*6, index=df.index)
    sig_fn = mock_signal_factory({4: (BULL, 100.0)})
    
    state = scan_full_history(df, cfg, atr_series=atr_series, signal_fn=sig_fn)
    assert state.is_holding is False
    assert state.close_reason == "SL_HIT"

def test_ts_hit():
    cfg = base_cfg()
    cfg.ts_mult = 1.0
    cfg.ts_on_close = True
    df = build_df([
        {"close": 100}, {"close": 100}, {"close": 100}, {"close": 100},
        {"close": 100},
        {"close": 120},
        {"close": 105} 
    ])
    atr_series = pd.Series([10.0]*len(df), index=df.index)
    sig_fn = mock_signal_factory({4: (BULL, 100.0)})
    
    state = scan_full_history(df, cfg, atr_series=atr_series, signal_fn=sig_fn)
    assert state.is_holding is False
    assert state.close_reason == "TS_HIT"

def test_reversed():
    cfg = base_cfg()
    df = build_df([
        {"close": 100}, {"close": 100}, {"close": 100}, {"close": 100},
        {"close": 100},
        {"close": 100}
    ])
    atr_series = pd.Series([10.0]*6, index=df.index)
    sig_fn = mock_signal_factory({4: (BULL, 100.0), 5: (BEAR, 100.0)})
    
    state = scan_full_history(df, cfg, atr_series=atr_series, signal_fn=sig_fn)
    assert state.is_holding is True
    assert state.direction == BEAR
    assert state.signal_action == "REVERSE"
    assert state.close_reason == "REVERSED"

def test_tp_and_new_signal_same_bar():
    cfg = base_cfg()
    cfg.tp_mult = 1.0
    df = build_df([
        {"close": 100}, {"close": 100}, {"close": 100}, {"close": 100},
        {"close": 100},
        {"close": 100, "high": 120} 
    ])
    atr_series = pd.Series([10.0]*6, index=df.index)
    sig_fn = mock_signal_factory({4: (BULL, 100.0), 5: (BULL, 100.0)})
    
    state, trades = scan_full_history(df, cfg, return_trades=True, atr_series=atr_series, signal_fn=sig_fn)
    
    assert len(trades) == 1
    assert trades[0].close_reason == "TP_HIT"
    
    assert state.is_holding is True
    assert state.signal_action == "OPEN"
    assert state.close_reason == "TP_HIT"

def test_exit_state_fully_reset():
    cfg = base_cfg()
    cfg.sl_mult = 1.0
    df = build_df([
        {"close": 100}, {"close": 100}, {"close": 100}, {"close": 100},
        {"close": 100},
        {"close": 80, "low": 80}
    ])
    atr_series = pd.Series([10.0]*6, index=df.index)
    sig_fn = mock_signal_factory({4: (BULL, 100.0)})
    
    state = scan_full_history(df, cfg, atr_series=atr_series, signal_fn=sig_fn)
    assert state.is_holding is False
    assert state.direction is None
    assert state.tp_level is None
    assert state.sl_level is None
    assert state.entry_date is None
    assert state.close_reason == "SL_HIT"

def test_bars_held_entry_bar_is_zero():
    cfg = base_cfg()
    df = build_df([{"close": 100}] * 7)
    atr_series = pd.Series([10.0]*7, index=df.index)
    sig_fn = mock_signal_factory({4: (BULL, 100.0)})
    
    state = scan_full_history(df, cfg, atr_series=atr_series, signal_fn=sig_fn)
    assert state.bars_held == 2

def test_signal_action_ignore_same_direction():
    cfg = base_cfg()
    df = build_df([{"close": 100}] * 6)
    atr_series = pd.Series([10.0]*6, index=df.index)
    sig_fn = mock_signal_factory({4: (BULL, 100.0), 5: (BULL, 100.0)})
    state = scan_full_history(df, cfg, atr_series=atr_series, signal_fn=sig_fn)
    assert state.signal_action == "IGNORE"

def test_signal_action_open():
    cfg = base_cfg()
    df = build_df([{"close": 100}] * 5)
    atr_series = pd.Series([10.0]*5, index=df.index)
    sig_fn = mock_signal_factory({4: (BULL, 100.0)})
    state = scan_full_history(df, cfg, atr_series=atr_series, signal_fn=sig_fn)
    assert state.signal_action == "OPEN"

def test_signal_action_reverse():
    cfg = base_cfg()
    df = build_df([{"close": 100}] * 6)
    atr_series = pd.Series([10.0]*6, index=df.index)
    sig_fn = mock_signal_factory({4: (BULL, 100.0), 5: (BEAR, 100.0)})
    state = scan_full_history(df, cfg, atr_series=atr_series, signal_fn=sig_fn)
    assert state.signal_action == "REVERSE"

def test_return_trades_backward_compat():
    cfg = base_cfg()
    df = build_df([{"close": 100}] * 5)
    atr_series = pd.Series([10.0]*5, index=df.index)
    sig_fn = mock_signal_factory({4: (BULL, 100.0)})
    state = scan_full_history(df, cfg, return_trades=False, atr_series=atr_series, signal_fn=sig_fn)
    assert isinstance(state, PositionState)

def test_no_literal_in_engine():
    df = build_df([
        {"close": 100}, {"close": 100}, {"close": 100}, {"close": 100},
        {"close": 100},
        {"close": 110, "high": 120}
    ])
    atr_series = pd.Series([10.0]*6, index=df.index)
    sig_fn = mock_signal_factory({4: (BULL, 100.0)})
    
    cfg1 = base_cfg()
    cfg1.tp_mult = 1.0
    state1 = scan_full_history(df, cfg1, atr_series=atr_series, signal_fn=sig_fn)
    
    cfg2 = base_cfg()
    cfg2.tp_mult = 5.0 
    state2 = scan_full_history(df, cfg2, atr_series=atr_series, signal_fn=sig_fn)
    
    assert state1.close_reason == "TP_HIT"
    assert state2.close_reason is None
    assert state2.is_holding is True
