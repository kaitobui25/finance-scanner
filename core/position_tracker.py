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
from datetime import date
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


# ══════════════════════════════════════════════════════════════════════════════
# Phase 4 — scan_full_history
# ══════════════════════════════════════════════════════════════════════════════

def scan_full_history(
    df:               pd.DataFrame,
    cfg:              PositionConfig,
    return_trades:    bool = False,
    summarize_trades: bool = False,
    atr_series:       pd.Series | None = None,
    signal_fn:        SignalFn | None = None,
    strategy_name:    str | None = None,
) -> (PositionState | None
      | tuple[PositionState | None, list[Trade]]
      | tuple[PositionState | None, TradesSummary]):
    """
    Quét toàn bộ lịch sử bar-by-bar. Pure function — không ghi DB, không IO.

    Args:
        df:               DataFrame tz-aware JST, sorted ascending.
        cfg:              Toàn bộ params. Không có hardcode trong engine.
        return_trades:    True → return (state, List[Trade]).
        summarize_trades: True → return (state, TradesSummary). Memory-efficient.
        atr_series:       Precomputed ATR. None → tính từ cfg.atr_period.
                          Dùng khi chạy nhiều configs cùng atr_period để tránh recompute.
        signal_fn:        SignalFn | None. None → make_imfvg_detector(cfg).
        strategy_name:    Override tên strategy cho log/DB. None → từ signal_fn.__name__.

    Returns:
        return_trades=False, summarize_trades=False → PositionState | None
        return_trades=True                          → (PositionState | None, List[Trade])
        summarize_trades=True                       → (PositionState | None, TradesSummary)
        None = df quá ngắn.

    Raises:
        ValueError: nếu cả return_trades và summarize_trades đều True.

    Behavioral guarantees (verifiable bằng test):
        - tp_mult tăng → avg bars_held tăng (TP khó hit hơn)
        - filter_width tăng → ít trades hơn
        - Thứ tự trong loop: TP/SL/TS check → signal detect (không đảo)
        - entry bar bars_held = 0

    Warning — Portfolio MDD:
        scan_full_history trả per-symbol MDD (trades sequential, đúng).
        KHÔNG dùng kết quả này để tính portfolio MDD cross-symbol
        (trades overlap in time → sequential sum sai).
    """
    if return_trades and summarize_trades:
        raise ValueError(
            "return_trades và summarize_trades không dùng cùng nhau. "
            "Chọn 1: return_trades=True (List[Trade]) "
            "hoặc summarize_trades=True (TradesSummary, memory-efficient)."
        )

    min_bars = cfg.atr_period + 4
    if len(df) < min_bars:
        log.debug(
            "scan_full_history: df quá ngắn (%d < %d), skip",
            len(df), min_bars
        )
        if return_trades:    return None, []
        if summarize_trades: return None, TradesSummary.from_accumulator(_make_accumulator())
        return None

    # ATR
    if atr_series is None:
        atr_series = compute_atr(
            df["high"], df["low"], df["close"],
            period=cfg.atr_period,   # LUÔN từ cfg
        )
    elif len(atr_series) != len(df):
        raise ValueError(
            f"atr_series length mismatch: "
            f"atr_series={len(atr_series)}, df={len(df)}. "
            f"Precomputed ATR phải được tính từ cùng DataFrame."
        )

    # Resolve signal_fn + name
    if signal_fn is None:
        signal_fn = make_imfvg_detector(cfg)
    name = _resolve_strategy_name(signal_fn, strategy_name)
    log.debug("scan_full_history: strategy=%s bars=%d", name, len(df))

    # ── State variables ────────────────────────────────────────────────────────
    direction:    str   | None = None
    ts:           float | None = None
    tp_level:     float | None = None
    sl_level:     float | None = None
    entry_date:   str   | None = None
    gap_top:      float | None = None
    gap_bottom:   float | None = None
    entry_close:  float | None = None
    atr_at_entry: float | None = None
    bars_held:    int          = 0

    close_reason:        str   | None = None
    close_price_at_exit: float | None = None
    last_signal_type:    str   | None = None
    last_signal_date:    str   | None = None
    last_bar_date:       str   | None = None
    signal_action:       str   | None = None

    # ── Collections ────────────────────────────────────────────────────────────
    trades: list[Trade] = []
    acc = _make_accumulator() if summarize_trades else None

    # ── Helpers (inner functions, closure over state vars) ─────────────────────

    def _bar_date_str(i: int) -> str:
        return df.index[i].tz_convert("Asia/Tokyo").date().isoformat()

    def _clean_exit() -> None:
        """Reset tất cả position state về None sau exit.
        bars_held KHÔNG reset — giữ để ghi vào trade record TRƯỚC khi gọi hàm này.
        """
        nonlocal direction, ts, tp_level, sl_level
        nonlocal entry_date, gap_top, gap_bottom, entry_close, atr_at_entry
        direction    = None
        ts           = None
        tp_level     = None
        sl_level     = None
        entry_date   = None
        gap_top      = None
        gap_bottom   = None
        entry_close  = None
        atr_at_entry = None

    def _record(exit_price: float, reason: str, bh: int, bd: str) -> None:
        """Ghi trade vào trades list hoặc accumulator."""
        nonlocal close_reason, close_price_at_exit
        close_reason        = reason
        close_price_at_exit = exit_price

        if return_trades:
            dir_ = direction   # snapshot trước khi _clean_exit() có thể được gọi
            actual = _apply_slippage(exit_price, dir_, reason, cfg)
            trades.append(Trade(
                entry_date        = entry_date,
                exit_date         = bd,
                direction         = dir_,   # snapshot
                entry_price       = entry_close,
                exit_price        = exit_price,
                actual_exit_price = actual,
                fee_per_trade     = cfg.fee_per_trade,
                close_reason      = reason,
                bars_held         = bh,
                gap_top           = gap_top,
                gap_bottom        = gap_bottom,
                atr_at_entry      = atr_at_entry,
                tp_level          = tp_level,
                sl_level          = sl_level,
            ))
        elif summarize_trades:
            dir_ = direction   # snapshot
            _accumulate(
                acc, exit_price, reason, bh,
                dir_, entry_close, atr_at_entry, cfg,
            )

    def _open(sig: str, meta: dict, atr_i: float, bd: str) -> None:
        """Mở position mới, update tất cả state vars."""
        nonlocal direction, ts, tp_level, sl_level
        nonlocal entry_date, gap_top, gap_bottom, entry_close, atr_at_entry
        nonlocal bars_held, close_reason, close_price_at_exit

        pos = _open_position(sig, meta, atr_i, bd, cfg)
        direction    = pos["direction"]
        entry_date   = pos["entry_date"]
        entry_close  = pos["entry_price"]
        gap_top      = pos["gap_top"]
        gap_bottom   = pos["gap_bottom"]
        atr_at_entry = pos["atr_at_entry"]
        tp_level     = pos["tp_level"]
        sl_level     = pos["sl_level"]
        ts           = pos["trailing_stop"]
        bars_held    = 0      # entry bar = 0
        # KHÔNG reset close_reason/close_price_at_exit:
        # chúng lưu reason của trade VỪA ĐÓNG, giúp caller biết lý do exit
        # ngay cả khi position mới đã mở (TP_HIT + new signal cùng bar)

    # Precompute bar dates (1 lần, không convert trong loop)
    bar_dates = [d.isoformat() for d in df.index.tz_convert("Asia/Tokyo").date]

    # ── Main loop ──────────────────────────────────────────────────────────────
    start = max(cfg.atr_period, 3)

    for i in range(start, len(df)):
        atr_i    = atr_series.iloc[i]
        bd       = bar_dates[i]   # precomputed — không tz_convert trong loop
        last_bar_date = bd

        # Context engine cung cấp cho signal_fn
        context: SignalContext = {
            "atr": float(atr_i) if not pd.isna(atr_i) else None
        }

        # Skip bar nếu ATR chưa ready (không thể tính TS/TP/SL)
        if pd.isna(atr_i) or atr_i <= 0:
            continue

        bar = df.iloc[i]

        # ══ STEP 1: TP/SL/TS check TRƯỚC khi detect signal ══
        if direction is not None:
            bars_held += 1   # entry bar = 0, tăng từ bar sau
            # bars_held includes current bar (exit happens within this bar)
            # Consistent với Pine Script: exit bar được tính vào holding period

            ts = _ratchet_ts(bar, direction, ts, float(atr_i), cfg)

            exit_reason, exit_price = _check_exit(
                bar, direction, tp_level, sl_level, ts, cfg
            )

            if exit_reason is not None:
                _record(exit_price, exit_reason, bars_held, bd)
                _clean_exit()

        # ══ STEP 2: Detect signal ══
        sig, meta = signal_fn(df, i, context)
        if sig is not None:
            last_signal_type = sig        # giữ signal gần nhất, không overwrite với None
            last_signal_date = bd

        if sig is not None:
            # signal_action chỉ set khi có signal — không reset nếu không có
            if direction is not None:
                # Đang HOLDING (chưa exit ở step 1)
                if sig == direction:
                    signal_action = "IGNORE"  # v2: no TS reset
                else:
                    # Đảo chiều → REVERSED
                    signal_action = "REVERSE"
                    _record(float(bar["close"]), "REVERSED", bars_held, bd)
                    _clean_exit()
                    _open(sig, meta, float(atr_i), bd)
            else:
                # Không holding (vừa exit step 1 hoặc chưa có position)
                # NOTE: allows exit + new entry in same bar (Pine-style execution)
                # Exit và entry cùng close bar — không realistic với gap/low liquidity
                # nhưng nhất quán với Pine Script behavior
                signal_action = "OPEN"
                _open(sig, meta, float(atr_i), bd)

    # ── Build final PositionState ──────────────────────────────────────────────
    state = PositionState(
        new_signal_detected   = last_signal_type is not None,
        signal_action         = signal_action,
        is_holding            = direction is not None,
        direction             = direction,
        entry_date            = entry_date,
        gap_top               = gap_top,
        gap_bottom            = gap_bottom,
        entry_close           = entry_close,
        tp_level              = tp_level,
        sl_level              = sl_level,
        trailing_stop         = ts,
        atr_at_entry          = atr_at_entry,
        bars_held             = bars_held,
        close_reason          = close_reason,
        close_price_at_exit   = close_price_at_exit,
        last_signal_type      = last_signal_type,
        last_signal_date      = last_signal_date,
        last_checked_bar_date = last_bar_date,
    )

    if return_trades:    return state, trades
    if summarize_trades: return state, TradesSummary.from_accumulator(acc)
    return state


