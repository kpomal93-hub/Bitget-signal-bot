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

# ---------- Tiny web server for health checks ----------
app = Flask(__name__)

@app.route("/")
@app.route("/health")
def health():
    return "Bot is running", 200

# ---------- Config from environment ----------
TELEGRAM_TOKEN = os.environ.get("TELEGRAM_TOKEN")
CHAT_ID = os.environ.get("CHAT_ID")

if not TELEGRAM_TOKEN or not CHAT_ID:
    raise SystemExit("Missing TELEGRAM_TOKEN or CHAT_ID environment variables. Set them in Render.")

# ---------- Bitget public connection (ccxt) ----------
exchange = ccxt.bitget({
    "enableRateLimit": True,
    "options": {"defaultType": "swap"}  # linear USDT swap markets
})

# ---------- Helpers ----------
def send_telegram(msg):
    """
    Send text message to your Telegram chat (bot must be admin/started).
    """
    try:
        url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
        resp = requests.post(url, json={"chat_id": CHAT_ID, "text": msg})
        if resp.status_code != 200:
            print("Telegram API response:", resp.status_code, resp.text)
    except Exception as e:
        print("Telegram error:", e)

def get_indicators(df):
    """Calculate RSI and Bollinger Bands on dataframe with 'close' column."""
    df = df.copy()
    df["rsi"] = RSIIndicator(df["close"], window=14).rsi()
    bb = BollingerBands(df["close"], window=20, window_dev=2)
    df["bb_high"] = bb.bollinger_hband()
    df["bb_low"]  = bb.bollinger_lband()
    return df

# memory for alerts to avoid repeat spam
alerted = {}  # symbol -> last_alert_type

# ---------- main symbol analysis ----------
def analyze_symbol(symbol):
    """
    Fetch 1h OHLCV, compute indicators and send alerts:
      - SHORT: price 40-60% above upper BB AND RSI > 89
      - EXTREME SHORT: price >40% above upper BB AND RSI > 99
      - LONG: price 60-80% below lower BB AND RSI < 10
      - EXTREME LONG: price >40% below lower BB AND RSI < 1
    Alerts include RSI, price, BB levels and percent distance.
    """
    try:
        ohlcv = exchange.fetch_ohlcv(symbol, timeframe="1h", limit=120)
        df = pd.DataFrame(ohlcv, columns=["time", "open", "high", "low", "close", "volume"])
        df = get_indicators(df)
        last = df.iloc[-1]

        price = float(last["close"])
        upper = float(last["bb_high"]) if not pd.isna(last["bb_high"]) else None
        lower = float(last["bb_low"]) if not pd.isna(last["bb_low"]) else None
        rsi   = float(last["rsi"]) if not pd.isna(last["rsi"]) else None

        # if indicators not ready yet, skip
        if upper is None or lower is None or rsi is None:
            return

        # calculate percent distances safely
        try:
            above = (price - upper) / upper * 100.0
        except Exception:
            above = 0.0
        try:
            below = (lower - price) / lower * 100.0
        except Exception:
            below = 0.0

        now = datetime.utcnow().strftime("%Y-%m-%d %H:%M UTC")

        # --- SHORT (standard) ---
        if 40 <= above <= 60 and rsi > 89:
            if alerted.get(symbol) != "SHORT":
                msg = (f"📉 SHORT Signal – {symbol}\n"
                       f"RSI: {rsi:.2f}\n"
                       f"Current Price: {price:.8f}\n"
                       f"Upper BB: {upper:.8f}\n"
                       f"Distance Above Upper BB: {above:.2f}%\n"
                       f"{now}")
                print(msg)
                send_telegram(msg)
                alerted[symbol] = "SHORT"

        # --- EXTREME SHORT ---
        elif above > 40 and rsi > 99:
            # EXTREME_SHORT uses >40 (not limited to 60) and RSI >99
            if alerted.get(symbol) != "EXTREME_SHORT":
                msg = (f"⚠️ EXTREME SHORT Alert – {symbol}\n"
                       f"RSI: {rsi:.2f} (>99)\n"
                       f"Current Price: {price:.8f}\n"
                       f"Upper BB: {upper:.8f}\n"
                       f"Distance Above Upper BB: {above:.2f}%\n"
                       f"{now}")
                print(msg)
                send_telegram(msg)
                alerted[symbol] = "EXTREME_SHORT"

        # --- LONG (standard) ---
        elif 60 <= below <= 80 and rsi < 10:
            if alerted.get(symbol) != "LONG":
                msg = (f"📈 LONG Signal – {symbol}\n"
                       f"RSI: {rsi:.2f}\n"
                       f"Current Price: {price:.8f}\n"
                       f"Lower BB: {lower:.8f}\n"
                       f"Distance Below Lower BB: {below:.2f}%\n"
                       f"{now}")
                print(msg)
                send_telegram(msg)
                alerted[symbol] = "LONG"

        # --- EXTREME LONG ---
        elif below > 40 and rsi < 1:
            if alerted.get(symbol) != "EXTREME_LONG":
                msg = (f"⚠️ EXTREME LONG Alert – {symbol}\n"
                       f"RSI: {rsi:.2f} (<1)\n"
                       f"Current Price: {price:.8f}\n"
                       f"Lower BB: {lower:.8f}\n"
                       f"Distance Below Lower BB: {below:.2f}%\n"
                       f"{now}")
                print(msg)
                send_telegram(msg)
                alerted[symbol] = "EXTREME_LONG"

        # --- Reset memory when market becomes neutral so it can alert again later ---
        elif above < 40 and below < 60:
            if symbol in alerted:
                print(f"Reset alert memory for {symbol}")
                alerted.pop(symbol, None)

    except Exception as e:
        # don't crash the bot on one symbol error
        print(f"{symbol} error:", repr(e))

# ---------- main loop ----------
def run_bot():
    while True:
        try:
            markets = exchange.load_markets()
            symbols = []
            for m in markets.values():
                # try multiple checks to grab USDT swap symbols regardless of ccxt version
                try:
                    if m.get("quote") == "USDT" and m.get("type") == "swap":
                        symbols.append(m["id"])
                except Exception:
                    pass
                # fallback checks
                try:
                    if isinstance(m, dict) and m.get("id", "").endswith(":USDT"):
                        symbols.append(m["id"])
                except Exception:
                    pass
                try:
                    if isinstance(m, dict) and m.get("symbol", "").endswith("/USDT"):
                        symbols.append(m["id"])
                except Exception:
                    pass

            # de-duplicate
            symbols = list(dict.fromkeys(symbols))

            print(f"\n🔎 Scanning {len(symbols)} pairs… {datetime.utcnow().strftime('%Y-%m-%d %H:%M:%S')} UTC")
            for sym in symbols:
                analyze_symbol(sym)

            print("Cycle complete. Sleeping 60s.\n")
        except Exception as e:
            print("Main loop error:", repr(e))
        time.sleep(60)

# ---------- start both thread and webserver ----------
if __name__ == "__main__":
    # run bot in background thread
    Thread(target=run_bot, daemon=True).start()

    # start Flask app (Render provides PORT env var)
    port = int(os.environ.get("PORT", 10000))
    app.run(host="0.0.0.0", port=port)
