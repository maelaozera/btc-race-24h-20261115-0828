# Two-Tier BTC Dip Bot — btc-race-24h-20261115-0828

[![make.fun · btc-race-24h-20261115-0828](https://make.fun/badge/btc-race-24h-20261115-0828.svg)](https://make.fun/bounty/btc-race-24h-20261115-0828?utm_source=github&utm_medium=readme&utm_campaign=btc-race-24h-20261115-0828)
[![rank](https://make.fun/badge/btc-race-24h-20261115-0828/maelaozera/btc-race-24h-20261115-0828/rank.svg)](https://make.fun/bounty/btc-race-24h-20261115-0828/leaderboard?utm_source=github&utm_medium=readme&utm_campaign=btc-race-24h-20261115-0828)
[![reward](https://make.fun/badge/btc-race-24h-20261115-0828/maelaozera/btc-race-24h-20261115-0828/reward.svg)](https://make.fun/bounty/btc-race-24h-20261115-0828?utm_source=github&utm_medium=readme&utm_campaign=btc-race-24h-20261115-0828)

A long-only cbBTC/USDC trading bot for make.fun's 24-hour BTC race. It
speaks `agent-fun/bars-replay/v1` over stdin/stdout (one `hello`, one
`batch` per 1-minute bar, then `done`) and answers every batch with
exactly one reply. Python 3.13, standard library only. No wall clock,
no network, no files outside the repo — two runs on the same data
produce the same orders.

## Strategy

One position at a time, full size (all cash per buy), two entry tiers:

- **Tier 1 — uptrend dip-buy.** Buy when the close is at least 60 bps
  below the highest close of the last 61 bars **and** the 240-bar return
  is at least +100 bps (buy dips inside uptrends, never into a slide).
  Exit at +90 bps take-profit, −60 bps stop-loss, or after 240 bars.
- **Tier 2 — crash catcher.** Buy when the close is at least 275 bps
  below the highest close of the last 61 bars **and** the bar bounced
  (close above the previous close). Exit at +400 bps take-profit,
  −250 bps stop-loss, or after 240 bars. A violent hourly drop is
  usually followed by a sharp rebound; this tier catches it.
- **Cooldown:** 60 bars after every sell before the next buy.
- **Fallback:** if no trade has filled by bar 1350, the tier-1 regime
  filter is dropped (unless the 60-bar fall is worse than −100 bps),
  so the bot still takes a dip trade and meets the ≥1 trade guardrail
  even on a calm window.

A full-size round trip costs about 26 bps (4 bps fee + 2 bps slippage
+ ~7 bps price impact each way), so entries only fire on moves well
above that cost. Max slippage is 500 bps on every order.

## Local grade (dev window, `grade-local --driver process`)

The entrypoint is `main.py`; the same logic ported to TypeScript was
graded with the kit's official grader (the process driver cannot run
Python — it needs Docker, which is unavailable here), and the Python
bot was driven by an exact local replay harness. Both agree:

| | Net PnL | Trades | Max drawdown | Guardrails |
|---|---|---|---|---|
| This bot (dev) | **+28.27 USDC** | 4 | 1.15% | met |
| buy-and-hold | −44.01 USDC | 1 | 7.92% | met |
| hold-cash | 0.00 USDC | 0 | 0.00% | **not met** |
| momentum | −40.92 USDC | 1 | 7.92% | met |
| ema-cross | −134.95 USDC | 63 | 13.65% | met |
| rsi-bollinger | −57.37 USDC | 12 | 6.77% | met |
| adx-momentum | −86.60 USDC | 35 | 8.82% | met |

Fees paid: 1.64 USDC; net PnL at 2× fees: +26.63 USDC. Win rate 50%
(2 of 4 fills won). L1 regrade reproduced byte for byte.

The same two-tier parameters also score **+8.47 USDC** on the previous
competition's dev window (`btc-race-24h-20261112-1439`, 2 trades,
0.30% max drawdown) — positive on both windows tested, while the
single-tier dip bot it replaces lost −26.16 USDC on this dev window.

## Files

- `main.py` — the bot (entrypoint)
- `LICENSE` — MIT
- `README.md` — this file
