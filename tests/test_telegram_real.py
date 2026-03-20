import logging
import sys

from core.notifier import send_telegram, format_message

logging.basicConfig(level=logging.DEBUG, stream=sys.stdout)

mock_signals = [
    {"symbol": "7203.T", "close_price": 3250},
    {"symbol": "6758.T", "close_price": 12480},
    {"symbol": "9984.T", "close_price": 8500}
]

text = format_message(
    signals_chunk=mock_signals,
    signal_type="BULLISH",
    signal_date="2026-02-01",
    timeframe="1MO",
    part=1,
    total_parts=1,
    total_signals=len(mock_signals)
)

print("\n--- Message Text ---")
print(text)
print("--------------------\n")

print("Sending to Telegram...")
success = send_telegram(text)
print(f"Success: {success}")
