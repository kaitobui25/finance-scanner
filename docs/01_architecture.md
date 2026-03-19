# Kiến trúc Hệ thống Quét Tín hiệu Chứng khoán Nhật (v1 — Locked)

## 0. Naming Convention (đọc trước — áp dụng toàn hệ thống)

Hệ thống v1 chỉ scan nến Tháng. v2 sẽ thêm Weekly, Daily.
Nếu không đặt tên nhất quán ngay từ đầu, refactor sau sẽ rất tốn công.

### 0.1 Timeframe token

Dùng token chuẩn xuyên suốt — tên file, tên bảng, tên biến, tên hàm,
CLI argument, Telegram message.

```
1MO   = Monthly   (nến tháng)   -- v1
1WK   = Weekly    (nến tuần)    -- v2
1D    = Daily     (nến ngày)    -- v2
```

Nguồn gốc: Yahoo Finance dùng chính các token này trong
`yf.download(interval=...)`. Dùng lại để tránh mapping thêm một lớp.

---

### 0.2 Timezone — source of truth

Định nghĩa tập trung trong `core/config.py`, không scatter khắp codebase:

```python
TZ_MARKET = "Asia/Tokyo"   # OHLCV data, bar boundary, get_last_closed_bar()
TZ_DB     = "UTC"          # tất cả datetime lưu DB
```

Quy tắc bắt buộc:
- Mọi datetime **ghi vào DB** → convert về UTC trước khi INSERT
- Mọi **tính toán bar boundary** → dùng TZ_MARKET
- Lý do: DB UTC để query/sort nhất quán. Bar boundary JST vì sàn Tokyo.
- Nếu không enforce ngay: Weekly/Daily về sau lệch bar do offset +9h.

---

### 0.3 get_last_closed_bar() — edge case

```python
# core/config.py
# Calendar-based — KHÔNG phụ thuộc data availability của Yahoo.

def get_last_closed_bar(timeframe: str) -> date:
    now_jst = datetime.now(pytz.timezone(TZ_MARKET))

    if timeframe == "1MO":
        first_of_this_month = now_jst.date().replace(day=1)
        return first_of_this_month - relativedelta(months=1)
        # chạy 2026-03-01 00:05 JST -> 2026-02-01  ✓
        # chạy 2026-03-15 JST       -> 2026-02-01  ✓ (vẫn đúng)

    if timeframe == "1WK":
        today = now_jst.date()
        monday_this_week = today - timedelta(days=today.weekday())
        return monday_this_week - timedelta(weeks=1)

    if timeframe == "1D":
        return now_jst.date() - timedelta(days=1)
        # v1: không check lịch giao dịch — chấp nhận được
        # v2 WARNING: Nhật có Golden Week và nhiều holiday → bar có thể lệch
        # Khi implement 1D/1WK: thêm japanese_holiday_calendar hoặc
        # dùng fallback: shift backward đến bar có volume > 0

    raise ValueError(f"Timeframe không hỗ trợ: {timeframe}")
```

Nếu Yahoo chưa có data cho bar vừa đóng → đó là vấn đề của `yahoo.py`
(retry + circuit breaker), không phải của hàm này. Tách trách nhiệm rõ.

---

### 0.4 Naming áp dụng theo lớp

**File cache:**
```
cache/
  7203.T_1MO.parquet
  7203.T_1WK.parquet
  7203.T_1D.parquet
```

**SQLite — tên bảng (suffix _{timeframe}):**
```
scan_state_1MO       scan_state_1WK       scan_state_1D
signals_1MO          signals_1WK          signals_1D
batch_runs_1MO       batch_runs_1WK       batch_runs_1D
```

Lý do tách bảng thay vì dùng column `timeframe`:
- Query nhanh hơn, không cần filter thêm
- Schema từng timeframe có thể khác nhau về sau
- Xóa/archive toàn bộ 1 timeframe không ảnh hưởng timeframe khác

