import os
import time
import ccxt
import pandas as pd
from ta.momentum import RSIIndicator
from ta.volatility import BollingerBands
import requests
from datetime import datetime
from threading import Thread
from flask import Flask

app = Flask(__name__)

@app.route("/")
@app.route("/health")
def health():
    return "Bot is running", 200

TELEGRAM_TOKEN = os.environ.get("8421284091:AAHHbjOr42_XNmb3l6JzXQq4UnVt3ymebRM")
CHAT_ID = os.environ.get("6243669766")

if not TELEGRAM_TOKEN or not CHAT_ID:
    raise SystemExit("Missing TELEGRAM_TOKEN or CHAT_ID in environment")

exchange = ccxt.bitget({"enableRateLimit": True, "options": {"defaultType": "swap"}})

def send_telegram(msg):
    try:
        url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
        requests.post(url, json={"chat_id": CHAT_ID, "text": msg})
    except Exception as e:
        print("Telegram error:", e)

def get_indicators(df):
    df["rsi"] = RSIIndicator(df["close"], window=14).rsi()
    bb = BollingerBands(df["close"], window=20, window_dev=2)
    df["bb_high"] = bb.bollinger_hband()
    df["bb_low"] = bb.bollinger_lband()
    return df

alerted = {}

def analyze_symbol(symbol):
    try:
        ohlcv = exchange.fetch_ohlcv(symbol, timeframe="1h", limit=100)
        df = pd.DataFrame(ohlcv, columns=["time","open","high","low","close","volume"])
        df = get_indicators(df)
        last = df.iloc[-1]
        price, upper, lower, rsi = last["close"], last["bb_high"], last["bb_low"], last["rsi"]

        above = (price - upper) / upper * 100
        below = (lower - price) / lower * 100
        now = datetime.utcnow().strftime("%Y-%m-%d %H:%M")

        if 40 <= above <= 60 and rsi > 89:
            if alerted.get(symbol) != "SHORT":
                msg = f"📉 SHORT Signal – {symbol}\nPrice {above:.1f}% above upper BB\nRSI {rsi:.1f}\n⏰ {now} UTC"
                print(msg)
                send_telegram(msg)
                alerted[symbol] = "SHORT"

        elif 60 <= below <= 80 and rsi < 10:
            if alerted.get(symbol) != "LONG":
                msg = f"📈 LONG Signal – {symbol}\nPrice {below:.1f}% below lower BB\nRSI {rsi:.1f}\n⏰ {now} UTC"
                print(msg)
                send_telegram(msg)
                alerted[symbol] = "LONG"

        elif above < 40 and below < 60:
            alerted.pop(symbol, None)
    except Exception as e:
        print(symbol, "error:", e)

def run_bot():
    while True:
        try:
            markets = exchange.load_markets()
            symbols = [m["id"] for m in markets.values() if m.get("quote") == "USDT" and m.get("type") == "swap"]
            print(f"\n🔎 Scanning {len(symbols)} pairs… {datetime.utcnow().strftime('%H:%M:%S')} UTC")
            for sym in symbols:
                analyze_symbol(sym)
            print("Cycle complete. Sleeping 60s.\n")
        except Exception as e:
            print("Main loop error:", e)
        time.sleep(60)

if __name__ == "__main__":
    Thread(target=run_bot, daemon=True).start()
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 10000)))
