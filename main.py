import os
import time
import ccxt
import pandas as pd
from ta.momentum import RSIIndicator
from ta.volatility import BollingerBands
import requests
from datetime import datetime, timezone
from threading import Thread
from flask import Flask

# ---------- Flask web app for Render health check ----------
app = Flask(__name__)

@app.route("/")
@app.route("/health")
def health():
    return "Bot is running", 200

# ---------- Telegram setup ----------
TELEGRAM_TOKEN = os.environ.get("TELEGRAM_TOKEN")
CHAT_ID = os.environ.get("CHAT_ID")

if not TELEGRAM_TOKEN or not CHAT_ID:
    raise SystemExit("Missing TELEGRAM_TOKEN or CHAT_ID environment variables.")

# ---------- Bitget API (public only) ----------
exchange = ccxt.bitget({
    "enableRateLimit": True,
    "options": {"defaultType": "swap"}
})

# ---------- Telegram sender ----------
def send_telegram(msg):
    try:
        url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
        requests.post(url, json={"chat_id": CHAT_ID, "text": msg})
    except Exception as e:
        print("Telegram error:", e)

# ---------- Indicators ----------
def get_indicators(df):
    df["rsi"] = RSIIndicator(df["close"], window=14).rsi()
    bb = BollingerBands(df["close"], window=20, window_dev=2)
    df["bb_high"] = bb.bollinger_hband()
    df["bb_low"] = bb.bollinger_lband()
    return df

alerted = {}

# ---------- Symbol analysis ----------
def analyze_symbol(symbol):
    try:
        ohlcv = exchange.fetch_ohlcv(symbol, timeframe="1h", limit=120)
        df = pd.DataFrame(ohlcv, columns=["time", "open", "high", "low", "close", "volume"])
        df = get_indicators(df)
        last = df.iloc[-1]

        price = float(last["close"])
        upper = float(last["bb_high"])
        lower = float(last["bb_low"])
        rsi = float(last["rsi"])
        above = (price - upper) / upper * 100
        below = (lower - price) / lower * 100
        now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")

        # SHORT
        if 40 <= above <= 60 and rsi > 89 and alerted.get(symbol) != "SHORT":
            msg = (f"📉 SHORT Signal – {symbol}\n"
                   f"RSI: {rsi:.1f}\n"
                   f"Current Price: {price:.4f}\n"
                   f"Upper BB: {upper:.4f}\n"
                   f"Distance Above Upper BB: {above:.1f}%\n"
                   f"⏰ {now}")
            send_telegram(msg)
            print(msg)
            alerted[symbol] = "SHORT"

        # EXTREME SHORT
        elif above > 40 and rsi > 99 and alerted.get(symbol) != "EXTREME_SHORT":
            msg = (f"⚠️ EXTREME SHORT Alert – {symbol}\n"
                   f"RSI: {rsi:.1f}\n"
                   f"Current Price: {price:.4f}\n"
                   f"Upper BB: {upper:.4f}\n"
                   f"Distance Above Upper BB: {above:.1f}%\n"
                   f"⏰ {now}")
            send_telegram(msg)
            print(msg)
            alerted[symbol] = "EXTREME_SHORT"

        # LONG
        elif 60 <= below <= 80 and rsi < 10 and alerted.get(symbol) != "LONG":
            msg = (f"📈 LONG Signal – {symbol}\n"
                   f"RSI: {rsi:.1f}\n"
                   f"Current Price: {price:.4f}\n"
                   f"Lower BB: {lower:.4f}\n"
                   f"Distance Below Lower BB: {below:.1f}%\n"
                   f"⏰ {now}")
            send_telegram(msg)
            print(msg)
            alerted[symbol] = "LONG"

        # EXTREME LONG
        elif below > 40 and rsi < 1 and alerted.get(symbol) != "EXTREME_LONG":
            msg = (f"⚠️ EXTREME LONG Alert – {symbol}\n"
                   f"RSI: {rsi:.1f}\n"
                   f"Current Price: {price:.4f}\n"
                   f"Lower BB: {lower:.4f}\n"
                   f"Distance Below Lower BB: {below:.1f}%\n"
                   f"⏰ {now}")
            send_telegram(msg)
            print(msg)
            alerted[symbol] = "EXTREME_LONG"

        # Reset memory when neutral
        elif above < 40 and below < 60:
            alerted.pop(symbol, None)

    except Exception as e:
        print(f"{symbol} error:", e)

# ---------- Bot loop ----------
def run_bot():
    while True:
        try:
            markets = exchange.load_markets()
            symbols = []

            # ✅ Only USDT-margined futures (swap)
            for m in markets.values():
                if (
                    isinstance(m, dict)
                    and m.get("quote") == "USDT"
                    and m.get("type") == "swap"
                    and m.get("contract", False)
                    and m.get("linear", False)
                ):
                    symbols.append(m["id"])

            symbols = list(dict.fromkeys(symbols))  # deduplicate

            print(f"\n🔎 Scanning {len(symbols)} futures pairs… {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S UTC')}")
            for sym in symbols:
                analyze_symbol(sym)
            print("Cycle complete. Sleeping 60s.\n")

        except Exception as e:
            print("Main loop error:", e)
        time.sleep(60)

# ---------- Start everything ----------
if __name__ == "__main__":
    Thread(target=run_bot, daemon=True).start()
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 10000)))