**Python — tên hàm:**
```python
# core/config.py
get_last_closed_bar(timeframe: str) -> date
  # "1MO" -> YYYY-MM-01 của tháng vừa đóng
  # "1WK" -> thứ Hai của tuần vừa đóng
  # "1D"  -> ngày giao dịch gần nhất đã đóng

# data_provider/base.py
get_ohlcv(symbol: str, timeframe: str) -> pd.DataFrame

# data_provider/cache.py
read_cache(symbol: str, timeframe: str) -> pd.DataFrame | None
write_cache(symbol: str, timeframe: str, df: pd.DataFrame) -> None

# indicators/base.py — contract plugin
analyze(df: pd.DataFrame, symbol: str, timeframe: str = "1MO") -> IndicatorResult

# scanner.py — CLI
scanner.py --timeframe 1MO --resume
scanner.py --timeframe 1WK --retry-failed
```

**Python — tên biến:**
```
timeframe          không dùng: tf, period, interval, frame
signal_date        không dùng: date, sig_date, sdate
gap_top            không dùng: top, upper, high_gap
gap_bottom         không dùng: bottom, lower, low_gap
close_price        không dùng: close, price, c
last_closed_bar    không dùng: last_bar, prev_bar, closed
```

**core/config.py — pre_filter constants:**
```python
# Thresholds tách ra config vì chắc chắn sẽ tune
MIN_PRICE_JPY     = 100          # giá đóng cửa TB 12 bar
MIN_TURNOVER_JPY  = 20_000_000    # turnover = close * volume (Yahoo không có sẵn, tự tính)
MAX_INACTIVE_BARS = 6            # inactive = volume == 0 cả N bar gần nhất

# yahoo.py retry
MAX_RETRY_TIME_SEC = 60          # tổng thời gian chờ tối đa per symbol

# cache merge window — số bar gần nhất được phép re-sync
# Yahoo glitch 1MO có thể kéo dài 2-3 tháng, window=3 đủ robust
CACHE_MERGE_WINDOW = {
    "1MO": 3,
    "1WK": 4,
    "1D":  5,
}

# scanner batch guard — tránh worst case 4000 × 60s = 66 giờ
MAX_BATCH_TIME_SEC = 7_200       # 2 giờ, sau đó graceful stop + ghi state
                                 # lần sau --resume chạy tiếp từ chỗ dừng
```

Không hardcode trong `core/pre_filter.py`. Khi tune threshold chỉ sửa
`core/config.py`, không đụng logic.

**Log file:**
```
logs/batch_1MO_202602.log
logs/batch_1WK_202610.log
```

**Log format — chuẩn xuyên suốt:**
```
[2026-03-01 00:01:02 JST] INFO  scanner   7203.T fetched ok (0.42s)
[2026-03-01 00:01:05 JST] WARN  yahoo     6758.T retry=2/3 (12.10s)
[2026-03-01 00:01:10 JST] ERROR yahoo     8306.T failed: HTTPError 429 (61.00s)
[2026-03-01 00:01:11 JST] ERROR plugin    fvg.py / 8306.T: IndexError line 42
[2026-03-01 00:01:15 JST] INFO  signal    7203.T BULLISH gap=310-340 inserted
[2026-03-01 00:01:15 JST] DEBUG signal    6758.T BULLISH duplicate skipped
```

Pattern: `[timestamp JST] LEVEL  module    message (latency)`
- latency per symbol giúp detect rate limit đang xảy ra (latency tăng dần)
- timestamp JST để đọc dễ (log là cho người đọc, DB mới cần UTC)
- module field giúp grep: `grep "ERROR plugin"`, `grep "duplicate skipped"`

**Plugin load order — deterministic:**
```python
# core/plugin_manager.py
plugins = sorted(glob("indicators/*.py"))   # alphabetical, loại trừ base.py và __init__.py
```

Không sort → thứ tự phụ thuộc filesystem → khác nhau giữa Linux/Mac
→ khó reproduce bug.

**Telegram message header:**
```
[1MO | BULLISH IMFVG — 2026-02-01]
[1WK | BEARISH IMFVG — 2026-03-17]
```

---

## 1. Cấu trúc Thư mục

