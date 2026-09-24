"""
Telegram AI Bot + TradingView Webhook Receiver for PythonAnywhere (WSGI/Flask)
- Menerima alert dari TradingView via POST /tradingview
- Query status teknikal TradingView via /status
- Panduan indikator & strategi via /indicator & /strategy
- Format kemas, bersih, tiada simbol bintang (*) yang berselerak
- 100% serasi dengan akaun percuma PythonAnywhere
"""

import json
import logging
import os
import re
import requests
from flask import Flask, jsonify, request

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

try:
    from tradingview_ta import TA_Handler, Interval
    HAS_TV_TA = True
except ImportError:
    HAS_TV_TA = False

try:
    import io
    import matplotlib
    matplotlib.use('Agg')
    import matplotlib.pyplot as plt
    import pandas as pd
    import yfinance as yf
    HAS_CHART = True
except ImportError:
    HAS_CHART = False

app = Flask(__name__)
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")

# ── Config ────────────────────────────────────────────────────────────────────
TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT_ID   = os.environ.get("TELEGRAM_CHAT_ID", "")
GEMINI_API_KEY     = os.environ.get("GEMINI_API_KEY", "")

GEMINI_MODELS = [
    "gemini-3-flash-preview",
    "gemini-3.6-flash",
    "gemini-3.5-flash-lite",
    "gemini-flash-latest",
    "gemini-3.5-flash",
]


# ── Text Cleaner ──────────────────────────────────────────────────────────────

def format_clean_telegram(text: str) -> str:
    """Membersihkan simbol bintang (*) dan formatkan dengan kemas untuk Telegram."""
    if not text:
        return ""
    text = re.sub(r'^#{1,6}\s*(.+)$', r'📌 <b>\1</b>', text, flags=re.MULTILINE)
    text = re.sub(r'\*\*(.+?)\*\*', r'<b>\1</b>', text)
    text = re.sub(r'(?<!\w)\*([^\*\n]+?)\*(?!\w)', r'<i>\1</i>', text)
    text = re.sub(r'(?<!\w)_([^_\n]+?)_(?!\w)', r'<i>\1</i>', text)
    text = re.sub(r'^\s*[\*\-•]\s+', '🔹 ', text, flags=re.MULTILINE)
    text = text.replace('*', '')
    text = re.sub(r'\n{3,}', '\n\n', text)
    return text.strip()


DISCLAIMER_HTML = (
    "⚠️ <i>Penafian: Maklumat dan analisis ini adalah untuk tujuan pembelajaran dan rujukan teknikal sahaja, "
    "bukan nasihat pelaburan atau kewangan. Sentiasa lakukan kajian anda sendiri (DYOR).</i>"
)

def attach_disclaimer(text: str) -> str:
    """Sertakan penafian bukan nasihat kewangan jika belum wujud."""
    if not text:
        return ""
    lower = text.lower()
    if any(k in lower for k in ["bukan nasihat kewangan", "penafian:", "dyor", "not financial advice"]):
        return text
    return f"{text.rstrip()}\n\n{DISCLAIMER_HTML}"


# ── Telegram Helper ───────────────────────────────────────────────────────────

def send_telegram(text: str, chat_id: str = None) -> bool:
    """Send HTML message to Telegram."""
    target_chat = chat_id or TELEGRAM_CHAT_ID
    if not TELEGRAM_BOT_TOKEN or not target_chat:
        app.logger.warning("TELEGRAM_BOT_TOKEN or CHAT_ID missing.")
        return False

    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    payload = {
        "chat_id": target_chat,
        "text": format_clean_telegram(text),
        "parse_mode": "HTML",
        "disable_web_page_preview": True,
    }
    try:
        r = requests.post(url, json=payload, timeout=10)
        return r.status_code == 200
    except Exception as e:
        app.logger.error("Failed to send Telegram message: %s", e)
        return False

def send_telegram_photo(photo_bytes: bytes, caption: str, chat_id: str = None) -> bool:
    """Send photo with caption to Telegram."""
    target_chat = chat_id or TELEGRAM_CHAT_ID
    if not TELEGRAM_BOT_TOKEN or not target_chat:
        return False
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendPhoto"
    try:
        if len(caption) <= 1024:
            r = requests.post(url, data={
                "chat_id": target_chat,
                "caption": caption,
                "parse_mode": "HTML",
            }, files={
                "photo": ("chart.png", photo_bytes, "image/png")
            }, timeout=15)
            return r.status_code == 200
        else:
            first_line = caption.split("\n")[0]
            requests.post(url, data={
                "chat_id": target_chat,
                "caption": first_line,
                "parse_mode": "HTML",
            }, files={
                "photo": ("chart.png", photo_bytes, "image/png")
            }, timeout=15)
            return send_telegram(caption, chat_id=target_chat)
    except Exception as e:
        app.logger.error("Failed to send Telegram photo: %s", e)
        return send_telegram(caption, chat_id=target_chat)


# ── Chart Engine (Candlestick + EMAs) ─────────────────────────────────────────

