# System Walkthrough — Japan Stock Scanner v1

> Tài liệu mô tả chi tiết luồng chạy thực tế của hệ thống, dựa trên code hiện tại.
> Cập nhật: 2026-03-20

---

## Tổng quan hệ thống

Hệ thống quét ~4000 mã cổ phiếu Nhật hàng tháng, phân tích bằng indicator IMFVG (Instantaneous Mitigation FVG), ghi signal vào SQLite, và gửi alert qua Telegram.

```
Cronjob (mùng 4/tháng, 00:05 JST)
       │
       ▼
    run.sh
       │
       ▼
  scanner.py --timeframe 1MO
       │
       ├── yahoo.py      → fetch OHLCV
       ├── cache.py       → lưu/đọc parquet
       ├── pre_filter.py  → lọc mã rác
       ├── fvg.py         → phát hiện IMFVG signal
       ├── signal_writer  → ghi signals vào DB
       ├── batch_log.py   → log kết quả batch
       └── notifier.py    → gửi Telegram
```

---

## Cấu trúc thư mục thực tế

```
finance-scanner/
├── .env                          # TELEGRAM_TOKEN, CHAT_ID
├── requirements.txt
├── run.sh                        # entry point cho cronjob
├── scanner.py                    # CLI chính — orchestrates mọi thứ
│
├── core/
│   ├── config.py                 # constants, timezone, get_last_closed_bar()
│   ├── pre_filter.py             # lọc penny stock, inactive, thin liquidity
│   ├── plugin_manager.py         # auto-load indicators/*.py, chạy analyze()
│   ├── signal_writer.py          # init DB, seed symbols, write/expire signals
│   ├── batch_log.py              # ghi batch_runs, export JSON
│   └── notifier.py               # Telegram alerts, chunking
│
├── data_provider/
│   ├── base.py                   # Protocol + custom exceptions
│   ├── yahoo.py                  # fetch từ Yahoo Finance + retry
│   └── cache.py                  # read/write parquet, merge window
│
├── indicators/
│   ├── base.py                   # IndicatorResult TypedDict, contract
│   └── fvg.py                    # IMFVG Bullish/Bearish logic
│
├── data/
│   ├── state.db                  # SQLite — scan_state, signals, batch_runs
│   └── symbols.csv               # ~4000 mã Nhật (.T suffix)
│
├── cache/
│   └── {symbol}_{timeframe}.parquet
│
├── logs/
│   └── batch_{timeframe}_{YYYYMM}.log
│
└── tests/
    ├── test_fvg.py
    ├── test_pre_filter.py
    ├── test_scanner.py
    └── ...
```

---

## Luồng chạy chi tiết — từng bước

### Bước 0: Cronjob gọi `run.sh`

