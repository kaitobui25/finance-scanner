import datetime
import sqlite3
from unittest.mock import patch, MagicMock
import pytest
from pathlib import Path
import pandas as pd
from dateutil.relativedelta import relativedelta

import sys
import scanner


# Add project root to sys.path so we can import modules correctly without PYTHONPATH hacks
project_root = Path(__file__).parent.parent
if str(project_root) not in sys.path:
    sys.path.insert(0, str(project_root))

from core.signal_writer import init_db
import core.signal_writer as sw
from data_provider.base import NoDataError, DataProviderError, DataIncompleteError

@pytest.fixture
def mock_db(tmp_path):
    db_path = tmp_path / "test_state.db"
    
    # Mock BOTH the core.signal_writer and scanner DB_PATHs
    old_sw_db_path = sw.DB_PATH
    old_scan_db_path = scanner.DB_PATH
    
    sw.DB_PATH = db_path
    scanner.DB_PATH = db_path
    
    try:
        # init_db to create tables for 1MO
        init_db("1MO")
        yield db_path
    finally:
        sw.DB_PATH = old_sw_db_path
        scanner.DB_PATH = old_scan_db_path

# Helpers to seed db for testing db functions
def seed_test_status(db_path, tf, rows):
    conn = sqlite3.connect(db_path)
    for r in rows:
        symbol = r.get("symbol")
        is_active = r.get("is_active", 1)
        status = r.get("status", "PENDING")
        retry_count = r.get("retry_count", 0)
        conn.execute(
            f"INSERT INTO scan_state_{tf} (symbol, is_active, status, retry_count) VALUES (?, ?, ?, ?)",
            (symbol, is_active, status, retry_count)
        )
    conn.commit()
    conn.close()

def test_db_helpers(mock_db):
    tf = "1MO"
    seed_rows = [
        {"symbol": "A", "status": "SCANNED"},
        {"symbol": "B", "status": "FAILED", "retry_count": 0}, # normal retry
        {"symbol": "C", "status": "FAILED", "retry_count": 3}, # max retry reached
        {"symbol": "D", "status": "PENDING"},
        {"symbol": "E", "is_active": 0, "status": "PENDING"}   # inactive, should be ignored
    ]
    seed_test_status(mock_db, tf, seed_rows)
    
    # 1. Test _load_symbols normal
    symbols_normal = scanner._load_symbols(tf, "normal")
    assert sorted(symbols_normal) == ["B", "D"] # Failed with <3 retries, and Pending, only active
    
    # 2. Test _load_symbols resume
    symbols_resume = scanner._load_symbols(tf, "resume")
    assert symbols_resume == ["D"] # Only pending
    
    # 3. Test _load_symbols retry-failed
    symbols_retry = scanner._load_symbols(tf, "retry-failed")
    assert sorted(symbols_retry) == ["B", "C"] # All failed regardless of retry count
    
    # 4. Test _reset_batch
    scanner._reset_batch(tf)
    # A should now be PENDING, retry_count=0
    conn = sqlite3.connect(mock_db)
    row_A = conn.execute(f"SELECT status, retry_count FROM scan_state_{tf} WHERE symbol='A'").fetchone()
    assert row_A == ("PENDING", 0)
    
    # 5. Test _mark_scanned
    scanner._mark_scanned(tf, "B")
    row_B = conn.execute(f"SELECT status, fail_reason FROM scan_state_{tf} WHERE symbol='B'").fetchone()
    assert row_B == ("SCANNED", None)
    
    # 6. Test _mark_failed
    scanner._mark_failed(tf, "D", "error1")
    row_D = conn.execute(f"SELECT status, fail_reason, retry_count FROM scan_state_{tf} WHERE symbol='D'").fetchone()
    assert row_D == ("FAILED", "error1", 1) # retry_count increments
    
    # 7. Test _set_inactive
    scanner._set_inactive(tf, "A", "delisted")
    row_A2 = conn.execute(f"SELECT is_active, fail_reason FROM scan_state_{tf} WHERE symbol='A'").fetchone()
    assert row_A2 == (0, "delisted")
    
    # 8. Test _get_retry_count
    assert scanner._get_retry_count(tf, "D") == 1
    conn.close()


