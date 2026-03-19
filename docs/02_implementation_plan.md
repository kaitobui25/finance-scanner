# Implementation Plan — Japan Stock Scanner v1
## IMFVG (Instantaneous Mitigation FVG) · Monthly · Telegram Alert

> **Quy tắc làm việc:** Test với 1 mã thực `7203.T` sau mỗi bước trước khi sang bước tiếp.
> Không bỏ qua bước test dù logic có vẻ đơn giản.

---

## 1. Implementation Order

### Phase 0 — Project Bootstrap

- [x] **0.1** Tạo cấu trúc thư mục
  ```
  japan-scanner/
  ├── data_provider/
  ├── indicators/
  ├── cache/
  ├── data/
  ├── logs/
  └── tests/
```
- [x] **0.2** Tạo `requirements.txt`
  ```
  yfinance
  pandas
  pyarrow        # parquet
  pytz
  python-dateutil
  python-dotenv
  requests
  ```
- [x] **0.3** Tạo `.env.example` và `.gitignore` (exclude `.env`, `cache/`, `data/`, `logs/`)
- [x] **0.4** Tạo `data/symbols.csv` — ~4000 mã Nhật, normalized suffix `.T`

---

### Phase 1 — core/config.py

- [x] **1.1** Load `.env` → `TELEGRAM_TOKEN`, `CHAT_ID`
- [x] **1.2** Khai báo timezone constants
  ```python
  TZ_MARKET = "Asia/Tokyo"
  TZ_DB     = "UTC"
  ```
- [x] **1.3** Khai báo pre_filter constants
  ```python
  MIN_PRICE_JPY     = 100
  MIN_TURNOVER_JPY  = 20_000_000
  MAX_INACTIVE_BARS = 6
  ```
- [x] **1.4** Khai báo operational constants
  ```python
  MAX_RETRY_TIME_SEC = 60
  MAX_BATCH_TIME_SEC = 7_200
  CACHE_MERGE_WINDOW = {"1MO": 3, "1WK": 4, "1D": 5}
  ```
- [x] **1.5** Implement `get_last_closed_bar(timeframe: str) -> date`
  - `"1MO"` → `first_of_this_month - 1 month` (calendar-based, không phụ thuộc Yahoo)
  - Raise `ValueError` cho timeframe không hỗ trợ
- [x] **1.6** Test: chạy 2026-03-15 JST → phải trả `2026-02-01` ✓

---

### Phase 2 — data_provider/base.py

- [x] **2.1** Định nghĩa `DataProviderProtocol` với method `get_ohlcv(symbol: str, timeframe: str) -> pd.DataFrame`
- [x] **2.2** Định nghĩa custom exceptions: `DataIncompleteError`, `NoDataError`

---

### Phase 3 — data_provider/cache.py

- [x] **3.1** Implement `read_cache(symbol, timeframe) -> pd.DataFrame | None`
  - Path pattern: `cache/{symbol}_{timeframe}.parquet`
  - Trả `None` nếu file chưa tồn tại
- [x] **3.2** Implement `write_cache(symbol, timeframe, df_new)`
  - Đọc cache hiện có → lấy `max_date`
  - Merge window = `CACHE_MERGE_WINDOW[timeframe]` bar gần nhất (re-syncable)
  - Bar cũ hơn window → **immutable**, không ghi đè
  - Normalize date index:
    - `1MO` → `YYYY-MM-01`
    - `1WK` → ISO Monday của tuần
    - `1D`  → `YYYY-MM-DD`
  - `drop_duplicates(subset=["date"])` + `sort_values("date")`
  - **Atomic write**: ghi ra `.tmp` → `os.replace()` (POSIX atomic)
- [x] **3.3** Implement **data gap protection** sau khi merge
  - Check continuity: `df["date"].diff()` — nếu có gap > 1 tháng (với `1MO`) → raise `DataIncompleteError("Data gap detected after merge")`
  - Lý do: Yahoo đôi khi silent-drop 1 tháng → merge vẫn pass → cache thiếu lịch sử mà không biết → FVG index mapping sai
  - Expected gap threshold theo timeframe:
    ```python
    MAX_DATE_GAP = {"1MO": 32, "1WK": 8, "1D": 4}  # days, dùng relativedelta để chính xác
    ```
