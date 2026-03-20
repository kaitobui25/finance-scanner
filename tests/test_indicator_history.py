#!/usr/bin/env python3
"""
test_indicator_history.py — Backtest IMFVG indicator trên lịch sử 7203.T

Mục đích:
  Verify IMFVG indicator có thực sự phát hiện signal trên dữ liệu thực.
  Thay vì chỉ check 4 bar cuối (hiện tại), quét TOÀN BỘ lịch sử nến tháng
  bằng sliding window → liệt kê mọi signal BULLISH/BEARISH từ 2000 đến nay.

Cách hoạt động:
  - Fetch 7203.T monthly từ 2000 (dùng yfinance start="2000-01-01")
  - Sliding window: tại mỗi vị trí i (i >= 3), cắt df[:i+1] rồi gọi
    fvg.analyze() → indicator check 4 bar cuối của slice đó
  - Thu thập tất cả signal → in timeline

Usage:
    python -m tests.test_indicator_history
    IMFVG_DEBUG=1 python -m tests.test_indicator_history   (xem raw bar values)
"""

import logging
import os
import sys
import time
from pathlib import Path

import pandas as pd
import yfinance as yf

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))
os.chdir(PROJECT_ROOT)

from core.config import TZ_MARKET
from indicators.fvg import analyze as fvg_analyze

# ── Config ────────────────────────────────────────────────────────────────────

SYMBOL    = "7203.T"
START     = "2000-01-01"
INTERVAL  = "1mo"
TIMEFRAME = "1MO"

# ── Logging ───────────────────────────────────────────────────────────────────

def setup_logging():
    logging.basicConfig(
        level=logging.WARNING,
        format="[%(asctime)s] %(levelname)-5s %(name)-14s %(message)s",
        datefmt="%H:%M:%S",
        handlers=[logging.StreamHandler(sys.stdout)],
    )


# ── Fetch ─────────────────────────────────────────────────────────────────────

def fetch_history(symbol: str, start: str) -> pd.DataFrame:
    """Fetch monthly OHLCV từ Yahoo, normalize."""
    print(f"  Fetching {symbol} monthly from {start} ...")
    t0 = time.time()

    ticker = yf.Ticker(symbol)
    df = ticker.history(start=start, interval=INTERVAL, auto_adjust=True)

    if df is None or df.empty:
        raise RuntimeError(f"No data returned for {symbol}")

    # Flatten MultiIndex if needed
    if isinstance(df.columns, pd.MultiIndex):
        df.columns = df.columns.get_level_values(0)

    df.columns = [c.lower() for c in df.columns]

    # Keep only OHLCV
    required = ["open", "high", "low", "close", "volume"]
    for col in required:
        if col not in df.columns:
            raise RuntimeError(f"Missing column: {col}")
    df = df[required].copy()

    # Timezone
    if not isinstance(df.index, pd.DatetimeIndex):
        df.index = pd.to_datetime(df.index)
    if df.index.tz is None:
        df.index = df.index.tz_localize("UTC").tz_convert(TZ_MARKET)
    else:
        df.index = df.index.tz_convert(TZ_MARKET)
    df.index.name = "date"

    # Clean
    df = df[~df.index.duplicated()]
    df = df.dropna(subset=required)
    df = df.sort_index()

    elapsed = time.time() - t0
    print(f"  Fetched {len(df)} monthly bars ({df.index[0].strftime('%Y-%m')} "
          f"→ {df.index[-1].strftime('%Y-%m')}) in {elapsed:.1f}s")
    return df


# ── Sliding window backtest ──────────────────────────────────────────────────

def run_backtest(df: pd.DataFrame):
    """
    Sliding window: tại mỗi bar i (i >= 3), cắt df[:i+1],
    gọi fvg.analyze() trên slice đó.
    Indicator sẽ check 4 bar cuối = df[i-3], df[i-2], df[i-1], df[i].
    """
    signals = []
    total_windows = len(df) - 3  # cần ít nhất 4 bar

    print(f"\n  Scanning {total_windows} windows (bar index 3 → {len(df)-1}) ...")

    for i in range(3, len(df)):
        # Slice từ đầu đến bar i (inclusive)
        window = df.iloc[:i + 1]

        result = fvg_analyze(window, SYMBOL, TIMEFRAME)

        if result.get("signal") is not None:
            bar_date = df.index[i]
            meta = result.get("meta", {})
            signals.append({
                "bar_index": i,
                "date": bar_date,
                "signal": result["signal"],
                "gap_top": meta.get("gap_top"),
                "gap_bottom": meta.get("gap_bottom"),
                "close_price": meta.get("close_price"),
                # 4 bar dùng để detect
                "b3_date": df.index[i - 3].strftime("%Y-%m"),
                "b2_date": df.index[i - 2].strftime("%Y-%m"),
                "b1_date": df.index[i - 1].strftime("%Y-%m"),
                "b0_date": df.index[i].strftime("%Y-%m"),
                # OHLC 4 bar
                "b3": _bar_str(df.iloc[i - 3]),
                "b2": _bar_str(df.iloc[i - 2]),
                "b1": _bar_str(df.iloc[i - 1]),
                "b0": _bar_str(df.iloc[i]),
            })

    return signals


def _bar_str(bar) -> str:
    """Format 1 bar OHLC ngắn gọn."""
    return (f"O={bar['open']:,.0f} H={bar['high']:,.0f} "
            f"L={bar['low']:,.0f} C={bar['close']:,.0f}")


