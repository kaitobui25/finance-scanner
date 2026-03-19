import pandas as pd
from indicators.fvg import analyze, INDICATOR_NAME, VERSION

# Helper for generating simple datetimes
def make_dates(n):
    return pd.date_range(start="2026-01-01", periods=n, freq="D")

def test_fvg_length_less_than_4():
    """Edge case: len(df) < 4 -> should return None signal"""
    df = pd.DataFrame({
        "open":  [100, 100, 100],
        "high":  [110, 110, 110],
        "low":   [90, 90, 90],
        "close": [105, 105, 105],
    }, index=make_dates(3))
    
    result = analyze(df, symbol="TEST")
    assert result["indicator"] == INDICATOR_NAME
    assert result["version"] == VERSION
    assert result["signal"] is None
    assert result["meta"] == {}

def test_fvg_no_gap():
    """Case không có gap -> phải trả None"""
    df = pd.DataFrame({
        "open":  [100, 100, 100, 100],
        "high":  [110, 110, 110, 110],
        "low":   [90,  90,  90,  90],
        "close": [105, 105, 105, 105],
    }, index=make_dates(4))
    
    result = analyze(df, symbol="TEST")
    assert result["signal"] is None

def test_fvg_bullish():
    """Case bullish rõ ràng -> phải detect và verify gap_top/gap_bottom đúng giá trị.
    
    Điều kiện:
    b3["low"] > b1["high"] -> gap tồn tại
    b2["close"] < b3["low"] -> bar giữa phá xuống
    b0["close"] > b3["low"] -> current close vào trong
    gap_top = b3["low"]
    gap_bottom = b1["high"]
    """
    df = pd.DataFrame({
        "open":  [100, 90, 80, 70],
        "high":  [110, 95, 85, 95],
        "low":   [90,  80, 70, 70],
        "close": [95,  85, 75, 92],
    }, index=make_dates(4))
    
    # b3=index 0: high=110, low=90, close=95
    # b2=index 1: high=95, low=80, close=85
    # b1=index 2: high=85, low=70, close=75
    # b0=index 3: high=95, low=70, close=92
    
    result = analyze(df, symbol="TEST")
    assert result["signal"] == "BULLISH"
    assert result["meta"]["gap_top"] == 90.0
    assert result["meta"]["gap_bottom"] == 85.0
    assert result["meta"]["close_price"] == 92.0

def test_fvg_bearish():
    """Case bearish rõ ràng -> phải detect và verify gap_top/gap_bottom đúng giá trị.
    
    Điều kiện:
    b1["low"] > b3["high"] -> gap tồn tại
    b2["close"] > b3["high"] -> bar giữa phá lên trên
    b0["close"] < b3["high"] -> current close vào trong
    gap_top = b1["low"]
    gap_bottom = b3["high"]
    """
    df = pd.DataFrame({
        "open":  [70, 80, 90, 100],
        "high":  [85, 95, 110, 95],
        "low":   [70, 80, 90, 70],
        "close": [75, 87, 105, 80],
    }, index=make_dates(4))
    
    # b3=index 0: high=85, low=70, close=75
    # b2=index 1: high=95, low=80, close=87
    # b1=index 2: high=110, low=90, close=105
    # b0=index 3: high=95, low=70, close=80
    
    result = analyze(df, symbol="TEST")
    assert result["signal"] == "BEARISH"
    assert result["meta"]["gap_top"] == 90.0
    assert result["meta"]["gap_bottom"] == 85.0
    assert result["meta"]["close_price"] == 80.0
