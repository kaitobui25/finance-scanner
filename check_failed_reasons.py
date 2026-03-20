import sqlite3
import re

DB_PATH = 'data/state.db'

try:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    
    rows = conn.execute("SELECT fail_reason FROM scan_state_1MO WHERE status = 'FAILED'").fetchall()
    
    summary = {}
    for r in rows:
        reason = r['fail_reason'] if r['fail_reason'] else 'None'
        clean_reason = re.sub(r'^[A-Z0-9.]+:\s*', '', reason)
        
        # Categorize
        if 'Parquet file size is' in clean_reason or 'Unexpected end of stream' in clean_reason or 'Malformed levels' in clean_reason:
            cat = "Corrupt Cache File (Parquet read error)"
        elif 'volume=0 (stale data)' in clean_reason:
            cat = "Stale Data (Volume = 0)"
        elif 'no bars remaining after dropping future bars' in clean_reason:
            cat = "Incomplete/Future Bar Mismatch"
        elif 'Yahoo not yet updated' in clean_reason or 'identical to previous' in clean_reason:
            cat = "Identical/Duplicate Bar (Sync lag)"
        elif '!= expected' in clean_reason:
            cat = "Missing Recent Closed Bar"
        elif 'no data after' in clean_reason or 'empty' in clean_reason:
            cat = "No Data from Yahoo"
        else:
            cat = clean_reason[:80] # Fallback
            
        summary[cat] = summary.get(cat, 0) + 1
        
    sorted_summary = sorted(summary.items(), key=lambda x: x[1], reverse=True)
    
    print("--- Aggregated Failed Reasons ---")
    for reason, cnt in sorted_summary:
        print(f"Count: {cnt:4d} | {reason}")
        
except Exception as e:
    print(f"Error: {e}")