# ── Pretty print ─────────────────────────────────────────────────────────────

def fmt_price(v):
    if v is None:
        return "-"
    return f"{v:,.0f}"


def print_results(signals, total_bars):
    print()
    print("=" * 90)
    print(f"  IMFVG Backtest Results — {SYMBOL} Monthly ({START} → now)")
    print(f"  Total bars: {total_bars} | Windows scanned: {total_bars - 3} | "
          f"Signals found: {len(signals)}")
    print("=" * 90)

    if not signals:
        print()
        print("  ⚠  No signals found across entire history!")
        print("  This would indicate a potential issue with the indicator logic.")
        print("=" * 90)
        return

    # Summary table
    print()
    print(f"  {'#':>3}  {'Date':>10}  {'Signal':<10}  {'Gap Top':>10}  "
          f"{'Gap Bot':>10}  {'Close':>10}  {'4-Bar Window'}")
    print("─" * 90)

    for idx, s in enumerate(signals, 1):
        date_str = s["date"].strftime("%Y-%m")
        window_str = f"{s['b3_date']} → {s['b2_date']} → {s['b1_date']} → {s['b0_date']}"
        print(f"  {idx:>3}  {date_str:>10}  {s['signal']:<10}  "
              f"{fmt_price(s['gap_top']):>10}  {fmt_price(s['gap_bottom']):>10}  "
              f"{fmt_price(s['close_price']):>10}  {window_str}")

    print("─" * 90)

    # Stats
    bull_count = sum(1 for s in signals if s["signal"] == "BULLISH")
    bear_count = sum(1 for s in signals if s["signal"] == "BEARISH")
    print(f"  BULLISH: {bull_count}  |  BEARISH: {bear_count}  |  Total: {len(signals)}")

    # Detail view cho mỗi signal
    print()
    print("=" * 90)
    print("  Detailed Bar Values (4 bars per signal)")
    print("=" * 90)

    for idx, s in enumerate(signals, 1):
        date_str = s["date"].strftime("%Y-%m")
        print()
        print(f"  Signal #{idx}: {s['signal']} @ {date_str}")
        print(f"    b3 ({s['b3_date']}): {s['b3']}")
        print(f"    b2 ({s['b2_date']}): {s['b2']}")
        print(f"    b1 ({s['b1_date']}): {s['b1']}")
        print(f"    b0 ({s['b0_date']}): {s['b0']}  ← current bar")
        print(f"    Gap: {fmt_price(s['gap_bottom'])} — {fmt_price(s['gap_top'])}")

        if s["signal"] == "BULLISH":
            print(f"    Check: b3.low({fmt_price(_get_val(s, 'b3', 'low'))}) "
                  f"> b1.high({fmt_price(_get_val(s, 'b1', 'high'))}) ? "
                  f"→ gap exists")
            print(f"    Check: b2.close({fmt_price(_get_val(s, 'b2', 'close'))}) "
                  f"< b3.low({fmt_price(_get_val(s, 'b3', 'low'))}) ? "
                  f"→ break below")
            print(f"    Check: b0.close({fmt_price(_get_val(s, 'b0', 'close'))}) "
                  f"> b3.low({fmt_price(_get_val(s, 'b3', 'low'))}) ? "
                  f"→ mitigate into gap")
        elif s["signal"] == "BEARISH":
            print(f"    Check: b1.low({fmt_price(_get_val(s, 'b1', 'low'))}) "
                  f"> b3.high({fmt_price(_get_val(s, 'b3', 'high'))}) ? "
                  f"→ gap exists")
            print(f"    Check: b2.close({fmt_price(_get_val(s, 'b2', 'close'))}) "
                  f"> b3.high({fmt_price(_get_val(s, 'b3', 'high'))}) ? "
                  f"→ break above")
            print(f"    Check: b0.close({fmt_price(_get_val(s, 'b0', 'close'))}) "
                  f"< b3.high({fmt_price(_get_val(s, 'b3', 'high'))}) ? "
                  f"→ mitigate into gap")

    print()
    print("=" * 90)


def _get_val(signal_dict, bar_key, field):
    """Parse value from bar string like 'O=1,234 H=1,300 L=1,200 C=1,250'."""
    bar_str = signal_dict.get(bar_key, "")
    # field mapping: open->O, high->H, low->L, close->C
    prefix = {"open": "O=", "high": "H=", "low": "L=", "close": "C="}[field]
    try:
        start = bar_str.index(prefix) + len(prefix)
        # Find next space or end
        end = bar_str.find(" ", start)
        if end == -1:
            end = len(bar_str)
        val_str = bar_str[start:end].replace(",", "")
        return float(val_str)
    except (ValueError, IndexError):
        return None


# ── Main ──────────────────────────────────────────────────────────────────────

def main():
    setup_logging()

    print()
    print("=" * 90)
    print(f"  IMFVG Indicator Backtest — {SYMBOL} Monthly")
    print(f"  From: {START} | Indicator: IMFVG v1.1")
    print("=" * 90)
    print()

    # Fetch
    df = fetch_history(SYMBOL, START)

    # Backtest
    t0 = time.time()
    signals = run_backtest(df)
    elapsed = time.time() - t0
    print(f"  Backtest completed in {elapsed:.2f}s")

    # Report
    print_results(signals, len(df))


if __name__ == "__main__":
    main()
