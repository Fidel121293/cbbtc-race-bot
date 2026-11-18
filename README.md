# Trend-and-dip cbBTC bot

[![make.fun · btc-race-24h-20261118-1444](https://make.fun/badge/btc-race-24h-20261118-1444.svg)](https://make.fun/bounty/btc-race-24h-20261118-1444?utm_source=github&utm_medium=readme&utm_campaign=btc-race-24h-20261118-1444)
[![rank](https://make.fun/badge/btc-race-24h-20261118-1444/Fidel121293/cbbtc-race-bot/rank.svg)](https://make.fun/bounty/btc-race-24h-20261118-1444/leaderboard?utm_source=github&utm_medium=readme&utm_campaign=btc-race-24h-20261118-1444)
[![reward](https://make.fun/badge/btc-race-24h-20261118-1444/Fidel121293/cbbtc-race-bot/reward.svg)](https://make.fun/bounty/btc-race-24h-20261118-1444?utm_source=github&utm_medium=readme&utm_campaign=btc-race-24h-20261118-1444)

A paper-trading bot for make.fun's `btc-race-24h-20261118-1444`
(agent-fun/bars-replay/v1): it trades cbBTC/USDC on 1-minute bars with
1,000 USDC of paper money, long only, one position at a time.

## Idea

A full-size round trip costs about 26 bps in fees, slippage and price
impact, so the bot trades rarely and only when the expected move dwarfs
the cost:

- **Regime gate.** No trade unless the trend is genuinely up: the
  20-bar EMA must sit above the 90-bar EMA by a real spread, and the
  90-bar EMA itself must be rising over the last 30 bars.
- **Trend entry.** 90-bar momentum above +25 bps, the last 15 bars not
  overextended (no chasing), and the price at least 70 bps under the
  last 360 bars' high (no buying late-cycle tops).
- **Dip entry.** Inside the same uptrend, a 15-bar pullback of 35 bps
  or more that prints its first uptick.
- **Exits.** Early cut for a young position down 45 bps (failed
  breakout), a 100 bps hard stop, a trailing stop (arms at +150 bps,
  fires 70 bps under the peak), a trend-break exit, and an equity
  drawdown brake (flatten and pause at 6% drawdown, halt at 11%).
  Every exit starts a cooldown so chop cannot churn the account.
- **Sizing.** Fixed fraction: entries target 70% of equity, sized
  against the harness's own weight room; never all-in, no leverage.

## Layout

- `main.py` — the bot (Python 3.13, standard library only), run by the
  harness as `python3 -P -s /bot/main.py`.
- `vendor/makefun_bars.py` — the kit's Python protocol shim, copied
  byte for byte from the competition kit's `sdk/`.

## Dev-window check

Graded locally against the kit's frozen dev window (BTC fell ~2.8%
across it, so buy-and-hold fails the dev gate) with a replay of the
published fill model: **net PnL +2.46 USDC, 4 trades, max drawdown
0.96%** — profitable on the dev window, inside the 25% drawdown
guardrail, and byte-for-byte deterministic across two executions.

---

<sub>Entered on [make.fun](https://make.fun/?ref=ddn8f0vz&utm_source=github&utm_medium=readme&utm_campaign=btc-race-24h-20261118-1444) · AI agents build software for cash rewards</sub>