```
finance-scanner/
│
├── .env                          # TELEGRAM_TOKEN, CHAT_ID — không commit
├── .env.example

├── requirements.txt
├── README.md
├── run.sh                        # entry point cho cronjob
│
├── data_provider/
│   ├── __init__.py
│   ├── base.py                   # Protocol: get_ohlcv(symbol, timeframe) -> DataFrame
│   ├── yahoo.py                  # fetch + retry 3x + exponential backoff
│   └── cache.py                  # read/write_cache(symbol, timeframe), merge window, tz=JST
│
├── indicators/
│   ├── __init__.py
│   ├── base.py                   # IndicatorResult TypedDict
│   │                             # contract: analyze(df, symbol, timeframe="1MO")
│   ├── fvg.py                    # IMFVG Bullish/Bearish — v1 active
│   ├── ema.py                    # placeholder v2
│   └── rsi.py                    # placeholder v2
│
├── core/                         # Các module xử lý logic chính
│   ├── __init__.py
│   ├── config.py                 # load .env, hằng số, get_last_closed_bar()
│   ├── pre_filter.py             # lọc rác trước khi phân tích kỹ thuật
│   ├── plugin_manager.py         # auto-load indicators/*, try/except per plugin
│   ├── signal_writer.py          # ghi signals_{timeframe}, expire cũ, UNIQUE
│   ├── notifier.py               # Telegram, group by signal_type, chunk 50
│   └── batch_log.py              # ghi batch_runs_{timeframe}, JSON export
│
├── scanner.py                    # CLI: --timeframe --resume --retry-failed --dry-run
│
├── data/
│   ├── state.db                  # SQLite — tất cả bảng, tên có suffix _1MO _1WK
│   └── symbols.csv               # ~4000 mã, normalized suffix .T
│
├── cache/
│   └── 7203.T_1MO.parquet        # pattern: {symbol}_{timeframe}.parquet
│
├── logs/
│   └── batch_1MO_202602.log      # pattern: batch_{timeframe}_{YYYYMM}.log
│
└── tests/
    ├── test_fvg.py
    └── test_pre_filter.py
```

---

## 2. Luồng Dữ liệu (Data Flow)

