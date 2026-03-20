import sqlite3
import os

DB_PATH = 'data/state.db'
if not os.path.exists(DB_PATH):
    print(f"DB not found at {DB_PATH}")
    exit(1)

try:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    # Check if table scan_state_1MO exists
    cur = conn.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='scan_state_1MO'")
    if not cur.fetchone():
        print("scan_state_1MO table does not exist")
        exit(1)
        
    rows = conn.execute('SELECT status, COUNT(*) FROM scan_state_1MO GROUP BY status').fetchall()
    print("--- Status Count ---")
    for r in rows:
        print(f"{r[0]}: {r[1]}")
    
    # Check if signal counts
    cur = conn.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='signals_1MO'")
    if cur.fetchone():
        signals = conn.execute("SELECT COUNT(*) FROM signals_1MO").fetchone()[0]
        print(f"Signals found: {signals}")
    else:
        print("signals_1MO table does not exist")
        
except Exception as e:
    print(f"Error: {e}")
