# Kế Hoạch Triển Khai: Hệ Thống DEAP Tối Ưu Chiến Lược (Hedge Fund V2 - Ultimate Alpha)

Khắc phục triệt để rủi ro Underfitting do mô hình ban đầu quá cơ bản, xử lý triệt để Tail Risk (Rủi ro đuôi) và Tối ưu hóa điểm vào/ra động lượng dựa trên định cỡ rủi ro đa chiều (Dynamic Scaling & Regimes) áp dụng trên 5 mã cốt lõi: `9434, 5401, 3350, 9984, 2181`.

## 1. Mở Rộng Hệ Gen Triệt Để Nạn Underfitting (~ 10 Chiều Tìm Kiếm)
Hệ thống 5 chiều cũ quá đơn sơ, dẫn tới thuật toán mất năng lực học các đặc trưng phức tạp khi thị trường biến đổi pha. Hệ Gen đập đi xây lại như sau:
- **Core Signal Base:** `ema_fast`, `ema_slow`, `rsi_period`, `rsi_entry_threshold`.
- **Dynamic Exit Rules:** `atr_stoploss_multiplier`, `atr_takeprofit_multiplier`, `trailing_stop_activation_offset` (Bao nhiêu Rợi Nhuận thì bắt đầu dời Cắt lỗ về mức Hòa vốn).
- **Position Scaling & Re-entry Logic:** `base_risk_pct`, `adx_scale_factor` (Hệ số nhồi thêm Size khi gia tốc Trend ADX vượt biên độ), `re_entry_cooldown` (Số nến đóng băng chờ trước khi thuật toán được phép bắt tín hiệu mới nếu bị Stophunt).

## 2. Tiêu Diệt Hàm Đánh Giá Đơn Sơ - Thay Bằng Tail Risk & Volatility Penalties
Công thức `CAGR / MaxDD` cơ bản đã che giấu hoàn toàn Rủi ro đuôi (Fat tails) của chuỗi lệnh và Độ biến động ngầm (Hidden Volatility).
- **Đánh giá Đa mục tiêu (NSGA-II) Khung V2:**
  - Objective 1: Trực tiếp tìm kiếm `Sortino Ratio` cao nhất (Hàm Sortino ưu việt hơn Sharpe vì nó chỉ trừng phạt các Biến động Cắm xuống - Downside Deviation, hoàn toàn ngó lơ các cú bắn Sharp lên trên).
  - Objective 2: Tìm kiếm `Calmar Ratio` (CAGR / MaxDD chu kỳ 3 năm).
- Chốt hạn Sàng lọc Pareto Scoring cuối cùng: `Final_Score = Sortino_Ratio * Calmar_Ratio * (1 / Annualized_Volatility)`. Triệt hạ triệt để các Code ăn may 1 nến kéo ảo PnL.

## 3. Quét Chu Kỳ Thị Trường Bằng Cụm Tinh Vi (Regime Detection V2)
Sử dụng đường cong MA200 bị trễ nhịp và quá thô ráp. Khung lọc Regime mới:
- **Trend Regime:** Đo bằng Trọng số Sức mạnh `ADX`. `ADX > 25` = Pha Trending, `ADX < 20` = Pha Ranging (Tích Lũy/Nhiễu).
- **Volatility Regime:** Đo lường bằng Lưới Phân Phối Tứ Phân Vị của `ATR(14)` gán trên 252 nến gần nhất (e.g. `> 80th percentile` = High Volatility).
- Bộ lọc chém đứt đuôi (Drop Penalty) các Cá thể nếu Equity Curve thoi thóp liên tục tại các mùa Ranging.

## 4. Kế Thừa Cấu Trúc Bê Tông Bản Tiền Nhiệm (Institutional Base)
Bộ ngàm chịu tải cũ vẫn là linh hồn phần Cứng:
- **Data Leakage & Liquidity:** Giữ nguyên quy tắc Tường lửa `Indicator.shift(1)`, Khớp lệnh tại Giá `Open`. Siết thanh khoản Cổ phiếu Bơm thổi ở mốc `2% Trung bình M20`.
- **Rolling WFA & Final Holdout:** Cuốn lưới dữ liệu suốt 12 năm tịnh tiến. Tập dữ liệu 2.5 năm cuối bị Niêm phong để Test Mù Vòng Cuối. Cắm Genome Hashing tiết kiệm Điện năng Máy chủ.
- **Monte Carlo Block & Parameter Perturbation:** Trộn mảng Block Bootstrap bảo tồn chuỗi Volatility Clustering cục bộ. Thí nghiệm rung lắc $\pm10\%$ Tham số Core.
- **Portfolio Volatility Scaling & Kill-Switch Live:** Tổng hợp 5 Mã, điều vốn đảo nghịch theo Thước đo ATR (Mã hiền được châm tiền, mã quậy bị bóp Margin). Dựng Limit Cầu dao Cắt Mạch (Kill-Switch DD) để Stop Algo Live tức thì.
