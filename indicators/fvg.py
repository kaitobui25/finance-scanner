import os
import logging
import pandas as pd
from indicators.base import IndicatorResult

INDICATOR_NAME = "IMFVG"
VERSION        = "1.1"

# Bật IMFVG_DEBUG=1 để inject bar values vào meta["debug"]
# Dùng khi verify signal sai mà không cần mở chart
DEBUG_MODE = os.getenv("IMFVG_DEBUG", "0") == "1"

log = logging.getLogger("fvg")

_NONE_RESULT: IndicatorResult = {
    "indicator": INDICATOR_NAME,
    "version":   VERSION,
    "signal":    None,
    "meta":      {},
}


def _bar_snapshot(bar: pd.Series) -> dict:
    """Extract OHLC từ một bar để ghi vào meta["debug"]."""
    return {
        "open":  float(bar["open"]),
        "high":  float(bar["high"]),
        "low":   float(bar["low"]),
        "close": float(bar["close"]),
    }


def analyze(
    df        : pd.DataFrame,
    symbol    : str,
    timeframe : str = "1MO",
) -> IndicatorResult:
    """
    Instantaneous Mitigation FVG (IMFVG) — dịch từ Pine Script LuxAlgo.

    FVG được tạo ra và bị mitigate ngay tại cùng bar quan sát.
    Bar hiện tại (iloc[-1]) đóng vào trong gap của 3 bar trước.

    Cần ít nhất 4 bar:
        iloc[-1] = current bar
        iloc[-2] = previous bar     (1 bar ago)
        iloc[-3] = 2 bars ago
        iloc[-4] = 3 bars ago

    Bullish IMFVG:
        1. iloc[-4]["low"]   > iloc[-2]["high"]   # gap tồn tại
        2. iloc[-3]["close"] < iloc[-4]["low"]    # bar giữa phá xuống dưới gap
        3. iloc[-1]["close"] > iloc[-4]["low"]    # current bar close vào trong gap
        gap_top    = iloc[-4]["low"]
        gap_bottom = iloc[-2]["high"]

    Bearish IMFVG:
        1. iloc[-2]["low"]   > iloc[-4]["high"]   # gap tồn tại
        2. iloc[-3]["close"] > iloc[-4]["high"]   # bar giữa phá lên trên gap
        3. iloc[-1]["close"] < iloc[-4]["high"]   # current bar close vào trong gap
        gap_top    = iloc[-2]["low"]
        gap_bottom = iloc[-4]["high"]

    Ưu tiên: nếu cả bull lẫn bear đều true → BEARISH
    (theo Pine Script: bear check sau bull, ghi đè os)
    """
    # Guard: không đủ bar
    if len(df) < 4:
        return _NONE_RESULT

    # Guard: NaN trong 4 bar cuối → skip, không miss signal silently
    if df[["open", "high", "low", "close"]].tail(4).isna().any().any():
        return _NONE_RESULT

    b0 = df.iloc[-1]   # current bar
    b1 = df.iloc[-2]   # 1 bar ago
    b2 = df.iloc[-3]   # 2 bars ago
    b3 = df.iloc[-4]   # 3 bars ago

    # Bullish IMFVG
    bull = (
        b3["low"]   > b1["high"]  and   # gap tồn tại
        b2["close"] < b3["low"]   and   # bar giữa phá xuống dưới
        b0["close"] > b3["low"]         # current close vào trong gap
    )

    # Bearish IMFVG
    bear = (
        b1["low"]   > b3["high"]  and   # gap tồn tại
        b2["close"] > b3["high"]  and   # bar giữa phá lên trên
        b0["close"] < b3["high"]        # current close vào trong gap
    )

    # Tích lũy — bear overwrite bull (đúng Pine Script intent)
    signal = None
    meta: dict = {}

    if bull:
        signal = "BULLISH"
        meta   = {
            "gap_top":    float(b3["low"]),
            "gap_bottom": float(b1["high"]),
            "close_price": float(b0["close"]),
        }

    if bear:
        signal = "BEARISH"
        meta   = {
            "gap_top":    float(b1["low"]),
            "gap_bottom": float(b3["high"]),
            "close_price": float(b0["close"]),
        }

    if signal is not None:
        log.debug(f"{symbol} {INDICATOR_NAME} {signal} detected "
                  f"gap={meta['gap_bottom']}-{meta['gap_top']} "
                  f"close={meta['close_price']}")

        if DEBUG_MODE:
            meta["debug"] = {
                "b0": _bar_snapshot(b0),
                "b1": _bar_snapshot(b1),
                "b2": _bar_snapshot(b2),
                "b3": _bar_snapshot(b3),
                "bar_dates": {
                    "b0": str(df.index[-1].date()),
                    "b1": str(df.index[-2].date()),
                    "b2": str(df.index[-3].date()),
                    "b3": str(df.index[-4].date()),
                },
            }

    return {
        "indicator": INDICATOR_NAME,
        "version":   VERSION,
        "signal":    signal,
        "meta":      meta,
    }