# ══════════════════════════════════════════════════════════════════════════════
# Phase 5 — check_latest_bar
# ══════════════════════════════════════════════════════════════════════════════

def check_latest_bar(
    df:            pd.DataFrame | None,
    position_row:  dict,
    cfg:           PositionConfig,
    signal_fn:     SignalFn | None = None,
    strategy_name: str | None = None,
) -> PositionState:
    """
    Check bar mới nhất cho 1 HOLDING position. Không ghi DB — caller làm.

    Dùng bởi position_monitor.run_normal() mỗi lần chạy hàng tháng.
    Khác với scan_full_history (full history replay):
        - Chỉ process 1 bar (bar cuối)
        - Restore state từ DB row (không replay từ đầu)
        - Trả PositionState để caller quyết định DB action

    Guards (trả _no_update_state thay vì raise):
        "cache_unavailable": df là None hoặc rỗng
        "no_new_bar":        bar cuối không mới hơn last_checked_at
        "atr_not_ready":     ATR chưa tính được tại bar cuối

    bars_held convention:
        position_row["bars_held"] là số bar đã hold TRƯỚC bar này.
        Hàm này check bar MỚI → bars_held += 1.

    Args:
        df:            Full DataFrame từ cache (cần đủ bars để tính ATR).
                       None = cache chưa có → guard "cache_unavailable".
        position_row:  Dict từ DB row. Required keys:
                           direction, trailing_stop, tp_level, sl_level,
                           entry_close, atr_at_entry, bars_held,
                           last_checked_at (YYYY-MM-DD JST string hoặc None)
        cfg:           PositionConfig.
        signal_fn:     None → make_imfvg_detector(cfg).
        strategy_name: Override cho log.
    """
    # Guard 1: cache unavailable
    if df is None or df.empty:
        log.debug("check_latest_bar: df unavailable → cache_unavailable")
        return _no_update_state(position_row, "cache_unavailable")

    # Guard 2: no new bar
    last_checked = position_row.get("last_checked_at")

    # TZ-safe conversion: yfinance đôi khi trả tz-naive index
    _last_idx = df.index[-1]
    if _last_idx.tz is None:
        _last_idx = _last_idx.tz_localize("UTC").tz_convert("Asia/Tokyo")
    else:
        _last_idx = _last_idx.tz_convert("Asia/Tokyo")
    bar_date = _last_idx.date().isoformat()

    # Dùng date object thay vì string comparison để tránh format mismatch
    _bar_d  = date.fromisoformat(bar_date)
    _last_d = date.fromisoformat(last_checked) if last_checked else None
    if _last_d is not None and _bar_d <= _last_d:
        log.debug(
            "check_latest_bar: bar_date=%s <= last_checked=%s → no_new_bar",
            bar_date, last_checked,
        )
        return _no_update_state(position_row, "no_new_bar")

    # Compute ATR
    if len(df) < cfg.atr_period + 1:
        return _no_update_state(position_row, "atr_not_ready")

    atr_series = compute_atr(
        df["high"], df["low"], df["close"], period=cfg.atr_period
    )
    i = len(df) - 1
    atr_i = atr_series.iloc[i]

    # Guard 3: ATR not ready
    if pd.isna(atr_i) or atr_i <= 0:
        log.debug("check_latest_bar: atr_i=%s → atr_not_ready", atr_i)
        return _no_update_state(position_row, "atr_not_ready")

    # Resolve signal_fn
    if signal_fn is None:
        signal_fn = make_imfvg_detector(cfg)
    name = _resolve_strategy_name(signal_fn, strategy_name)
    log.debug("check_latest_bar: strategy=%s bar=%s", name, bar_date)

    # Restore position state từ DB row
    direction    = position_row["direction"]
    ts           = float(position_row["trailing_stop"])
    tp_level     = float(position_row["tp_level"])
    sl_level     = float(position_row["sl_level"])
    entry_close  = float(position_row["entry_close"])
    atr_at_entry = float(position_row["atr_at_entry"])
    bars_held    = int(position_row.get("bars_held", 0)) + 1   # bar mới

    bar = df.iloc[i]

    # Context
    context: SignalContext = {
        "atr": float(atr_i),
    }

    # Ratchet TS
    ts = _ratchet_ts(bar, direction, ts, float(atr_i), cfg)

    # ══ STEP 1: TP/SL/TS check ══
    close_reason        = None
    close_price_at_exit = None
    signal_action       = None

    exit_reason, exit_price = _check_exit(
        bar, direction, tp_level, sl_level, ts, cfg
    )

    if exit_reason is not None:
        close_reason        = exit_reason
        close_price_at_exit = exit_price
        # After exit: direction = None (no longer holding)
        direction_after = None
        tp_after = sl_after = ts_after = None
        entry_after = gap_top_after = gap_bottom_after = atr_after = None
        entry_date_after = None
        bars_held_after  = bars_held
    else:
        direction_after  = direction
        tp_after         = tp_level
        sl_after         = sl_level
        ts_after         = ts
        entry_after      = entry_close
        gap_top_after    = position_row.get("gap_top")
        gap_bottom_after = position_row.get("gap_bottom")
        atr_after        = atr_at_entry
        entry_date_after = position_row.get("entry_date")
        bars_held_after  = bars_held

    # ══ STEP 2: Signal detection ══
    sig, meta = signal_fn(df, i, context)

    last_signal_type = position_row.get("last_signal_type")
    last_signal_date = position_row.get("last_signal_date")

    if sig is not None:
        last_signal_type = sig
        last_signal_date = bar_date

        if direction_after is not None:
            # Còn holding sau step 1
            if sig == direction_after:
                signal_action = "IGNORE"
            else:
                # Reverse
                signal_action   = "REVERSE"
                close_reason    = "REVERSED"
                close_price_at_exit = float(bar["close"])
                # Open new position
                # KeyError nếu meta thiếu "entry_price" = programming error trong signal_fn
                try:
                    pos = _open_position(sig, meta, float(atr_i), bar_date, cfg)
                except KeyError as e:
                    raise KeyError(
                        f"signal_fn {getattr(signal_fn, '__name__', '?')} "
                        f"trả meta thiếu key {e} khi signal={sig}. "
                        f"meta phải có 'entry_price' khi signal != None."
                    ) from e
                direction_after  = pos["direction"]
                entry_after      = pos["entry_price"]
                gap_top_after    = pos["gap_top"]
                gap_bottom_after = pos["gap_bottom"]
                atr_after        = pos["atr_at_entry"]
                tp_after         = pos["tp_level"]
                sl_after         = pos["sl_level"]
                ts_after         = pos["trailing_stop"]
                entry_date_after = pos["entry_date"]
                bars_held_after  = 0
        else:
            # Không holding (vừa exit step 1)
            # NOTE: Pine-style — exit + entry cùng bar
            signal_action = "OPEN"
            try:
                pos = _open_position(sig, meta, float(atr_i), bar_date, cfg)
            except KeyError as e:
                raise KeyError(
                    f"signal_fn {getattr(signal_fn, '__name__', '?')} "
                    f"trả meta thiếu key {e} khi signal={sig}. "
                    f"meta phải có 'entry_price' khi signal != None."
                ) from e
            direction_after  = pos["direction"]
            entry_after      = pos["entry_price"]
            gap_top_after    = pos["gap_top"]
            gap_bottom_after = pos["gap_bottom"]
            atr_after        = pos["atr_at_entry"]
            tp_after         = pos["tp_level"]
            sl_after         = pos["sl_level"]
            ts_after         = pos["trailing_stop"]
            entry_date_after = pos["entry_date"]
            bars_held_after  = 0

    return PositionState(
        new_signal_detected   = sig is not None,
        signal_action         = signal_action,
        is_holding            = direction_after is not None,
        direction             = direction_after,
        entry_date            = entry_date_after,
        gap_top               = gap_top_after,
        gap_bottom            = gap_bottom_after,
        entry_close           = entry_after,
        tp_level              = tp_after,
        sl_level              = sl_after,
        trailing_stop         = ts_after,
        atr_at_entry          = atr_after,
        bars_held             = bars_held_after,
        close_reason          = close_reason,
        close_price_at_exit   = close_price_at_exit,
        last_signal_type      = last_signal_type,
        last_signal_date      = last_signal_date,
        last_checked_bar_date = bar_date,
    )