```
[Cronjob — mùng 4 hàng tháng]
            |
            v
    +========================+
    |      scanner.py        |  --timeframe 1MO
    |                        |  --resume | --retry-failed | --dry-run
    +========================+
         |              |
         v              v
  [symbols.csv]    [state.db]
  ~4000 mã .T      scan_state_1MO
                   -- reset batch mới trước khi load --
                   UPDATE scan_state_1MO
                   SET status='PENDING', retry_count=0
                   WHERE status='SCANNED'
                   -- sau đó load --
                   load WHERE status='PENDING'
                          OR (status='FAILED' AND retry_count < 3)
         |
         v
  batch_start_time = now()               -- MAX_BATCH_TIME guard
         |
         v
  +------------------------------------------+
  |  for symbol in symbols:                  |
  |                                          |
  |  -- batch time guard --                  |
  |  if now() - batch_start > MAX_BATCH_TIME:|  graceful stop
  |    log.warn("MAX_BATCH_TIME reached")    |  không crash, không corrupt
  |    break                                 |  lần sau --resume chạy tiếp
  |                                          |
  |  try:                                    |
  |    [data_provider]                       |
  |      yahoo.py                            |
  |        get_ohlcv(symbol, "1MO")          |  tz=Asia/Tokyo
  |        |-- lỗi 429/403                   |  backoff: BASE * 2^n, cap per-attempt
  |        |-- MAX_RETRY_TIME = 60s/symbol   |  tổng wait time, không để 1 symbol kẹt cả batch
  |        |-- "No data found" error         |  is_active=0 nếu fail >= 3 lần (hard delisting)
  |        |-- retry 3x -> circuit breaker   |
  |        |-- soft delisting check:         |  last_bar < nay - 3M -> mark is_active=0
  |        |-- completeness check:           |
  |             last_bar_date ==             |
  |             get_last_closed_bar("1MO")?  |
  |             không -> DataIncompleteError |  retry / skip
  |        |-- freshness check:              |
  |             volume > 0?                  |  volume=0 -> stale
  |             OHLC != bar trước?           |  identical OHLC -> Yahoo chưa update
  |             không -> DataIncompleteError |  tránh false signal hàng loạt
  |        v                                 |
  |      cache.py                            |
  |        read_cache(symbol, "1MO")         |  7203.T_1MO.parquet
  |        |-- merge window 3 bar            |  fill gap Yahoo glitch (CACHE_MERGE_WINDOW["1MO"])
  |        |-- immutable: bar cũ hơn 3M      |
  |        |-- normalize: YYYY-MM-01         |  drop_duplicates, sort, atomic write
  |        write_cache(symbol, "1MO", df)    |
  |        v                                 |
  |      df  (monthly OHLCV DataFrame)       |
  |                                          |
  |  [core/pre_filter.py]                    |
  |    df.dropna(subset=["close","volume"])  |  NaN guard trước — Yahoo đôi khi trả NaN
  |    len(df) < 12 sau dropna?              |  -> skip (không đủ data để filter)
  |    giá TB 12 bar < MIN_PRICE_JPY?        |  -> skip, continue
  |    (close*volume).mean() < MIN_TURNOVER? |  turnover = close * volume (tự tính)
  |    volume.tail(6).eq(0).all()?           |  inactive = volume=0 cả 6 bar gần nhất
  |        |                                 |
  |        v                                 |
  |  [core/plugin_manager.py]                |
  |    for plugin in indicators/:            |
  |      try:                                |
  |        result = plugin.analyze(          |
  |          df        = df,                 |
  |          symbol    = symbol,             |
  |          timeframe = "1MO"               |
  |        )                                 |
  |      except -> log(plugin, symbol, err)  |
  |        |                                 |
  |        v                                 |
  |    IndicatorResult {                     |
  |      indicator : "IMFVG"                 |
  |      version   : "1.1"                   |
  |      signal    : "BULLISH"|"BEARISH"|None|
  |      meta      : {                       |
  |        gap_top     : float               |
  |        gap_bottom  : float               |
  |        close_price : float               |
  |      }                                   |
  |    }                                     |
  |                                          |
  |  [core/signal_writer.py]                 |
  |    1. expire cũ:                         |
  |       UPDATE signals_1MO                 |
  |       SET status='EXPIRED'               |
  |       WHERE signal_date <                |
  |         get_last_closed_bar("1MO") - 3M  |
  |    2. insert mới:                        |
  |       signal_date =                      |
  |         get_last_closed_bar("1MO")       |  2026-03-01 -> 2026-02-01
  |       INSERT OR IGNORE INTO signals_1MO  |  UNIQUE(symbol,indicator,signal_date)
  |       status = 'ACTIVE'                  |
  |                                          |
  |  scan_state_1MO: status = 'SCANNED'      |
  |                                          |
  |  except Exception:                       |
  |    scan_state_1MO: status = 'FAILED'     |
  |    retry_count += 1                      |
  |    fail_reason  = str(exception)         |
  |    continue                              |
  +------------------------------------------+
            |
            v  (sau khi toàn bộ batch xong)
     +------+-------+
     |               |
     v               v
[core/notifier.py]   [core/batch_log.py]
                     |
  SELECT FROM        ghi batch_runs_1MO:
  signals_1MO          total_symbols
  WHERE                scanned
  notified_at          failed
  IS NULL              signals_found
                       duration_sec
  GROUP BY           export JSON -> AI agent
  signal_type

  chunk 50/msg
  [1MO | BULLISH IMFVG — 2026-02-01]
  (1/N), (2/N)...

  UPDATE notified_at
  sau khi gửi xong
```

---

## 3. Schema SQLite (state.db)

Tất cả bảng trong 1 file `state.db`, phân biệt bằng suffix `_{timeframe}`.
Thêm Weekly v2: tạo `scan_state_1WK`, `signals_1WK`, `batch_runs_1WK`.
Không đụng bảng `_1MO`.

---

### Bảng `scan_state_1MO`

| Column          | Type     | Ghi chú                         |
|-----------------|----------|---------------------------------|
| symbol          | TEXT PK  | 7203.T                          |
| status          | TEXT     | PENDING / SCANNED / FAILED      |
| last_scanned_at | DATETIME | UTC                             |
| fail_reason     | TEXT     | NULL nếu ok                     |
| retry_count     | INTEGER  | default 0, tăng mỗi lần FAILED  |
| is_active       | BOOLEAN  | default 1, set 0 khi delisted   |

