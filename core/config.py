TZ_MARKET = "Asia/Tokyo"   # dùng cho tính toán nến
TZ_DB     = "UTC"          # dùng khi lưu DB

# --- Timeframe tokens ---
# Dùng chính token của Yahoo Finance để tránh mapping
TIMEFRAMES = {
    "1MO": "1mo",   # Monthly
    "1WK": "1wk",   # Weekly
    "1D":  "1d",    # Daily
}

# --- Pre-filter thresholds ---
# Các ngưỡng lọc sơ bộ để giảm số lượng mã cần tính toán
MIN_PRICE_JPY     = 100          # giá đóng cửa TB 12 tháng >= 100 JPY
MIN_TURNOVER_JPY  = 5_000_000    # turnover = close * volume, TB 12 tháng >= 5M JPY
MAX_INACTIVE_BARS = 6            # volume == 0 trong 6 tháng gần nhất → loại

# --- Yahoo Finance retry & batch ---
MAX_RETRY_TIME_SEC = 60          # tổng thời gian chờ tối đa cho 1 symbol
MAX_BATCH_TIME_SEC = 7_200       # 2 giờ: sau đó graceful stop + save state

# --- Cache merge window ---
# Số bar gần nhất được phép re-sync từ Yahoo
# Yahoo đôi khi glitch 1-2 tháng → window=3 đủ robust
CACHE_MERGE_WINDOW = {
    "1MO": 3,
    "1WK": 4,
    "1D":  5,
}

# --- Data gap protection ---
# Số ngày tối đa cho phép gap giữa các bar
# 1MO: gap > 32 ngày → chắc chắn có tháng bị mất
MAX_DATE_GAP = {
    "1MO": 32,
    "1WK": 8,
    "1D":  4,
}

