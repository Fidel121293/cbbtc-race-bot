"""Trend-and-dip BTC bot for agent-fun/bars-replay/v1 (make.fun btc-race).

Strategy, in one paragraph: stay in cash unless the tape says otherwise.
Two entries, both only inside a confirmed uptrend (fast EMA above the
slow one by a real spread, the slow one itself rising): (1) a trend entry
when the trailing 90-bar momentum is positive enough to clear the ~26 bps
a round trip costs, the last 15 bars are not overextended, and the price
is not hugging the last 360 bars' high; and (2) a dip entry when a sharp
15-bar pullback inside that same uptrend prints its first uptick. Exits
are defensive: an early cut for a young position that sags (a failed
breakout), a hard stop-loss under cost, a trailing stop that arms after a
gain, a trend-break exit when the fast EMA falls back under the slow one
with negative momentum, and an equity drawdown brake that flattens and
pauses the bot after a bad stretch. Every exit is followed by a cooldown
so the bot cannot churn itself to death in chop. Position size is a
fixed fraction of equity (never all-in), one position at a time.

Run by the harness as: python3 -P -s /bot/main.py
"""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "vendor"))
import makefun_bars

SYMBOL = "CBBTC"
BPS = 10_000

# --- indicator lengths (1-minute bars) ---
FAST_LEN = 20
SLOW_LEN = 90
MOM_SHORT = 15
MOM_LONG = 90

# --- sizing ---
TARGET_BPS = 7_000          # aim to hold 70% of equity in the position
MIN_TRADE_BPS = 800         # never open for less than 8% of equity

# --- entries ---
TREND_MOM_MIN_BPS = 25      # 90-bar momentum needed for a trend entry
EXTENDED_MAX_BPS = 45       # ...but not after a 15-bar run hotter than this
RECENT_HIGH_BARS = 360      # no trend entries this close under the high of
NEAR_HIGH_BLOCK_BPS = 70    # the last 360 bars: late-cycle tops fade here
SPREAD_MIN_BPS = 6          # fast EMA must clear the slow one by this much
SLOW_RISE_BARS = 30         # slow EMA must be rising over this many bars
DIP_DROP_BPS = -35          # 15-bar fall that counts as a sharp pullback
DIP_TREND_TOL_BPS = 20      # dip entry may sit this far under the slow EMA

# --- exits ---
STOP_LOSS_BPS = 100         # hard stop under the position's cost
EARLY_FAIL_BARS = 40        # a young position down more than this...
EARLY_FAIL_BPS = 45         # ...was a failed breakout: cut it early
TRAIL_ARM_BPS = 150         # trailing stop arms after this gain
TRAIL_GIVE_BPS = 70         # ...and fires this far under the peak price
EXIT_COOLDOWN_BARS = 60     # bars to sit out after any ordinary exit
STOP_COOLDOWN_BARS = 120    # bars to sit out after a stop-loss
DD_BRAKE_BPS = 600          # equity drawdown from its peak: flatten + pause
DD_HALT_BPS = 1_100         # deeper drawdown: stop trading for the window
PAUSE_BARS = 180

# --- orders ---
BUY_SLIP_BPS = 250          # max_slippage_bps sent with buys
SELL_SLIP_BPS = 600         # exits get a wider cap so they actually fill
LATE_CUTOFF_SEQ = 1_400     # no fresh entries this late in a 1,440-bar window
MIN_NOTIONAL = 1_000_000    # the harness's 1 USDC floor, in quote units


