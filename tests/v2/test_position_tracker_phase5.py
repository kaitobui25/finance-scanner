"""
tests/test_position_tracker_phase5.py
Phase 5 Tests — check_latest_bar & Trade properties
"""
import pytest
import pandas as pd
from datetime import date

from core.position_tracker import (
    PositionConfig,
    check_latest_bar,
    Trade,
    PositionState,
    SignalContext
)
from indicators.fvg_core import BULL, BEAR

# ── Helper for check_latest_bar ──
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

def build_df(data: list[dict], base_date="2024-01-01"):
    dates = pd.date_range(base_date, periods=len(data), tz="Asia/Tokyo")
    df = pd.DataFrame(data, index=dates)
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

def dummy_db_row(last_checked: str | None = None) -> dict:
    return {
        "direction": BULL,
        "trailing_stop": 90.0,
        "tp_level": 120.0,
        "sl_level": 80.0,
        "entry_close": 100.0,
        "atr_at_entry": 10.0,
        "bars_held": 5,
        "last_checked_at": last_checked,
        "gap_top": 95.0,
        "gap_bottom": 85.0,
        "entry_date": "2024-01-01",
        "last_signal_type": BULL,
        "last_signal_date": "2024-01-01"
    }

# ── TRADE PROPERTIES TESTS ──

def test_rr_ratio_signed_positive():
    trade = Trade("D1", "D2", BULL, 100.0, 110.0, 110.0, 0.0, "TP_HIT", 1, 95, 85, 10.0, 120.0, 80.0)
    assert trade.rr_ratio == 1.0

def test_rr_ratio_signed_negative():
    trade = Trade("D1", "D2", BULL, 100.0, 90.0, 90.0, 0.0, "SL_HIT", 1, 95, 85, 10.0, 120.0, 80.0)
    assert trade.rr_ratio == -1.0
    
    trade2 = Trade("D1", "D2", BEAR, 100.0, 110.0, 110.0, 0.0, "SL_HIT", 1, 105, 115, 10.0, 80.0, 120.0)
    assert trade2.rr_ratio == -1.0

def test_net_pnl_includes_fee():
    fee = 0.01 
    trade = Trade("D1", "D2", BULL, 100.0, 110.0, 110.0, fee, "TP_HIT", 1, 95, 85, 10.0, 120.0, 80.0)
    assert trade.pnl_pct == 0.1 
    assert trade.net_pnl_pct == pytest.approx(0.09) 

def test_is_win_tp_hit_but_high_fee():
    fee = 0.15 
    trade = Trade("D1", "D2", BULL, 100.0, 110.0, 110.0, fee, "TP_HIT", 1, 95, 85, 10.0, 120.0, 80.0)
    assert trade.is_win is False

def test_is_tp_hit_property():
    t1 = Trade("D1", "D2", BULL, 100.0, 110.0, 110.0, 0.0, "TP_HIT", 1, 95, 85, 10.0, 120.0, 80.0)
    assert t1.is_tp_hit is True
    t2 = Trade("D1", "D2", BULL, 100.0, 110.0, 110.0, 0.0, "TS_HIT", 1, 95, 85, 10.0, 120.0, 80.0)
    assert t2.is_tp_hit is False

# ── CHECK LATEST BAR GUARDS ──

def test_check_latest_bar_cache_unavailable():
    cfg = base_cfg()
    row = dummy_db_row()
    state = check_latest_bar(None, row, cfg)
    assert state.close_reason == "cache_unavailable"
    state2 = check_latest_bar(pd.DataFrame(), row, cfg)
    assert state2.close_reason == "cache_unavailable"

def test_check_latest_bar_no_new_bar():
    cfg = base_cfg()
    df = build_df([{"close": 100}]) 
    row = dummy_db_row(last_checked="2024-01-01")
    state = check_latest_bar(df, row, cfg)
    assert state.close_reason == "no_new_bar"
    
    row2 = dummy_db_row(last_checked="2024-01-02") 
    state2 = check_latest_bar(df, row2, cfg)
    assert state2.close_reason == "no_new_bar"

def test_check_latest_bar_insufficient_bars():
    cfg = base_cfg()
    cfg.atr_period = 5
    df = build_df([{"close": 100}] * 3) 
    row = dummy_db_row(last_checked="2023-12-31")
    state = check_latest_bar(df, row, cfg)
    assert state.close_reason == "atr_not_ready" 

# ── CHECK LATEST BAR LOGIC ──

def test_check_latest_bar_holding_no_exit():
    cfg = base_cfg()
    df = build_df([{"close": 100}, {"close": 105}], base_date="2024-01-01")
    
    row = dummy_db_row(last_checked="2023-12-31") # Set yesterday to not trigger early exit
    row["trailing_stop"] = 90.0
    
    sig_fn = mock_signal_factory({}) 
    state = check_latest_bar(df, row, cfg, signal_fn=sig_fn)
    
    assert state.is_holding is True
    assert state.close_reason is None 
    assert state.bars_held == 6 
    assert state.trailing_stop >= 90.0
    assert state.last_checked_bar_date == "2024-01-02"

def test_check_latest_bar_tp_hit():
    cfg = base_cfg()
    df = build_df([{"close": 100}, {"close": 130, "high": 130}], base_date="2024-01-01")
    row = dummy_db_row(last_checked="2023-12-31")
    row["tp_level"] = 120.0
    
    sig_fn = mock_signal_factory({})
    state = check_latest_bar(df, row, cfg, signal_fn=sig_fn)
    
    assert state.is_holding is False
    assert state.close_reason == "TP_HIT"
    assert state.close_price_at_exit == 120.0

def test_check_latest_bar_signal_reverse():
    cfg = base_cfg()
    df = build_df([{"close": 100}, {"close": 105}], base_date="2024-01-01")
    row = dummy_db_row(last_checked="2023-12-31")
    row["direction"] = BULL
    
    sig_fn = mock_signal_factory({1: (BEAR, 105.0)})
    state = check_latest_bar(df, row, cfg, signal_fn=sig_fn)
    
    assert state.signal_action == "REVERSE"
    assert state.is_holding is True
    assert state.direction == BEAR
    assert state.close_reason == "REVERSED"
    assert state.bars_held == 0 

def test_check_latest_bar_signal_ignore():
    cfg = base_cfg()
    df = build_df([{"close": 100}, {"close": 105}], base_date="2024-01-01")
    row = dummy_db_row(last_checked="2023-12-31")
    row["direction"] = BULL
    
    sig_fn = mock_signal_factory({1: (BULL, 105.0)})
    state = check_latest_bar(df, row, cfg, signal_fn=sig_fn)
    
    assert state.signal_action == "IGNORE"
    assert state.is_holding is True
    assert state.direction == BULL
    assert state.close_reason is None

def test_check_latest_bar_exit_then_open():
    cfg = base_cfg()
    df = build_df([{"close": 100}, {"close": 130, "high": 130}], base_date="2024-01-01")
    row = dummy_db_row(last_checked="2023-12-31")
    row["tp_level"] = 120.0
    
    sig_fn = mock_signal_factory({1: (BULL, 125.0)})
    state = check_latest_bar(df, row, cfg, signal_fn=sig_fn)
    
    assert state.close_reason == "TP_HIT"
    assert state.signal_action == "OPEN"
    assert state.is_holding is True
    assert state.direction == BULL 
    assert state.entry_close == 125.0
    assert state.bars_held == 0
