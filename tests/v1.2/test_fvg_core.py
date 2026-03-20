"""
tests/test_fvg_core.py — Unit tests cho indicators/fvg_core.py

Pure function tests: không cần DataFrame, không cần pandas.
Chạy nhanh, không có side effects.
"""

import sys
import os
import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from indicators.fvg_core import detect_imfvg_from_bars, BULL, BEAR, FVGResult


# ── Helpers ───────────────────────────────────────────────────────────────────

def make_bull_bars(gap_size: float = 10.0, close_in_gap: float = True):
    """
    Tạo 4 bars có Bullish IMFVG rõ ràng.
    b3: low=100, high=110
    b1: low=80,  high=89   → gap = 100 - 89 = 11 (> gap_size default)
    b2: close=95           → phá xuống dưới b3.low (95 < 100)
    b0: close=92 hoặc 101  → trong gap (> b3.low=100) hoặc ngoài
    """
    b3_low, b3_high, b3_close = 100.0, 110.0, 105.0
    b1_low, b1_high           = 80.0, 89.0
    b2_close                  = 95.0   # < b3_low → phá xuống
    b0_close                  = 102.0 if close_in_gap else 88.0
    return b3_low, b3_high, b3_close, b2_close, b1_low, b1_high, b0_close


def make_bear_bars(close_in_gap: bool = True):
    """
    Tạo 4 bars có Bearish IMFVG rõ ràng.
    b3: low=80, high=90
    b1: low=102, high=115  → gap = 102 - 90 = 12
    b2: close=95           → phá lên trên b3.high (95 > 90)
    b0: close=88 hoặc 96   → trong gap (< b3.high=90) hoặc ngoài
    """
    b3_low, b3_high, b3_close = 80.0, 90.0, 85.0
    b1_low, b1_high           = 102.0, 115.0
    b2_close                  = 95.0   # > b3_high → phá lên
    b0_close                  = 88.0 if close_in_gap else 96.0
    return b3_low, b3_high, b3_close, b2_close, b1_low, b1_high, b0_close


# ── T39: Bullish detected ─────────────────────────────────────────────────────

def test_bull_detected():
    """Bullish IMFVG rõ ràng → signal=BULL, gap_top/bottom đúng."""
    b3_low, b3_high, b3_close, b2_close, b1_low, b1_high, b0_close = make_bull_bars()
    result = detect_imfvg_from_bars(
        b3_low, b3_high, b3_close,
        b2_close,
        b1_low, b1_high,
        b0_close,
    )
    assert result["signal"]     == BULL
    assert result["gap_top"]    == b3_low   # = 100.0
    assert result["gap_bottom"] == b1_high  # = 89.0


# ── T40: Bearish detected ─────────────────────────────────────────────────────

def test_bear_detected():
    """Bearish IMFVG rõ ràng → signal=BEAR, gap_top/bottom đúng."""
    b3_low, b3_high, b3_close, b2_close, b1_low, b1_high, b0_close = make_bear_bars()
    result = detect_imfvg_from_bars(
        b3_low, b3_high, b3_close,
        b2_close,
        b1_low, b1_high,
        b0_close,
    )
    assert result["signal"]     == BEAR
    assert result["gap_top"]    == b1_low   # = 102.0
    assert result["gap_bottom"] == b3_high  # = 90.0


# ── T41: Bear overrides Bull ──────────────────────────────────────────────────

def test_bear_overrides_bull():
    """
    Construct case cả Bull lẫn Bear đều True → BEAR phải win.

    Điều kiện Bull:  b3.low > b1.high AND b2.close < b3.low AND b0.close > b3.low
    Điều kiện Bear:  b1.low > b3.high AND b2.close > b3.high AND b0.close < b3.high

    Để cả hai cùng True cùng lúc là rất khó trong thực tế,
    nhưng có thể construct số liệu đặc biệt:
    b3: low=50, high=60
    b1: low=70, high=80  → bull gap = 50-80 < 0 → không có bull gap!

    Thực ra Pine Script không thể có cả bull và bear cùng True
    vì bull cần b3.low > b1.high còn bear cần b1.low > b3.high —
    hai điều kiện này loại trừ nhau về mặt toán học:
      bull: b3.low > b1.high
      bear: b1.low > b3.high
      Nếu b3.low > b1.high VÀ b1.low > b3.high
      → b3.low > b1.high > b3.high (cần b1.high > b3.high)
      → b3.low > b3.high (vô lý: low > high)

    Vì vậy bear override bull chỉ là safety net trong Pine,
    không thể xảy ra trong practice. Test này verify code đúng.

    Ta mock bằng cách check logic từng condition riêng:
    Verify bear được return khi bear=True (dù bull=False trong thực tế).
    """
    # Bear case rõ ràng — verify BEAR được trả
    b3_low, b3_high, b3_close, b2_close, b1_low, b1_high, b0_close = make_bear_bars()
    result = detect_imfvg_from_bars(
        b3_low, b3_high, b3_close,
        b2_close,
        b1_low, b1_high,
        b0_close,
    )
    assert result["signal"] == BEAR, "Bear case phải trả BEAR"

    # Bull case rõ ràng — verify BULL được trả
    b3_low, b3_high, b3_close, b2_close, b1_low, b1_high, b0_close = make_bull_bars()
    result = detect_imfvg_from_bars(
        b3_low, b3_high, b3_close,
        b2_close,
        b1_low, b1_high,
        b0_close,
    )
    assert result["signal"] == BULL, "Bull case phải trả BULL"

    # Verify bear override: nếu force bear condition → BEAR
    # Dùng bear bars và verify không bị ghi đè bởi gì
    result2 = detect_imfvg_from_bars(
        80.0, 90.0, 85.0,   # b3
        95.0,               # b2: > b3.high=90 → bear condition 2
        102.0, 115.0,       # b1: b1.low=102 > b3.high=90 → bear condition 1
        88.0,               # b0: < b3.high=90 → bear condition 3
    )
    assert result2["signal"] == BEAR


