"""
Telegram AI Bot + TradingView Webhook Receiver for PythonAnywhere (WSGI/Flask)
- Receives alerts from TradingView via POST /tradingview
- Live TradingView technical indicators query via /status
- Step-by-step indicator & strategy guides via /indicator and /strategy
- Sends alerts directly to Telegram with Gemini AI analysis
- Handles user chats from Telegram via POST /webhook
- 100% compatible with PythonAnywhere free tier
"""

import json
import logging
import os
import re
import requests
from flask import Flask, jsonify, request

# Load .env if present
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

app = Flask(__name__)
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")

# ── Config ────────────────────────────────────────────────────────────────────
TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT_ID   = os.environ.get("TELEGRAM_CHAT_ID", "")
GEMINI_API_KEY     = os.environ.get("GEMINI_API_KEY", "")

GEMINI_MODEL = "gemini-3-flash-preview"


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
        "text": text,
        "parse_mode": "HTML",
        "disable_web_page_preview": True,
    }
    try:
        r = requests.post(url, json=payload, timeout=10)
        return r.status_code == 200
    except Exception as e:
        app.logger.error("Failed to send Telegram message: %s", e)
        return False


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

def clean_crypto_symbol(raw: str) -> str:
    raw = raw.upper().strip().replace("/", "").replace("-", "").replace("PERP", "")
    known = {
        "BITCOIN": "BTCUSDT",
        "ETHEREUM": "ETHUSDT",
        "SOLANA": "SOLUSDT",
        "RIPPLE": "XRPUSDT",
        "DOGECOIN": "DOGEUSDT",
        "CARDANO": "ADAUSDT",
    }
    if raw in known:
        return known[raw]
    if not raw.endswith("USDT") and not raw.endswith("USD") and not raw.endswith("BUSD"):
        return f"{raw}USDT"
    return raw

def get_tradingview_ta(raw_symbol: str, interval_key: str = "1h") -> dict | None:
    if not HAS_TV_TA:
        return None

    sym = clean_crypto_symbol(raw_symbol)
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

    for ex in ["BINANCE", "BYBIT", "OKX", "COINBASE"]:
        try:
            handler = TA_Handler(
                symbol=sym,
                screener="crypto",
                exchange=ex,
                interval=interval,
            )
            analysis = handler.get_analysis()
            inds = analysis.indicators
            summary = analysis.summary

            return {
                "symbol": sym,
                "exchange": ex,
                "interval": interval_key,
                "price": inds.get("close"),
                "recommendation": summary.get("RECOMMENDATION", "NEUTRAL"),
                "buy": summary.get("BUY", 0),
                "sell": summary.get("SELL", 0),
                "neutral": summary.get("NEUTRAL", 0),
                "rsi": round(inds.get("RSI", 0) or 0, 2),
                "macd": round(inds.get("MACD.macd", 0) or 0, 2),
                "macd_signal": round(inds.get("MACD.signal", 0) or 0, 2),
                "ema20": round(inds.get("EMA20", 0) or 0, 2),
                "ema50": round(inds.get("EMA50", 0) or 0, 2),
                "ema200": round(inds.get("EMA200", 0) or 0, 2),
            }
        except Exception:
            continue
    return None


# ── Gemini Helper (REST API) ──────────────────────────────────────────────────