**File**: [run.sh](file:///d:/Phong/03_Finance/finance-scanner/run.sh)

Cronjob chạy mùng 4 hàng tháng lúc 00:05 JST:
```
5 0 4 * * /path/to/finance-scanner/run.sh
```

`run.sh` thực hiện:

1. `cd` vào thư mục project
2. Tìm Python interpreter (ưu tiên `.venv/bin/python`)
3. Load `.env` (TELEGRAM_TOKEN, CHAT_ID)
4. **Lock file** `/tmp/japan_scanner_1MO.lock` — dùng `flock` để tránh chạy trùng batch
5. Gọi `python scanner.py --timeframe 1MO`
6. Log cron output vào `logs/cron.log`

> [!NOTE]
> Lock file ngăn trường hợp batch trước chưa xong mà cron trigger batch mới.

---

### Bước 1: `scanner.py` — CLI parse + orchestration

**File**: [scanner.py](file:///d:/Phong/03_Finance/finance-scanner/scanner.py)

#### 1.1 Parse CLI arguments

```
--timeframe    "1MO" (default) | "1WK" | "1D"
--resume       chỉ quét PENDING (tiếp tục batch dang dở)
--retry-failed force retry tất cả FAILED
--dry-run      chạy full nhưng không ghi DB, không gửi Telegram
```

`--resume` và `--retry-failed` không được dùng cùng nhau → `sys.exit(1)`.

Mode được xác định:
- `--resume` → `mode = "resume"`
- `--retry-failed` → `mode = "retry-failed"` 
- default → `mode = "normal"`

#### 1.2 Setup logging

Tạo 2 handler:
- **File handler**: `logs/batch_1MO_202603.log` (theo YYYYMM hiện tại)
- **Stdout handler**: in ra terminal

Format chuẩn:
```
[2026-03-04 00:05:02 JST] INFO  scanner        batch start | tf=1MO ...
```

Dùng `JSTFormatter` custom — override `formatTime()` để luôn in giờ JST bất kể server timezone. Lý do: `logging.Formatter` mặc định dùng `time.localtime()` → nếu server chạy UTC thì timestamp sẽ sai 9 giờ.

#### 1.3 Gọi `run_scan(timeframe, mode, dry_run)`

Hàm chính, trả về dict stats:
```python
{"total_symbols": int, "scanned": int, "failed": int,
 "signals_found": int, "duration_sec": float}
```

---

### Bước 2: Init DB + Seed symbols

**File**: [signal_writer.py](file:///d:/Phong/03_Finance/finance-scanner/core/signal_writer.py)

#### 2.1 `init_db("1MO")`

Tạo 3 bảng nếu chưa tồn tại:

**`scan_state_1MO`** — tracking trạng thái quét từng symbol:
| Column | Type | Ghi chú |
|--------|------|---------|
| symbol | TEXT PK | VD: `7203.T` |
| status | TEXT | `PENDING` / `SCANNED` / `FAILED` |
| last_scanned_at | DATETIME | UTC |
| fail_reason | TEXT | NULL nếu ok |
| retry_count | INTEGER | default 0 |
| is_active | INTEGER | default 1, set 0 khi delisted |

**`signals_1MO`** — lưu signal được phát hiện:
| Column | Type | Ghi chú |
|--------|------|---------|
| id | INTEGER PK | autoincrement |
| symbol | TEXT | |
| indicator | TEXT | `IMFVG` |
| signal_date | DATE | VD: `2026-02-01` |
| signal_type | TEXT | `BULLISH` / `BEARISH` |
| status | TEXT | `ACTIVE` / `EXPIRED` |
| gap_top | REAL | biên trên vùng FVG |
| gap_bottom | REAL | biên dưới vùng FVG |
| close_price | REAL | giá đóng cửa lúc signal |
| indicator_version | TEXT | `1.1` |
| notified_at | DATETIME | NULL = chưa gửi Telegram |
| created_at | DATETIME | UTC |

Constraint: `UNIQUE(symbol, indicator, signal_date)` — chống duplicate.

**`batch_runs_1MO`** — tracking metadata mỗi lần chạy batch:
| Column | Type |
|--------|------|
| id | INTEGER PK |
| run_date | DATE |
| total_symbols | INTEGER |
| scanned | INTEGER |
| failed | INTEGER |
| signals_found | INTEGER |
| duration_sec | REAL |

Indexes:
```sql
idx_scan_state_1MO_status_retry  ON scan_state_1MO(status, retry_count)
idx_signals_1MO_active_notify    ON signals_1MO(status, notified_at)
```

#### 2.2 `seed_symbols("data/symbols.csv", "1MO")`

Đọc `data/symbols.csv` (~4000 mã), mỗi dòng 1 symbol (VD: `7203.T`).

`INSERT OR IGNORE INTO scan_state_1MO (symbol) VALUES (?)` — idempotent, chạy lại không mất state hiện có.

Log: `seed_symbols: 0 new symbols inserted (3987 total) tf=1MO`

---

### Bước 3: Batch Reset + Load symbols

#### 3.1 Batch reset (chỉ normal mode)

```sql
UPDATE scan_state_1MO
   SET status = 'PENDING', retry_count = 0
 WHERE status = 'SCANNED'
```

Tất cả mã đã `SCANNED` tháng trước → reset về `PENDING` để quét lại.
Mã `FAILED` **không** bị reset — giữ nguyên để retry logic xử lý.

#### 3.2 Expire old signals

```sql
UPDATE signals_1MO SET status='EXPIRED'
 WHERE signal_date < [last_closed_bar - 3 tháng]
   AND status = 'ACTIVE'
```

VD: chạy tháng 3/2026, `last_closed_bar = 2026-02-01`, cutoff = `2025-11-01`.
Signal trước tháng 11/2025 → `EXPIRED`.

#### 3.3 Load symbols theo mode

- **normal**: `WHERE is_active=1 AND (status='PENDING' OR (status='FAILED' AND retry_count < 3))`
- **resume**: `WHERE is_active=1 AND status='PENDING'` — chỉ tiếp tục các mã chưa quét
- **retry-failed**: `WHERE is_active=1 AND status='FAILED'` — force retry, không giới hạn retry_count

Log: `batch start | tf=1MO mode=normal dry_run=False symbols=3987 last_closed=2026-02-01`

---

### Bước 4: Vòng lặp chính — quét từng symbol

Lặp qua từng symbol trong danh sách. Mỗi symbol đi qua pipeline gồm 7 sub-step.

#### 4.0 Batch time guard

```python
if time.time() - batch_start > MAX_BATCH_TIME_SEC:   # 7200s = 2 giờ
    log.warning("MAX_BATCH_TIME reached — stopping gracefully")
    break
```

Nếu batch chạy quá 2 giờ → dừng, không crash. Lần sau chạy `--resume` để tiếp tục.

---

#### 4.1 Fetch OHLCV từ Yahoo

**File**: [yahoo.py](file:///d:/Phong/03_Finance/finance-scanner/data_provider/yahoo.py)

```python
df_fresh = get_ohlcv(symbol, "1MO")
```

Chi tiết bên trong `get_ohlcv()`:

**a) Gọi yfinance:**
```python
ticker = yf.Ticker("7203.T")
df = ticker.history(period="10y", interval="1mo", auto_adjust=True)
```
- `period="10y"` — lấy 10 năm lịch sử
- `interval="1mo"` — nến tháng
- `auto_adjust=True` — Yahoo tự adjust chia cổ tức

**b) Retry với exponential backoff:**
- Base wait: 2s
- Formula: `min(2 * 2^(attempt-1), remaining_time)`
- Tổng thời gian retry tối đa: `MAX_RETRY_TIME_SEC = 60s` per symbol
- 3 lần nhận "no data" → raise `NoDataError`

**c) Normalize DataFrame (`_normalize_df()`):**
- Flatten MultiIndex columns (yfinance v0.2+ trả MultiIndex)
- Lowercase columns: `Open` → `open`
- Guard: phải có đủ 5 cột `open, high, low, close, volume`
- Timezone: tz-naive → assume UTC → convert `Asia/Tokyo`
- Drop duplicate index, drop NaN, sort ascending

**d) Drop bar chưa đóng:**
```python
expected_last = get_last_closed_bar("1MO")   # 2026-02-01
cutoff = Timestamp("2026-02-01", tz=TZ_MARKET) + 1 day - 1 second
df = df[df.index <= cutoff]
```
Yahoo luôn include bar đang giao dịch (tháng hiện tại chưa đóng) → cần filter bỏ.

