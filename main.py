"""Two-tier BTC dip bot for make.fun btc-race-24h-20261115-0828.

Speaks agent-fun/bars-replay/v1 over stdin/stdout (NDJSON): the harness
sends one hello, then one batch per minute bar, then done. This bot
answers every batch with exactly one reply echoing its seq, carrying
zero or one orders. It never reads the wall clock, the environment or
the network; every decision comes from the bars in the batches, so two
runs on the same data produce the same orders.

Strategy (long-only, one position, full size, all cash per buy):
  * Tier 1, uptrend dip-buy: when the latest close is at least 60 bps
    below the highest close of the last 61 bars, and the 240-bar return
    is at least +100 bps (the market is in an uptrend, not a slide),
    buy CBBTC with all cash. Sell at +90 bps take-profit, -60 bps
    stop-loss, or after holding 240 bars.
  * Tier 2, crash catcher: when the latest close is at least 275 bps
    below the highest close of the last 61 bars, and the bar bounced
    (close above the previous close, so the fall is pausing), buy with
    all cash. Sell at +400 bps take-profit, -250 bps stop-loss, or
    after holding 240 bars. A violent hourly drop is usually followed
    by a sharp rebound; this tier catches that rebound.
  * After a sell, wait 60 bars before the next buy (cooldown).
  * Fallback: if no trade has filled by bar 1350, the tier-1 regime
    filter is dropped (provided the 60-bar return is no worse than
    -100 bps, so the market is not in free fall), so the bot still
    takes a dip trade and meets the guardrails (at least one trade)
    even on a calm window.

Costs: a full-size round trip pays about 26 bps (4 bps fee + 2 bps
slippage + ~7 bps price impact each way), so tier 1 only fires on
dips of 60 bps with exits at +90 bps, and tier 2 only on crashes of
275 bps with exits at +400 bps, both comfortably above the cost.
"""

import json
import sys
from collections import deque

SYMBOL = "CBBTC"

# Tier 1: buy the dip inside an uptrend.
DIP_BPS = 60             # buy when close is this far below the 61-bar high
DIP_WINDOW = 60          # lookback for the high, in bars (61 closes incl. current)
REGIME_WINDOW = 240      # lookback for the trend filter, in bars
REGIME_MIN_BPS = 100     # tier 1 needs a 240-bar return of at least this
TAKE_PROFIT_BPS = 90     # tier 1 sell when close is this far above the fill price
STOP_LOSS_BPS = 60       # tier 1 sell when close is this far below the fill price

# Tier 2: buy the crash, sell the rebound.
CRASH_BPS = 275          # buy when close is this far below the 61-bar high
CRASH_TAKE_PROFIT_BPS = 400
CRASH_STOP_LOSS_BPS = 250

MAX_HOLD_BARS = 240      # sell after holding this many bars at the latest
COOLDOWN_BARS = 60       # bars to wait after a sell before the next buy
FALLBACK_BAR = 1350      # drop the tier-1 regime filter after this bar if never traded
FALLBACK_MAX_FALL_BPS = 100  # ...but only when the 60-bar fall is no worse than this
MAX_SLIPPAGE_BPS = 500   # 5%: rejects only absurd gap fills, never normal ones
MIN_NOTIONAL = 1000000   # 1 USDC, in quote units


def at_least_bps_below(price, ref, bps):
    """True when price is at least bps below ref (integer math)."""
    return price * 10000 <= ref * (10000 - bps)


def at_least_bps_above(price, ref, bps):
    """True when price is at least bps above ref (integer math)."""
    return price * 10000 >= ref * (10000 + bps)