- [x] **3.4** Test: write → crash simulation → verify file không corrupt
- [x] **3.5** Test gap protection: inject DataFrame thiếu 1 tháng giữa → phải raise `DataIncompleteError`

---

### Phase 4 — data_provider/yahoo.py

- [x] **4.1** Implement `get_ohlcv(symbol, timeframe) -> pd.DataFrame`
  - Dùng `yfinance`, `interval` map từ timeframe token (1MO → `"1mo"`)
  - Normalize columns về lowercase: `open, high, low, close, volume`
  - Normalize datetime index về `TZ_MARKET`
- [x] **4.2** Implement `is_no_data_error(e, df) -> bool`
  - Cover các case: `None`, `df.empty`, thiếu required columns, message "no data"
- [x] **4.3** Implement retry với exponential backoff
  - `BASE * 2^n`, cap per-attempt
  - Tổng thời gian chờ ≤ `MAX_RETRY_TIME_SEC` (60s/symbol)
  - Retry 3x → raise `NoDataError` nếu `is_no_data_error`, else raise thường
- [x] **4.4** Implement completeness check
  - `last_bar_date == get_last_closed_bar(timeframe)` → nếu không: raise `DataIncompleteError`
- [x] **4.5** Implement freshness check
  - `volume[-1] > 0` → nếu không: raise `DataIncompleteError`
  - `OHLC[-1] != OHLC[-2]` (ít nhất 1 giá trị khác) → nếu không: raise `DataIncompleteError`
- [ ] **4.6** Implement **soft delisting detection** trong `scanner.py` (gọi sau khi fetch thành công)
  - Nếu `last_bar_date < get_last_closed_bar(timeframe) - 3 months` → set `is_active=0`
  - Lý do: symbol vẫn tồn tại trên Yahoo nhưng không update nữa (suspended, delisted ngầm, merger) → fetch không lỗi nhưng data cũ → tiếp tục scan vô nghĩa, lãng phí quota
  - Log: `WARN scanner {symbol} soft-delisted: last_bar={last_bar_date}, setting is_active=0`
  - Khác với hard delisting (is_no_data_error): case này Yahoo vẫn trả data, chỉ là data cũ
- [x] **4.7** Test với `7203.T` thực → verify DataFrame shape, dates, columns

---

### Phase 5 — core/pre_filter.py

- [x] **5.1** Implement `passes_filter(df: pd.DataFrame) -> bool`
  - Step 1: `df.dropna(subset=["close", "volume"])` — NaN guard trước
  - Step 2: `len(df) < 12` → return `False` (không đủ data để tính TB)
  - Step 3: `df["close"].tail(12).mean() < MIN_PRICE_JPY` → return `False`
  - Step 4: `(df["close"] * df["volume"]).tail(12).mean() < MIN_TURNOVER_JPY` → return `False`
  - Step 5: `df["volume"].tail(MAX_INACTIVE_BARS).eq(0).all()` → return `False`
  - Else → return `True`
- [x] **5.2** Test với mock DataFrame: penny stock, zero-volume, thin turnover → phải bị lọc

---

### Phase 6 — indicators/base.py

- [x] **6.1** Định nghĩa `IndicatorResult` TypedDict
  ```python
  class IndicatorResult(TypedDict):
      indicator : str            # "IMFVG"
      version   : str            # "1.1"
      signal    : Optional[str]  # "BULLISH" | "BEARISH" | None
      meta      : dict           # gap_top, gap_bottom, close_price
  ```
- [x] **6.2** Định nghĩa contract `analyze(df, symbol, timeframe="1MO") -> IndicatorResult`
  - Document rõ index mapping: `df.iloc[-1]` = bar[0] current, `df.iloc[-4]` = bar[3]

---

### Phase 7 — indicators/fvg.py

> **Chú ý logic IMFVG (Instantaneous Mitigation):**
> FVG được tạo ra và bị mitigate ngay tại cùng bar quan sát — đây là điều kiện đặc biệt
> so với FVG thông thường. Bar hiện tại (`iloc[-1]`) đóng vào trong gap của 3 bar trước.