class Bot:
    """All strategy state, derived only from the bars the harness sends."""

    def __init__(self):
        self.closes = []            # every close seen, oldest first
        self.slow_hist = []         # slow EMA after each bar, oldest first
        self.ema_fast = None
        self.ema_slow = None
        self.peak_equity = None
        self.peak_price_in_pos = None
        self.entry_seq = None
        self.cooldown_until = -1
        self.pause_until = -1
        self.halted = False

    # -- indicators ------------------------------------------------------
    def _push_close(self, price):
        self.closes.append(price)
        kf = 2.0 / (FAST_LEN + 1)
        ks = 2.0 / (SLOW_LEN + 1)
        if self.ema_fast is None:
            self.ema_fast = float(price)
            self.ema_slow = float(price)
        else:
            self.ema_fast += kf * (price - self.ema_fast)
            self.ema_slow += ks * (price - self.ema_slow)
        self.slow_hist.append(self.ema_slow)

    def _momentum_bps(self, lookback):
        """Close-to-close return over `lookback` bars in bps, or None."""
        if len(self.closes) <= lookback:
            return None
        then = self.closes[-1 - lookback]
        if then <= 0:
            return None
        return (self.closes[-1] - then) * BPS // then

    # -- orders ----------------------------------------------------------
    def _buy_to_target(self, state):
        room = state.weight_room(SYMBOL)
        want = state.equity * TARGET_BPS // BPS
        held = state.positions.get(SYMBOL)
        if held is not None:
            want -= state.value(SYMBOL, held.qty)
        spend = min(state.cash, room, want)
        if spend < MIN_NOTIONAL or spend * BPS < state.equity * MIN_TRADE_BPS:
            return []
        return [{"side": "buy", "symbol": SYMBOL,
                 "notional_quote_units": spend,
                 "max_slippage_bps": BUY_SLIP_BPS}]

    def _sell_all(self, state):
        held = state.positions.get(SYMBOL)
        if held is None or held.qty <= 0:
            return []
        return [{"side": "sell", "symbol": SYMBOL, "qty": held.qty,
                 "max_slippage_bps": SELL_SLIP_BPS}]

    # -- the per-batch decision ------------------------------------------
    def on_batch(self, state, batch):
        price = state.close(SYMBOL)
        if price is None:
            return []
        self._push_close(price)
        seq = state.seq

        if self.peak_equity is None or state.equity > self.peak_equity:
            self.peak_equity = state.equity
        drawdown_bps = 0
        if self.peak_equity:
            drawdown_bps = (self.peak_equity - state.equity) * BPS // self.peak_equity
        if drawdown_bps >= DD_HALT_BPS:
            self.halted = True

        held = state.positions.get(SYMBOL)
        holding = held is not None and held.qty > 0
        if holding:
            if self.entry_seq is None:
                self.entry_seq = seq
            if self.peak_price_in_pos is None or price > self.peak_price_in_pos:
                self.peak_price_in_pos = price
        else:
            self.peak_price_in_pos = None
            self.entry_seq = None

        if state.warming_up:
            return []

        # ---- exits first: protecting capital outranks every entry ----
        if holding:
            value_now = state.value(SYMBOL, held.qty)
            gain_bps = (value_now - held.cost) * BPS // held.cost if held.cost else 0
            if (self.entry_seq is not None
                    and seq - self.entry_seq <= EARLY_FAIL_BARS
                    and gain_bps <= -EARLY_FAIL_BPS):
                self.cooldown_until = seq + STOP_COOLDOWN_BARS
                return self._sell_all(state)
            if gain_bps <= -STOP_LOSS_BPS:
                self.cooldown_until = seq + STOP_COOLDOWN_BARS
                return self._sell_all(state)
            if drawdown_bps >= DD_BRAKE_BPS:
                self.pause_until = seq + PAUSE_BARS
                return self._sell_all(state)
            peak = self.peak_price_in_pos or price
            peak_gain_bps = (state.value(SYMBOL, held.qty, peak) - held.cost) * BPS // held.cost if held.cost else 0
            if peak_gain_bps >= TRAIL_ARM_BPS and price * BPS <= peak * (BPS - TRAIL_GIVE_BPS):
                self.cooldown_until = seq + EXIT_COOLDOWN_BARS
                return self._sell_all(state)
            mom_long = self._momentum_bps(MOM_LONG)
            if (self.ema_fast < self.ema_slow and mom_long is not None
                    and mom_long < 0):
                self.cooldown_until = seq + EXIT_COOLDOWN_BARS
                return self._sell_all(state)
            return []  # holding and no exit fired: stay the course

        # ---- entries, only when flat and allowed to trade ----
        if self.halted or seq < self.pause_until or seq < self.cooldown_until:
            return []
        if seq >= LATE_CUTOFF_SEQ or len(self.closes) <= SLOW_LEN:
            return []
        mom_long = self._momentum_bps(MOM_LONG)
        mom_short = self._momentum_bps(MOM_SHORT)
        if mom_long is None or mom_short is None:
            return []
        spread_bps = (self.ema_fast - self.ema_slow) * BPS // self.ema_slow
        slow_rising = len(self.slow_hist) > SLOW_RISE_BARS and \
            self.slow_hist[-1] > self.slow_hist[-1 - SLOW_RISE_BARS]
        trend_up = spread_bps >= SPREAD_MIN_BPS and slow_rising
        recent_high = max(self.closes[-RECENT_HIGH_BARS:])
        if (trend_up and price > self.ema_slow
                and mom_long >= TREND_MOM_MIN_BPS
                and mom_short <= EXTENDED_MAX_BPS
                and price * BPS <= recent_high * (BPS - NEAR_HIGH_BLOCK_BPS)):
            return self._buy_to_target(state)
        dipped = mom_short <= DIP_DROP_BPS and len(self.closes) >= 2 \
            and self.closes[-1] > self.closes[-2]
        near_trend = price * BPS >= self.ema_slow * (BPS - DIP_TREND_TOL_BPS)
        if trend_up and near_trend and dipped:
            return self._buy_to_target(state)
        return []


BOT = Bot()


def on_batch(state, batch):
    return BOT.on_batch(state, batch)


if __name__ == "__main__":
    makefun_bars.run(on_batch)