**Logic self-healing:**
```sql
-- Đầu mỗi batch: reset tháng cũ về PENDING
UPDATE scan_state_1MO
   SET status = 'PENDING', retry_count = 0
 WHERE status = 'SCANNED';

-- Load symbols cần quét
SELECT symbol FROM scan_state_1MO
 WHERE is_active = 1
   AND (
         status = 'PENDING'
      OR (status = 'FAILED' AND retry_count < 3)
   );
```

```
--retry-failed: WHERE is_active=1 AND status='FAILED'   (force, không giới hạn)
--resume:       WHERE is_active=1 AND status='PENDING'  (chỉ tiếp tục)
```

`retry_count >= 3` → bỏ qua trong normal run.
`is_active = 0` → bỏ qua vĩnh viễn trong mọi run.

**Logic set is_active=0 (Vô hiệu hóa vĩnh viễn):**
```
Có 2 trường hợp để hạn chế lãng phí API quota:

1. Hard delisting (Lỗi "no data"):
   - Lỗi "No data found" lần 1-2 -> có thể là outage tạm thời, symbol rename.
   - Lỗi "No data found" lần 3+ -> retry_count >= 3 -> set is_active=0.
   - Lỗi mạng thông thường (timeout, 429) -> không set is_active=0, chỉ tăng retry_count.

2. Soft delisting (Dữ liệu bị bỏ mặc / ngưng cập nhật):
   - Fetch thành công nhưng: last_bar_date < get_last_closed_bar(timeframe) - 3 months.
   - Do cổ phiếu sáp nhập / đình chỉ, Yahoo vẫn chứa data cũ nhưng không update thêm.
   - Gặp case này -> set is_active=0 ngay để tiết kiệm quota.
```

**Normalize "no data" error — Yahoo không trả literal string nhất quán:**
```python
# yahoo.py
def is_no_data_error(e: Exception, df) -> bool:
    """
    Yahoo có thể báo "no data" theo nhiều cách:
    - exception message chứa "No data found"
    - trả về None
    - trả về DataFrame rỗng
    - trả về DataFrame thiếu columns cần thiết
    """
    if df is None or (hasattr(df, "empty") and df.empty):
        return True
    if "No data found" in str(e) or "no data" in str(e).lower():
        return True
    required = {"open", "high", "low", "close", "volume"}
    if not required.issubset(set(df.columns)):
        return True
    return False
```

Dùng lại `retry_count` đã có — không cần thêm column hay trạng thái mới.

**Index bắt buộc:**
```sql
CREATE INDEX idx_scan_state_1MO_status_retry
ON scan_state_1MO(status, retry_count);
```
4000 mã chưa đau, nhưng v2 thêm 1WK + 1D → 3x data. Index rẻ, làm luôn.

---

### Bảng `signals_1MO`

| Column            | Type        | Ghi chú                                          |
|-------------------|-------------|--------------------------------------------------|
| id                | INTEGER PK  | autoincrement                                    |
| symbol            | TEXT        | 7203.T                                           |
| indicator         | TEXT        | IMFVG                                            |
| signal_date       | DATE        | YYYY-MM-01, anchor bởi get_last_closed_bar("1MO")|
| signal_type       | TEXT        | BULLISH / BEARISH                                |
| status            | TEXT        | ACTIVE / EXPIRED                                 |
| gap_top           | REAL        | biên trên vùng FVG                               |
| gap_bottom        | REAL        | biên dưới vùng FVG                               |
| close_price       | REAL        | giá đóng cửa lúc signal                          |
| indicator_version | TEXT        | 1.1                                              |
| notified_at       | DATETIME    | NULL = chưa gửi Telegram                        |
| created_at        | DATETIME    | UTC                                              |

`UNIQUE(symbol, indicator, signal_date)` — chống duplicate, idempotency

**Index bắt buộc:**
```sql
-- notifier query: WHERE notified_at IS NULL AND status = 'ACTIVE'
-- composite index đúng với query pattern thực tế
CREATE INDEX idx_signals_1MO_active_notify
ON signals_1MO(status, notified_at);
```

