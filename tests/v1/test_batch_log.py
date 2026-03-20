import json
import pytest
from datetime import datetime, timezone
from zoneinfo import ZoneInfo
from core.signal_writer import init_db
import core.batch_log as bl

@pytest.fixture
def mock_db(tmp_path):
    db_path = tmp_path / "test_state.db"
    
    old_db_path = bl.DB_PATH
    bl.DB_PATH = db_path
    
    # Initialize DB for all timeframes
    import core.signal_writer as sw
    old_sw_db_path = sw.DB_PATH
    sw.DB_PATH = db_path
    
    init_db("1MO")
    init_db("1WK")
    init_db("1D")
    
    yield db_path
    
    bl.DB_PATH = old_db_path
    sw.DB_PATH = old_sw_db_path

def test_log_batch_run_success(mock_db):
    stats = {
        "total_symbols": 100,
        "scanned": 90,
        "failed": 10,
        "signals_found": 5,
        "duration_sec": 12.345
    }
    
    run_id = bl.log_batch_run("1MO", stats)
    assert run_id > 0
    
    # Export immediately
    js_str = bl.export_json("1MO", run_id=run_id)
    data = json.loads(js_str)
    
    assert data["run_id"] == run_id
    assert data["timeframe"] == "1MO"
    assert data["total_symbols"] == 100
    assert data["scanned"] == 90
    assert data["failed"] == 10
    assert data["signals_found"] == 5
    assert data["duration_sec"] == 12.35  # round to 2 decimals

def test_export_json_latest(mock_db):
    # Log two runs
    bl.log_batch_run("1D", {"scanned": 1})
    bl.log_batch_run("1D", {"scanned": 99})
    
    js_str = bl.export_json("1D") # no run_id, gets latest
    data = json.loads(js_str)
    
    assert data["timeframe"] == "1D"
    assert data["scanned"] == 99

def test_export_json_not_found(mock_db):
    # Empty db
    js_str = bl.export_json("1WK")
    data = json.loads(js_str)
    assert data.get("error") == "no_batch_run"

def test_invalid_timeframe():
    with pytest.raises(ValueError):
        bl.log_batch_run("1M", {})
    with pytest.raises(ValueError):
        bl.export_json("1HOUR")