- [x] **7.1** Guard đầu hàm: `len(df) < 4` → return `IndicatorResult(signal=None, meta={})`
- [x] **7.2** Implement Bullish IMFVG
  ```
  Điều kiện (filterWidth=0, tức gap thực sự tồn tại):
    1. df.iloc[-4]["low"]   > df.iloc[-2]["high"]   # gap tồn tại
    2. df.iloc[-3]["close"] < df.iloc[-4]["low"]    # bar giữa phá xuống dưới
    3. df.iloc[-1]["close"] > df.iloc[-4]["low"]    # bar hiện tại close vào trong gap

  gap_top    = df.iloc[-4]["low"]
  gap_bottom = df.iloc[-2]["high"]
  ```
- [x] **7.3** Implement Bearish IMFVG
  ```
  Điều kiện:
    1. df.iloc[-2]["low"]   > df.iloc[-4]["high"]   # gap tồn tại
    2. df.iloc[-3]["close"] > df.iloc[-4]["high"]   # bar giữa phá lên trên
    3. df.iloc[-1]["close"] < df.iloc[-4]["high"]   # bar hiện tại close vào trong gap

  gap_top    = df.iloc[-2]["low"]
  gap_bottom = df.iloc[-4]["high"]
  ```
- [x] **7.4** Ưu tiên: nếu cả bull lẫn bear đều true trong cùng 1 bar → return `BEARISH`
  (theo Pine Script: `bear` được check sau `bull`, ghi đè `os`)
- [x] **7.5** Return `IndicatorResult` đầy đủ với `meta = {gap_top, gap_bottom, close_price}`
- [x] **7.6** Test với mock DataFrame constructed thủ công
  - Case bullish rõ ràng → phải detect ✓
  - Case bearish rõ ràng → phải detect ✓
  - Case không có gap → phải trả `None` ✓
  - Edge case: `len(df) == 3` → phải trả `None` ✓

---

### Phase 8 — core/plugin_manager.py

- [x] **8.1** Auto-load tất cả `*.py` trong `indicators/`, loại trừ `base.py` và `__init__.py`
- [x] **8.2** Sort alphabetical → deterministic load order (cross-platform)
  ```python
  plugins = sorted(glob("indicators/*.py"))
  ```
- [x] **8.3** Mỗi plugin `analyze()` wrap trong `try/except`
  - Exception → `log.error(f"plugin {plugin_name} / {symbol}: {err}")` rồi `continue`
  - Không để 1 plugin lỗi crash toàn bộ symbol
- [x] **8.4** Return `List[IndicatorResult]` (filter bỏ những result `signal=None`)
- [x] **8.5** Test: inject plugin lỗi → verify scanner vẫn tiếp tục, log có `ERROR plugin`

---

### Phase 9 — core/signal_writer.py + DB init

- [x] **9.1** Implement `init_db()` — tạo 3 bảng `_1MO` nếu chưa tồn tại
  - `scan_state_1MO`: `symbol PK, status, last_scanned_at, fail_reason, retry_count, is_active`
  - `signals_1MO`: schema đầy đủ + `UNIQUE(symbol, indicator, signal_date)`
  - `batch_runs_1MO`: tracking metadata
- [x] **9.2** Tạo indexes bắt buộc
  ```sql
  CREATE INDEX IF NOT EXISTS idx_scan_state_1MO_status_retry
    ON scan_state_1MO(status, retry_count);
  CREATE INDEX IF NOT EXISTS idx_signals_1MO_active_notify
    ON signals_1MO(status, notified_at);
  ```
- [x] **9.3** Implement `seed_symbols(symbols_csv_path)` — populate `scan_state_1MO` từ CSV
  - `INSERT OR IGNORE` để idempotent (chạy lại không mất state)
- [x] **9.4** Implement `expire_old_signals(timeframe)` — ACTIVE → EXPIRED
  ```sql
  UPDATE signals_1MO SET status='EXPIRED'
  WHERE signal_date < [last_closed_bar - 3 months]
    AND status = 'ACTIVE'
  ```
- [x] **9.5** Implement `write_signal(symbol, result, timeframe)`
  - `signal_date = get_last_closed_bar(timeframe)` (không dùng `date.today()`)
  - `INSERT OR IGNORE INTO signals_1MO ...`
  - Log `DEBUG "duplicate skipped"` nếu `rowcount == 0`
  - Log `INFO "inserted"` nếu `rowcount == 1`
- [x] **9.6** Test: insert → insert lại cùng `(symbol, indicator, signal_date)` → phải ignore, không raise

---

### Phase 10 — scanner.py

