"""
tests/test_position_monitor_phase8.py
Integration tests cho CLI & Monitor (Phase 8)
"""
import pytest
import sqlite3
import pandas as pd
from unittest.mock import patch, MagicMock

from position_monitor import (
    run_full_scan,
    run_normal,
    _get_all_holding
)
from core.position_tracker import PositionConfig, init_positions_db
from indicators.fvg_core import BULL, BEAR

class NoCloseConn:
    def __init__(self, c): self.c = c
    def __getattr__(self, k): return getattr(self.c, k)
    def __enter__(self): return self.c.__enter__()
    def __exit__(self, *args): return self.c.__exit__(*args)
    def close(self): pass
    def execute(self, *args, **kwargs): return self.c.execute(*args, **kwargs)

@pytest.fixture
def mock_db():
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    init_positions_db("1MO", conn)
    
    with patch("position_monitor._get_db_conn", return_value=NoCloseConn(conn)):
        yield conn

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

# --- T100: test_tp_and_new_signal_same_bar_db ---
@patch("position_monitor.read_cache")
@patch("position_monitor._load_symbols")
def test_tp_and_new_signal_same_bar_db(mock_load, mock_read, mock_db):
    """
    Test Integration: Bar cuối vừa hit TP của lệnh BULL, vừa xuất hiện signal BEAR mới.
    Đầu tiên full_scan với dữ liệu đến nến 5.
    Sau đó normal_scan với nến 6.
    """
    sym = "7203.T"
    mock_load.return_value = [sym]
    
    df1 = build_df([
        {"close": 100}, {"close": 100}, {"close": 100}, {"close": 100},
        {"close": 100}, # entry BULL at 100
    ])
    
    df2 = build_df([
        {"close": 100}, {"close": 100}, {"close": 100}, {"close": 100},
        {"close": 100}, 
        {"close": 100, "high": 120}, # Hit TP 120, AND new signal at i=5
    ])
    
    # Sig: 
    # i=4 triggers BULL
    # i=5 triggers BEAR
    sig_fn = mock_signal_factory({4: (BULL, 100.0), 5: (BEAR, 100.0)})
    cfg = PositionConfig(tp_mult=1.0, sl_mult=1.0, exit_priority="TP_FIRST", atr_period=1)
    
    # 1. Full scan first up to df1
    mock_read.return_value = df1
    run_full_scan("1MO", cfg, dry_run=False, signal_fn=sig_fn)
    
    holds1 = _get_all_holding(mock_db, "1MO")
    assert len(holds1) == 1
    assert holds1[0]["direction"] == BULL
    
    # 2. Normal scan on df2
    mock_read.return_value = df2
    run_normal("1MO", cfg, dry_run=False, signal_fn=sig_fn)
    
    holds2 = _get_all_holding(mock_db, "1MO")
    # Result should be exactly ONE holding, which is the new BEAR
    assert len(holds2) == 1
    assert holds2[0]["direction"] == BEAR
    
    # History must contain the CLOSED BULL
    hists = mock_db.execute("SELECT * FROM position_history_1MO").fetchall()
    assert len(hists) == 1
    assert hists[0]["close_reason"] == "REVERSED"
    assert hists[0]["direction"] == BULL

# --- T101: test_full_scan_mode ---
@patch("position_monitor.read_cache")
@patch("position_monitor._load_symbols")
def test_full_scan_mode(mock_load, mock_read, mock_db):
    symbols = ["A", "B", "C"]
    mock_load.return_value = symbols
    
    df = build_df([{"close": 100}] * 5)
    mock_read.return_value = df
    
    sig_fn = mock_signal_factory({4: (BULL, 100.0)}) # Always trigger at i=4 for all
    cfg = PositionConfig(atr_period=1)
    
    stats = run_full_scan("1MO", cfg, dry_run=False, signal_fn=sig_fn)
    
    assert stats["with_signal"] == 3
    assert stats["scanned"] == 3
    
    holds = _get_all_holding(mock_db, "1MO")
    assert len(holds) == 3

# --- T102: test_normal_mode_update_trailing_stop ---
@patch("position_monitor.read_cache")
@patch("position_monitor._load_symbols")
def test_normal_mode_update_trailing_stop(mock_load, mock_read, mock_db):
    sym = "7203.T"
    mock_load.return_value = [sym]
    
    # i=4 Entry at 100, TS = 100 - (10 * atr) = ...
    df1 = build_df([
        {"close": 100}, {"close": 100}, {"close": 100}, {"close": 100},
        {"close": 100},
    ])
    
    # i=5 new bar, close=120 -> TS should ratchet up!
    df2 = build_df([
        {"close": 100}, {"close": 100}, {"close": 100}, {"close": 100},
        {"close": 100},
        {"close": 150}, # big jump to force TS update
    ])
    
    sig_fn = mock_signal_factory({4: (BULL, 100.0)})
    cfg = PositionConfig(ts_mult=1.0, tp_mult=100.0, atr_period=1) # easy TS trigger, unreachable TP
    
    mock_read.return_value = df1
    run_full_scan("1MO", cfg, dry_run=False, signal_fn=sig_fn)
    
    h1 = _get_all_holding(mock_db, "1MO")[0]
    ts1 = h1["trailing_stop"]
    
    mock_read.return_value = df2
    stats = run_normal("1MO", cfg, dry_run=False, signal_fn=sig_fn)
    
    assert stats["updated"] == 1
    
    h2 = _get_all_holding(mock_db, "1MO")[0]
    ts2 = h2["trailing_stop"]
    
    assert ts2 > ts1
    assert h2["bars_held"] == h1["bars_held"] + 1

# --- T103: test_no_update_when_bar_date_same_as_last_checked ---
@patch("position_monitor.read_cache")
@patch("position_monitor._load_symbols")
def test_no_update_when_bar_date_same_as_last_checked(mock_load, mock_read, mock_db):
    sym = "7203.T"
    mock_load.return_value = [sym]
    
    df1 = build_df([
        {"close": 100}, {"close": 100}, {"close": 100}, {"close": 100},
        {"close": 100},
    ])
    
    sig_fn = mock_signal_factory({4: (BULL, 100.0)})
    cfg = PositionConfig(atr_period=1)
    
    # 1. Full scan creates the position checking up to df1's last bar
    mock_read.return_value = df1
    run_full_scan("1MO", cfg, dry_run=False, signal_fn=sig_fn)
    
    h1 = _get_all_holding(mock_db, "1MO")[0]
    
    # 2. Normal scan WITH THE EXACT SAME DataFrame
    # It should skip via "no_new_bar" protection
    stats = run_normal("1MO", cfg, dry_run=False, signal_fn=sig_fn)
    
    # stats["updated"] should be 0 because it skipped
    assert stats["updated"] == 0
    assert stats["exited"] == 0
    
    h2 = _get_all_holding(mock_db, "1MO")[0]
    # State should remain completely untouched
    assert h1["bars_held"] == h2["bars_held"]
    assert h1["trailing_stop"] == h2["trailing_stop"]
