"""
core/position_tracker.py — IMFVG Position Monitor Engine.

Dùng bởi:
  - position_monitor.py  (CLI, ghi DB)
  - backtest / AI optimizer (pure compute, không DB)

Không import DB, không import cache.py ở module level.
scan_full_history() là pure function — safe to parallelize.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Callable, Literal, TypedDict

import pandas as pd

from indicators.fvg_core import BULL, BEAR, EMPTY, detect_imfvg_from_bars

log = logging.getLogger("position_tracker")


# ══════════════════════════════════════════════════════════════════════════════
# T00b — SignalContext
# ══════════════════════════════════════════════════════════════════════════════

class SignalContext(TypedDict, total=False):
    """
    Context dict engine truyền vào signal_fn mỗi bar.

    total=False: mọi key đều optional về mặt typing.
    Engine contract: "atr" key luôn có trong mọi context engine tạo ra,
    value có thể None nếu ATR chưa tính được (đầu series).

    signal_fn nên dùng context.get("atr") — không bao giờ context["atr"].

    v2 keys:
        atr: float | None  — ATR tại bar hiện tại

    v3 additions (khi cần, không đổi SignalFn signature):
        volume:   float | None
        rsi:      float | None
        ema_fast: float | None
        ema_slow: float | None
    """
    atr: float | None


# ══════════════════════════════════════════════════════════════════════════════
# T00c — SignalMeta + SignalFn
# ══════════════════════════════════════════════════════════════════════════════

class SignalMeta(TypedDict, total=False):
    """
    Meta dict signal_fn trả về khi signal != None.

    Required:
        entry_price: float — giá vào lệnh (thường là close của bar signal).
                             Engine dùng meta["entry_price"] để tính tp/sl/ts.
                             KeyError tại đây = programming error trong signal_fn.

    Optional (strategy-specific):
        gap_top:    float — biên trên của gap (IMFVG-specific).
        gap_bottom: float — biên dưới của gap (IMFVG-specific).
        Các key khác strategy tự thêm nếu muốn lưu vào DB.

    Khi signal = None: trả {} (empty dict).
    total=False vì entry_price là required về mặt contract nhưng
    TypedDict không support "required + optional" mix cleanly trước Python 3.11.
    Engine sẽ raise KeyError nếu entry_price missing — đó là desired behavior.
    """
    entry_price: float
    gap_top:     float
    gap_bottom:  float


SignalFn = Callable[
    [pd.DataFrame, int, SignalContext],
    tuple[str | None, SignalMeta],
]
"""
Type alias cho signal detection function. Returns (signal, SignalMeta).

Contract:
    Args:
        df:      Full DataFrame tz-aware JST, sorted ascending.
        i:       Bar index hiện tại (df.iloc[i] = current bar).
                 Với IMFVG: cần i >= 3.
        context: Dict từ engine. "atr" key luôn có, value có thể None.

    Returns:
        (signal, meta) tuple:
            signal: "BULL" | "BEAR" | None
            meta:   dict với "entry_price": float khi signal != None.
                    Các key khác là strategy-specific (optional).

    Error policy:
        Expected data issue (NaN, i < min_bars) → return (None, {})
        Programming error (impossible state)    → raise

    Naming:
        Factory function nên set __name__ để log/DB có tên rõ ràng.
        Ví dụ: _fn.__name__ = "imfvg_fw0.0"
