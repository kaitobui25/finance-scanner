import os
from datetime import date
from dateutil.relativedelta import relativedelta
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo
from dotenv import load_dotenv

load_dotenv()

# --- Telegram ---
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN", "")
CHAT_ID        = os.getenv("CHAT_ID", "")

# --- Timezone ---
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
MIN_PRICE_JPY     = 100          # giá đóng cửa TB 12 tháng >= 100 JPY
MIN_TURNOVER_JPY  = 20_000_000    # turnover = close * volume, TB 12 tháng >= 20M JPY
MAX_INACTIVE_BARS = 6            # volume == 0 trong 6 tháng gần nhất → loại

# --- Yahoo Finance retry & batch ---
MAX_RETRY_TIME_SEC = 60          # tổng thời gian chờ tối đa cho 1 symbol
MAX_BATCH_TIME_SEC = 7_200       # 2 giờ: sau đó graceful stop + save state

# --- Cache merge window ---
# Số bar gần nhất được phép re-sync từ Yahoo
CACHE_MERGE_WINDOW = {
    "1MO": 3,
    "1WK": 4,
    "1D":  5,
}

# --- Data gap protection ---
MAX_DATE_GAP = {
    "1MO": 32,
    "1WK": 8,
    "1D":  4,
}


def get_last_closed_bar(timeframe: str) -> date:
    """
    Trả về ngày bắt đầu của bar gần nhất đã đóng, tính theo TZ_MARKET (JST).
    Calendar-based — KHÔNG phụ thuộc data availability của Yahoo.

    1MO: chạy 2026-03-15 JST → 2026-02-01  (tháng 2 đã đóng)
    1WK: chạy thứ Tư 2026-03-18 → 2026-03-09 (thứ Hai của tuần trước)
    1D:  chạy 2026-03-15 JST → 2026-03-14

    Raises:
        ValueError: nếu timeframe không được hỗ trợ
    """
    now_jst = datetime.now(ZoneInfo(TZ_MARKET))

    if timeframe == "1MO":
        first_of_this_month = now_jst.date().replace(day=1)
        return first_of_this_month - relativedelta(months=1)

    if timeframe == "1WK":
        today = now_jst.date()
        monday_this_week = today - timedelta(days=today.weekday())
        return monday_this_week - timedelta(weeks=1)

    if timeframe == "1D":
        # v1: không check lịch giao dịch — chấp nhận được
        # v2 WARNING: Nhật có Golden Week → cần japanese_holiday_calendar
        return now_jst.date() - timedelta(days=1)

    raise ValueError(f"Timeframe không hỗ trợ: {timeframe!r}. Dùng: {list(TIMEFRAMES)}")

if __name__ == "__main__":
    print(get_last_closed_bar("1MO"))