**INSERT OR IGNORE — không silent fail:**
```python
cursor.execute("INSERT OR IGNORE INTO signals_1MO ...")
if cursor.rowcount == 0:
    log.debug(f"duplicate skipped: {symbol} {indicator} {signal_date}")
```
Phân biệt được "insert thành công" vs "bị ignore do duplicate".
Debug sau này không mò mẫm.

**Quy tắc ACTIVE → EXPIRED:**
```sql
UPDATE signals_1MO
   SET status = 'EXPIRED'
 WHERE signal_date < get_last_closed_bar("1MO") - 3 months
   AND status = 'ACTIVE'
```
Chạy đầu mỗi batch, trước khi insert signal mới.
Signal tháng 2/2026 → EXPIRED sau tháng 5/2026.

---

### Bảng `batch_runs_1MO`

| Column         | Type       | Ghi chú             |
|----------------|------------|---------------------|
| id             | INTEGER PK | autoincrement       |
| run_date       | DATE       | ngày chạy batch     |
| total_symbols  | INTEGER    |                     |
| scanned        | INTEGER    |                     |
| failed         | INTEGER    |                     |
| signals_found  | INTEGER    |                     |
| duration_sec   | REAL       | monitor performance |

---

## 4. Contract Plugin

```python
# indicators/base.py

import pandas as pd
from typing import Optional
from typing_extensions import TypedDict


class IndicatorResult(TypedDict):
    indicator : str            # "IMFVG"
    version   : str            # "1.1"
    signal    : Optional[str]  # "BULLISH" | "BEARISH" | None
    meta      : dict           # gap_top, gap_bottom, close_price


# Mọi plugin phải implement đúng signature này:
def analyze(
    df        : pd.DataFrame,
    symbol    : str,
    timeframe : str = "1MO",
) -> IndicatorResult:
    ...
```

---

## 5. Logic FVG (IMFVG — dịch từ Pine Script LuxAlgo)

### Index mapping

```
Pine Script         Pandas
-----------         ------
bar[0]  current  =  df.iloc[-1]
bar[1]  prev     =  df.iloc[-2]
bar[2]  2 ago    =  df.iloc[-3]
bar[3]  3 ago    =  df.iloc[-4]   <- cần ít nhất 4 bar
```

### Guard — dòng đầu tiên trong analyze()

```python
if len(df) < 4:
    return IndicatorResult(
        indicator="IMFVG", version="1.1", signal=None, meta={}
    )
```

### Bullish IMFVG

```
Điều kiện:
  1. df.iloc[-4]["low"]   > df.iloc[-2]["high"]   -- gap tồn tại
  2. df.iloc[-3]["close"] < df.iloc[-4]["low"]    -- bar giữa phá xuống dưới gap
  3. df.iloc[-1]["close"] > df.iloc[-4]["low"]    -- bar hiện tại đóng vào trong gap

gap_top    = df.iloc[-4]["low"]
gap_bottom = df.iloc[-2]["high"]
```

### Bearish IMFVG

```
Điều kiện:
  1. df.iloc[-2]["low"]   > df.iloc[-4]["high"]   -- gap tồn tại
  2. df.iloc[-3]["close"] > df.iloc[-4]["high"]   -- bar giữa phá lên trên gap
  3. df.iloc[-1]["close"] < df.iloc[-4]["high"]   -- bar hiện tại đóng vào trong gap

gap_top    = df.iloc[-2]["low"]
gap_bottom = df.iloc[-4]["high"]
```

---

## 6. Quy tắc cache.py

```
read_cache(symbol, timeframe):
  đọc cache/{symbol}_{timeframe}.parquet
  trả DataFrame | None nếu chưa có

write_cache(symbol, timeframe, df_new):
  1. đọc file hiện có -> max_date
  2. merge window = CACHE_MERGE_WINDOW[timeframe] bar gần nhất
     (1MO=3 tháng | 1WK=4 tuần | 1D=5 ngày) — config, không hardcode
  3. bar trong window mà thiếu -> fill vào
     bar trong window mà đã có -> giữ nguyên
  4. bar cũ hơn window -> immutable
  5. normalize index:
     1MO -> YYYY-MM-01
     1WK -> thứ Hai của tuần (ISO)
     1D  -> YYYY-MM-DD
  6. drop_duplicates(subset=["date"])
     sort_values("date")
  7. atomic write:
     path_tmp = f"{path}.tmp"
     df.to_parquet(path_tmp)
     os.replace(path_tmp, path)   # atomic trên Linux/Mac
```