"""


# ══════════════════════════════════════════════════════════════════════════════
# T08 — PositionConfig
# ══════════════════════════════════════════════════════════════════════════════

@dataclass
class PositionConfig:
    """
    Toàn bộ params của position tracker engine.

    Không có literal nào trong engine — mọi thứ đều từ đây.
    AI optimizer chỉ cần thay đổi object này.

    Signal Detection (IMFVG-specific, dùng bởi make_imfvg_detector):
        filter_width: Tương đương Pine filterWidth. Gap phải > ATR * filter_width.
                      0.0 = không lọc (Pine default).
                      Custom signal_fn có thể bỏ qua param này hoàn toàn.

    Coupling note:
        filter_width và atr_period là coupled parameters.
        filter_width=0.5 với atr_period=5 ≠ filter_width=0.5 với atr_period=20.
        Khi AI optimize cả hai: dùng Bayesian/genetic, KHÔNG dùng grid search.

    ATR:
        atr_period: Số bar để tính ATR (rolling SMA of True Range).
                    Pine dùng 200 nhưng monthly cache ~60-120 bar → 14 hợp lý.

    Position Levels (Pine defaults):
        tp_mult: TP = entry ± ATR * tp_mult
        sl_mult: SL = entry ∓ ATR * sl_mult
        ts_mult: Trailing Stop initial = entry ∓ ATR * ts_mult, ratchet mỗi bar.

    Exit Execution Model:
        exit_on_wick: True  = TP/SL trigger khi high/low chạm level (Pine default).
                      False = TP/SL trigger khi close chạm level (conservative).
        ts_on_close:  True  = TS trigger khi close vượt TS level (Pine default).
                      False = TS trigger khi wick; exit price = TS level (stop fill).
        exit_priority: "TP_FIRST" = TP > SL > TS (Pine style, default).
                       "SL_FIRST" = SL > TP > TS (conservative).
                       Edge case monthly: cùng bar có high >= TP và low <= SL.

    Cost Model (để AI không overfit vào lý thuyết):
        slippage:      Fraction per exit. 0.001 = 0.1%. Default = 0 (theoretical).
        fee_per_trade: Fraction per round-trip. Default = 0.

    AI Optimization Dimensions (8):
        filter_width, atr_period, tp_mult, sl_mult, ts_mult,
        exit_on_wick, ts_on_close, exit_priority

    Production defaults:
        slippage=0.001, fee_per_trade=0.002
    """
    # Signal detection
    filter_width:   float = 0.0

    # ATR
    atr_period:     int   = 14

    # Position levels
    tp_mult:        float = 4.0
    sl_mult:        float = 2.0
    ts_mult:        float = 3.0

    # Exit execution model
    exit_on_wick:   bool  = True
    ts_on_close:    bool  = True
    exit_priority:  Literal["TP_FIRST", "SL_FIRST"] = "TP_FIRST"

    # Cost model
    slippage:       float = 0.0
    fee_per_trade:  float = 0.0


# ══════════════════════════════════════════════════════════════════════════════
# T09 — PositionState
# ══════════════════════════════════════════════════════════════════════════════

@dataclass
class PositionState:
    """
    State trả về từ scan_full_history() hoặc check_latest_bar().

    signal_action values:
        None      = không có signal tại bar này
        "OPEN"    = mở position mới (không có position cũ hoặc vừa exit)
        "REVERSE" = đảo chiều (có position cũ, signal ngược hướng)
        "IGNORE"  = signal cùng hướng với position đang hold → bỏ qua (v2)

    bars_held convention:
        Entry bar = 0 (bar tạo signal, chưa có bar nào qua đêm).
        Mỗi bar tiếp theo sau entry: +1.
        bars_held tăng ở đầu Step 1 (TP/SL/TS check), chỉ khi direction != None.
        → Không bao giờ tăng tại entry bar vì direction set tại Step 2.

    close_reason:
        Reason của lần exit gần nhất. None nếu chưa có exit trong history.
        Khi is_holding=True: None (không có exit active).
        Khi is_holding=False: "TP_HIT"|"SL_HIT"|"TS_HIT"|"REVERSED".
    """
    new_signal_detected:   bool

    # "OPEN" | "REVERSE" | "IGNORE" | None
    signal_action:         str | None

    is_holding:            bool
    direction:             str | None       # BULL | BEAR | None

    entry_date:            str | None       # "YYYY-MM-DD" JST
    gap_top:               float | None
    gap_bottom:            float | None
    entry_close:           float | None     # close tại bar entry (từ meta["entry_price"])

    tp_level:              float | None
    sl_level:              float | None
    trailing_stop:         float | None     # rolling, update mỗi bar
    atr_at_entry:          float | None

    bars_held:             int

    close_reason:          str | None       # "TP_HIT"|"SL_HIT"|"TS_HIT"|"REVERSED"|None
    close_price_at_exit:   float | None

    last_signal_type:      str | None       # signal gần nhất detect được tại bar check
    last_signal_date:      str | None       # "YYYY-MM-DD" JST
    last_checked_bar_date: str | None       # date của bar cuối đã process


# ══════════════════════════════════════════════════════════════════════════════
# T10 — Trade
# ══════════════════════════════════════════════════════════════════════════════

@dataclass
class Trade:
    """
    In-memory record của 1 completed trade. Dùng cho AI backtest, không ghi DB.
    Khác với position_history_1MO (DB record — để audit production).

    Price fields:
        entry_price:       Giá vào lệnh (từ meta["entry_price"]).
                           Thường là close của bar signal.
        exit_price:        Fill price lý thuyết TRƯỚC slippage.
                           TP_HIT  → tp_level
                           SL_HIT  → sl_level
                           TS_HIT (ts_on_close=True)  → bar["close"]
                           TS_HIT (ts_on_close=False) → ts_value (stop fill level)
                           REVERSED → bar["close"]
        actual_exit_price: exit_price ± slippage. Dùng để tính P&L.
                           BULL: exit * (1 - slippage)  [bán được thấp hơn]
                           BEAR: exit * (1 + slippage)  [mua đắt hơn]

    P&L: Tất cả tính từ actual_exit_price (sau slippage), KHÔNG từ exit_price.

    is_win vs is_tp_hit:
        is_win:    net_pnl_pct > 0 — economic profit after all costs.
        is_tp_hit: close_reason == "TP_HIT" — exit event type.
        Phân biệt: TP_HIT với fee cao → net_pnl_pct < 0 → is_win=False.
        Dùng is_win cho win_rate. Dùng is_tp_hit cho signal quality analysis.

    Memory note:
        ~300 bytes/Trade. Single symbol OK.
        500 symbols × 1000 configs × 50 trades ≈ 7.5 GB — dùng summarize_trades=True.
    """
    entry_date:         str
    exit_date:          str
    direction:          str        # BULL | BEAR
    entry_price:        float      # giá vào lệnh
    exit_price:         float      # trước slippage
    actual_exit_price:  float      # sau slippage — dùng để tính P&L
    fee_per_trade:      float      # từ cfg.fee_per_trade lúc tạo
    close_reason:       str        # "TP_HIT"|"SL_HIT"|"TS_HIT"|"REVERSED"
    bars_held:          int
    gap_top:            float | None
    gap_bottom:         float | None
    atr_at_entry:       float
    tp_level:           float
    sl_level:           float

    @property
    def signed_pnl(self) -> float:
        """
        P&L tuyệt đối có dấu, sau slippage, trước fee.
        Dùng actual_exit_price (không phải exit_price).

        BULL: actual_exit - entry_price  (+ = profit, - = loss)
        BEAR: entry_price - actual_exit  (+ = profit, - = loss)
        """
        if self.direction == BULL:
            return self.actual_exit_price - self.entry_price
        return self.entry_price - self.actual_exit_price

    @property
    def pnl_pct(self) -> float:
        """P&L % sau slippage, trước fee."""
        return self.signed_pnl / self.entry_price if self.entry_price != 0 else 0.0

    @property
    def net_pnl_pct(self) -> float:
        """P&L % sau slippage VÀ fee. Số thực tế trader nhận."""
        return self.pnl_pct - self.fee_per_trade

    @property
    def rr_ratio(self) -> float:
        """
        Risk-Reward ratio có dấu = signed_pnl / atr_at_entry.
        + = win trade, - = loss trade.
        avg_rr across trades = expectancy của strategy.
        """
        return self.signed_pnl / self.atr_at_entry if self.atr_at_entry > 0 else 0.0

    @property
    def is_win(self) -> bool:
        """
        True nếu net_pnl_pct > 0 (sau slippage và fee).
        Win = economic profit after all costs, NOT TP hit event.
        """
        return self.net_pnl_pct > 0

    @property
    def is_tp_hit(self) -> bool:
        """True nếu exit reason là TP_HIT (exit event, không phải economic result)."""
        return self.close_reason == "TP_HIT"


# ══════════════════════════════════════════════════════════════════════════════
# T11 — TradesSummary
# ══════════════════════════════════════════════════════════════════════════════

@dataclass
class TradesSummary:
    """
    Pre-computed metrics từ sequential trade list của 1 symbol.
    Memory-efficient alternative to List[Trade] khi mass optimization.

    Scope: Per-symbol only. NOT a portfolio metric.

    Equity Model (Additive):
        equity += net_pnl_pct per trade (fixed-size, không compounding).
        Suitable for comparing configs. WARNING: may understate risk vs
        compounding, đặc biệt với high-volatility strategies.
        v3: multiplicative option (equity *= 1 + net_pnl_pct).

    max_drawdown Convention:
        ≤ 0. Ví dụ: -0.15 = drawdown 15% từ peak.
        Tính từ additive equity curve, per-symbol (trades sequential).
        KHÔNG dùng như portfolio MDD (trades across symbols overlap in time).

    sum_sq_rr, sum_sq_pnl:
        Dùng để tính variance/std trong 1 pass.
        variance = sum_sq/n - mean² (population variance).
    """
    n_trades:      int
    n_wins:        int
    n_tp:          int
    n_sl:          int
    n_ts:          int
    n_reversed:    int
    total_bars:    int
    total_pnl_pct: float
    total_net_pnl: float
    total_rr:      float
    max_drawdown:  float      # ≤ 0
    sum_sq_rr:     float      # sum of rr_ratio²
    sum_sq_pnl:    float      # sum of net_pnl_pct²

    @property
    def win_rate(self) -> float:
        return self.n_wins / self.n_trades if self.n_trades > 0 else 0.0

    @property
    def avg_rr(self) -> float:
        """Signed avg RR per ATR. > 0 = positive expectancy."""
        return self.total_rr / self.n_trades if self.n_trades > 0 else 0.0

    @property
    def avg_net_pnl(self) -> float:
        """Avg net P&L % per trade (after slippage + fee)."""
        return self.total_net_pnl / self.n_trades if self.n_trades > 0 else 0.0

    @property
    def avg_bars(self) -> float:
        return self.total_bars / self.n_trades if self.n_trades > 0 else 0.0

    @property
    def expectancy(self) -> float:
        """
        Trading expectancy (ATR-normalized) = avg_rr.

        UNIT: ATR per trade, NOT dollar per trade.
        avg_rr correctly weights wins (+) and losses (-).
        DO NOT compute as win_rate × avg_rr — double-weights wins.

        Proof: E = win_rate × avg_win + (1-win_rate) × avg_loss = avg_rr.
        """
        return self.avg_rr

    @property
    def var_rr(self) -> float:
        """Population variance của rr_ratio. 0.0 nếu n_trades < 2."""
        if self.n_trades < 2:
            return 0.0
        return max(0.0, self.sum_sq_rr / self.n_trades - self.avg_rr ** 2)

    @property
    def std_rr(self) -> float:
        """Population std của rr_ratio."""
        return self.var_rr ** 0.5

    @property
    def var_pnl(self) -> float:
        """Population variance của net_pnl_pct. 0.0 nếu n_trades < 2."""
        if self.n_trades < 2:
            return 0.0
        return max(0.0, self.sum_sq_pnl / self.n_trades - self.avg_net_pnl ** 2)

    @property
    def std_pnl(self) -> float:
        """Population std của net_pnl_pct."""
        return self.var_pnl ** 0.5

    @property
    def sharpe(self) -> float:
        """
        Per-trade Sharpe proxy = avg_net_pnl / std_pnl.
        NOT annualized. Suitable for config comparison.
        0.0 nếu std_pnl = 0 (undefined, not infinity).
        """
        return self.avg_net_pnl / self.std_pnl if self.std_pnl > 0 else 0.0

    @property
    def calmar(self) -> float:
        """
        Per-trade Calmar proxy = avg_net_pnl / abs(max_drawdown).
        Unit: % / % → dimensionally consistent.
        NOT annualized Calmar. Suitable for config comparison.
        0.0 nếu max_drawdown = 0 (no drawdown, undefined).
        """
        if self.max_drawdown == 0.0:
            return 0.0
        return self.avg_net_pnl / abs(self.max_drawdown)

    @classmethod
    def from_accumulator(cls, acc: dict) -> TradesSummary:
        """Build TradesSummary từ accumulator dict sau khi scan xong."""
        return cls(
            n_trades      = acc["n_trades"],
            n_wins        = acc["n_wins"],
            n_tp          = acc["n_tp"],
            n_sl          = acc["n_sl"],
            n_ts          = acc["n_ts"],
            n_reversed    = acc["n_reversed"],
            total_bars    = acc["total_bars"],
            total_pnl_pct = acc["total_pnl_pct"],
            total_net_pnl = acc["total_net_pnl"],
            total_rr      = acc["total_rr"],
            max_drawdown  = acc["max_drawdown"],
            sum_sq_rr     = acc["sum_sq_rr"],
            sum_sq_pnl    = acc["sum_sq_pnl"],
        )


# ══════════════════════════════════════════════════════════════════════════════
# T12 — REASON_COUNTER_MAP
# ══════════════════════════════════════════════════════════════════════════════

# Map close_reason → accumulator key trong summary_acc.
# Thêm exit reason mới ở v3:
#   1. Thêm entry vào dict này
#   2. Thêm field tương ứng vào TradesSummary
#   3. Initialize trong _make_accumulator()
#   4. Chạy test với strict=True để verify
REASON_COUNTER_MAP: dict[str, str] = {
    "TP_HIT":   "n_tp",
    "SL_HIT":   "n_sl",
    "TS_HIT":   "n_ts",
    "REVERSED": "n_reversed",
    # v3 additions:
    # "PYRAMID_EXIT": "n_pyramid",
}


# ══════════════════════════════════════════════════════════════════════════════
# T13 — compute_atr
# ══════════════════════════════════════════════════════════════════════════════

def compute_atr(
    high:   pd.Series,
    low:    pd.Series,
    close:  pd.Series,
    period: int,
) -> pd.Series:
    """
    Tính ATR (Average True Range) = SMA của True Range.
    Tương đương ta.atr() trong Pine Script.

    True Range = max(high - low, |high - prev_close|, |low - prev_close|)
    ATR = rolling SMA(TR, period)

    Args:
        period: LUÔN từ cfg.atr_period — không hardcode.

    Returns:
        Series cùng index với input.
        NaN cho period-1 bar đầu (không đủ data).
    """
    prev_close = close.shift(1)
    tr = pd.concat([
        high - low,
        (high - prev_close).abs(),
        (low  - prev_close).abs(),
    ], axis=1).max(axis=1)
    return tr.rolling(window=period).mean()


# ══════════════════════════════════════════════════════════════════════════════
# T14 — _detect_imfvg_at  (wrapper gọi fvg_core)
# ══════════════════════════════════════════════════════════════════════════════

def _detect_imfvg_at(
    df:      pd.DataFrame,
    i:       int,
    cfg:     PositionConfig,
    context: SignalContext,
) -> tuple[str | None, dict]:
    """
    Detect IMFVG signal tại bar df.iloc[i].

    Wrapper: extract bars từ df → gọi fvg_core.detect_imfvg_from_bars().
    Không duplicate logic — fvg_core là single source of truth.

    Error policy:
        Expected (i < 3, NaN) → return (None, {})
        Programming error     → raise (propagate từ fvg_core)

    Returns meta với key "entry_price" (không phải "entry_close").
    """
    if i < 3:
        return None, {}

    b0 = df.iloc[i]
    b1 = df.iloc[i - 1]
    b2 = df.iloc[i - 2]
    b3 = df.iloc[i - 3]

    # NaN guard — expected data issue
    for bar_idx, bar in enumerate((b3, b2, b1, b0)):
        if pd.isna(bar["high"]) or pd.isna(bar["low"]) or pd.isna(bar["close"]):
            log.debug(
                "_detect_imfvg_at: NaN at df[%d] (bar-%d from current), "
                "skipping signal detection", i, 3 - bar_idx
            )
            return None, {}

    atr_i = context.get("atr")

    result = detect_imfvg_from_bars(
        b3_low=float(b3["low"]),   b3_high=float(b3["high"]),  b3_close=float(b3["close"]),
        b2_close=float(b2["close"]),
        b1_low=float(b1["low"]),   b1_high=float(b1["high"]),
        b0_close=float(b0["close"]),
        filter_width=cfg.filter_width,
        atr=float(atr_i) if atr_i is not None else None,
    )

    if result["signal"] is None:
        return None, {}

    return result["signal"], {
        "entry_price": float(b0["close"]),   # "entry_price" — không phải "entry_close"
        "gap_top":     result["gap_top"],
        "gap_bottom":  result["gap_bottom"],
    }


# ══════════════════════════════════════════════════════════════════════════════
# T14b — make_imfvg_detector  (factory function)
# ══════════════════════════════════════════════════════════════════════════════

def make_imfvg_detector(cfg: PositionConfig) -> SignalFn:
    """
    Factory: tạo SignalFn cho IMFVG detection với params từ cfg.

    Tại sao factory function (không phải lambda):
        - Có tên trong traceback → dễ debug
        - __name__ set rõ ràng → log/DB có tên đẹp
        - Testable riêng

    Usage:
        detector = make_imfvg_detector(cfg)
        scan_full_history(df, cfg, signal_fn=detector)

        # Hoặc để default (signal_fn=None → auto dùng IMFVG):
        scan_full_history(df, cfg)
    """
    def _imfvg_detector(
        df:      pd.DataFrame,
        i:       int,
        context: SignalContext,
    ) -> tuple[str | None, dict]:
        return _detect_imfvg_at(df, i, cfg, context)

    _imfvg_detector.__name__ = f"imfvg_fw{cfg.filter_width}"
    return _imfvg_detector


# ══════════════════════════════════════════════════════════════════════════════
# T14c — _resolve_strategy_name
# ══════════════════════════════════════════════════════════════════════════════

def _resolve_strategy_name(
    signal_fn: SignalFn | None,
    override:  str | None,
) -> str:
    """
    Resolve tên strategy theo priority:
        1. override (explicit từ caller)
        2. signal_fn.__name__ nếu là factory-set name (không phải lambda/partial)
        3. Tên của wrapped function nếu functools.partial
        4. "unknown" fallback

    Convention cho factory functions:
        make_imfvg_detector(cfg) → __name__ = "imfvg_fw0.0"
        make_rsi_detector(14)    → __name__ = "rsi_14"

    Covers 4 cases:
        factory fn     → __name__ đã set → dùng luôn
        lambda         → __name__ = "<lambda>" → "unknown"
        functools.partial → lấy từ .func.__name__
        override       → wins over everything
    """
    if override:
        return override
    name = getattr(signal_fn, "__name__", None)
    if name and not name.startswith("<"):  # skip <lambda>, <genexpr>, etc.
        return name
    # functools.partial: lấy từ wrapped function
    wrapped = getattr(signal_fn, "func", None)
    if wrapped:
        return getattr(wrapped, "__name__", "unknown")
    return "unknown"


# ══════════════════════════════════════════════════════════════════════════════
# T15 — _ratchet_ts
# ══════════════════════════════════════════════════════════════════════════════

def _ratchet_ts(
    bar:       pd.Series,
    direction: str,
    ts:        float,
    atr:       float,
    cfg:       PositionConfig,
) -> float:
    """
    Ratchet trailing stop theo hướng position.

    Pine behavior: TS ratchet luôn dùng close làm base.
    Không phụ thuộc cfg.ts_on_close — đó chỉ ảnh hưởng đến TRIGGER check.

    BULL: TS chỉ tăng (monotonic non-decreasing)
        new_ts = close - atr * cfg.ts_mult
        return max(ts, new_ts)

    BEAR: TS chỉ giảm (monotonic non-increasing)
        new_ts = close + atr * cfg.ts_mult
        return min(ts, new_ts)

    Args:
        atr: ATR rolling tại bar này (từ atr_series.iloc[i]).
             Không dùng atr_at_entry — TS dùng ATR rolling.
    """
    close = float(bar["close"])
    if direction == BULL:
        new_ts = close - atr * cfg.ts_mult
        return new_ts if new_ts > ts else ts   # chỉ tăng
    else:  # BEAR
        new_ts = close + atr * cfg.ts_mult
        return new_ts if new_ts < ts else ts   # chỉ giảm


# ══════════════════════════════════════════════════════════════════════════════
# T16 — _check_exit
# ══════════════════════════════════════════════════════════════════════════════

def _check_exit(
    bar:       pd.Series,
    direction: str,
    tp_level:  float,
    sl_level:  float,
    ts:        float,
    cfg:       PositionConfig,
) -> tuple[str | None, float | None]:
    """
    Check exit conditions cho 1 bar. Trả (reason, price) hoặc (None, None).

    Execution order (document rõ để tránh confusion):
    ─────────────────────────────────────────────────
    Step 1: Xác định giá dùng cho TP/SL check
        exit_on_wick=True  → dùng high/low (Pine default, optimistic)
        exit_on_wick=False → dùng close (conservative)

    Step 2: Xác định TS trigger và exit price
        ts_on_close=True:
            trigger = close
            exit_price = close    (Pine barcolor default)
        ts_on_close=False:
            trigger = low  (BULL) hoặc high (BEAR) — wick
            exit_price = ts       (stop fill tại TS level)

    Step 3: Apply exit_priority
        "TP_FIRST": check TP → SL → TS (Pine style)
        "SL_FIRST": check SL → TP → TS (conservative)
        TS luôn cuối cùng — priority chỉ ảnh hưởng TP vs SL.

    Edge case (monthly candles):
        Cùng 1 bar có thể high >= TP và low <= SL đồng thời.
        exit_priority quyết định winner.
    ─────────────────────────────────────────────────
    """
    high  = float(bar["high"])
    low   = float(bar["low"])
    close = float(bar["close"])

    # Step 1: TP/SL trigger price
    check_high = high  if cfg.exit_on_wick else close
    check_low  = low   if cfg.exit_on_wick else close

    # Step 2: TS trigger + exit price
    if cfg.ts_on_close:
        ts_trigger_bull = close
        ts_trigger_bear = close
        ts_exit_price   = close
    else:
        ts_trigger_bull = low    # wick trigger
        ts_trigger_bear = high
        ts_exit_price   = ts     # stop fill tại TS level

    # Step 3: Build hits + apply priority
    if direction == BULL:
        tp_hit = check_high >= tp_level
        sl_hit = check_low  <= sl_level
        ts_hit = ts_trigger_bull <= ts
        tp_ret = ("TP_HIT", tp_level)
        sl_ret = ("SL_HIT", sl_level)
        ts_ret = ("TS_HIT", ts_exit_price)
    else:  # BEAR
        tp_hit = check_low  <= tp_level
        sl_hit = check_high >= sl_level
        ts_hit = ts_trigger_bear >= ts
        tp_ret = ("TP_HIT", tp_level)
        sl_ret = ("SL_HIT", sl_level)
        ts_ret = ("TS_HIT", ts_exit_price)

    if cfg.exit_priority == "TP_FIRST":
        if tp_hit: return tp_ret
        if sl_hit: return sl_ret
        if ts_hit: return ts_ret
    else:  # SL_FIRST
        if sl_hit: return sl_ret
        if tp_hit: return tp_ret
        if ts_hit: return ts_ret

    return None, None


# ══════════════════════════════════════════════════════════════════════════════
# T17 — _open_position
# ══════════════════════════════════════════════════════════════════════════════

def _open_position(
    sig:          str,
    meta:         dict,
    atr_i:        float,
    bar_date_str: str,
    cfg:          PositionConfig,
) -> dict:
    """
    Tính toán tất cả levels khi mở position mới.

    Args:
        sig:          "BULL" | "BEAR"
        meta:         dict từ signal_fn. PHẢI có "entry_price" key.
                      KeyError tại đây = programming error trong signal_fn.
        atr_i:        ATR rolling tại bar này — dùng cho tp/sl/ts calculation.
                      KHÔNG dùng atr_at_entry vì đây chính là atr_at_entry.
        bar_date_str: "YYYY-MM-DD" JST string.
        cfg:          PositionConfig — mọi thứ từ đây, không hardcode.

    Returns:
        dict với tất cả fields cần thiết cho state + DB INSERT.
    """
    entry_price = meta["entry_price"]   # KeyError nếu missing = bug trong signal_fn

    if sig == BULL:
        tp_level = entry_price + atr_i * cfg.tp_mult
        sl_level = entry_price - atr_i * cfg.sl_mult
        ts       = entry_price - atr_i * cfg.ts_mult
    else:  # BEAR
        tp_level = entry_price - atr_i * cfg.tp_mult
        sl_level = entry_price + atr_i * cfg.sl_mult
        ts       = entry_price + atr_i * cfg.ts_mult

    return {
        "direction":    sig,
        "entry_date":   bar_date_str,
        "entry_price":  entry_price,
        "gap_top":      meta.get("gap_top"),
        "gap_bottom":   meta.get("gap_bottom"),
        "atr_at_entry": atr_i,
        "tp_level":     tp_level,
        "sl_level":     sl_level,
        "trailing_stop": ts,
    }


# ══════════════════════════════════════════════════════════════════════════════
# T18 — _apply_slippage
# ══════════════════════════════════════════════════════════════════════════════

def _apply_slippage(
    exit_price:  float,
    direction:   str,
    close_reason: str,
    cfg:         PositionConfig,
) -> float:
    """
    Apply slippage vào exit price. Slippage luôn làm xấu kết quả.

    BULL (bán): actual = exit * (1 - slippage)  → bán được giá thấp hơn
    BEAR (mua): actual = exit * (1 + slippage)  → mua phải giá cao hơn

    Simplified model: symmetric slippage dựa trên direction.
    Không phân biệt TP/SL/TS — slippage apply đều.
    close_reason: reserved for future asymmetric slippage modeling (e.g. SL > TP).

    Returns exit_price nếu cfg.slippage == 0.0 (no-op, nhanh).
    """
    if cfg.slippage == 0.0:
        return exit_price
    if direction == BULL:
        return exit_price * (1.0 - cfg.slippage)
    return exit_price * (1.0 + cfg.slippage)


# ══════════════════════════════════════════════════════════════════════════════
# T19 — _accumulate_reason
# ══════════════════════════════════════════════════════════════════════════════

def _accumulate_reason(
    summary_acc:  dict,
    close_reason: str,
    strict:       bool = True,
) -> None:
    """
    Update reason counter trong summary_acc dùng REASON_COUNTER_MAP.

    Args:
        strict: True  (default) → raise ValueError khi unknown reason.
                                   Dùng trong dev/test và scan_full_history.
                False → increment n_unknown, log warning.
                        Dùng khi batch production lo ngại future code changes.

    Raises:
        ValueError: khi strict=True và close_reason không có trong REASON_COUNTER_MAP.

    Khi thêm exit reason mới (v3):
        1. Thêm vào REASON_COUNTER_MAP
        2. Thêm field vào TradesSummary
        3. Initialize trong _make_accumulator()
        4. Chạy test với strict=True
    """
    counter_key = REASON_COUNTER_MAP.get(close_reason)
    if counter_key is not None:
        summary_acc[counter_key] += 1
    elif strict:
        raise ValueError(
            f"_accumulate_reason: unknown close_reason={close_reason!r}. "
            f"Known: {list(REASON_COUNTER_MAP)}. "
            f"Update REASON_COUNTER_MAP when adding new exit reasons."
        )
    else:
        summary_acc["n_unknown"] = summary_acc.get("n_unknown", 0) + 1
        log.warning(
            f"_accumulate_reason: unknown reason={close_reason!r}, "
            f"counted in n_unknown (strict=False)"
        )


# ══════════════════════════════════════════════════════════════════════════════
# T20 — _make_accumulator + _accumulate
# ══════════════════════════════════════════════════════════════════════════════

def _make_accumulator() -> dict:
    """Tạo accumulator dict với tất cả keys initialized về 0."""
    return {
        "n_trades":      0,
        "n_wins":        0,
        "n_tp":          0,
        "n_sl":          0,
        "n_ts":          0,
        "n_reversed":    0,
        "total_bars":    0,
        "total_pnl_pct": 0.0,
        "total_net_pnl": 0.0,
        "total_rr":      0.0,
        "sum_sq_rr":     0.0,
        "sum_sq_pnl":    0.0,
        # MDD tracking
        "equity":        0.0,
        "peak_equity":   0.0,
        "max_drawdown":  0.0,
    }


def _accumulate(
    summary_acc:  dict,
    exit_price:   float,
    close_reason: str,
    bars_held:    int,
    direction:    str,
    entry_price:  float,
    atr_at_entry: float,
    cfg:          PositionConfig,
) -> None:
    """
    Update accumulator in-place sau mỗi trade exit.
    Không tạo Trade object — memory efficient cho mass optimization.

    Tính toán:
        actual_exit = _apply_slippage(...)
        signed_pnl  = actual_exit - entry (BULL) hoặc entry - actual_exit (BEAR)
        pnl_pct     = signed_pnl / entry_price
        net_pnl     = pnl_pct - fee_per_trade
        rr          = signed_pnl / atr_at_entry

    MDD tracking (additive equity model):
        equity      += net_pnl
        peak_equity  = max(peak_equity, equity)
        max_drawdown = min(max_drawdown, equity - peak_equity)
    """
    actual_exit = _apply_slippage(exit_price, direction, close_reason, cfg)

    if direction == BULL:
        signed_pnl = actual_exit - entry_price
    else:
        signed_pnl = entry_price - actual_exit

    pnl_pct = signed_pnl / entry_price if entry_price != 0 else 0.0
    net_pnl = pnl_pct - cfg.fee_per_trade
    rr      = signed_pnl / atr_at_entry if atr_at_entry >= 1e-9 else 0.0

    summary_acc["n_trades"]      += 1
    summary_acc["n_wins"]        += 1 if net_pnl > 0 else 0
    summary_acc["total_pnl_pct"] += pnl_pct
    summary_acc["total_net_pnl"] += net_pnl
    summary_acc["total_rr"]      += rr
    summary_acc["total_bars"]    += bars_held
    summary_acc["sum_sq_rr"]     += rr * rr
    summary_acc["sum_sq_pnl"]    += net_pnl * net_pnl

    _accumulate_reason(summary_acc, close_reason, strict=True)

    # MDD tracking
    summary_acc["equity"]      += net_pnl
    summary_acc["peak_equity"]  = max(summary_acc["peak_equity"],
                                      summary_acc["equity"])
    dd = summary_acc["equity"] - summary_acc["peak_equity"]
    summary_acc["max_drawdown"] = min(summary_acc["max_drawdown"], dd)


# ══════════════════════════════════════════════════════════════════════════════
# T21 — _no_update_state
# ══════════════════════════════════════════════════════════════════════════════

def _no_update_state(position_row: dict, reason: str) -> PositionState:
    """
    Trả PositionState khi check_latest_bar không có gì mới để update.
    Preserve tất cả fields từ DB row, set close_reason = reason.

    Dùng cho: cache_unavailable, no_new_bar, insufficient_bars_for_atr.
    """
    return PositionState(
        new_signal_detected   = False,
        signal_action         = None,
        is_holding            = True,    # vẫn holding, không có thay đổi
        direction             = position_row.get("direction"),
        entry_date            = position_row.get("entry_date"),
        gap_top               = position_row.get("gap_top"),
        gap_bottom            = position_row.get("gap_bottom"),
        entry_close           = position_row.get("entry_close"),
        tp_level              = position_row.get("tp_level"),
        sl_level              = position_row.get("sl_level"),
        trailing_stop         = position_row.get("trailing_stop"),
        atr_at_entry          = position_row.get("atr_at_entry"),
        bars_held             = position_row.get("bars_held", 0),
        close_reason          = reason,
        close_price_at_exit   = None,
        last_signal_type      = position_row.get("last_signal_type"),
        last_signal_date      = position_row.get("last_signal_date"),
        last_checked_bar_date = position_row.get("last_checked_at"),
    )