# ── T42: No signal ────────────────────────────────────────────────────────────

def test_no_signal():
    """Không có gap → signal=None, gap_top/bottom=None."""
    # b3.low = b1.high → không có gap (cần b3.low > b1.high)
    result = detect_imfvg_from_bars(
        b3_low=100.0, b3_high=110.0, b3_close=105.0,
        b2_close=105.0,
        b1_low=95.0, b1_high=100.0,   # b1.high = b3.low → không có gap
        b0_close=102.0,
    )
    assert result["signal"]     is None
    assert result["gap_top"]    is None
    assert result["gap_bottom"] is None


def test_no_signal_b0_does_not_close_in_gap():
    """Gap tồn tại nhưng b0 không close vào trong gap → None."""
    # Bull setup nhưng b0.close < b3.low → không mitigate
    b3_low, b3_high, b3_close, b2_close, b1_low, b1_high, b0_close = make_bull_bars(
        close_in_gap=False
    )
    result = detect_imfvg_from_bars(
        b3_low, b3_high, b3_close,
        b2_close,
        b1_low, b1_high,
        b0_close,
    )
    assert result["signal"] is None


# ── T43: filter_width=0 không cần atr ────────────────────────────────────────

def test_filter_width_zero_no_atr_needed():
    """filter_width=0 (default) → không cần truyền atr, không raise."""
    b3_low, b3_high, b3_close, b2_close, b1_low, b1_high, b0_close = make_bull_bars()

    # Không truyền filter_width và atr → default filter_width=0.0
    result = detect_imfvg_from_bars(
        b3_low, b3_high, b3_close,
        b2_close,
        b1_low, b1_high,
        b0_close,
    )
    assert result["signal"] == BULL

    # Truyền filter_width=0.0 tường minh, vẫn không cần atr
    result2 = detect_imfvg_from_bars(
        b3_low, b3_high, b3_close,
        b2_close,
        b1_low, b1_high,
        b0_close,
        filter_width=0.0,
        # atr không truyền
    )
    assert result2["signal"] == BULL


# ── T44: filter_width > 0 cần atr ────────────────────────────────────────────

def test_filter_width_nonzero_requires_atr_none():
    """filter_width > 0 nhưng atr=None → ValueError."""
    b3_low, b3_high, b3_close, b2_close, b1_low, b1_high, b0_close = make_bull_bars()
    with pytest.raises(ValueError, match="atr phải là số dương"):
        detect_imfvg_from_bars(
            b3_low, b3_high, b3_close,
            b2_close,
            b1_low, b1_high,
            b0_close,
            filter_width=0.5,
            atr=None,
        )


def test_filter_width_nonzero_requires_atr_zero():
    """filter_width > 0 nhưng atr=0 → ValueError."""
    b3_low, b3_high, b3_close, b2_close, b1_low, b1_high, b0_close = make_bull_bars()
    with pytest.raises(ValueError, match="atr phải là số dương"):
        detect_imfvg_from_bars(
            b3_low, b3_high, b3_close,
            b2_close,
            b1_low, b1_high,
            b0_close,
            filter_width=0.5,
            atr=0.0,
        )


def test_filter_width_nonzero_requires_atr_negative():
    """filter_width > 0 nhưng atr < 0 → ValueError."""
    b3_low, b3_high, b3_close, b2_close, b1_low, b1_high, b0_close = make_bull_bars()
    with pytest.raises(ValueError):
        detect_imfvg_from_bars(
            b3_low, b3_high, b3_close,
            b2_close,
            b1_low, b1_high,
            b0_close,
            filter_width=0.5,
            atr=-5.0,
        )