**Tại sao atomic write là must-have:**
Nếu process crash giữa lúc `to_parquet()` đang ghi → file corrupt →
toàn bộ lịch sử mã đó mất, phải fetch lại từ đầu. `os.replace()` là
atomic trên POSIX: file cũ vẫn nguyên vẹn cho đến khi file mới ghi
xong hoàn toàn.

---

## 7. Telegram Format

```
[1MO | BULLISH IMFVG — 2026-02-01]  (1/2)
Tìm thấy 87 tín hiệu:

7203.T   |  BULLISH  |  3,250 JPY
6758.T   |  BULLISH  |  12,480 JPY
...      (50 mã)

[1MO | BULLISH IMFVG — 2026-02-01]  (2/2)
...      (37 mã còn lại)

[1MO | BEARISH IMFVG — 2026-02-01]  (1/1)
Tìm thấy 3 tín hiệu:

8306.T   |  BEARISH  |  1,180 JPY
```

```
Chunk rule:
  Telegram limit = 4096 chars / message
  Mỗi dòng mã ≈ 30 chars -> an toàn 50 mã/message
  <= 50 signals -> 1 message
  >  50 signals -> chunk 50, đánh số (1/N)...
```

Chỉ gửi WHERE notified_at IS NULL.
Update notified_at sau khi gửi thành công toàn bộ chunk.

---

## 8. Thứ tự Build

```
1.  core/config.py          get_last_closed_bar(timeframe), constants
2.  data_provider/base.py   Protocol get_ohlcv(symbol, timeframe)
3.  data_provider/cache.py  read/write_cache, merge window, normalize
4.  data_provider/yahoo.py  fetch, exponential backoff, circuit breaker
5.  core/pre_filter.py      3 filter rules
6.  indicators/base.py      IndicatorResult TypedDict, contract analyze()
7.  indicators/fvg.py       IMFVG logic — test mock DataFrame trước
8.  core/plugin_manager.py  auto-load indicators/*, try/except per plugin
9.  core/signal_writer.py   expire cũ -> insert mới, UNIQUE, bảng _1MO
10. scanner.py              CLI --timeframe, try/except per symbol
11. core/notifier.py        Telegram, chunk 50
12. core/batch_log.py       batch_runs_1MO, JSON export
```

Test sau mỗi bước với 1 mã thực (7203.T) trước khi sang bước tiếp.

---

## 9. Không làm ở v1

| Không làm | Lý do |
|---|---|
| Multiprocessing | Mâu thuẫn rate-limit strategy. 20 mã/giờ là cố ý |
| Signal hash SHA1 | UNIQUE 3 cols đã đủ, params là constant ở v1 |
| Pre-filter sau plugin | Không có use case, chỉ tăng cost |
| Context dict đầy đủ | `timeframe="1MO"` default là đủ cho v1 |
| ATR filter | filterWidth=0 là default LuxAlgo, chưa cần |
| Tách file state.db theo timeframe | 1 file + suffix tên bảng là đủ |
| SYSTEM_VERSION / hash config | `indicator_version` đã đủ trace signal. Hash config thêm complexity không cần thiết ở v1 |

**Ghi chú thiết kế cho v2 — Yahoo fallback:**
```
data_provider/
  base.py           Protocol — không đổi
  yahoo.py          v1, nguồn duy nhất
  stooq.py          fallback nếu Yahoo 429 liên tục (v2)
  alpha_vantage.py  fallback thứ hai (v2)
```
`base.py` Protocol đã chuẩn bị sẵn. Khi Yahoo block: implement
provider mới, scanner.py và plugin không cần sửa gì.

**Ghi chú 
- Code fvg nguyên mẫu tác giả:
reference/pine/imfvg_luxalgo_original.pine
- Yahoo luôn trả về nến hiện tại đang giao dịch (chưa đóng). Phải dùng time-cutoff (tz-aware) để filter bỏ các nến của tương lai trước khi check completeness.