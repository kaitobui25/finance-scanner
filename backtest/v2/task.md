# Checklist Triển Khai Hệ Thống DEAP (Hedge Fund V2 - Ultimate Alpha)

## Phase 1: Chuẩn bị Dữ liệu & Hệ sinh thái (Data & Environment)
- [ ] 1.1 Khởi tạo thư mục dự án và `data_loader.py` kết nối cache 15 năm.
- [ ] 1.2 Viết `preprocessor.py` tính toán: EMA, RSI, ATR, và **ADX (Trend Strength)**.
- [ ] 1.3 **Data Leakage Guard:** Ép toàn bộ mảng Indicator (ngoại trừ Giá Mở cửa nến hiện tại) qua `shift(1)`.
- [ ] 1.4 Khởi tạo Data Splitter: Final Holdout (2022-2024) và Rolling WFA trên 12 năm trước đó.

## Phase 2: Khung xương DEAP & Định nghĩa Gen (Genomes ~10 Chiều)
- [ ] 2.1 Cấu trúc Cá thể Đa chiều (Trống Tránh Underfitting):
  - [ ] **Tín hiệu Base:** `ema_fast`, `ema_slow`, `rsi_period`, `rsi_threshold` (Vùng mua).
  - [ ] **Risk & Scaling:** `risk_per_trade_base`, `position_scaling_factor` (Bơm tiền nếu trend ADX cực mạnh).
  - [ ] **Dynamic Exits:** `atr_sl_multiplier` (Cắt lỗ động), `atr_tp_multiplier` (Chốt lời động), `trailing_activation`.
  - [ ] **Re-entry:** `re_entry_cooldown` (Đếm số nến đóng băng chờ trước khi vào lại lệnh).
- [ ] 2.2 Cài đặt DEAP cơ sở: Tối ưu 2 mục tiêu `base.Fitness(weights=(1.0, 1.0))` (Thúc Sortino/Calmar, Ép MaxDD/Volatility).
- [ ] 2.3 Cắm Ngàm Hạn mức Gen (Constraints): Xử trảm cá thể vi phạm logic chéo.

## Phase 3: Lõi Động cơ Giả lập (Simulator Engine)
- [ ] 3.1 Dựng hàm `simulate_trades(individual, price_dataframe)`.
- [ ] 3.2 Nhúng Giới hạn Thị trường thật:
  - [ ] Position Size kịch trần `<=` 2% của `SMA_Volume_20`. Tiền nhàn rỗi phế bỏ.
  - [ ] Trừ phí đi lệnh (0.1%) và Trượt giá Slippage (0.05%) 2 chiều.
- [ ] 3.3 Module Đánh giá Chu kỳ Tinh vi (Advanced Market Regime):
  - [ ] Lọc Regime bằng: **Trend Strength (ADX)** và **Volatility Percentile (ATR)**.
  - [ ] Cắt Fitness nếu hệ thống thoi thóp ở môi trường Ranging/Low Vol.
- [ ] 3.4 Dựng Lớp LRU-Cache (Genome Hashing) tàng hình độ trễ của NSGA-II lặp Gen.

## Phase 4: Vòng quay NSGA-II & Chấm điểm Tail Risk (Evolution Loop)
- [ ] 4.1 Khai phá Sức mạnh Đa luồng (`multiprocessing.Pool`).
- [ ] 4.2 Kích hoạt WFA Rolling Loop trên Data 12 năm băm nhỏ.
- [ ] 4.3 Chấm điểm Chống Rủi Ro Đuôi (Tail Risk Pareto Scoring):
  - [ ] Dùng `Score = Sortino_Ratio * Calmar_Ratio * (1/Volatility)`.

## Phase 5: Ải Sinh Tử Bọc Hậu (Robustness Integrity Tests)
- [ ] 5.1 Test Động Đất Gen (Neighborhood Perturbation Test): $\pm$ 10% giá trị cụm Tham số, xác nhận biên độ kháng cự sụp đổ.
- [ ] 5.2 Test Monte Carlo Block Bootstrap: Trộn Cấu trúc lệnh Trade (Theo mảng Block) 1000 lượt. Giữ vững 5th Percentile đỉnh đạc.
- [ ] 5.3 Final Holdout Execution: Đánh cược The Chosen One (TOP 1) trên Tập thời gian Tự Cấp Đông 2022-2024.

## Phase 6: Danh Mục Tổng & Kill Switch (Live Portfolio & Guardrail)
- [ ] 6.1 Tổng hợp 5 Bộ tham số Đích tôn của 5 mã (9434, 5401, 3350, 9984, 2181).
- [ ] 6.2 Quản trị Vốn Hệ Thống Toàn Cục: **Portfolio Volatility Scaling** (Phân bổ vốn nghịch đảo với độ chướng ngại ATR).
- [ ] 6.3 Viết Module Kill-Switch Live: Phát còi cảnh báo Stop toàn máy nếu vỡ Threshold Live MaxDD.