# ── T45: filter_width lọc gap nhỏ ────────────────────────────────────────────

def test_filter_width_filters_small_gap():
    """
    Gap = 11 (100 - 89), ATR = 100, filter_width = 0.2
    Threshold = 100 * 0.2 = 20 > gap 11 → bị lọc → None.
    """
    b3_low, b3_high, b3_close, b2_close, b1_low, b1_high, b0_close = make_bull_bars()
    # gap_bull = b3_low - b1_high = 100 - 89 = 11
    result = detect_imfvg_from_bars(
        b3_low, b3_high, b3_close,
        b2_close,
        b1_low, b1_high,
        b0_close,
        filter_width=0.2,
        atr=100.0,  # threshold = 100 * 0.2 = 20 > gap 11 → filtered
    )
    assert result["signal"] is None, (
        f"Gap=11 phải bị lọc với threshold=20, nhận signal={result['signal']}"
    )


def test_filter_width_filters_small_gap_bear():
    """Bear gap nhỏ cũng bị lọc."""
    b3_low, b3_high, b3_close, b2_close, b1_low, b1_high, b0_close = make_bear_bars()
    # gap_bear = b1_low - b3_high = 102 - 90 = 12
    result = detect_imfvg_from_bars(
        b3_low, b3_high, b3_close,
        b2_close,
        b1_low, b1_high,
        b0_close,
        filter_width=0.2,
        atr=100.0,  # threshold = 20 > gap 12 → filtered
    )
    assert result["signal"] is None


# ── T46: filter_width pass large gap ─────────────────────────────────────────

def test_filter_width_passes_large_gap():
    """
    Gap lớn vượt threshold → signal không bị lọc.
    b3.low=150, b1.high=90 → gap = 60, ATR=100, threshold=0.2*100=20 < 60 → pass.
    """
    result = detect_imfvg_from_bars(
        b3_low=150.0, b3_high=160.0, b3_close=155.0,
        b2_close=140.0,              # < b3_low=150 → phá xuống
        b1_low=80.0, b1_high=90.0,   # gap = 150 - 90 = 60
        b0_close=152.0,              # > b3_low=150 → mitigate
        filter_width=0.2,
        atr=100.0,                   # threshold = 20 < gap 60 → pass
    )
    assert result["signal"] == BULL
    assert result["gap_top"]    == 150.0
    assert result["gap_bottom"] == 90.0


def test_filter_width_passes_large_gap_bear():
    """Bear gap lớn cũng pass filter."""
    result = detect_imfvg_from_bars(
        b3_low=80.0, b3_high=90.0, b3_close=85.0,
        b2_close=135.0,              # > b3_high=90 → phá lên
        b1_low=150.0, b1_high=170.0, # gap = 150 - 90 = 60
        b0_close=88.0,               # < b3_high=90 → mitigate
        filter_width=0.2,
        atr=100.0,                   # threshold = 20 < gap 60 → pass
    )
    assert result["signal"] == BEAR
    assert result["gap_top"]    == 150.0
    assert result["gap_bottom"] == 90.0


# ── Extra: gap_top/gap_bottom accuracy ───────────────────────────────────────

def test_bull_gap_values_exact():
    """Bull: gap_top = b3.low, gap_bottom = b1.high — không phải high/low khác."""
    result = detect_imfvg_from_bars(
        b3_low=123.4, b3_high=200.0, b3_close=150.0,
        b2_close=100.0,
        b1_low=50.0, b1_high=99.9,
        b0_close=125.0,
    )
    assert result["signal"]     == BULL
    assert result["gap_top"]    == pytest.approx(123.4)
    assert result["gap_bottom"] == pytest.approx(99.9)


def test_bear_gap_values_exact():
    """Bear: gap_top = b1.low, gap_bottom = b3.high — không phải high/low khác."""
    result = detect_imfvg_from_bars(
        b3_low=50.0, b3_high=77.7, b3_close=60.0,
        b2_close=88.8,
        b1_low=100.1, b1_high=200.0,
        b0_close=75.0,
    )
    assert result["signal"]     == BEAR
    assert result["gap_top"]    == pytest.approx(100.1)
    assert result["gap_bottom"] == pytest.approx(77.7)


# ── Extra: constants ──────────────────────────────────────────────────────────

def test_constants():
    """BULL và BEAR constants đúng giá trị."""
    assert BULL == "BULL"
    assert BEAR == "BEAR"


def test_result_is_typed_dict():
    """FVGResult trả đúng type."""
    b3_low, b3_high, b3_close, b2_close, b1_low, b1_high, b0_close = make_bull_bars()
    result = detect_imfvg_from_bars(
        b3_low, b3_high, b3_close, b2_close, b1_low, b1_high, b0_close
    )
    assert "signal"     in result
    assert "gap_top"    in result
    assert "gap_bottom" in result
