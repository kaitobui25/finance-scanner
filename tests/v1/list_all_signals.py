import sqlite3

DB_PATH = 'data/state.db'

try:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    print("--- Detected Signals ---")
    sig_rows = conn.execute("SELECT symbol, signal_type, signal_date, close_price, gap_bottom, gap_top FROM signals_1MO ORDER BY signal_type, symbol ASC").fetchall()
    
    bullish = [r for r in sig_rows if r['signal_type'] == 'BULLISH']
    bearish = [r for r in sig_rows if r['signal_type'] == 'BEARISH']
    
    print(f"\n🟢 BULLISH SIGNALS ({len(bullish)} items):")
    for s in bullish:
         print(f"  - {s['symbol']} | Close: {s['close_price']} | Gap: {s['gap_bottom']} - {s['gap_top']}")
         
    print(f"\n🔴 BEARISH SIGNALS ({len(bearish)} items):")
    for s in bearish:
         print(f"  - {s['symbol']} | Close: {s['close_price']} | Gap: {s['gap_bottom']} - {s['gap_top']}")
         
except Exception as e:
    print(f"Error: {e}")