# ══════════════════════════════════════════════════════════════════════════════
# Phase 6 — backtest_symbol + backtest_portfolio
# ══════════════════════════════════════════════════════════════════════════════

def backtest_symbol(
    symbol:        str,
    cfg:           PositionConfig,
    timeframe:     str = "1MO",
    atr_cache:     pd.Series | None = None,
    signal_fn:     SignalFn | None = None,
    strategy_name: str | None = None,
) -> dict | None:
    """
    Backtest 1 symbol từ cache parquet. Không fetch Yahoo.

    Args:
        symbol:    Mã chứng khoán, VD "7203.T".
        cfg:       PositionConfig.
        timeframe: "1MO" | "1WK" | "1D".
        atr_cache: Precomputed ATR Series. None → compute từ cfg.atr_period.
                   Dùng khi batch nhiều configs cùng atr_period để tránh recompute.
        signal_fn: None → make_imfvg_detector(cfg).
        strategy_name: Override tên strategy cho log/DB.

    Returns:
        dict với metrics hoặc None nếu:
            - Cache không tồn tại
            - Không đủ bars
            - 0 trades trong lịch sử

    Keys trả về:
        symbol, strategy, n_trades, win_rate, avg_rr, expectancy_rr,
        avg_net_pnl, max_drawdown, sharpe, calmar, avg_bars,
        pct_tp, pct_sl, pct_ts, pct_reversed
    """
    # Import lazily để không circular import khi test
    try:
        from data_provider.cache import read_cache
        df = read_cache(symbol, timeframe)
    except Exception as e:
        log.debug("backtest_symbol: cannot read cache %s/%s: %s", symbol, timeframe, e)
        df = None

    if df is None or df.empty:
        log.debug("backtest_symbol: no cache data for %s/%s — skip", symbol, timeframe)
        return None

    # Resolve name trước khi scan
    if signal_fn is None:
        signal_fn = make_imfvg_detector(cfg)
    name = _resolve_strategy_name(signal_fn, strategy_name)

    result = scan_full_history(
        df, cfg,
        summarize_trades = True,
        atr_series       = atr_cache,
        signal_fn        = signal_fn,
        strategy_name    = name,
    )

    if result is None:
        return None

    _, summary = result

    if summary.n_trades == 0:
        log.debug(
            "backtest_symbol: 0 trades for %s/%s strategy=%s "
            "(symbol has data but no signals matched cfg)",
            symbol, timeframe, name,
        )
        return None

    n = summary.n_trades
    pct_tp       = summary.n_tp       / n
    pct_sl       = summary.n_sl       / n
    pct_ts       = summary.n_ts       / n
    pct_reversed = summary.n_reversed / n

    return {
        "symbol":        symbol,
        "strategy":      name,
        "n_trades":      n,
        "win_rate":      summary.win_rate,
        "avg_rr":        summary.avg_rr,
        "expectancy_rr": summary.expectancy,
        "avg_net_pnl":   summary.avg_net_pnl,
        "max_drawdown":  summary.max_drawdown,
        "sharpe":        summary.sharpe,
        "calmar":        summary.calmar,
        "avg_bars":      summary.avg_bars,
        "pct_tp":        pct_tp,
        "pct_sl":        pct_sl,
        "pct_ts":        pct_ts,
        "pct_reversed":  pct_reversed,
    }