- [ ] **10.1** CLI setup với `argparse`
  ```
  --timeframe    default "1MO"
  --resume       chỉ quét PENDING (tiếp tục batch dang dở)
  --retry-failed force retry tất cả FAILED (không giới hạn retry_count)
  --dry-run      chạy full pipeline nhưng không ghi DB, không gửi Telegram
  ```
- [ ] **10.2** Implement batch reset logic (đầu mỗi normal run)
  ```sql
  UPDATE scan_state_1MO SET status='PENDING', retry_count=0
  WHERE status='SCANNED'
  ```
- [ ] **10.3** Implement symbol load theo mode (`--resume` / `--retry-failed` / normal)
- [ ] **10.4** Implement `batch_start_time` guard
  - Mỗi iteration: check `now() - batch_start > MAX_BATCH_TIME_SEC`
  - Nếu vượt: `log.warn("MAX_BATCH_TIME reached")`, `break` gracefully
- [ ] **10.5** Implement vòng lặp chính per-symbol
  ```
  try:
    symbol_start = time.time()           # ← latency tracking
    1. yahoo.get_ohlcv()
    2. handle is_no_data_error → set is_active=0 nếu retry_count >= 3
    3. soft delisting check → set is_active=0 nếu last_bar quá cũ
    4. cache.write_cache()
    5. df = merge cache + fresh data
    6. pre_filter.passes_filter() → skip nếu False
    7. plugin_manager.run_all(df, symbol, timeframe)
    8. signal_writer.write_signal() cho mỗi result có signal
    9. UPDATE scan_state: status='SCANNED', last_scanned_at=now(UTC)
    latency = time.time() - symbol_start
    log.info(f"{symbol} done ({latency:.2f}s)")   # ← latency per symbol
  except:
    UPDATE scan_state: status='FAILED', retry_count+=1, fail_reason=str(e)
    continue
  ```
  - Latency per symbol giúp phát hiện Yahoo throttle đang xảy ra (latency tăng dần)
  - Nếu thấy latency đột ngột tăng từ ~0.5s lên ~55s → đang bị rate limit, cần điều chỉnh backoff
- [ ] **10.6** Setup logging
  - Format chuẩn: `[{timestamp JST}] {LEVEL}  {module}    {message} ({latency}s)`
  - File: `logs/batch_1MO_{YYYYMM}.log`
  - Cả file lẫn stdout
- [ ] **10.7** Test end-to-end với `7203.T` duy nhất, `--dry-run` trước

---

### Phase 11 — core/notifier.py

- [ ] **11.1** Implement `get_unnotified_signals(timeframe) -> List[dict]`
  ```sql
  SELECT * FROM signals_1MO
  WHERE notified_at IS NULL AND status = 'ACTIVE'
  ORDER BY signal_type, symbol
  ```
- [ ] **11.2** Implement chunking: group by `signal_type`, chunk 50 mã/message
- [ ] **11.3** Implement `format_message(signals_chunk, signal_type, signal_date, part, total)`
  ```
  [1MO | BULLISH IMFVG — 2026-02-01]  (1/2)
  Tìm thấy 87 tín hiệu:

  7203.T   |  BULLISH  |  3,250 JPY
  6758.T   |  BULLISH  |  12,480 JPY
  ```
- [ ] **11.4** Implement `send_telegram(text)` với Telegram Bot API
  - POST `https://api.telegram.org/bot{TOKEN}/sendMessage`
  - Retry 1 lần nếu gặp lỗi network
- [ ] **11.5** Update `notified_at = now(UTC)` **chỉ sau khi toàn bộ chunk gửi thành công**
  - Nếu chunk giữa chừng fail → không update → lần sau retry tự động
  - **Edge case crash:** process crash sau khi gửi Telegram nhưng trước khi update DB → lần sau query `notified_at IS NULL` → gửi lại → **duplicate message**
  - **Quyết định v1: accept duplicate** (Option A)
    - Xác suất thấp (chỉ xảy ra khi crash đúng lúc)
    - Duplicate không gây hại nghiêm trọng (chỉ nhận 2 lần)
    - Option B (log `message_id` vào DB) tăng complexity không cần thiết ở v1
    - Ghi chú cho v2: nếu muốn fix, thêm column `telegram_message_id` vào `signals_1MO`
- [ ] **11.6** Test với mock data, verify chunk đúng 50/message, format đúng

---

### Phase 12 — core/batch_log.py