@patch("scanner.expire_old_signals")
@patch("scanner.get_ohlcv")
@patch("scanner.write_cache")
@patch("scanner.read_cache")
@patch("scanner.passes_filter")
@patch("scanner.run_all")
@patch("scanner.write_signal")
@patch("scanner.get_last_closed_bar")
@patch("scanner.seed_symbols")
def test_run_scan_success(mock_seed, mock_glc, mock_ws, mock_ra, mock_pf, mock_rc, mock_wc, mock_go, mock_eos, mock_db):
    # Setup
    tf = "1MO"
    seed_test_status(mock_db, tf, [{"symbol": "7203.T", "status": "PENDING"}])
    
    mock_glc.return_value = datetime.date(2023, 10, 31)
    
    # Create fake df
    dates = pd.date_range("2023-01-01", "2023-11-01", freq="ME")
    df = pd.DataFrame({"close": [1]*len(dates)}, index=dates)
    mock_go.return_value = df
    mock_rc.return_value = df
    
    mock_pf.return_value = True
    # Plugin return 1 signal
    mock_ra.return_value = [{"indicator": "MACD", "signal": "BULLISH", "meta": {}}]
    mock_ws.return_value = True
    
    # Run scan
    # Also patch sys.argv or just call run_scan directly
    with patch("scanner.SYMBOLS_CSV", Path("nonexistent_so_no_seed")):
        stats = scanner.run_scan(tf, "normal", dry_run=False)
    
    # Verify stats
    assert stats["total"] == 1
    assert stats["scanned"] == 1
    assert stats["failed"] == 0
    assert stats["signals_found"] == 1
    
    # Verify DB update
    conn = sqlite3.connect(mock_db)
    row = conn.execute(f"SELECT status FROM scan_state_{tf} WHERE symbol='7203.T'").fetchone()
    assert row[0] == "SCANNED"
    conn.close()

    mock_ws.assert_called_once()
    mock_wc.assert_called_once()
    mock_eos.assert_called_once()


@patch("scanner.expire_old_signals")
@patch("scanner.get_ohlcv")
@patch("scanner.get_last_closed_bar")
def test_run_scan_failure_nodata(mock_glc, mock_go, mock_eos, mock_db):
    tf = "1MO"
    seed_test_status(mock_db, tf, [{"symbol": "FAIL.T", "status": "PENDING"}])
    mock_glc.return_value = datetime.date(2023, 10, 31)
    
    # Raise NoDataError
    mock_go.side_effect = NoDataError("no data")
    
    with patch("scanner.SYMBOLS_CSV", Path("nonexistent")):
        stats = scanner.run_scan(tf, "normal", dry_run=False)
        
    assert stats["failed"] == 1
    assert stats["scanned"] == 0
    
    conn = sqlite3.connect(mock_db)
    row = conn.execute(f"SELECT status, retry_count FROM scan_state_{tf} WHERE symbol='FAIL.T'").fetchone()
    assert row == ("FAILED", 1)
    conn.close()

@patch("scanner.expire_old_signals")
@patch("scanner.get_ohlcv")
@patch("scanner.get_last_closed_bar")
def test_run_scan_soft_delisting(mock_glc, mock_go, mock_eos, mock_db):
    tf = "1MO"
    seed_test_status(mock_db, tf, [{"symbol": "OLD.T", "status": "PENDING"}])
    mock_glc.return_value = datetime.date(2023, 10, 31)
    
    # Data ending 4 months ago (soft delisting triggers if > 3 months)
    dates = pd.date_range("2020-01-01", "2023-06-30", freq="ME")
    df = pd.DataFrame({"close": [1]*len(dates)}, index=dates)
    mock_go.return_value = df
    
    with patch("scanner.SYMBOLS_CSV", Path("nonexistent")):
        stats = scanner.run_scan(tf, "normal", dry_run=False)
        
    # the symbol loop continues without incrementing scanned or failed according to current logic
    assert stats["failed"] == 0
    assert stats["scanned"] == 0
    
    conn = sqlite3.connect(mock_db)
    row = conn.execute(f"SELECT is_active, status, fail_reason FROM scan_state_{tf} WHERE symbol='OLD.T'").fetchone()
    # Should be set inactive, status remains PENDING
    assert row[0] == 0
    assert "soft delisted" in row[2]
    conn.close()