def backtest_portfolio(
    symbols:       list[str],
    cfg:           PositionConfig,
    timeframe:     str = "1MO",
    weight_by:     str = "trades",
    signal_fn:     SignalFn | None = None,
    strategy_name: str | None = None,
) -> dict:
    """
    Aggregate backtest metrics across nhiều symbols.

    Args:
        symbols:       List mã chứng khoán.
        cfg:           PositionConfig.
        timeframe:     "1MO" | "1WK" | "1D".
        weight_by:     "trades" (default) → weighted avg theo n_trades.
                       "symbol" → equal weight per symbol.
                       Raises ValueError nếu giá trị khác.
        signal_fn:     None → make_imfvg_detector(cfg).
        strategy_name: Override tên.

    Returns:
        dict với portfolio-level metrics. Always returns dict (never None).
        Nếu 0 symbols có data → metrics đều 0.

    Portfolio keys:
        portfolio_win_rate, portfolio_avg_rr, portfolio_expectancy_rr,
        portfolio_avg_net_pnl, portfolio_total_net_pnl, portfolio_avg_bars,
        pct_tp, pct_sl, pct_ts, pct_reversed,
        total_trades, n_symbols_with_data, n_symbols_no_data,
        weight_by, strategy

    WARNING — Portfolio MDD:
        Hàm này KHÔNG tính portfolio max_drawdown.
        Per-symbol MDD từ backtest_symbol là MDD của trades sequential (đúng).
        Portfolio MDD cross-symbol cần time-sorted equity curve (v3):
            trades từ tất cả symbols → sort by exit_date → cumulative P&L → MDD.
        DO NOT approximate bằng cách cộng per-symbol MDD —
        trades overlap in time → con số sẽ sai.
    """
    if weight_by not in ("trades", "symbol"):
        raise ValueError(
            f"weight_by phải là 'trades' hoặc 'symbol', nhận: {weight_by!r}"
        )

    # Resolve signal_fn + name 1 lần cho toàn portfolio
    if signal_fn is None:
        signal_fn = make_imfvg_detector(cfg)
    name = _resolve_strategy_name(signal_fn, strategy_name)

    results = []
    n_no_data = 0

    for sym in symbols:
        r = backtest_symbol(
            sym, cfg, timeframe,
            signal_fn     = signal_fn,
            strategy_name = name,
        )
        if r is None:
            n_no_data += 1
            log.debug("backtest_portfolio: %s skipped (no cache or 0 trades)", sym)
        else:
            results.append(r)

    n_with_data = len(results)
    total_trades = sum(r["n_trades"] for r in results)

    _empty = {
        "portfolio_win_rate":       0.0,
        "portfolio_avg_rr":         0.0,
        "portfolio_expectancy_rr":  0.0,
        "portfolio_avg_net_pnl":    0.0,
        "portfolio_total_net_pnl":     0.0,   # sum(avg_net_pnl * n_trades) — not normalized by capital
        "portfolio_avg_bars":       0.0,
        "pct_tp":                   0.0,
        "pct_sl":                   0.0,
        "pct_ts":                   0.0,
        "pct_reversed":             0.0,
        "total_trades":             0,
        "n_symbols_with_data":      n_with_data,
        "n_symbols_no_data":        n_no_data,
        "weight_by":                weight_by,
        "strategy":                 name,
    }

    if not results or total_trades == 0:
        return _empty

    def _wavg(key: str) -> float:
        """
        Weighted average của metric theo weight_by.

        NOTE: Sharpe và Calmar được averaged per-symbol — KHÔNG phải true
        portfolio Sharpe/Calmar. True portfolio Sharpe cần raw trade returns
        từ tất cả symbols rồi recompute (v3). Dùng giá trị này chỉ để
        compare configs relative với nhau, không dùng để report absolute.
        """
        if weight_by == "trades":
            # Weighted by n_trades — đúng cho tính win_rate, avg_rr tổng
            total_w = total_trades
            if total_w == 0:
                return 0.0
            return sum(r[key] * r["n_trades"] for r in results) / total_w
        else:
            # Equal weight per symbol
            if n_with_data == 0:
                return 0.0
            return sum(r[key] for r in results) / n_with_data

    return {
        "portfolio_win_rate":          _wavg("win_rate"),
        "portfolio_avg_rr":            _wavg("avg_rr"),
        "portfolio_expectancy_rr":     _wavg("expectancy_rr"),
        "portfolio_avg_net_pnl":       _wavg("avg_net_pnl"),
        "portfolio_total_net_pnl":     sum(r["avg_net_pnl"] * r["n_trades"] for r in results),
        "portfolio_avg_bars":          _wavg("avg_bars"),
        "pct_tp":                      _wavg("pct_tp"),
        "pct_sl":                      _wavg("pct_sl"),
        "pct_ts":                      _wavg("pct_ts"),
        "pct_reversed":                _wavg("pct_reversed"),
        "total_trades":                total_trades,
        "n_symbols_with_data":         n_with_data,
        "n_symbols_no_data":           n_no_data,
        "weight_by":                   weight_by,
        "strategy":                    name,
    }