- [ ] **12.1** Implement `log_batch_run(timeframe, stats_dict)`
  - INSERT vào `batch_runs_1MO`: `total_symbols, scanned, failed, signals_found, duration_sec`
- [ ] **12.2** Implement `export_json(timeframe, run_id) -> str`
  - Export kết quả batch ra JSON cho AI agent đọc
- [ ] **12.3** Tích hợp vào `scanner.py`: gọi `log_batch_run()` cuối mỗi batch

---

### Phase 13 — run.sh + Cronjob

- [ ] **13.1** Viết `run.sh`
  ```bash
  #!/bin/bash
  cd /path/to/japan-scanner
  source .env
  python scanner.py --timeframe 1MO >> logs/cron.log 2>&1
  ```
- [ ] **13.2** Setup cronjob: mùng 4 hàng tháng, ~00:05 JST (sau khi Yahoo có data)
  ```
  5 0 4 * * /path/to/japan-scanner/run.sh
  ```
- [ ] **13.3** Test manual run toàn bộ pipeline với 5 mã thực
- [ ] **13.4** Test `--resume`: kill giữa chừng → chạy lại với `--resume` → tiếp tục đúng chỗ

---

### Phase 14 — Tests

- [x] **14.1** `tests/test_fvg.py`
  - Mock DataFrame với giá trị cụ thể → verify bullish detect
  - Mock DataFrame với giá trị cụ thể → verify bearish detect
  - Mock DataFrame `len < 4` → verify trả `None`
  - Mock DataFrame không có gap → verify trả `None`
  - Verify `gap_top` và `gap_bottom` đúng giá trị
- [ ] **14.2** `tests/test_pre_filter.py`
  - Penny stock (`close < 100 JPY`) → verify bị filter
  - Thin liquidity (turnover thấp) → verify bị filter
  - Inactive (6 bar volume=0) → verify bị filter
  - Mã hợp lệ → verify pass filter
  - Mã có NaN → verify xử lý đúng không crash

---

## 2. Dependency Graph

```
core/config.py
    └─> data_provider/base.py
            ├─> data_provider/cache.py
            └─> data_provider/yahoo.py
                        └─> core/pre_filter.py
                                └─> indicators/base.py
                                        └─> indicators/fvg.py
                                                └─> core/plugin_manager.py
                                                        └─> core/signal_writer.py (+ DB init)
                                                                └─> scanner.py
                                                                        ├─> core/notifier.py
                                                                        └─> core/batch_log.py
```

---

## 3. Critical Rules (nhắc lại để không quên)

| Rule | Lý do |
|------|-------|
| Tên biến: `timeframe`, không phải `tf`/`interval` | Consistency cho v2 |
| Tên biến: `signal_date`, không phải `date` | Tránh shadow builtin |
| Tên biến: `gap_top`/`gap_bottom`, không phải `top`/`bottom` | Clarity |
| Tên biến: `close_price`, không phải `close` | Tránh shadow DataFrame column |
| Mọi datetime ghi DB → UTC trước | Sort/query nhất quán |
| Mọi bar boundary → TZ_MARKET (JST) | Sàn Tokyo |
| `signal_date` = `get_last_closed_bar()`, không phải `date.today()` | Idempotency |
| Atomic write cho cache | Chống corrupt khi crash |
| `INSERT OR IGNORE` + log rowcount | Phân biệt insert vs duplicate |
| Plugin sort alphabetical | Deterministic, cross-platform |
| Không multiprocessing | Rate-limit strategy là cố ý |

---

## 4. Không làm ở v1

| Không làm | Lý do |
|-----------|-------|
| Multiprocessing | Mâu thuẫn rate-limit. 20 mã/giờ là cố ý |
| Signal hash SHA1 | `UNIQUE(symbol, indicator, signal_date)` đã đủ |
| ATR filter (`filterWidth > 0`) | Default LuxAlgo là 0, chưa cần |
| Tách `state.db` theo timeframe | 1 file + suffix tên bảng là đủ |
| `SYSTEM_VERSION` / config hash | `indicator_version="1.1"` đã đủ trace |
| Pre-filter sau plugin | Không có use case |
| Weekly / Daily scan | v2 — bảng đã chuẩn bị sẵn suffix `_1WK`, `_1D` |
| Fallback provider (Stooq, Alpha Vantage) | v2 — Protocol đã sẵn sàng |