BURSA_CODE_MAP = {
    "MAYBANK": "1155.KL",
    "CIMB": "1023.KL",
    "TENAGA": "5347.KL",
    "PBBANK": "1295.KL",
    "PUBLICBANK": "1295.KL",
    "IHH": "5225.KL",
    "PMETAL": "8869.KL",
    "PRESSMETAL": "8869.KL",
    "YTL": "4677.KL",
    "YTLPOWR": "6742.KL",
    "CELCOMDIGI": "6947.KL",
    "CDB": "6947.KL",
    "MAXIS": "6012.KL",
    "AXIATA": "6888.KL",
    "SIME": "4197.KL",
    "SIMEPROP": "5288.KL",
    "INARI": "0166.KL",
    "SUNWAY": "5211.KL",
    "GENTING": "3182.KL",
    "GENM": "4715.KL",
    "TOPGLOV": "7113.KL",
    "TOPGLOVE": "7113.KL",
    "HARTA": "5168.KL",
    "HARTALEGA": "5168.KL",
    "MISC": "3816.KL",
    "PETDAG": "5681.KL",
    "PETGAS": "6033.KL",
    "PCHEM": "5183.KL",
    "RHBBANK": "1066.KL",
    "RHB": "1066.KL",
    "HLBANK": "5819.KL",
    "AMBANK": "1015.KL",
    "GAMUDA": "5398.KL",
    "AIRASIA": "5099.KL",
    "CAPITALA": "5099.KL",
    "MRDIY": "5296.KL",
    "MYEG": "0138.KL",
    "ECOWLD": "8206.KL",
    "SPSETIA": "8664.KL",
    "KPJ": "5878.KL",
    "DIALOG": "7277.KL",
}

