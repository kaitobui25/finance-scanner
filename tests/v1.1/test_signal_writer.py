import sqlite3
import pytest
from core.signal_writer import init_db, write_signal, _get_conn
import core.signal_writer as sw

@pytest.fixture
def mock_db(tmp_path):
    # Dùng tmp_path của pytest thay vì file thật state.db
    db_path = tmp_path / "test_state.db"
    
    # Đổi hằng số DB_PATH sang file tạm
    old_db_path = sw.DB_PATH
    sw.DB_PATH = db_path
    
    init_db("1MO")
    
    yield db_path
    
    # Phục hồi
    sw.DB_PATH = old_db_path


def test_write_signal_duplicate_ignored(mock_db):
    """
    Test 9.6: insert → insert lại cùng (symbol, indicator, signal_date) 
    → phải ignore, không raise exception.
    """
    symbol = "7203.T"
    result = {
        "indicator": "IMFVG",
        "version": "1.1",
        "signal": "BULLISH",
        "meta": {
            "gap_top": 100,
            "gap_bottom": 90,
            "close_price": 105
        }
    }
    
    # 1. Insert lần đầu
    first_insert = write_signal(symbol=symbol, result=result, timeframe="1MO")
    assert first_insert is True, "First insert should return True"
    
    # Verify DB
    with _get_conn() as conn:
        count = conn.execute("SELECT COUNT(*) FROM signals_1MO WHERE symbol=? AND indicator=?", (symbol, result["indicator"])).fetchone()[0]
        assert count == 1
        
    # 2. Insert lần 2 cùng symbol, indicator, signal_date (signal_date sẽ tự lấy current từ get_last_closed_bar("1MO"))
    second_insert = write_signal(symbol=symbol, result=result, timeframe="1MO")
    assert second_insert is False, "Second insert should return False (duplicate skipped)"
    
    # Verify DB again, still 1 row
    with _get_conn() as conn:
        count = conn.execute("SELECT COUNT(*) FROM signals_1MO WHERE symbol=? AND indicator=?", (symbol, result["indicator"])).fetchone()[0]
        assert count == 1, "Should ignore duplicate, row count must stay 1"
