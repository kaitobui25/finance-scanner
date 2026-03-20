"""
tests/test_position_tracker_phase7.py
Phase 7 Tests — Database Layer
"""
import pytest
import sqlite3
from datetime import datetime, timezone

from core.position_tracker import (
    init_positions_db,
    _get_holding_position,
    _insert_position,
    _close_and_log,
    _update_position,
    _process_symbol,
    PositionState
)
from indicators.fvg_core import BULL, BEAR

@pytest.fixture
def memory_db():
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    init_positions_db("1MO", conn)
    yield conn
    conn.close()

def dummy_state(direction=BULL, is_holding=True, close_reason=None) -> PositionState:
    return PositionState(
        new_signal_detected   = True,
        signal_action         = "OPEN",
        is_holding            = is_holding,
        direction             = direction,
        entry_date            = "2024-01-01",
        gap_top               = 95.0,
        gap_bottom            = 85.0,
        entry_close           = 100.0,
        tp_level              = 120.0,
        sl_level              = 80.0,
        trailing_stop         = 90.0,
        atr_at_entry          = 10.0,
        bars_held             = 2,
        close_reason          = close_reason,
        close_price_at_exit   = 120.0 if close_reason is not None else None,
        last_signal_type      = direction,
        last_signal_date      = "2024-01-01",
        last_checked_bar_date = "2024-02-01",
    )

def test_partial_index_prevents_duplicate_holding(memory_db):
    conn = memory_db
    tf = "1MO"
    state1 = dummy_state(BULL)
    
    _insert_position(conn, tf, "7203.T", state1, "strat1")
    
    state2 = dummy_state(BEAR)
    with pytest.raises(sqlite3.IntegrityError):
        _insert_position(conn, tf, "7203.T", state2, "strat1")
        
    conn.execute(f"INSERT INTO positions_{tf} (symbol, direction, entry_date, entry_close, tp_level, sl_level, trailing_stop, atr_at_entry, status, created_at) VALUES ('AAPL', 'BULL', '2024-01-01', 100, 120, 80, 90, 10, 'CLOSED', '2024-01-01T00:00:00Z')")
    conn.execute(f"INSERT INTO positions_{tf} (symbol, direction, entry_date, entry_close, tp_level, sl_level, trailing_stop, atr_at_entry, status, created_at) VALUES ('AAPL', 'BULL', '2024-02-01', 100, 120, 80, 90, 10, 'CLOSED', '2024-02-01T00:00:00Z')")
    count = conn.execute(f"SELECT COUNT(*) FROM positions_{tf} WHERE symbol='AAPL'").fetchone()[0]
    assert count == 2

def test_close_and_log_uses_db_row_not_state(memory_db):
    conn = memory_db
    tf = "1MO"
    sym = "7203.T"
    
    state_old = dummy_state(BULL)
    _insert_position(conn, tf, sym, state_old, "strat1")
    
    state_new = dummy_state(BEAR, is_holding=True, close_reason="REVERSED")
    state_new.signal_action = "REVERSE"
    
    _process_symbol(conn, tf, sym, state_new, "2024-03-01", "strat1")
    
    hist = conn.execute(f"SELECT * FROM position_history_{tf} WHERE symbol=?", (sym,)).fetchone()
    assert hist["direction"] == BULL
    assert hist["close_reason"] == "REVERSED"
    
    holding = _get_holding_position(conn, tf, sym)
    assert holding["direction"] == BEAR

def test_atomic_transaction(memory_db):
    conn = memory_db
    tf = "1MO"
    sym = "7203.T"
    
    _insert_position(conn, tf, sym, dummy_state(BULL), "strat")
    conn.commit()
    
    state_reverse = dummy_state(BEAR, close_reason="REVERSED")
    state_reverse.signal_action = "REVERSE"
    
    import core.position_tracker
    original_insert = core.position_tracker._insert_position
    
    def buggy_insert(*args, **kwargs):
        raise RuntimeError("Fake Error")
        
    core.position_tracker._insert_position = buggy_insert
    try:
        with pytest.raises(RuntimeError):
            _process_symbol(conn, tf, sym, state_reverse, "2024-03-01", "strat")
            
        holding = _get_holding_position(conn, tf, sym)
        assert holding is not None
        assert holding["status"] == "HOLDING"
        assert holding["direction"] == BULL
        
        hist_count = conn.execute(f"SELECT COUNT(*) FROM position_history_{tf}").fetchone()[0]
        assert hist_count == 0
    finally:
        core.position_tracker._insert_position = original_insert
    
    _process_symbol(conn, tf, sym, state_reverse, "2024-03-01", "strat")
    hist_count = conn.execute(f"SELECT COUNT(*) FROM position_history_{tf}").fetchone()[0]
    assert hist_count == 1 
    holding = _get_holding_position(conn, tf, sym)
    assert holding["direction"] == BEAR 

def test_update_position(memory_db):
    conn = memory_db
    tf = "1MO"
    sym = "7203.T"
    
    pos_id = _insert_position(conn, tf, sym, dummy_state(), "strat")
    
    state2 = dummy_state()
    state2.trailing_stop = 110.0
    state2.bars_held = 3
    state2.last_checked_bar_date = "2024-04-01"
    
    _update_position(conn, tf, pos_id, state2)
    
    row = _get_holding_position(conn, tf, sym)
    assert row["trailing_stop"] == 110.0
    assert row["bars_held"] == 3
    assert row["last_checked_at"] == "2024-04-01"

def test_process_symbol_close_without_open(memory_db):
    conn = memory_db
    tf = "1MO"
    sym = "7203.T"
    
    _insert_position(conn, tf, sym, dummy_state(BULL), "strat")
    
    state_exit = dummy_state(BULL, is_holding=False, close_reason="TP_HIT")
    state_exit.signal_action = None 
    
    _process_symbol(conn, tf, sym, state_exit, "2024-05-01", "strat")
    
    holding = _get_holding_position(conn, tf, sym)
    assert holding is None 
    
    hist = conn.execute(f"SELECT * FROM position_history_{tf}").fetchall()
    assert len(hist) == 1
    assert hist[0]["close_reason"] == "TP_HIT"