def generate_candlestick_chart(raw_symbol: str, interval: str = "1h", market_type: str = "") -> bytes | None:
    """Menjana carta candlestick dark mode berkualiti tinggi sebagai gambar PNG."""
    if not HAS_CHART:
        return None
    try:
        sym = raw_symbol.upper().strip().replace("/", "").replace("-", "").replace("PERP", "")
        df = None
        curr = "$"

        if market_type == "Bursa Malaysia" or sym in BURSA_CODE_MAP or (len(sym) == 4 and sym.isdigit()):
            b_sym = BURSA_CODE_MAP.get(sym, f"{sym}.KL")
            yf_df = yf.Ticker(b_sym).history(period="1mo", interval="1d")
            if not yf_df.empty:
                df = yf_df.reset_index()
                df.columns = [c.lower() for c in df.columns]
                curr = "RM"
        elif "crypto" in market_type.lower() or sym.endswith("USDT") or sym in ["BTC", "ETH", "SOL", "XRP", "DOGE", "BNB", "ADA"]:
            c_sym = sym if sym.endswith("USDT") else f"{sym}USDT"
            binance_tf = interval.lower() if interval.lower() in ["1m", "5m", "15m", "1h", "4h", "1d", "1w"] else "1h"
            try:
                r = requests.get(
                    f"https://api.binance.com/api/v3/klines?symbol={c_sym}&interval={binance_tf}&limit=40",
                    timeout=5.0
                )
                if r.status_code == 200:
                    raw = r.json()
                    df = pd.DataFrame(raw, columns=[
                        "timestamp", "open", "high", "low", "close", "volume",
                        "close_time", "qav", "num_trades", "taker_base_vol", "taker_quote_vol", "ignore"
                    ])
                    for col in ["open", "high", "low", "close", "volume"]:
                        df[col] = df[col].astype(float)
                    df["date"] = pd.to_datetime(df["timestamp"], unit="ms")
                    curr = "$"
            except Exception:
                pass

        if df is None or df.empty:
            yf_sym = f"{sym}=X" if ("forex" in market_type.lower() or (len(sym) == 6 and any(fx in sym for fx in ["MYR", "EUR", "GBP", "USD", "JPY", "SGD"]))) else sym
            yf_tf = "1d" if interval.lower() in ["1d", "1w"] else "1h"
            yf_df = yf.Ticker(yf_sym).history(period="1mo", interval=yf_tf)
            if not yf_df.empty:
                df = yf_df.reset_index()
                df.columns = [c.lower() for c in df.columns]
                curr = "" if "forex" in market_type.lower() else "$"

        if df is None or df.empty or len(df) < 5:
            return None

        df = df.iloc[-35:].reset_index(drop=True)

        fig, ax = plt.subplots(figsize=(10, 5), facecolor="#0B0E14")
        ax.set_facecolor("#0B0E14")

        col_up = "#089981"
        col_down = "#F23645"
        width = 0.6
        x = range(len(df))

        for i in x:
            o = df.loc[i, "open"]
            c = df.loc[i, "close"]
            h = df.loc[i, "high"]
            l = df.loc[i, "low"]
            col = col_up if c >= o else col_down
            ax.vlines(i, l, h, color=col, linewidth=1.2)
            bottom = min(o, c)
            height = max(abs(c - o), (h - l) * 0.03)
            ax.bar(i, height, bottom=bottom, color=col, width=width)

        df["ema20"] = df["close"].ewm(span=20).mean()
        ax.plot(x, df["ema20"], color="#00F0FF", label="EMA 20", linewidth=1.5)
        if len(df) >= 25:
            df["ema50"] = df["close"].ewm(span=50).mean()
            ax.plot(x, df["ema50"], color="#FFA500", label="EMA 50", linewidth=1.3)

        ax.grid(True, color="#1E222D", linestyle="--", alpha=0.7)
        ax.tick_params(colors="#848E9C")
        for spine in ax.spines.values():
            spine.set_color("#1E222D")

        step = max(1, len(df) // 6)
        date_col = "date" if "date" in df.columns else ("datetime" if "datetime" in df.columns else df.columns[0])
        labels = []
        for i in range(0, len(df), step):
            dt = pd.to_datetime(df.loc[i, date_col])
            labels.append(dt.strftime("%d %b\n%H:%M") if interval not in ["1d", "1w"] else dt.strftime("%d %b\n%Y"))
        ax.set_xticks(range(0, len(df), step))
        ax.set_xticklabels(labels, color="#848E9C")

        last_p = df.iloc[-1]["close"]
        price_fmt = f"{curr}{last_p:,.2f}" if curr else (f"{last_p:,.4f}" if last_p < 1 else f"{last_p:,.2f}")
        ax.set_title(f"{sym} ({interval}) • {price_fmt} | LangkahTrade AI", color="#E1E3E6", fontsize=12, fontweight="bold", pad=12)
        ax.legend(facecolor="#131722", edgecolor="#2A2E39", labelcolor="#E1E3E6", loc="upper left")

        buf = io.BytesIO()
        plt.tight_layout()
        plt.savefig(buf, format="png", dpi=120, facecolor=fig.get_facecolor(), bbox_inches="tight")
        plt.close(fig)
        return buf.getvalue()
    except Exception as e:
        app.logger.error("Ralat carta: %s", e)
        return None


# ── TradingView TA Helper ──────────────────────────────────────────────────────

INTERVAL_MAP = {
    "1m": "1m",
    "5m": "5m",
    "15m": "15m",
    "1h": "1h",
    "4h": "4h",
    "1d": "1d",
    "1w": "1W",
}

KNOWN_NAMES = {
    "BITCOIN": ("BTCUSDT", "crypto", "BINANCE"),
    "ETHEREUM": ("ETHUSDT", "crypto", "BINANCE"),
    "SOLANA": ("SOLUSDT", "crypto", "BINANCE"),
    "TESLA": ("TSLA", "america", "NASDAQ"),
    "NVIDIA": ("NVDA", "america", "NASDAQ"),
    "APPLE": ("AAPL", "america", "NASDAQ"),
    "MICROSOFT": ("MSFT", "america", "NASDAQ"),
    "AMAZON": ("AMZN", "america", "NASDAQ"),
    "GOOGLE": ("GOOGL", "america", "NASDAQ"),
}

def get_fallback_ta(raw_symbol: str, interval_key: str = "1h") -> dict | None:
    """
    Kiraan teknikal sokongan (Fallback TA) terus dari candle Binance / Yahoo Finance
    apabila TradingView API mengalami masalah rate-limit (429) atau disekat.
    """
    try:
        sym = raw_symbol.upper().strip().replace("/", "").replace("-", "").replace("PERP", "")
        df = None
        market_type = "Kripto"
        exchange = "BINANCE"
        curr = "$"

        # 1. Bursa Malaysia
        if sym in BURSA_CODE_MAP or (len(sym) == 4 and sym.isdigit()):
            b_sym = BURSA_CODE_MAP.get(sym, f"{sym}.KL")
            yf_df = yf.Ticker(b_sym).history(period="2mo", interval="1d")
            if not yf_df.empty:
                df = yf_df.reset_index()
                df.columns = [c.lower() for c in df.columns]
                market_type = "Bursa Malaysia"
                exchange = "MYX"
                curr = "RM"

        # 2. Crypto via Binance Public API
        if df is None or df.empty:
            c_sym = sym if (sym.endswith("USDT") or sym.endswith("USD") or sym.endswith("BUSD")) else f"{sym}USDT"
            binance_tf = interval_key.lower() if interval_key.lower() in ["1m", "5m", "15m", "1h", "4h", "1d", "1w"] else "1h"
            try:
                r = requests.get(
                    f"https://api.binance.com/api/v3/klines?symbol={c_sym}&interval={binance_tf}&limit=60",
                    timeout=5.0
                )
                if r.status_code == 200:
                    raw = r.json()
                    df = pd.DataFrame(raw, columns=[
                        "timestamp", "open", "high", "low", "close", "volume",
                        "close_time", "qav", "num_trades", "taker_base_vol", "taker_quote_vol", "ignore"
                    ])
                    for col in ["open", "high", "low", "close", "volume"]:
                        df[col] = df[col].astype(float)
                    sym = c_sym
                    market_type = "Kripto"
                    exchange = "BINANCE"
                    curr = "$"
            except Exception:
                pass

        # 3. US Stocks / Forex via yfinance
        if df is None or df.empty:
            is_forex = len(sym) == 6 and any(fx in sym for fx in ["MYR", "EUR", "GBP", "USD", "JPY", "SGD"])
            yf_sym = f"{sym}=X" if is_forex else sym
            yf_tf = "1d" if interval_key.lower() in ["1d", "1w"] else "1h"
            yf_df = yf.Ticker(yf_sym).history(period="2mo", interval=yf_tf)
            if not yf_df.empty:
                df = yf_df.reset_index()
                df.columns = [c.lower() for c in df.columns]
                market_type = "Forex" if is_forex else "Saham US"
                exchange = "OANDA" if is_forex else "NASDAQ"
                curr = "" if is_forex else "$"

        if df is None or df.empty or len(df) < 5:
            return None

        close = df["close"].astype(float)
        price = close.iloc[-1]
        price_val = round(price, 4) if price < 1 else round(price, 2)

        # Hitung RSI (14)
        delta = close.diff()
        gain = (delta.where(delta > 0, 0)).rolling(window=14).mean()
        loss = (-delta.where(delta < 0, 0)).rolling(window=14).mean()
        rs = gain / (loss + 1e-9)
        rsi_series = 100 - (100 / (1 + rs))
        rsi = round(float(rsi_series.iloc[-1]), 2) if not rsi_series.empty and not pd.isna(rsi_series.iloc[-1]) else 50.0

        # Hitung EMAs
        ema20 = round(float(close.ewm(span=20).mean().iloc[-1]), 2)
        ema50 = round(float(close.ewm(span=50).mean().iloc[-1]), 2)
        ema200 = round(float(close.ewm(span=200).mean().iloc[-1]), 2) if len(close) >= 200 else ema50

        # MACD
        ema12 = close.ewm(span=12).mean()
        ema26 = close.ewm(span=26).mean()
        macd_line = ema12 - ema26
        macd_val = round(float(macd_line.iloc[-1]), 2)

        # Scoring
        buy_score = 0
        sell_score = 0
        neutral_score = 0

        if rsi < 35:
            buy_score += 1
        elif rsi > 65:
            sell_score += 1
        else:
            neutral_score += 1

        if price > ema20:
            buy_score += 1
        else:
            sell_score += 1

        if ema20 > ema50:
            buy_score += 1
        else:
            sell_score += 1

        if macd_val > 0:
            buy_score += 1
        else:
            sell_score += 1

        if buy_score >= 3:
            rec = "STRONG_BUY" if buy_score == 4 else "BUY"
        elif sell_score >= 3:
            rec = "STRONG_SELL" if sell_score == 4 else "SELL"
        else:
            rec = "NEUTRAL"

        chart_url = f"https://www.tradingview.com/chart/?symbol={exchange}:{sym}"

        return {
            "symbol": sym,
            "market": market_type,
            "exchange": exchange,
            "interval": interval_key,
            "currency": curr,
            "price": price_val,
            "recommendation": rec,
            "buy": buy_score,
            "sell": sell_score,
            "neutral": neutral_score,
            "rsi": rsi,
            "macd": macd_val,
            "ema20": ema20,
            "ema50": ema50,
            "ema200": ema200,
            "chart_url": chart_url,
        }
    except Exception as e:
        app.logger.error("Ralat fallback TA: %s", e)
        return None

def get_tradingview_ta(raw_symbol: str, interval_key: str = "1h") -> dict | None:
    if not HAS_TV_TA:
        return get_fallback_ta(raw_symbol, interval_key)

    sym = raw_symbol.upper().strip().replace("/", "").replace("-", "").replace("PERP", "")
    tv_intervals = {
        "1m": Interval.INTERVAL_1_MINUTE,
        "5m": Interval.INTERVAL_5_MINUTES,
        "15m": Interval.INTERVAL_15_MINUTES,
        "1h": Interval.INTERVAL_1_HOUR,
        "4h": Interval.INTERVAL_4_HOURS,
        "1d": Interval.INTERVAL_1_DAY,
        "1w": Interval.INTERVAL_1_WEEK,
    }
    interval = tv_intervals.get(interval_key.lower(), Interval.INTERVAL_1_HOUR)

    candidates = []
    if sym in KNOWN_NAMES:
        candidates.append(KNOWN_NAMES[sym])

    if len(sym) == 6 and any(fx in sym for fx in ["MYR", "EUR", "GBP", "JPY", "AUD", "SGD"]):
        candidates.append((sym, "forex", "FX_IDC"))
        candidates.append((sym, "forex", "OANDA"))

    crypto_sym = sym if (sym.endswith("USDT") or sym.endswith("USD") or sym.endswith("BUSD")) else f"{sym}USDT"
    candidates.append((crypto_sym, "crypto", "BINANCE"))
    candidates.append((crypto_sym, "crypto", "BYBIT"))
    candidates.append((crypto_sym, "crypto", "OKX"))
    candidates.append((sym, "america", "NASDAQ"))
    candidates.append((sym, "america", "NYSE"))
    candidates.append((sym, "malaysia", "MYX"))

    for symbol_candidate, screener, exchange in candidates:
        try:
            handler = TA_Handler(
                symbol=symbol_candidate,
                screener=screener,
                exchange=exchange,
                interval=interval,
            )
            analysis = handler.get_analysis()
            inds = analysis.indicators
            summary = analysis.summary
            price = inds.get("close")

            if price is not None:
                if screener == "crypto":
                    market_type = "Kripto"
                    curr = "$"
                elif screener == "malaysia":
                    market_type = "Bursa Malaysia"
                    curr = "RM"
                elif screener == "america":
                    market_type = f"Saham US ({exchange})"
                    curr = "$"
                else:
                    market_type = "Forex"
                    curr = ""

                price_val = round(price, 4) if price < 1 else round(price, 2)
                chart_url = f"https://www.tradingview.com/chart/?symbol={exchange}:{symbol_candidate}"

                return {
                    "symbol": symbol_candidate,
                    "market": market_type,
                    "exchange": exchange,
                    "interval": interval_key,
                    "currency": curr,
                    "price": price_val,
                    "recommendation": summary.get("RECOMMENDATION", "NEUTRAL"),
                    "buy": summary.get("BUY", 0),
                    "sell": summary.get("SELL", 0),
                    "neutral": summary.get("NEUTRAL", 0),
                    "rsi": round(inds.get("RSI", 0) or 0, 2),
                    "macd": round(inds.get("MACD.macd", 0) or 0, 2),
                    "ema20": round(inds.get("EMA20", 0) or 0, 2),
                    "ema50": round(inds.get("EMA50", 0) or 0, 2),
                    "ema200": round(inds.get("EMA200", 0) or 0, 2),
                    "chart_url": chart_url,
                }
        except Exception:
            continue

    # Fallback automatik jika TradingView rate-limit / disekat
    return get_fallback_ta(raw_symbol, interval_key)



def load_brain_prompt() -> str:
    """Muat naik arahan AI secara dinamik daripada fail brain.md jika wujud."""
    brain_path = os.path.join(os.path.dirname(__file__), "brain.md")
    if os.path.exists(brain_path):
        try:
            with open(brain_path, "r", encoding="utf-8") as f:
                content = f.read().strip()
                if content:
                    return content
        except Exception as e:
            app.logger.warning("Gagal membaca brain.md: %s", e)
    return (
        "Anda adalah pembantu analisis crypto profesional. "
        "DILARANG guna simbol bintang (*). Gunakan ikon (🔹, 📈, 📉, 💡, 🛡️) untuk poin. "
        "Gunakan Bahasa Melayu yang ringkas dan padat."
    )

def call_gemini(prompt: str, system_prompt: str = "") -> str:
    """Call Gemini REST API directly with automatic model failover / swap."""
    if not GEMINI_API_KEY:
        return ""

    sys_instruction = system_prompt or load_brain_prompt()
    body = {
        "contents": [{"parts": [{"text": prompt}]}],
        "systemInstruction": {"parts": [{"text": sys_instruction}]},
    }

    for model in GEMINI_MODELS:
        url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent?key={GEMINI_API_KEY}"
        try:
            r = requests.post(url, json=body, timeout=12)
            if r.status_code == 200:
                res_data = r.json()
                candidates = res_data.get("candidates", [])
                if candidates:
                    parts = candidates[0].get("content", {}).get("parts", [])
                    if parts:
                        return format_clean_telegram(parts[0].get("text", "").strip())
            else:
                app.logger.warning("Model %s ralat (%s): %s", model, r.status_code, r.text[:80])
                continue
        except Exception as e:
            app.logger.warning("Model %s exception: %s", model, e)
            continue

    return ""


# ── TradingView Webhook Endpoint ──────────────────────────────────────────────

@app.route("/tradingview", methods=["POST"])
def tradingview_alert():
    """Receives webhook payload from TradingView alert."""
    try:
        data = request.get_json(silent=True)
        if not data:
            raw = request.get_data(as_text=True)
            try:
                data = json.loads(raw)
            except Exception:
                data = {"message": raw}

        ticker = str(data.get("ticker") or data.get("symbol") or "Trading Signal")
        action = str(data.get("action") or data.get("order_action") or "ALERT").upper()
        price  = str(data.get("price") or data.get("close") or "")
        time_s = str(data.get("time") or "")
        msg    = str(data.get("message") or "")

        if "BUY" in action or "LONG" in action:
            badge = "🟢 <b>BUY / LONG SIGNAL</b>"
        elif "SELL" in action or "SHORT" in action:
            badge = "🔴 <b>SELL / SHORT SIGNAL</b>"
        else:
            badge = "🔔 <b>TRADINGVIEW ALERT</b>"

        lines = [
            badge,
            "────────────────────────",
            f"📊 <b>Pair:</b> <code>{ticker}</code>",
            f"🎯 <b>Isyarat:</b> <b>{action}</b>",
        ]
        if price:
            lines.append(f"💰 <b>Harga:</b> <code>{price}</code>")
        if time_s:
            lines.append(f"⏱ <b>Masa:</b> {time_s}")
        if msg:
            lines.append(f"\n📝 <b>Butiran:</b> {msg}")

        ai_prompt = (
            f"Alert triggered: {action} on {ticker} at price {price}. "
            f"Beri ulasan teknikal & peringatan risiko dalam 2 baris ringkas tanpa simbol bintang."
        )
        ai_analysis = call_gemini(ai_prompt)
        if ai_analysis:
            lines.append(f"\n💡 <b>Ulasan AI:</b>\n{ai_analysis}")
        lines.append(f"\n{DISCLAIMER_HTML}")

        formatted_msg = "\n".join(lines)
        send_telegram(formatted_msg)

        return jsonify({"status": "ok", "message": "Alert sent to Telegram"}), 200

    except Exception as e:
        app.logger.error("TradingView processing error: %s", e)
        return jsonify({"status": "error", "message": str(e)}), 500


# ── Telegram Webhook Endpoint (Optional Chat) ─────────────────────────────────

@app.route("/webhook", methods=["POST"])
def telegram_webhook():
    """Handles incoming Telegram messages if webhook is registered."""
    try:
        update = request.get_json(silent=True) or {}
        message = update.get("message")
        if not message:
            return "OK", 200

        chat_id = str(message["chat"]["id"])
        text = message.get("text", "").strip()

        if not text:
            return "OK", 200

        clean_lower = text.lower().strip()

        # 0. Start & Help
        if text.startswith("/start"):
            send_telegram(
                "👋 <b>Selamat Datang ke AI Trading Assistant!</b>\n"
                "Disambungkan terus ke TradingView untuk Kripto, Saham Bursa Malaysia & Global (NASDAQ/NYSE).\n"
                "────────────────────────\n\n"
                "💡 <b>Paling Mudah:</b> Anda <b>TIDAK PERLU</b> taip simbol '/' langsung! Boleh taip nama saham atau tanya soalan macam biasa.\n\n"
                "📌 <b>Contoh Taip Terus (Tanpa '/'):</b>\n"
                "🔹 <code>btc</code> atau <code>sol 4h</code> — Analisis Kripto\n"
                "🔹 <code>maybank</code> atau <code>cimb</code> — Saham Bursa Malaysia\n"
                "🔹 <code>nvda</code> atau <code>tsla 1d</code> — Saham US / NASDAQ\n"
                "🔹 <code>usdmyr</code> — Pasaran Forex\n"
                "🔹 <i>'tengok harga maybank'</i> — Analisis automatik\n"
                "🔹 <i>'panduan indikator'</i> — Cara pasang RSI & EMA\n"
                "🔹 <i>'setup alert'</i> — Cara sambung webhook TradingView\n\n"
                "🤖 Boleh juga gunakan arahan biasa:\n"
                f"🔹 <code>/status BTC</code> | <code>/indicator</code> | <code>/strategy</code>\n\n"
                f"{DISCLAIMER_HTML}",
                chat_id=chat_id,
            )
            return "OK", 200

        if text.startswith("/help") or clean_lower in ["help", "bantuan", "menu", "arahan"]:
            send_telegram(
                "🤖 <b>Panduan Penggunaan Bot:</b>\n"
                "────────────────────────\n"
                "💡 <i>Tip: Anda boleh taip terus tanpa simbol '/'!</i>\n\n"
                "🔹 <b>Carian Ticker Pantas:</b> Taip <code>btc</code>, <code>maybank</code>, <code>nvda</code>, atau <code>sol 4h</code>\n"
                "🔹 <b>Panduan Indikator:</b> Taip <i>'indikator'</i> atau <code>/indicator</code>\n"
                "🔹 <b>Setup Alert:</b> Taip <i>'alert'</i>, <i>'strategi'</i> atau <code>/strategy</code>\n"
                "🔹 <b>Tanya Soalan Terbuka:</b> Taip apa sahaja seperti <i>'adakah bagus beli btc sekarang?'</i>\n\n"
                f"{DISCLAIMER_HTML}",
                chat_id=chat_id,
            )
            return "OK", 200

        # 1. Panduan Indikator
        if text.startswith("/indicator") or text.startswith("/indikator") or clean_lower in ["indikator", "indicator"] or any(k in clean_lower for k in [
            "masuk indikator", "pasang indikator", "cara indikator", "panduan indikator", "setting rsi", "setting ema", "guna indikator"
        ]):
            guide = (
                "🛠 <b>PANDUAN MEMASUKKAN INDIKATOR DI TRADINGVIEW</b>\n"
                "────────────────────────\n\n"
                "1️⃣ <b>Buka Menu Indikator</b>\n"
                "Buka carta TradingView, klik butang <b>Indicators (fx)</b> pada bar atas.\n\n"
                "2️⃣ <b>Indikator Utama yang Disyorkan</b>\n\n"
                "🔹 <b>RSI (Relative Strength Index)</b>\n"
                "▫️ Taip 'RSI' dan pilih <i>Relative Strength Index</i>.\n"
                "▫️ Setting: Length 14.\n"
                "▫️ Panduan: Bawah 30 (Oversold / Potensi Beli), atas 70 (Overbought / Potensi Jual).\n\n"
                "🔹 <b>EMA Cross (Moving Average Exponential)</b>\n"
                "▫️ Masukkan EMA dua kali ke carta.\n"
                "▫️ EMA Pertama: Tukar Length kepada <b>20</b>.\n"
                "▫️ EMA Kedua: Tukar Length kepada <b>50</b>.\n"
                "▫️ Strategi: EMA 20 silang ke atas EMA 50 menandakan permulaan trend kenaikan.\n\n"
                "🔹 <b>MACD (Moving Average Convergence Divergence)</b>\n"
                "▫️ Mengesan momentum pasaran dan titik perubahan arah harga.\n\n"
                "👉 Taip <i>'strategi'</i> untuk melihat cara memasang alert webhook ke Telegram!\n\n"
                f"{DISCLAIMER_HTML}"
            )
            send_telegram(guide, chat_id=chat_id)
            return "OK", 200

        # 2. Panduan Strategi & Alert
        if text.startswith("/strategy") or text.startswith("/strategi") or clean_lower in ["strategi", "strategy", "alert", "webhook"] or any(k in clean_lower for k in [
            "cara buat alert", "pasang alert", "setup alert", "buat webhook", "sambung webhook", "panduan strategi"
        ]):
            guide = (
                "📈 <b>PANDUAN SETUP STRATEGI & ALERT WEBHOOK</b>\n"
                "────────────────────────\n\n"
                "1️⃣ <b>Cipta Alert Baharu</b>\n"
                "Di carta TradingView, klik ikon jam loceng ⏰ atau tekan <b>Alt + A</b>.\n\n"
                "2️⃣ <b>Tetapan Webhook URL</b>\n"
                "Buka tab <b>Notifications</b>, tandakan <b>Webhook URL</b> dan masukkan URL webhook anda.\n\n"
                "3️⃣ <b>Format Mesej Alert</b>\n"
                "Buka tab <b>Settings</b>, masukkan kod JSON ini ke dalam kotak <b>Message</b>:\n\n"
                "<code>{\n"
                '  "ticker": "{{ticker}}",\n'
                '  "action": "{{strategy.order.action}}",\n'
                '  "price": "{{close}}",\n'
                '  "time": "{{time}}",\n'
                '  "message": "Isyarat masuk dari strategi!"\n'
                "}</code>\n\n"
                "4️⃣ <b>Simpan Alert</b>\n"
                "Klik butang <b>Create</b>. Bot akan menghantar notifikasi kemas ke Telegram setiap kali alert berbunyi.\n\n"
                f"{DISCLAIMER_HTML}"
            )
            send_telegram(guide, chat_id=chat_id)
            return "OK", 200

        if text == "/ping" or clean_lower in ["ping", "test"]:
            send_telegram("🏓 Pong! Webhook aktif dengan sambungan TradingView pelbagai pasaran.", chat_id=chat_id)
            return "OK", 200

        # Helper to send structured TA card
        def send_ta_card(ta_data):
            rec = ta_data["recommendation"]
            if "STRONG_BUY" in rec:
                badge = "🟢🔥 <b>STRONG BUY</b>"
            elif "BUY" in rec:
                badge = "🟢 <b>BUY</b>"
            elif "STRONG_SELL" in rec:
                badge = "🔴🔥 <b>STRONG SELL</b>"
            elif "SELL" in rec:
                badge = "🔴 <b>SELL</b>"
            else:
                badge = "⚪ <b>NEUTRAL</b>"

            curr = ta_data["currency"]
            price_str = f"{curr}{ta_data['price']:,.2f}" if curr else f"{ta_data['price']:,.4f}"
            rsi_str = f"{ta_data['rsi']}"
            if ta_data['rsi'] >= 70:
                rsi_str += " ⚠️ Overbought"
            elif ta_data['rsi'] <= 30:
                rsi_str += " 💡 Oversold"

            ai_text = call_gemini(
                f"TradingView data for {ta_data['symbol']} ({ta_data['market']}): Price {price_str}, RSI {ta_data['rsi']}, Signal {rec}, EMA20 {ta_data['ema20']}, EMA50 {ta_data['ema50']}. Ulas dalam 3 baris ringkas tanpa simbol bintang."
            )
            msg = (
                f"📊 <b>ANALISIS PASARAN: {ta_data['symbol']}</b>\n"
                f"🏛 Pasaran: <b>{ta_data['market']}</b> ({ta_data['exchange']})\n"
                f"⏱ Timeframe: <code>{ta_data['interval']}</code>\n"
                f"────────────────────────\n"
                f"💰 <b>Harga Semasa:</b> <code>{price_str}</code>\n"
                f"🎯 <b>Isyarat:</b> {badge}\n"
                f"📈 <b>Skor Indikator:</b> 🟢 {ta_data['buy']} Beli | ⚪ {ta_data['neutral']} Neutral | 🔴 {ta_data['sell']} Jual\n\n"
                f"📋 <b>Indikator Utama:</b>\n"
                f"🔹 <b>RSI (14):</b> <code>{rsi_str}</code>\n"
                f"🔹 <b>MACD:</b> <code>{ta_data['macd']}</code>\n"
                f"🔹 <b>EMA 20:</b> <code>{curr}{ta_data['ema20']:,.2f}</code>\n"
                f"🔹 <b>EMA 50:</b> <code>{curr}{ta_data['ema50']:,.2f}</code>\n"
                f"🔹 <b>EMA 200:</b> <code>{curr}{ta_data['ema200']:,.2f}</code>\n\n"
                f"💡 <b>Ulasan AI:</b>\n{ai_text}\n\n"
                f"🌐 <a href=\"{ta_data['chart_url']}\">Buka Carta di TradingView</a>\n\n"
                f"{DISCLAIMER_HTML}\n"
            )
            chart_bytes = generate_candlestick_chart(ta_data["symbol"], ta_data["interval"], ta_data["market"])
            if chart_bytes:
                send_telegram_photo(chart_bytes, msg, chat_id=chat_id)
            else:
                send_telegram(msg, chat_id=chat_id)

        # 3. Arahan Standard /status atau /ta
        if text.startswith("/status") or text.startswith("/ta") or text.startswith("/analisa"):
            parts = text.split()
            sym = parts[1] if len(parts) > 1 else "BTC"
            tf = parts[2] if len(parts) > 2 else "1h"
            ta = get_tradingview_ta(sym, tf)
            if ta:
                send_ta_card(ta)
            else:
                send_telegram(f"❌ Simbol tidak ditemui di TradingView: <code>{sym}</code>", chat_id=chat_id)
            return "OK", 200

        # 4. Permintaan Carta & Analisis Pasaran
        # Menyokong: 'chart xrp', 'carta btc', 'tunjuk graf maybank', 'xrp 4h', 'sol', 'user request chart xrp'
        status_handled = False
        clean_words = re.findall(r'[A-Za-z0-9]+', text)
        clean_upper = [w.upper() for w in clean_words]

        CHART_AND_TA_TRIGGERS = {
            "CHART", "CARTA", "GRAF", "GRAPH", "KANDIL", "CANDLESTICK",
            "STATUS", "HARGA", "PRICE", "ANALIS", "ANALISIS", "ANALISA",
            "TREND", "TENGOK", "CHECK", "SEMAK", "VIEW", "SHOW"
        }
        is_ta_or_chart_intent = any(w in CHART_AND_TA_TRIGGERS for w in clean_upper) or len(clean_words) <= 3

        if is_ta_or_chart_intent:
            timeframe = "1h"
            for w in clean_words:
                if w.lower() in INTERVAL_MAP:
                    timeframe = w.lower()
                    break

            IGNORE_WORDS = {
                "CHART", "CARTA", "GRAF", "GRAPH", "KANDIL", "CANDLESTICK",
                "USER", "REQUEST", "MINTA", "TUNJUK", "BAGI", "LIHAT", "TENGOK",
                "TOLONG", "NAK", "VIEW", "SHOW", "PLEASE", "UNTUK", "INI", "ITU",
                "PADA", "HARGA", "STATUS", "ANALISIS", "ANALISA", "TREND", "CHECK",
                "SEMAK", "DAN", "SAYA", "KAU", "HARI", "MACAM", "MANA", "TAK",
                "DI", "KE", "DARI", "PASARAN", "BOLEH", "BERIKAN", "APA", "APAKAH",
                "BERAPA", "TF", "TIMEFRAME", "1M", "5M", "15M", "1H", "4H", "1D", "1W"
            }

            for w in clean_words:
                if len(w) >= 2 and w.upper() not in IGNORE_WORDS:
                    ta_res = get_tradingview_ta(w, timeframe)
                    if ta_res:
                        send_ta_card(ta_res)
                        status_handled = True
                        break

        # 6. Soalan Terbuka Umum
        if not status_handled:
            context_ta = ""
            words = re.findall(r'[A-Za-z0-9]+', text)
            common_words = {
                "APA", "ADA", "ADAKAH", "BAGUS", "BOLEH", "BELI", "JUAL", "SEKARANG",
                "HARI", "INI", "NAK", "UNTUK", "SAYA", "KAU", "MACAM", "MANA", "KENAPA",
                "CARA", "KALAU", "JIKA", "TAK", "PATUT", "HOLD"
            }
            for w in words:
                if len(w) >= 2 and w.upper() not in common_words:
                    ta_cand = get_tradingview_ta(w, "1h")
                    if ta_cand:
                        curr = ta_cand["currency"]
                        p_s = f"{curr}{ta_cand['price']:,.2f}" if curr else str(ta_cand['price'])
                        context_ta = (
                            f"Data Pasaran TradingView Semasa untuk {ta_cand['symbol']} ({ta_cand['market']}):\n"
                            f"Harga: {p_s}, Isyarat Teknikal: {ta_cand['recommendation']}, "
                            f"RSI(14): {ta_cand['rsi']}, MACD: {ta_cand['macd']}, "
                            f"EMA20: {ta_cand['ema20']}, EMA50: {ta_cand['ema50']}."
                        )
                        break

            prompt = f"Data Pasaran:\n{context_ta}\n\nSoalan: {text}" if context_ta else text
            ai_reply = call_gemini(prompt)
            final_reply = attach_disclaimer(ai_reply) if ai_reply else "Maaf, tiada respon dijana."
            send_telegram(final_reply, chat_id=chat_id)

        return "OK", 200
    except Exception as e:
        app.logger.error("Telegram webhook error: %s", e)
        return "OK", 200


# ── Health Check ──────────────────────────────────────────────────────────────

@app.route("/", methods=["GET"])
def index():
    return jsonify({
        "status": "online",
        "service": "TradingView & Telegram Bot",
        "platform": "PythonAnywhere",
        "endpoints": {
            "tradingview_webhook": "/tradingview",
            "telegram_webhook": "/webhook",
        }
    })


if __name__ == "__main__":
    app.run(port=5000, debug=True)