# ══════════════════════════════════════════════════════════════════════════════
# Phase 7 — Database Layer
# ══════════════════════════════════════════════════════════════════════════════

import sqlite3
from datetime import datetime, timezone
from pathlib import Path

POSITIONS_DB_PATH = Path("data/state.db")


def _get_db_conn(db_path: Path = POSITIONS_DB_PATH) -> sqlite3.Connection:
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    # WAL mode: concurrent read/write, ít lock hơn default journal mode
    conn.execute("PRAGMA journal_mode=WAL;")
    conn.execute("PRAGMA synchronous=NORMAL;")
    return conn


def init_positions_db(timeframe: str, conn: sqlite3.Connection) -> None:
    """
    Tạo bảng positions_{tf} và position_history_{tf} nếu chưa tồn tại.
    Idempotent — gọi lại không ảnh hưởng data hiện có.

    positions_{tf} schema:
        entry_close: giữ tên column này trong DB (không đổi thành entry_price)
                     để tránh migration. Python dict dùng "entry_price",
                     DB column dùng "entry_close" — map tại INSERT/SELECT.
        strategy_name: nullable — backward compat với positions không có strategy.
        gap_top/gap_bottom: nullable — optional, IMFVG-specific.
        status: "HOLDING" | "CLOSED"

    Partial unique index (SQLite 3.8.9+):
        Chỉ 1 HOLDING position per symbol — không thể có 2 HOLDING cùng lúc.
        CLOSED positions không bị ảnh hưởng bởi index này.

    position_history_{tf}:
        Audit trail cho mọi closed trade. Source từ DB row (không từ state).
        Immutable sau khi insert.
    """
    tf = timeframe
    conn.executescript(f"""
        CREATE TABLE IF NOT EXISTS positions_{tf} (
            id                  INTEGER PRIMARY KEY AUTOINCREMENT,
            symbol              TEXT    NOT NULL,
            strategy_name       TEXT,
            direction           TEXT    NOT NULL,
            entry_date          TEXT    NOT NULL,
            gap_top             REAL,
            gap_bottom          REAL,
            entry_close         REAL    NOT NULL,
            tp_level            REAL    NOT NULL,
            sl_level            REAL    NOT NULL,
            trailing_stop       REAL    NOT NULL,
            atr_at_entry        REAL    NOT NULL,
            status              TEXT    NOT NULL DEFAULT 'HOLDING',
            bars_held           INTEGER NOT NULL DEFAULT 0,
            close_price_at_exit REAL,
            last_signal_type    TEXT,
            last_signal_date    TEXT,
            last_checked_at     TEXT,
            created_at          DATETIME NOT NULL,
            closed_at           DATETIME
        );

        CREATE UNIQUE INDEX IF NOT EXISTS idx_unique_holding_{tf}
            ON positions_{tf}(symbol)
            WHERE status = 'HOLDING';

        CREATE INDEX IF NOT EXISTS idx_positions_{tf}_symbol_status
            ON positions_{tf}(symbol, status);

        CREATE TABLE IF NOT EXISTS position_history_{tf} (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            symbol          TEXT    NOT NULL,
            strategy_name   TEXT,
            direction       TEXT    NOT NULL,
            entry_date      TEXT    NOT NULL,
            exit_date       TEXT    NOT NULL,
            entry_price     REAL    NOT NULL,
            exit_price      REAL    NOT NULL,
            close_reason    TEXT    NOT NULL,
            bars_held       INTEGER NOT NULL,
            atr_at_entry    REAL    NOT NULL,
            tp_level        REAL    NOT NULL,
            sl_level        REAL    NOT NULL,
            gap_top         REAL,
            gap_bottom      REAL,
            created_at      DATETIME NOT NULL
        );

        CREATE INDEX IF NOT EXISTS idx_position_history_{tf}_symbol
            ON position_history_{tf}(symbol);

        CREATE INDEX IF NOT EXISTS idx_position_history_{tf}_exit_date
            ON position_history_{tf}(exit_date);
    """)
    log.debug("init_positions_db done for timeframe=%s", tf)