def test_cli_resume_retry_conflict(capsys):
    test_args = ["scanner.py", "--resume", "--retry-failed"]
    with patch("sys.argv", test_args):
        with pytest.raises(SystemExit) as excinfo:
            scanner.main()
        assert excinfo.value.code == 1
        
        captured = capsys.readouterr()
        assert "không dùng cùng nhau" in captured.out

# --- MORE EXTREME EDGE CASES ---

@patch("scanner.expire_old_signals")
@patch("scanner.get_ohlcv")
@patch("scanner.get_last_closed_bar")
def test_run_scan_data_provider_error(mock_glc, mock_go, mock_eos, mock_db):
    tf = "1MO"
    seed_test_status(mock_db, tf, [{"symbol": "ERR.T", "status": "PENDING"}])
    mock_glc.return_value = datetime.date(2023, 10, 31)
    
    # Raise DataProviderError
    mock_go.side_effect = DataProviderError("Network timeout")
    
    with patch("scanner.SYMBOLS_CSV", Path("nonexistent")):
        stats = scanner.run_scan(tf, "normal", dry_run=False)
        
    assert stats["failed"] == 1
    
    conn = sqlite3.connect(mock_db)
    row = conn.execute(f"SELECT status, fail_reason, retry_count FROM scan_state_{tf} WHERE symbol='ERR.T'").fetchone()
    assert row[0] == "FAILED"
    assert "Network timeout" in row[1]
    assert row[2] == 1
    conn.close()

@patch("scanner.expire_old_signals")
@patch("scanner.get_ohlcv")
@patch("scanner.get_last_closed_bar")
def test_run_scan_generic_exception(mock_glc, mock_go, mock_eos, mock_db):
    tf = "1MO"
    seed_test_status(mock_db, tf, [{"symbol": "GEN_ERR.T", "status": "PENDING"}])
    mock_glc.return_value = datetime.date(2023, 10, 31)
    
    # Raise generic Exception
    mock_go.side_effect = ValueError("Some weird index error")
    
    with patch("scanner.SYMBOLS_CSV", Path("nonexistent")):
        stats = scanner.run_scan(tf, "normal", dry_run=False)
        
    assert stats["failed"] == 1
    
    conn = sqlite3.connect(mock_db)
    row = conn.execute(f"SELECT status, fail_reason FROM scan_state_{tf} WHERE symbol='GEN_ERR.T'").fetchone()
    assert row[0] == "FAILED"
    assert "ValueError: Some weird index error" in row[1]
    conn.close()

@patch("scanner.expire_old_signals")
@patch("scanner.get_ohlcv")
@patch("scanner.get_last_closed_bar")
def test_run_scan_hard_delisted(mock_glc, mock_go, mock_eos, mock_db):
    tf = "1MO"
    # Seed symbol that's already reached 2 retries
    seed_test_status(mock_db, tf, [{"symbol": "DELIST.T", "status": "FAILED", "retry_count": 2}])
    mock_glc.return_value = datetime.date(2023, 10, 31)
    
    # 3rd NoDataError
    mock_go.side_effect = NoDataError("No more data provided")
    
    with patch("scanner.SYMBOLS_CSV", Path("nonexistent")):
        stats = scanner.run_scan(tf, "normal", dry_run=False)
        
    assert stats["failed"] == 1
    
    conn = sqlite3.connect(mock_db)
    row = conn.execute(f"SELECT is_active, status, fail_reason FROM scan_state_{tf} WHERE symbol='DELIST.T'").fetchone()
    # It should become is_active=0, and since we don't change `status` when setting inactive, it might remain FAILED
    assert row[0] == 0
    assert "No more data provided" in row[2]
    conn.close()

