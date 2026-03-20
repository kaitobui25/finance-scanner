"""
indicators/fvg_core.py — Pure IMFVG detection logic.

Single source of truth. Dùng bởi:
  - indicators/fvg.py        (scanner plugin)
  - core/position_tracker.py (position monitor)

Không import pandas, không phụ thuộc bất kỳ module nào trong project.
Nếu logic FVG thay đổi → chỉ sửa file này → cả hai hệ thống cập nhật.
"""

from typing import TypedDict

# Constants: tránh typo string xuyên suốt codebase
BULL = "BULL"
BEAR = "BEAR"


class FVGResult(TypedDict):
    signal:     str | None    # BULL | BEAR | None
    gap_top:    float | None
    gap_bottom: float | None


# Sentinel để tránh lặp lại dict literal ở nhiều chỗ
EMPTY: FVGResult = {"signal": None, "gap_top": None, "gap_bottom": None}


def detect_imfvg_from_bars(
    b3_low:       float,
    b3_high:      float,
    b3_close:     float,   # unused in IMFVG logic — kept for API consistency with caller
    b2_close:     float,
    b1_low:       float,
    b1_high:      float,
    b0_close:     float,
    filter_width: float = 0.0,
    atr:          float | None = None,
) -> FVGResult:
    """
    Detect Instantaneous Mitigation FVG từ 4 bar OHLC primitives.

    Dịch 1:1 từ Pine Script LuxAlgo (filterWidth = 0 mặc định).
    Bear override Bull nếu cả hai đều True.

    Bar mapping (oldest → current):
        b3 = 3 bars ago
        b2 = 2 bars ago
        b1 = 1 bar ago
        b0 = current bar

    Bullish IMFVG:
        b3.low > b1.high          # gap tồn tại giữa b3 và b1
        b2.close < b3.low         # bar giữa phá xuống dưới gap
        b0.close > b3.low         # bar hiện tại close vào trong gap (mitigate)
        gap_top    = b3.low
        gap_bottom = b1.high

    Bearish IMFVG:
        b1.low > b3.high          # gap tồn tại phía trên
        b2.close > b3.high        # bar giữa phá lên trên gap
        b0.close < b3.high        # bar hiện tại close vào trong gap (mitigate)
        gap_top    = b1.low
        gap_bottom = b3.high

    Args:
        b3_low .. b0_close: Raw OHLC floats.
                            Caller phải guard NaN trước khi gọi.
        filter_width:       Tương đương Pine filterWidth. Gap size phải > atr * filter_width.
                            0.0 = không lọc (Pine default). Không phải pixel width.
        atr:                ATR tại bar hiện tại.
                            Bắt buộc (và phải > 0) khi filter_width > 0.

    Raises:
        ValueError: nếu filter_width > 0 nhưng atr là None hoặc <= 0.

    Returns:
        FVGResult với signal=BULL|BEAR|None và gap levels.
        gap_top/gap_bottom = None khi signal = None.

    Error policy:
        Expected data issue (NaN, thiếu bar) → caller trả EMPTY
        Programming error (invalid args)     → raise ValueError
    """
    if filter_width > 0:
        if atr is None or atr <= 0:
            raise ValueError(
                f"atr phải là số dương khi filter_width > 0 "
                f"(nhận: atr={atr}, filter_width={filter_width})"
            )

    # --- Bullish IMFVG ---
    bull = (
        b3_low   > b1_high and
        b2_close < b3_low  and
        b0_close > b3_low
    )
    if bull and filter_width > 0:
        bull = (b3_low - b1_high) > atr * filter_width

    # --- Bearish IMFVG ---
    bear = (
        b1_low   > b3_high and
        b2_close > b3_high and
        b0_close < b3_high
    )
    if bear and filter_width > 0:
        bear = (b1_low - b3_high) > atr * filter_width

    # Bear override Bull (Pine Script: bear check sau bull, ghi đè os)
    if bear:
        return {"signal": BEAR, "gap_top": b1_low,  "gap_bottom": b3_high}
    if bull:
        return {"signal": BULL, "gap_top": b3_low,  "gap_bottom": b1_high}
    return EMPTY