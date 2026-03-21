#!/usr/bin/env python3
"""
tests/test_v2_system_scan.py — Test tổng thể v2 với 6758.T và 9984.T.
Quét 10 năm dữ liệu, lưu DB riêng, và hiển thị kết quả trades để kiểm chứng.
"""

import sys
from pathlib import Path
import sqlite3
import pandas as pd

# Add root directory to path to import modules properly if needed
sys.path.append(str(Path(__file__).resolve().parent.parent))

# 1. Override Database Path / Connection function
import core.position_tracker as pt
TEST_DB_PATH = Path("data/positions_test_v2.db")

def _get_test_conn(db_path=None):
    import sqlite3
    conn = sqlite3.connect(TEST_DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn

pt._get_db_conn = _get_test_conn

# Imports
from data_provider.yahoo import get_ohlcv
from data_provider.cache import write_cache, read_cache
from core.position_tracker import PositionConfig, scan_full_history
import position_monitor

def main():
    print("=== TEST TỔNG THỂ V2 ===")
    
    # Symbols list (Top 30 Blue Chips of Japan)
    symbols = [
        "7203.T", "6758.T", "9984.T", "8306.T", "6861.T", # Toyota, Sony, SBG, MUFG, Keyence
        "9432.T", "8058.T", "8031.T", "4063.T", "6501.T", # NTT, Mitsubishi Corp, Mitsui & Co, Shin-Etsu, Hitachi
        "4568.T", "8316.T", "7974.T", "6098.T", "4502.T", # Daiichi Sankyo, SMBC, Nintendo, Recruit, Takeda
        "9433.T", "8766.T", "6367.T", "8001.T", "2914.T", # KDDI, Tokio Marine, Daikin, Itochu, JT
        "6954.T", "7267.T", "4503.T", "3382.T", "7751.T", # Fanuc, Honda, Astellas, Seven&i, Canon
        "6981.T", "1605.T", "4661.T", "6902.T", "8411.T", # Murata, Inpex, Oriental Land, Denso, Mizuho
    ]
    timeframe = "1MO"
    
    # 2. Setup separate database (xóa nếu có từ trước để test sạch)
    if TEST_DB_PATH.exists():
        TEST_DB_PATH.unlink()
    print(f"Database test: {TEST_DB_PATH}")

    # 3. Download/Cache 10 năm dữ liệu
    print("\n--- 1. Download & Cache Data (10 Years) ---")
    data_frames = {}
    for sym in symbols:
        print(f"Fetching {sym} ({timeframe})...")
        try:
            df = get_ohlcv(sym, timeframe)
            # Ghi vào cache để run_full_scan có thể đọc
            write_cache(sym, timeframe, df)
            data_frames[sym] = df
            print(f"-> {sym} ok. Rows: {len(df)}")
        except Exception as e:
            print(f"-> Fetch {sym} FAILED: {e}")
            return

    # 4. Override _load_symbols trong position_monitor để chỉ chạy 2 symbols này
    def mock_load_symbols():
         return symbols
    position_monitor._load_symbols = mock_load_symbols

    # 5. Khởi tạo config
    cfg = PositionConfig()
    print(f"\nPositionConfig: tp={cfg.tp_mult}, sl={cfg.sl_mult}, ts={cfg.ts_mult}")

    # 6. Chạy scan_full_history trước để lấy LIST TRADES in ra màn hình kiểm chứng
    print("\n--- 2. Lịch sử giao dịch (History) ---")
    
    for sym in symbols:
        df = data_frames[sym]
        print(f"\n=== TRADES CHO {sym} ===")
        # summarize_trades=False, return_trades=True để có list object
        state, trades = scan_full_history(df, cfg, return_trades=True)
        
        if not trades:
            print("Không có giao dịch nào được thực hiện trong 10 năm qua.")
        else:
            print(f"{'Entry Date':<12} {'Exit Date':<12} {'Dir':<5} {'Reason':<10} {'Entry':>8} {'Exit':>8} {'Bars':>4} {'PnL%':>8}")
            print("-" * 75)
            for t in trades:
                pnl_pct = t.pnl_pct * 100 # Chuyển sang %
                print(
                    f"{t.entry_date:<12} "
                    f"{t.exit_date:<12} "
                    f"{t.direction:<5} "
                    f"{t.close_reason:<10} "
                    f"{t.entry_price:>8.0f} "
                    f"{t.actual_exit_price:>8.0f} "
                    f"{t.bars_held:>4} "
                    f"{pnl_pct:>7.2f}%"
                )
        
        if state and state.is_holding:
            print(f"\n[CURRENTLY HOLDING] {sym} | Dir: {state.direction} | Entry: {state.entry_close:.0f} | TP: {state.tp_level:.0f} | SL: {state.sl_level:.0f}")

    # 7. Chay run_full_scan để kiểm tra DB insertion
    print("\n--- 3. Running run_full_scan (Ghi Database) ---")
    stats = position_monitor.run_full_scan(timeframe, cfg, dry_run=False)
    print(f"Stats: {stats}")

    # 8. Đọc DB ra để VERIFY
    print("\n--- 4. Kiểm tra Database Đã Ghi ---")
    conn = sqlite3.connect(TEST_DB_PATH)
    conn.row_factory = sqlite3.Row

    print(f"\n[Bảng positions_{timeframe} (Vị thế đang giữ / State cuối)]")
    rows = conn.execute(f"SELECT * FROM positions_{timeframe}").fetchall()
    if not rows:
        print("Không có vị thế nào.")
    else:
        for r in rows:
             d = dict(r)
             print(f"Symbol: {d['symbol']} | Status: {d['status']} | Dir: {d['direction']} | Entry: {d['entry_close']} | Date: {d['entry_date']}")

    print(f"\n[Bảng position_history_{timeframe} (Lịch sử giao dịch)]")
    hist_rows = conn.execute(f"SELECT * FROM position_history_{timeframe}").fetchall()
    if not hist_rows:
        print("Không có lịch sử giao dịch.")
    else:
        for r in hist_rows:
            d = dict(r)
            print(f"Symbol: {d['symbol']} | Dir: {d['direction']} | Entry: {d['entry_price']} | Exit: {d['exit_price']} | Reason: {d['close_reason']} | Bars: {d['bars_held']}")

    conn.close()
    print("\n=== TEST HOÀN TẤT ===")

if __name__ == "__main__":
    main()