def _get_holding_position(
    conn:   sqlite3.Connection,
    tf:     str,
    symbol: str,
) -> dict | None:
    """
    Lấy HOLDING position cho symbol. Trả None nếu không có.
    Dùng partial unique index → tối đa 1 row.
    """
    row = conn.execute(
        f"SELECT * FROM positions_{tf} WHERE symbol = ? AND status = 'HOLDING'",
        (symbol,),
    ).fetchone()
    return dict(row) if row else None


def _close_and_log(
    conn:         sqlite3.Connection,
    tf:           str,
    position_id:  int,
    position_row: dict,
    exit_date:    str,
    close_reason: str,
    exit_price:   float,
    bars_held:    int,
) -> None:
    """
    Đóng position + ghi vào history. Atomic — caller phải gọi trong `with conn:`.

    SOURCE OF TRUTH: position_row (DB row), KHÔNG từ PositionState.
    Lý do: PositionState có thể đã được update cho position MỚI
    (ví dụ: REVERSED → state đã mang direction mới). DB row là record
    của position VỪA ĐÓNG, luôn đúng.

    Args:
        position_row: dict từ _get_holding_position() — data của position cũ.
        exit_date:    bar date khi exit (YYYY-MM-DD JST).
        exit_price:   fill price (trước slippage — DB lưu theoretical price).
        bars_held:    số bar đã hold (từ state.bars_held).
    """
    now_utc = datetime.now(timezone.utc).isoformat()

    # 1. Mark position as CLOSED
    conn.execute(
        f"""UPDATE positions_{tf}
               SET status = 'CLOSED',
                   close_price_at_exit = ?,
                   closed_at = ?
             WHERE id = ?""",
        (exit_price, now_utc, position_id),
    )

    # 2. Insert into history — source = position_row (DB), không từ state
    conn.execute(
        f"""INSERT INTO position_history_{tf}
            (symbol, strategy_name, direction, entry_date, exit_date,
             entry_price, exit_price, close_reason, bars_held,
             atr_at_entry, tp_level, sl_level, gap_top, gap_bottom, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (
            position_row["symbol"],
            position_row.get("strategy_name"),
            position_row["direction"],           # direction của position CŨ
            position_row["entry_date"],
            exit_date,
            position_row["entry_close"],         # entry_price từ DB column
            exit_price,
            close_reason,
            bars_held,
            position_row["atr_at_entry"],
            position_row["tp_level"],
            position_row["sl_level"],
            position_row.get("gap_top"),
            position_row.get("gap_bottom"),
            now_utc,
        ),
    )
    log.info(
        "position closed: %s %s %s → %s (bars=%d)",
        position_row["symbol"], position_row["direction"],
        close_reason, exit_price, bars_held,
    )


def _insert_position(
    conn:          sqlite3.Connection,
    tf:            str,
    symbol:        str,
    state:         PositionState,
    strategy_name: str,
) -> int:
    """
    Insert HOLDING position mới. Trả row id.

    Mapping:
        state.entry_close → DB column entry_close
        (Python dùng "entry_price", DB column giữ tên "entry_close")

    Raises:
        sqlite3.IntegrityError: nếu đã có HOLDING position cho symbol này
        (partial unique index vi phạm). Caller phải close cũ trước.
    """
    now_utc = datetime.now(timezone.utc).isoformat()
    cur = conn.execute(
        f"""INSERT INTO positions_{tf}
            (symbol, strategy_name, direction, entry_date,
             gap_top, gap_bottom, entry_close,
             tp_level, sl_level, trailing_stop, atr_at_entry,
             status, bars_held, last_signal_type, last_signal_date,
             last_checked_at, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'HOLDING', ?, ?, ?, ?, ?)""",
        (
            symbol,
            strategy_name,
            state.direction,
            state.entry_date,
            state.gap_top,
            state.gap_bottom,
            state.entry_close,        # entry_close column ← state.entry_close
            state.tp_level,
            state.sl_level,
            state.trailing_stop,
            state.atr_at_entry,
            state.bars_held,
            state.last_signal_type,
            state.last_signal_date,
            state.last_checked_bar_date,
            now_utc,
        ),
    )
    log.info(
        "position opened: %s %s entry=%.4f tp=%.4f sl=%.4f",
        symbol, state.direction, state.entry_close or 0,
        state.tp_level or 0, state.sl_level or 0,
    )
    return cur.lastrowid


def _update_position(
    conn:        sqlite3.Connection,
    tf:          str,
    position_id: int,
    state:       PositionState,
) -> None:
    """
    Update HOLDING position — trailing_stop, bars_held, last_signal, last_checked.
    Không update entry fields (immutable sau khi INSERT).
    """
    now_utc = datetime.now(timezone.utc).isoformat()
    conn.execute(
        f"""UPDATE positions_{tf}
               SET trailing_stop    = ?,
                   bars_held        = ?,
                   last_signal_type = ?,
                   last_signal_date = ?,
                   last_checked_at  = ?
             WHERE id = ?""",
        (
            state.trailing_stop,
            state.bars_held,
            state.last_signal_type,
            state.last_signal_date,
            state.last_checked_bar_date,
            position_id,
        ),
    )
    log.debug("position updated: id=%d bars_held=%d ts=%.4f",
              position_id, state.bars_held, state.trailing_stop or 0)


def _process_symbol(
    conn:          sqlite3.Connection,
    tf:            str,
    symbol:        str,
    state:         PositionState,
    bar_date:      str,
    strategy_name: str,
) -> None:
    """
    Atomic: quyết định DB action dựa trên PositionState. 1 transaction.

    Logic:
        existing = _get_holding_position(symbol)

        Case A: có exit (close_reason set) + có signal mới (OPEN/REVERSE):
            _close_and_log(existing)
            _insert_position(new direction)

        Case B: có exit, không có signal mới:
            _close_and_log(existing)

        Case C: không exit + có signal OPEN (không có existing):
            _insert_position(new direction)

        Case D: không exit + still HOLDING (existing vẫn đó):
            _update_position(existing.id)

        Case E: không exit, không holding, không signal → no-op

    Tất cả trong 1 `with conn:` block → SQLite auto-commit hoặc rollback.

    Args:
        state:    PositionState từ check_latest_bar() hoặc scan_full_history().
        bar_date: Date string của bar vừa process (last_checked_bar_date).
        strategy_name: Tên strategy để lưu vào DB.
    """
    with conn:
        existing = _get_holding_position(conn, tf, symbol)

        has_exit    = state.close_reason is not None
        has_new_pos = state.is_holding and state.signal_action in ("OPEN", "REVERSE")

        # Case A + B: có exit — PHẢI close trước, insert sau
        # INVARIANT: close existing trước khi insert mới (UNIQUE index on HOLDING)
        if has_exit and existing:
            # Fix: bars_held của position CŨ = existing["bars_held"] + 1 (bar hiện tại)
            # Không dùng state.bars_held vì sau REVERSE state đã reset về 0
            bars_held_old = existing["bars_held"] + 1
            _close_and_log(
                conn, tf,
                position_id  = existing["id"],
                position_row = existing,
                exit_date    = bar_date,
                close_reason = state.close_reason,
                exit_price   = state.close_price_at_exit,
                bars_held    = bars_held_old,
            )

        # Case A + C: mở position mới — MUST be after close (UNIQUE index)
        if has_new_pos:
            try:
                _insert_position(conn, tf, symbol, state, strategy_name)
            except sqlite3.IntegrityError:
                log.error(
                    "_process_symbol: duplicate HOLDING for %s — "
                    "existing not closed before insert. Skipping insert.",
                    symbol,
                )

        # Case D: update holding (không có exit, vẫn holding, không có signal mới)
        elif existing and not has_exit and state.is_holding:
            _update_position(conn, tf, existing["id"], state)

        # Case E: no-op (không holding, không exit, không signal)