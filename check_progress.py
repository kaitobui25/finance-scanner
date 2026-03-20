import sqlite3

DB_PATH = 'data/state.db'

try:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    
    # State counts
    rows = conn.execute('SELECT status, COUNT(*) FROM scan_state_1MO GROUP BY status').fetchall()
    print("--- Status Count ---")
    for r in rows:
        print(f"{r[0]}: {r[1]}")
    
    # Check if signals table exists
    cur = conn.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='signals_1MO'")
    if cur.fetchone():
        signals = conn.execute("SELECT COUNT(*) FROM signals_1MO").fetchone()[0]
        print(f"Signals found: {signals}")
    else:
        print("signals_1MO table does not exist")
        
except Exception as e:
    print(f"Error: {e}")