**e) Completeness check:**
```python
actual_last = df.index[-1].normalize().date()
if actual_last != expected_last:   # VD: 2026-01-01 != 2026-02-01
    raise DataIncompleteError("last bar mismatch")
```

**f) Freshness check:**
- `volume[-1] > 0` — nếu 0 → Yahoo chưa update data
- `OHLC[-1] != OHLC[-2]` — nếu identical → data stale

**g) `is_no_data_error()` — Normalize "no data" từ Yahoo:**

Yahoo có nhiều cách báo "không có data":
- Exception message chứa "No data found" / "no data" / "no timezone"
- Trả `None`
- Trả DataFrame rỗng
- Trả DataFrame thiếu columns

Hàm này catch tất cả case → return `True`.

---

#### 4.2 Soft delisting check

```python
soft_cutoff = last_closed - 3 months
if df_fresh.index[-1].date() < soft_cutoff:
    _set_inactive(timeframe, symbol, reason)   # is_active = 0
```

Nếu bar cuối của symbol quá cũ (>3 tháng trước `last_closed_bar`):
- Symbol đã bị suspended/delisted nhưng Yahoo vẫn giữ data cũ
- Set `is_active = 0` → vĩnh viễn bỏ qua trong mọi run sau
- Tiết kiệm API quota