@patch("scanner.expire_old_signals")
@patch("scanner.get_ohlcv")
@patch("scanner.write_cache")
@patch("scanner.read_cache")
@patch("scanner.passes_filter")
@patch("scanner.run_all")
@patch("scanner.write_signal")
@patch("scanner.get_last_closed_bar")
def test_run_scan_cache_incomplete_error(mock_glc, mock_ws, mock_ra, mock_pf, mock_rc, mock_wc, mock_go, mock_eos, mock_db):
    # Test fallback to fresh df when writing cache raises DataIncompleteError
    tf = "1MO"
    seed_test_status(mock_db, tf, [{"symbol": "GAP.T", "status": "PENDING"}])
    mock_glc.return_value = datetime.date(2023, 10, 31)
    
    dates = pd.date_range("2023-01-01", "2023-11-01", freq="ME")
    df = pd.DataFrame({"close": [1]*len(dates)}, index=dates)
    mock_go.return_value = df
    
    # Force incomplete error
    mock_wc.side_effect = DataIncompleteError("Cache gap found")
    
    mock_pf.return_value = True
    mock_ra.return_value = []
    
    with patch("scanner.SYMBOLS_CSV", Path("nonexistent")):
        stats = scanner.run_scan(tf, "normal", dry_run=False)
        
    # It should still succeed (scanned = 1) and proceed using df_fresh
    assert stats["scanned"] == 1
    assert stats["failed"] == 0
    
    conn = sqlite3.connect(mock_db)
    row = conn.execute(f"SELECT status FROM scan_state_{tf} WHERE symbol='GAP.T'").fetchone()
    assert row[0] == "SCANNED"
    conn.close()

@patch("scanner.expire_old_signals")
@patch("scanner.time.time")
@patch("scanner.get_last_closed_bar")
def test_run_scan_batch_timeout(mock_glc, mock_time, mock_eos, mock_db):
    tf = "1MO"
    # Seed 3 symbols
    seed_test_status(mock_db, tf, [
        {"symbol": "SYM1.T", "status": "PENDING"},
        {"symbol": "SYM2.T", "status": "PENDING"},
        {"symbol": "SYM3.T", "status": "PENDING"}
    ])
    mock_glc.return_value = datetime.date(2023, 10, 31)
    
    # We mock time.time() to simulate time passing.
    # The batch loop checks: if time.time() - batch_start > MAX_BATCH_TIME_SEC: break
    # Mock return values: 
    #   1. batch_start (e.g. 1000)
    #   2. time.time() - batch_start check for loop 1 -> difference is 1 sec.
    #   3. symbol_start for SYM1
    #   Later calls can blow up the timeout just to stop the loop early.
    
    current_time = [1000.0]
    
    def fake_time():
        t = current_time[0]
        # Fast forward time significantly so the next iteration trips MAX_BATCH_TIME_SEC guard
        # MAX_BATCH_TIME_SEC is usually e.g. 50 minutes (3000s). We jump by 4000s each call to quickly trigger it.
        current_time[0] += 4000.0
        return t
        
    mock_time.side_effect = fake_time
    
    with patch("scanner.SYMBOLS_CSV", Path("nonexistent")):
        stats = scanner.run_scan(tf, "normal", dry_run=False)
        
    # the time will jump after the very first check, so it might process 0 or 1 symbol depending on exact sequence
    # `total` should be 3, but `scanned` + `failed` should be 0 or 1, definitely not 3.
    assert stats["total"] == 3
    assert stats["scanned"] + stats["failed"] < 3
