import pytest
from unittest.mock import patch, MagicMock

from core.notifier import format_message, notify, CHUNK_SIZE

def test_format_message_single_part():
    """Test format_message khi chỉ có 1 phần (part=1, total_parts=1)."""
    signals = [
        {"symbol": "7203.T", "close_price": 3250},
        {"symbol": "6758.T", "close_price": 12480},
    ]
    
    text = format_message(
        signals_chunk=signals,
        signal_type="BULLISH",
        signal_date="2026-02-01",
        timeframe="1MO",
        part=1,
        total_parts=1,
        total_signals=2
    )

    assert "[1MO | BULLISH IMFVG — 2026-02-01]" in text
    assert "(1/1)" not in text  # Do total_parts = 1 nên không hiện (1/1)
    assert "Tìm thấy 2 tín hiệu:" in text
    assert "7203.T" in text
    assert "3,250 JPY" in text
    assert "6758.T" in text
    assert "12,480 JPY" in text

def test_format_message_multi_parts():
    """Test format_message khi có nhiều phần (part=2, total_parts=3)."""
    signals = [
        {"symbol": "9984.T", "close_price": 8500},
    ]
    
    text = format_message(
        signals_chunk=signals,
        signal_type="BEARISH",
        signal_date="2026-02-01",
        timeframe="1MO",
        part=2,
        total_parts=3,
        total_signals=101
    )

    assert "[1MO | BEARISH IMFVG — 2026-02-01]  (2/3)" in text
    assert "Tìm thấy" not in text  # Chỉ phần 1 mới hiện tổng số
    assert "9984.T" in text
    assert "8,500 JPY" in text

@patch("core.notifier.TELEGRAM_TOKEN", "dummy_token")
@patch("core.notifier.CHAT_ID", "dummy_chat_id")
@patch("core.notifier.get_unnotified_signals")
@patch("core.notifier.send_telegram")
@patch("core.notifier._mark_notified")
@patch("core.notifier.time.sleep")  # Để test chạy nhanh
def test_notify_chunking(mock_sleep, mock_mark, mock_send, mock_get_signals):
    """Test logic chia chunk (50/message) trong hàm notify()."""
    
    # Tạo mock data với 120 signals cùng signal_type="BULLISH"
    mock_signals = []
    for i in range(120):
        mock_signals.append({
            "id": i + 1,
            "symbol": f"{1000 + i}.T",
            "indicator": "IMFVG",
            "signal_date": "2026-02-01",
            "signal_type": "BULLISH",
            "gap_top": 100,
            "gap_bottom": 50,
            "close_price": 75
        })
    
    mock_get_signals.return_value = mock_signals
    mock_send.return_value = True  # Luôn gửi thành công

    messages_sent = notify(timeframe="1MO")

    # 120 signals với 50/chunk => 3 messages (50, 50, 20)
    assert messages_sent == 3
    assert mock_send.call_count == 3
    
    # Kiểm tra __mark_notified được gọi
    assert mock_mark.call_count == 1
    
    # Kiểm tra args chuyền vào _mark_notified (danh sách ID từ 1 đến 120)
    marked_ids = mock_mark.call_args[0][0]
    assert len(marked_ids) == 120
    assert marked_ids[0] == 1
    assert marked_ids[-1] == 120

@patch("core.notifier.TELEGRAM_TOKEN", "dummy_token")
@patch("core.notifier.CHAT_ID", "dummy_chat_id")
@patch("core.notifier.get_unnotified_signals")
@patch("core.notifier.send_telegram")
@patch("core.notifier._mark_notified")
def test_notify_chunking_failure(mock_mark, mock_send, mock_get_signals):
    """Test khi gửi 1 chunk thất bại thì không gửi chunk tiếp và không mark những chunk chưa gửi."""
    
    # Tạo mock data với 120 signals
    mock_signals = []
    for i in range(120):
        mock_signals.append({
            "id": i + 1,
            "symbol": f"{1000 + i}.T",
            "indicator": "IMFVG",
            "signal_date": "2026-02-01",
            "signal_type": "BULLISH",
            "gap_top": 100,
            "gap_bottom": 50,
            "close_price": 75
        })
    
    mock_get_signals.return_value = mock_signals
    
    # Chunk 1 (50) thành công, Chunk 2 (50) thất bại 
    # => Dừng vòng lặp, tổng messages_sent = 1
    mock_send.side_effect = [True, False, True]

    messages_sent = notify(timeframe="1MO")

    assert messages_sent == 1
    assert mock_send.call_count == 2  # Gọi 2 lần (lần 2 thất bại)
    
    # _mark_notified chỉ được gọi với ID của chunk 1 (50 signals)
    marked_ids = mock_mark.call_args[0][0]
    assert len(marked_ids) == 50
    assert marked_ids[0] == 1
    assert marked_ids[-1] == 50