> [!IMPORTANT]
> Khác với **hard delisting** (NoDataError retry ≥ 3 lần), soft delisting là khi Yahoo vẫn trả data nhưng data đã cũ — fetch không lỗi, chỉ vô nghĩa.

---

#### 4.3 Cache merge

**File**: [cache.py](file:///d:/Phong/03_Finance/finance-scanner/data_provider/cache.py)

```python
write_cache(symbol, "1MO", df_fresh)
df = read_cache(symbol, "1MO")
```

**`write_cache()` chi tiết:**

1. Convert df về internal format: date column (tz-naive date object)
2. Normalize dates: `1MO` → `YYYY-MM-01`
3. Đọc cache file hiện có (nếu tồn tại)
4. **Merge logic:**
   - `CACHE_MERGE_WINDOW["1MO"] = 3` bar gần nhất
   - Bar **trong** window → cho phép re-sync (ghi đè bằng data mới)
   - Bar **cũ hơn** window → **immutable** (giữ nguyên từ cache)
   - Lý do: Yahoo đôi khi glitch nến tháng 2-3 tháng, window=3 đủ robust
5. Normalize + dedup + sort
6. **Gap check** (`_check_gaps()`):
   - `1MO`: tạo `date_range(freq="MS")` → so sánh → phát hiện tháng thiếu
   - Nếu gap → raise `DataIncompleteError` 
7. **Atomic write**: ghi ra `.tmp` → `os.replace()` (POSIX atomic)
   - Nếu crash giữa lúc ghi → file cũ vẫn nguyên vẹn

**`read_cache()` trả về:**
- DataFrame với DatetimeIndex tz-aware (`Asia/Tokyo`)
- Sorted ascending

File path pattern: `cache/7203.T_1MO.parquet`

> [!WARNING]
> Nếu cache gap detected → scanner fallback dùng `df_fresh` (data mới từ Yahoo, không merge). Log warning, không crash.

---

#### 4.4 Pre-filter

**File**: [pre_filter.py](file:///d:/Phong/03_Finance/finance-scanner/core/pre_filter.py)

```python
if not passes_filter(df, symbol):
    _mark_scanned(timeframe, symbol)
    continue   # skip → symbol tiếp theo
```

`passes_filter()` kiểm tra 5 điều kiện, **bất kỳ cái nào False → loại:**

| Step | Kiểm tra | Threshold |
|------|----------|-----------|
| 1 | `dropna(close, volume)` | NaN guard |
| 2 | `len(df) < 12` | Không đủ data |
| 3 | `close.tail(12).mean() < 100` | Penny stock (< 100 JPY) |
| 4 | `(close×volume).tail(12).median() < 20M` | Thin liquidity |
| 5 | `volume.tail(6).eq(0).all()` | Inactive 6 tháng |

Thresholds từ [config.py](file:///d:/Phong/03_Finance/finance-scanner/core/config.py):
```python
MIN_PRICE_JPY     = 100
MIN_TURNOVER_JPY  = 20_000_000
MAX_INACTIVE_BARS = 6
```

> [!NOTE]
> Step 4 dùng **median** thay vì mean — chống outlier (1 tháng volume spike cao bất thường).

Nếu symbol bị filter → vẫn mark `SCANNED` (đã xử lý xong, không cần retry).

---

#### 4.5 Plugin analysis (IMFVG)

**File**: [plugin_manager.py](file:///d:/Phong/03_Finance/finance-scanner/core/plugin_manager.py)

```python
results = run_all(df, symbol, "1MO")
```

**Plugin loading** (1 lần duy nhất lúc import module):
1. Glob `indicators/*.py`, loại trừ `base.py` và `__init__.py`
2. Sort alphabetical → deterministic load order cross-platform
3. Mỗi file phải có hàm `analyze()`
4. Hiện tại chỉ có 1 plugin: `fvg.py`

**`run_all()` per symbol:**
- Lặp qua mỗi plugin đã load
- Gọi `plugin.analyze(df=df, symbol=symbol, timeframe="1MO")`
- Mỗi plugin wrap trong `try/except` — 1 plugin lỗi **không** crash toàn bộ symbol
- Chỉ trả về result có `signal != None`

---

**IMFVG Indicator logic** — [fvg.py](file:///d:/Phong/03_Finance/finance-scanner/indicators/fvg.py)

```python
result = analyze(df, "7203.T", "1MO")
```

**Guard:**
- `len(df) < 4` → return `signal=None` (cần ít nhất 4 bar)
- 4 bar cuối có NaN → return `signal=None`

**Index mapping:**
```
df.iloc[-4] = b3 (3 bars ago)
df.iloc[-3] = b2 (2 bars ago)
df.iloc[-2] = b1 (previous bar)
df.iloc[-1] = b0 (current bar)
```

**Bullish IMFVG** — có 3 điều kiện:

```
1. b3["low"] > b1["high"]        # gap tồn tại giữa b3 và b1
2. b2["close"] < b3["low"]       # bar giữa (b2) phá xuống dưới gap
3. b0["close"] > b3["low"]       # bar hiện tại close vào trong gap (mitigate)

gap_top    = b3["low"]
gap_bottom = b1["high"]
```

Ý nghĩa: Một FVG xuất hiện (b3-b1 tạo gap) → bị phá (b2 close dưới gap) → rồi price quay lại lấp gap ngay (b0 close trên b3.low). Đây là "Instantaneous Mitigation" — gap vừa tạo vừa bị lấp.

**Bearish IMFVG** — ngược lại:

```
1. b1["low"] > b3["high"]        # gap tồn tại (phía trên)
2. b2["close"] > b3["high"]      # bar giữa phá lên trên gap
3. b0["close"] < b3["high"]      # bar hiện tại close xuống trong gap

gap_top    = b1["low"]
gap_bottom = b3["high"]
```

**Ưu tiên:** Nếu cả bull lẫn bear đều True → return `BEARISH` (Pine Script: bear check sau bull, ghi đè).

**Return:**
```python
IndicatorResult {
    "indicator": "IMFVG",
    "version":   "1.1",
    "signal":    "BULLISH" | "BEARISH" | None,
    "meta": {
        "gap_top":     float,
        "gap_bottom":  float,
        "close_price": float,
    }
}
```

**Debug mode:** Set `IMFVG_DEBUG=1` → inject raw bar OHLC + dates vào `meta["debug"]`.

---

#### 4.6 Write signals vào DB

**File**: [signal_writer.py](file:///d:/Phong/03_Finance/finance-scanner/core/signal_writer.py)

```python
for result in results:
    inserted = write_signal(symbol, result, "1MO")
```

`write_signal()` chi tiết:

1. Validate result format: phải có `indicator`, `signal`, `version`
2. Skip nếu `signal is None`
3. `signal_date = get_last_closed_bar("1MO")` — **không** dùng `date.today()`
   - VD: chạy ngày 4/3/2026 → `signal_date = 2026-02-01`
   - Lý do: idempotency — chạy lại cùng tháng → cùng signal_date → UNIQUE constraint chặn duplicate
4. `INSERT OR IGNORE INTO signals_1MO (...) VALUES (...)`
   - UNIQUE(symbol, indicator, signal_date) → nếu đã tồn tại → ignore, không crash
5. Check `changes()`:
   - `1` → inserted → log `INFO "signal inserted: 7203.T IMFVG BULLISH 2026-02-01"`
   - `0` → duplicate → log `DEBUG "duplicate skipped: 7203.T IMFVG 2026-02-01"`

---

#### 4.7 Mark scanned + error handling

**Thành công:**
```python
_mark_scanned(timeframe, symbol)
# UPDATE scan_state_1MO SET status='SCANNED', last_scanned_at=now(UTC)
```

**Lỗi — 3 tầng xử lý:**

| Exception | Hành xử |
|-----------|---------|
| `NoDataError` | Tăng retry_count. Nếu ≥ 3 → `is_active=0` (hard delisted). Log `ERROR`. |
| `DataProviderError` | Mark `FAILED`, tăng retry_count. Log `WARNING`. |
| Unexpected `Exception` | Mark `FAILED`, tăng retry_count, log stacktrace. Log `ERROR`. |

Tất cả lỗi đều `continue` → **không crash batch**, tiếp tục symbol kế tiếp.

---

### Bước 5: Sau vòng lặp — Batch log + Notify

#### 5.1 Log batch run

**File**: [batch_log.py](file:///d:/Phong/03_Finance/finance-scanner/core/batch_log.py)

```python
run_id = log_batch_run("1MO", stats)
```

- INSERT vào `batch_runs_1MO` với: run_date, total_symbols, scanned, failed, signals_found, duration_sec
- Return `run_id` (autoincrement PK)

```python
export_json("1MO", run_id)
```

- Export batch run ra JSON string (cho AI agent đọc)
- Log ở level `DEBUG` — chỉ hiện khi cần

#### 5.2 Gửi Telegram notification

**File**: [notifier.py](file:///d:/Phong/03_Finance/finance-scanner/core/notifier.py)

```python
notify("1MO")
```

**Luồng bên trong:**

1. **Fail fast**: nếu `TELEGRAM_TOKEN` hoặc `CHAT_ID` chưa set → return 0 ngay
2. **Query unnotified signals**:
   ```sql
   SELECT * FROM signals_1MO
    WHERE notified_at IS NULL AND status = 'ACTIVE'
    ORDER BY signal_type, symbol
   ```
3. **Group by signal_type**: `BULLISH` gom 1 nhóm, `BEARISH` 1 nhóm
4. **Chunk 50 mã/message**: Telegram limit 4096 chars, mỗi mã ~30 chars → safe 50 mã
5. **Format message**:
   ```
   [1MO | BULLISH IMFVG — 2026-02-01]  (1/2)
   Tìm thấy 87 tín hiệu:

   7203.T   |  BULLISH  |  3,250 JPY
   6758.T   |  BULLISH  |  12,480 JPY
   ```
6. **Send qua Telegram Bot API**: 
   - POST `https://api.telegram.org/bot{TOKEN}/sendMessage`
   - Retry 1 lần nếu network error, wait 3s giữa retry
   - Rate limit: sleep 1s giữa các chunk
7. **Mark notified**: `UPDATE notified_at = now(UTC)` cho các signal đã gửi thành công
   - Chỉ mark **sau khi** toàn bộ chunk của 1 group gửi xong
   - Nếu chunk giữa fail → dừng group, không mark → lần sau retry tự động

> [!WARNING]
> **Edge case crash**: process crash SAU khi gửi Telegram NHƯNG TRƯỚC khi update `notified_at` → lần sau gửi lại → duplicate message. **v1 accept** vì xác suất thấp, impact nhỏ.

---

## Sơ đồ luồng dữ liệu tổng thể

```
┌─────────────────────────────────────────────────────────────────────┐
│                           run.sh (cronjob)                          │
│  Lock → Python → scanner.py --timeframe 1MO                        │
└────────────────────────────┬────────────────────────────────────────┘
                             │
                             ▼
┌─────────────────────────────────────────────────────────────────────┐
│                        scanner.py → run_scan()                      │
│                                                                     │
│  1. init_db("1MO")         → CREATE TABLE IF NOT EXISTS ...         │
│  2. seed_symbols(csv)      → INSERT OR IGNORE ~4000 mã              │
│  3. _reset_batch()         → SCANNED → PENDING (normal mode)        │
│  4. expire_old_signals()   → ACTIVE → EXPIRED (> 3 tháng)           │
│  5. _load_symbols()        → SELECT WHERE PENDING/FAILED            │
└────────────────────────────┬────────────────────────────────────────┘
                             │
              ┌──────────────┴──────────────┐
              │    for symbol in symbols:    │
              │    (batch time guard)        │
              └──────────────┬──────────────┘
                             │
              ┌──────────────▼──────────────┐
              │     yahoo.get_ohlcv()       │
              │  yfinance → normalize →     │
              │  drop future bar →          │
              │  completeness check →       │
              │  freshness check            │
              │                             │
              │  Retry: 2^n backoff,        │
              │  max 60s per symbol         │
              └──────────────┬──────────────┘
                             │
              ┌──────────────▼──────────────┐
              │    Soft delisting check      │
              │  last_bar < 3M ago?          │
              │  → is_active=0, skip         │
              └──────────────┬──────────────┘
                             │
              ┌──────────────▼──────────────┐
              │    cache.write_cache()       │
              │  merge window (3 bar)        │
              │  gap check (calendar-aware)  │
              │  atomic write (.tmp→replace) │
              │                              │
              │    cache.read_cache()         │
              │  → df (DatetimeIndex, JST)   │
              └──────────────┬──────────────┘
                             │
              ┌──────────────▼──────────────┐
              │    pre_filter.passes_filter() │
              │  1. dropna                    │
              │  2. len >= 12                 │
              │  3. avg price >= 100 JPY      │
              │  4. median turnover >= 20M    │
              │  5. not inactive (6 bar v=0)  │
              │                               │
              │  False → mark SCANNED, skip   │
              └──────────────┬──────────────┘
                             │
              ┌──────────────▼──────────────┐
              │  plugin_manager.run_all()     │
              │  → fvg.analyze(df, sym, tf)   │
              │                               │
              │  IMFVG logic (4 bar cuối):    │
              │  b3, b2, b1, b0               │
              │  check bull → check bear      │
              │  → IndicatorResult            │
              └──────────────┬──────────────┘
                             │
              ┌──────────────▼──────────────┐
              │  signal_writer.write_signal() │
              │  signal_date = last_closed    │
              │  INSERT OR IGNORE             │
              │  log: inserted / dup skipped  │
              └──────────────┬──────────────┘
                             │
              ┌──────────────▼──────────────┐
              │  _mark_scanned()             │
              │  status='SCANNED'            │
              │  last_scanned_at=now(UTC)     │
              └──────────────┬──────────────┘
                             │
              ─────── loop next symbol ──────
                             │
                             ▼
┌─────────────────────────────────────────────────────────────────────┐
│                     Sau vòng lặp (post-batch)                       │
│                                                                     │
│  1. batch_log.log_batch_run() → INSERT batch_runs_1MO               │
│  2. batch_log.export_json()   → JSON string (DEBUG log)             │
│  3. notifier.notify()         → query unnotified → chunk →          │
│                                  Telegram API → mark notified_at    │
└─────────────────────────────────────────────────────────────────────┘
```

---

## Timezone rules

| Context | Timezone | Lý do |
|---------|----------|-------|
| Bar boundary (`get_last_closed_bar`) | `Asia/Tokyo` (JST) | Sàn Tokyo |
| OHLCV data (DataFrame index) | `Asia/Tokyo` | Consistent với bar boundary |
| DB datetime (`last_scanned_at`, `created_at`, `notified_at`) | `UTC` | Sort/query nhất quán |
| Log timestamp | `JST` | Dễ đọc cho người dùng |
| Cache parquet (date column) | tz-naive date object | Internal format, convert khi read |

---

## Error handling flow

```
Exception
  └── DataProviderError (base)
        ├── NoDataError
        │     - Yahoo không có data
        │     - retry_count tăng mỗi lần
        │     - retry_count >= 3 → is_active = 0 (hard delisted)
        │
        └── DataIncompleteError
              - Data chưa đủ (missing bar, volume=0, stale)
              - Mark FAILED, retry next batch
              - Cache gap → fallback dùng fresh data
```

---

## Naming convention tổng thể

| Loại | Convention | Ví dụ |
|------|-----------|-------|
| Timeframe token | uppercase 3-char | `1MO`, `1WK`, `1D` |
| DB table | suffix `_{timeframe}` | `scan_state_1MO`, `signals_1WK` |
| Cache file | `{symbol}_{timeframe}.parquet` | `7203.T_1MO.parquet` |
| Log file | `batch_{timeframe}_{YYYYMM}.log` | `batch_1MO_202603.log` |
| Python variable | full name, no abbreviation | `timeframe` (not `tf`), `signal_date` (not `date`) |

---

## Các CLI mode

| Command | Behavior |
|---------|----------|
| `scanner.py --timeframe 1MO` | Normal: reset SCANNED→PENDING, quét tất cả |
| `scanner.py --timeframe 1MO --resume` | Chỉ quét PENDING (tiếp tục batch dang dở) |
| `scanner.py --timeframe 1MO --retry-failed` | Force retry tất cả FAILED |
| `scanner.py --timeframe 1MO --dry-run` | Chạy full pipeline, không ghi DB, không gửi Telegram |
