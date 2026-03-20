"""
tests/test_position_tracker.py
Phase 3 Tests — Helper Functions
"""
import pytest
import pandas as pd
import numpy as np

from core.position_tracker import (
    PositionConfig,
    compute_atr,
    _detect_imfvg_at,
    _ratchet_ts,
    _check_exit,
    _apply_slippage,
    _accumulate_reason,
    _make_accumulator,
    _accumulate,
    _no_update_state,
    REASON_COUNTER_MAP,
    SignalContext,
    make_imfvg_detector,
    _resolve_strategy_name,
)
from indicators.fvg_core import BULL, BEAR
from indicators import fvg

# --- T47: test_compute_atr_correct ---
def test_compute_atr_correct():
    """T47: verify atr calculation with hand-calculated values."""
    df = pd.DataFrame({
        "high":  [10, 20, 30, 40, 50],
        "low":   [10, 15, 20, 25, 30],
        "close": [10, 18, 28, 38, 48],
    })
    atr_series = compute_atr(df["high"], df["low"], df["close"], period=3)
    
    assert pd.isna(atr_series.iloc[0])
    assert pd.isna(atr_series.iloc[1])
    assert atr_series.iloc[2] == pytest.approx(22/3)
    assert atr_series.iloc[3] == pytest.approx(37/3)
    assert atr_series.iloc[4] == pytest.approx(47/3)

# --- T48: test_compute_atr_insufficient_bars ---
def test_compute_atr_insufficient_bars():
    df = pd.DataFrame({
        "high":  [10, 20],
        "low":   [10, 15],
        "close": [10, 18],
    })
    atr_series = compute_atr(df["high"], df["low"], df["close"], period=5)
    assert len(atr_series) == 2
    assert pd.isna(atr_series.iloc[0])
    assert pd.isna(atr_series.iloc[1])

# --- T49: test_fvg_py_and_tracker_same_result ---
def test_fvg_py_and_tracker_same_result():
    df = pd.DataFrame({
        "open":  [100, 90, 80, 80],
        "high":  [110, 95, 90, 110],
        "low":   [100, 80, 80, 80],
        "close": [105, 95, 85, 105],
    }, index=pd.date_range("2023-01-01", periods=4))
    
    res_fvg = fvg.analyze(df, symbol="TEST")
    
    cfg = PositionConfig(filter_width=0.0)
    sig, meta = _detect_imfvg_at(df, 3, cfg, SignalContext(atr=5.0))
    
    assert sig == BULL
    assert res_fvg["signal"] == "BULLISH"
    assert meta["entry_price"] == 105.0
    assert meta["gap_top"] == 100.0
    assert meta["gap_bottom"] == 90.0

# --- T58: test_ratchet_ts_bull_only_increases ---
def test_ratchet_ts_bull_only_increases():
    cfg = PositionConfig(ts_mult=2.0)
    ts = 100.0
    
    bar = pd.Series({"close": 110})
    ts = _ratchet_ts(bar, BULL, ts, 5.0, cfg)
    assert ts == 100.0
    
    bar = pd.Series({"close": 115})
    ts = _ratchet_ts(bar, BULL, ts, 5.0, cfg)
    assert ts == 105.0
    
    bar = pd.Series({"close": 112})
    ts = _ratchet_ts(bar, BULL, ts, 5.0, cfg)
    assert ts == 105.0

# --- T59: test_ratchet_ts_bear_only_decreases ---
def test_ratchet_ts_bear_only_decreases():
    cfg = PositionConfig(ts_mult=2.0)
    ts = 100.0
    
    bar = pd.Series({"close": 90})
    ts = _ratchet_ts(bar, BEAR, ts, 5.0, cfg)
    assert ts == 100.0
    
    bar = pd.Series({"close": 85})
    ts = _ratchet_ts(bar, BEAR, ts, 5.0, cfg)
    assert ts == 95.0
    
    bar = pd.Series({"close": 88})
    ts = _ratchet_ts(bar, BEAR, ts, 5.0, cfg)
    assert ts == 95.0

# --- T55: test_ts_exit_price_at_ts_level_when_wick ---
def test_ts_exit_price_at_ts_level_when_wick():
    cfg = PositionConfig(ts_on_close=False, exit_priority="TP_FIRST", exit_on_wick=True)
    
    bar = pd.Series({"high": 110, "low": 90, "close": 105})
    reason, price = _check_exit(bar, BULL, tp_level=120, sl_level=80, ts=95, cfg=cfg)
    assert reason == "TS_HIT"
    assert price == 95.0
    
    bar = pd.Series({"high": 110, "low": 90, "close": 95})
    reason, price = _check_exit(bar, BEAR, tp_level=80, sl_level=120, ts=100, cfg=cfg)
    assert reason == "TS_HIT"
    assert price == 100.0

# --- T56: test_exit_priority_tp_first ---
def test_exit_priority_tp_first():
    cfg = PositionConfig(exit_priority="TP_FIRST", exit_on_wick=True, ts_on_close=True)
    bar = pd.Series({"high": 125, "low": 75, "close": 100})
    tp_level = 120
    sl_level = 80
    ts = 90
    
    reason, price = _check_exit(bar, BULL, tp_level, sl_level, ts, cfg)
    assert reason == "TP_HIT"
    assert price == 120.0

# --- T57: test_exit_priority_sl_first ---
def test_exit_priority_sl_first():
    cfg = PositionConfig(exit_priority="SL_FIRST", exit_on_wick=True, ts_on_close=True)
    bar = pd.Series({"high": 125, "low": 75, "close": 100})
    tp_level = 120
    sl_level = 80
    ts = 90
    
    reason, price = _check_exit(bar, BULL, tp_level, sl_level, ts, cfg)
    assert reason == "SL_HIT"
    assert price == 80.0

# --- T86: test_accumulate_reason_all_known ---
def test_accumulate_reason_all_known():
    acc = _make_accumulator()
    for reason in ["TP_HIT", "SL_HIT", "TS_HIT", "REVERSED"]:
        _accumulate_reason(acc, reason, strict=True)
    
    assert acc["n_tp"] == 1
    assert acc["n_sl"] == 1
    assert acc["n_ts"] == 1
    assert acc["n_reversed"] == 1

# --- T87: test_accumulate_reason_strict_raises ---
def test_accumulate_reason_strict_raises():
    acc = _make_accumulator()
    with pytest.raises(ValueError):
        _accumulate_reason(acc, "UNKNOWN_HIT", strict=True)

# --- T88: test_accumulate_reason_strict_false_n_unknown ---
def test_accumulate_reason_strict_false_n_unknown():
    acc = _make_accumulator()
    _accumulate_reason(acc, "UNKNOWN_HIT", strict=False)
    assert acc["n_unknown"] == 1

# --- T89: test_reason_map_covers_all_known_reasons ---
def test_reason_map_covers_all_known_reasons():
    assert "TP_HIT" in REASON_COUNTER_MAP
    assert "SL_HIT" in REASON_COUNTER_MAP
    assert "TS_HIT" in REASON_COUNTER_MAP
    assert "REVERSED" in REASON_COUNTER_MAP

# --- T90: test_reason_map_dynamic_lookup ---
def test_reason_map_dynamic_lookup():
    original = REASON_COUNTER_MAP.copy()
    REASON_COUNTER_MAP["TEST_HIT"] = "n_test"
    try:
        acc = _make_accumulator()
        acc["n_test"] = 0
        _accumulate_reason(acc, "TEST_HIT", strict=True)
        assert acc["n_test"] == 1
    finally:
        REASON_COUNTER_MAP.clear()
        REASON_COUNTER_MAP.update(original)