def call_gemini(prompt: str, system_prompt: str = "") -> str:
    """Call Gemini REST API directly using requests (PythonAnywhere compatible)."""
    if not GEMINI_API_KEY:
        return ""

    url = f"https://generativelanguage.googleapis.com/v1beta/models/{GEMINI_MODEL}:generateContent?key={GEMINI_API_KEY}"
    body = {
        "contents": [{"parts": [{"text": prompt}]}],
    }
    if system_prompt:
        body["systemInstruction"] = {"parts": [{"text": system_prompt}]}

    try:
        r = requests.post(url, json=body, timeout=15)
        if r.status_code == 200:
            res_data = r.json()
            candidates = res_data.get("candidates", [])
            if candidates:
                parts = candidates[0].get("content", {}).get("parts", [])
                if parts:
                    return parts[0].get("text", "").strip()
        else:
            app.logger.warning("Gemini API error: %s - %s", r.status_code, r.text[:120])
    except Exception as e:
        app.logger.error("Error calling Gemini: %s", e)

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
            "━━━━━━━━━━━━━━━━━━━━",
            f"📊 <b>Pair/Ticker:</b> <code>{ticker}</code>",
            f"🎯 <b>Action:</b> <b>{action}</b>",
        ]
        if price:
            lines.append(f"💰 <b>Price:</b> <code>{price}</code>")
        if time_s:
            lines.append(f"⏱ <b>Time:</b> {time_s}")
        if msg:
            lines.append(f"\n📝 <b>Details:</b> {msg}")

        ai_prompt = (
            f"Alert triggered: {action} on {ticker} at price {price}. Alert message: {msg}. "
            f"Give a 2-sentence concise trading risk & technical reminder in Bahasa Melayu or English."
        )
        ai_analysis = call_gemini(ai_prompt, "You are a professional crypto & forex risk manager.")
        if ai_analysis:
            lines.append(f"\n🧠 <b>AI Quick Take:</b>\n<i>{ai_analysis}</i>")

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

        if text.startswith("/start"):
            send_telegram(
                "👋 <b>Helo! Saya AI Trading Assistant anda yang disambung ke TradingView.</b>\n\n"
                "<b>Arahan yang boleh anda cuba:</b>\n"
                "📊 <code>/status BTC</code> — Semak status teknikal live TradingView\n"
                "📊 <code>/status SOL 4h</code> — Semak status timeframe 4 Jam\n"
                "🛠 <code>/indicator</code> — Panduan masukkan indikator dalam TradingView\n"
                "📈 <code>/strategy</code> — Panduan setup alert strategi ke Telegram\n"
                "🧹 <code>/clear</code> — Kosongkan ingatan perbualan\n\n"
                "Atau taip sebarang soalan pasaran terus!",
                chat_id=chat_id,
            )
        elif text.startswith("/status") or text.startswith("/ta"):
            parts = text.split()
            sym = parts[1] if len(parts) > 1 else "BTC"
            tf = parts[2] if len(parts) > 2 else "1h"
            ta = get_tradingview_ta(sym, tf)
            if ta:
                rec = ta["recommendation"]
                badge = "🟢 BUY" if "BUY" in rec else ("🔴 SELL" if "SELL" in rec else "⚪ NEUTRAL")
                p_str = f"${ta['price']:,.2f}" if isinstance(ta['price'], (int, float)) else str(ta['price'])
                ai_text = call_gemini(
                    f"TradingView data for {ta['symbol']}: Price {p_str}, RSI {ta['rsi']}, Signal {rec}, EMA20 {ta['ema20']}, EMA50 {ta['ema50']}. Give a 3-sentence technical summary.",
                    "Expert Crypto Technical Analyst"
                )
                msg = (
                    f"📊 <b>STATUS TRADINGVIEW: {ta['symbol']}</b> (TF: <code>{ta['interval']}</code>)\n"
                    f"━━━━━━━━━━━━━━━━━━━━\n"
                    f"💰 <b>Harga Semasa:</b> <code>{p_str}</code>\n"
                    f"🎯 <b>Isyarat Teknikal:</b> <b>{badge}</b>\n"
                    f"📈 <b>Skor:</b> 🟢 {ta['buy']} | ⚪ {ta['neutral']} | 🔴 {ta['sell']}\n\n"
                    f"• <b>RSI (14):</b> <code>{ta['rsi']}</code>\n"
                    f"• <b>MACD:</b> <code>{ta['macd']}</code>\n"
                    f"• <b>EMA 20:</b> <code>${ta['ema20']:,.2f}</code>\n"
                    f"• <b>EMA 50:</b> <code>${ta['ema50']:,.2f}</code>\n\n"
                    f"🧠 <b>Ulasan AI:</b>\n{ai_text}"
                )
                send_telegram(msg, chat_id=chat_id)
            else:
                send_telegram(f"❌ Tidak dapat mengambil data TradingView untuk {sym}.", chat_id=chat_id)

        elif text.startswith("/indicator") or text.startswith("/indikator"):
            guide = (
                "🛠 <b>CARA MEMASUKKAN INDIKATOR DI TRADINGVIEW:</b>\n"
                "━━━━━━━━━━━━━━━━━━━━\n"
                "1. Buka carta TradingView, klik butang <b>Indicators (fx)</b> di bar atas.\n"
                "2. <b>Relative Strength Index (RSI):</b> Taip 'RSI' (Setting: 14).\n"
                "3. <b>EMA (Exponential Moving Average):</b> Masukkan 2 kali (EMA 20 & EMA 50).\n"
                "4. <b>MACD:</b> Untuk mengesan momentum pembalikan arah.\n\n"
                "👉 Taip <code>/strategy</code> untuk cara pasang Alert Webhook ke Telegram!"
            )
            send_telegram(guide, chat_id=chat_id)

        elif text.startswith("/strategy") or text.startswith("/strategi"):
            guide = (
                "📈 <b>CARA SETUP ALERT STRATEGI KE TELEGRAM:</b>\n"
                "━━━━━━━━━━━━━━━━━━━━\n"
                "1. Di carta TradingView, klik ikon jam loceng (Alert / Alt+A).\n"
                "2. Di tab <b>Notifications</b>: Tanda <b>Webhook URL</b> dan masukkan URL webhook anda.\n"
                "3. Di tab <b>Settings (Message)</b>: Masukkan JSON alert.\n"
                "4. Klik <b>Create</b>. Selesai!"
            )
            send_telegram(guide, chat_id=chat_id)

        elif text == "/ping":
            send_telegram("🏓 Pong! Webhook aktif.", chat_id=chat_id)
        else:
            match = re.search(r'\b(btc|eth|sol|xrp|doge|ada|bnb|avax|link|near|sui|pepe|bitcoin|ethereum|solana)\b', text, re.IGNORECASE)
            context = ""
            if match and any(k in text.lower() for k in ["status", "harga", "price", "analis", "analisis", "signal", "trend", "tengok"]):
                ta = get_tradingview_ta(match.group(1), "1h")
                if ta:
                    context = (
                        f"Live TradingView technical indicators: Pair {ta['symbol']}, Price ${ta['price']}, Signal {ta['recommendation']} "
                        f"(Buy {ta['buy']}, Sell {ta['sell']}), RSI {ta['rsi']}, EMA20 {ta['ema20']}, EMA50 {ta['ema50']}."
                    )
            prompt = f"Data Pasaran:\n{context}\n\nSoalan: {text}" if context else text
            ai_reply = call_gemini(prompt, "You are an expert AI trading & crypto assistant.")
            send_telegram(ai_reply or "Maaf, tidak dapat menjana respons.", chat_id=chat_id)

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