def main():
    # Rolling closes: 241 entries, so closes[0] is the close 240 bars back.
    closes = deque(maxlen=REGIME_WINDOW + 1)
    bars_seen = 0        # total bars folded (closes is capped, so count separately)
    entry_price = None   # fill price of the open position, or None when flat
    entry_seq = None     # seq of the batch that reported the buy fill
    entry_tier = 0       # 1 or 2: which tier opened the current position
    next_buy_after = -1  # cooldown: no buy before this seq
    order_no = 0         # deterministic client-id counter
    traded = False       # True once a buy has filled in this run
    pending_buys = set()  # client ids of buys sent, awaiting their fill report
    warmup = 0

    out = sys.stdout
    for raw in sys.stdin.buffer:
        line = raw.strip()
        if not line:
            continue
        msg = json.loads(line.decode("utf-8"))
        kind = msg.get("type")
        if kind == "hello":
            warmup = int(msg.get("warmup_bars") or 0)
            continue
        if kind == "done":
            return 0
        if kind != "batch":
            continue
        seq = int(msg["seq"])

        # Fold the fills of the orders decided on the previous batch:
        # learn the entry price from a buy fill, clear it on a sell fill,
        # and retire the pending buy ids whatever their outcome was.
        for fill in msg.get("fills") or []:
            if fill.get("symbol") != SYMBOL:
                continue
            pending_buys.discard(fill.get("client_id"))
            if fill.get("status") != "filled":
                continue
            if fill.get("side") == "buy":
                entry_price = int(fill["price"])
                entry_seq = seq
                traded = True
            elif fill.get("side") == "sell":
                entry_price = None
                entry_seq = None
                entry_tier = 0

        # Fold this batch's bar (a minute with no sample carries no bar).
        for bar in msg.get("bars") or []:
            if bar.get("symbol") == SYMBOL:
                closes.append(int(bar["c"]))
                bars_seen += 1

        account = msg.get("account") or {}
        cash = int(account.get("cash_quote_units") or 0)
        qty = 0
        for pos in account.get("positions") or []:
            if pos.get("symbol") == SYMBOL:
                qty = int(pos.get("qty") or 0)

        orders = []
        if seq >= warmup and closes:
            close = closes[-1]
            if qty > 0 and entry_price is not None:
                # We hold a position: check the exits against the fill price.
                if entry_tier == 2:
                    take_profit = at_least_bps_above(
                        close, entry_price, CRASH_TAKE_PROFIT_BPS)
                    stop_loss = at_least_bps_below(
                        close, entry_price, CRASH_STOP_LOSS_BPS)
                else:
                    take_profit = at_least_bps_above(
                        close, entry_price, TAKE_PROFIT_BPS)
                    stop_loss = at_least_bps_below(
                        close, entry_price, STOP_LOSS_BPS)
                too_long = entry_seq is not None and seq - entry_seq >= MAX_HOLD_BARS
                if take_profit or stop_loss or too_long:
                    order_no += 1
                    orders.append({
                        "side": "sell",
                        "client_id": "s%d" % order_no,
                        "symbol": SYMBOL,
                        "qty": str(qty),
                        "max_slippage_bps": MAX_SLIPPAGE_BPS,
                    })
                    next_buy_after = seq + COOLDOWN_BARS
            elif qty == 0 and not pending_buys and seq >= next_buy_after:
                # We are flat: look for an entry. Tier 2 first, so a crash
                # is never missed because a tier-1 dip fired on the way down.
                tier = 0
                if len(closes) >= DIP_WINDOW + 1:
                    high = max(list(closes)[-(DIP_WINDOW + 1):])
                    bounced = len(closes) >= 2 and close > closes[-2]
                    if at_least_bps_below(close, high, CRASH_BPS) and bounced:
                        tier = 2
                    elif at_least_bps_below(close, high, DIP_BPS):
                        regime_ok = True
                        if len(closes) > REGIME_WINDOW:
                            ref = closes[0]
                            regime_ok = at_least_bps_above(
                                close, ref, REGIME_MIN_BPS)
                        if not regime_ok and not traded and bars_seen >= FALLBACK_BAR:
                            # Fallback: no trade yet and the day is nearly
                            # over; drop the regime filter unless the
                            # market is falling steeply right now.
                            if len(closes) > DIP_WINDOW:
                                ref60 = closes[-(DIP_WINDOW + 1)]
                                regime_ok = close * 10000 >= ref60 * (
                                    10000 - FALLBACK_MAX_FALL_BPS)
                            else:
                                regime_ok = True
                        if regime_ok:
                            tier = 1
                if tier and cash >= MIN_NOTIONAL:
                    order_no += 1
                    client_id = "b%d" % order_no
                    pending_buys.add(client_id)
                    entry_tier = tier
                    orders.append({
                        "side": "buy",
                        "client_id": client_id,
                        "symbol": SYMBOL,
                        "notional_quote_units": str(cash),
                        "max_slippage_bps": MAX_SLIPPAGE_BPS,
                    })

        out.write(json.dumps(
            {"type": "reply", "seq": seq, "orders": orders},
            separators=(",", ":"),
        ) + "\n")
        out.flush()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
