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

app = Flask(__name__)
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")

# ── Config ────────────────────────────────────────────────────────────────────
TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT_ID   = os.environ.get("TELEGRAM_CHAT_ID", "")
GEMINI_API_KEY     = os.environ.get("GEMINI_API_KEY", "")

GEMINI_MODEL = "gemini-3-flash-preview"


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
                "ema20": round(inds.get("EMA20", 0) or 0, 2),
                "ema50": round(inds.get("EMA50", 0) or 0, 2),
                "ema200": round(inds.get("EMA200", 0) or 0, 2),
            }
        except Exception:
            continue
    return None


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
    """Call Gemini REST API directly using requests (PythonAnywhere compatible)."""
    if not GEMINI_API_KEY:
        return ""

    url = f"https://generativelanguage.googleapis.com/v1beta/models/{GEMINI_MODEL}:generateContent?key={GEMINI_API_KEY}"
    body = {
        "contents": [{"parts": [{"text": prompt}]}],
    }
    sys_instruction = system_prompt or load_brain_prompt()
    body["systemInstruction"] = {"parts": [{"text": sys_instruction}]}

    try:
        r = requests.post(url, json=body, timeout=15)
        if r.status_code == 200:
            res_data = r.json()
            candidates = res_data.get("candidates", [])
            if candidates:
                parts = candidates[0].get("content", {}).get("parts", [])
                if parts:
                    return format_clean_telegram(parts[0].get("text", "").strip())
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
                "👋 <b>Selamat Datang ke AI Trading Assistant!</b>\n"
                "Disambungkan terus ke data langsung TradingView.\n"
                "────────────────────────\n\n"
                "📌 <b>Arahan Pantas:</b>\n"
                "🔹 <code>/status BTC</code> — Semak status teknikal semasa\n"
                "🔹 <code>/status SOL 4h</code> — Semak status timeframe 4 jam\n"
                "🔹 <code>/indicator</code> — Panduan masukkan indikator TradingView\n"
                "🔹 <code>/strategy</code> — Panduan setup alert strategi ke Telegram\n"
                "🔹 <code>/clear</code> — Kosongkan ingatan perbualan\n\n"
                "💬 Anda juga boleh bertanya soalan pasaran terus!",
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
                    f"TradingView data for {ta['symbol']}: Price {p_str}, RSI {ta['rsi']}, Signal {rec}, EMA20 {ta['ema20']}, EMA50 {ta['ema50']}. Ulas dalam 3 baris ringkas tanpa simbol bintang."
                )
                msg = (
                    f"📊 <b>ANALISIS PASARAN: {ta['symbol']}</b>\n"
                    f"⏱ Timeframe: <code>{ta['interval']}</code>\n"
                    f"────────────────────────\n"
                    f"💰 <b>Harga Semasa:</b> <code>{p_str}</code>\n"
                    f"🎯 <b>Isyarat:</b> <b>{badge}</b>\n"
                    f"📈 <b>Skor Indikator:</b> 🟢 {ta['buy']} Beli | ⚪ {ta['neutral']} Neutral | 🔴 {ta['sell']} Jual\n\n"
                    f"📋 <b>Indikator Utama:</b>\n"
                    f"🔹 <b>RSI (14):</b> <code>{ta['rsi']}</code>\n"
                    f"🔹 <b>MACD:</b> <code>{ta['macd']}</code>\n"
                    f"🔹 <b>EMA 20:</b> <code>${ta['ema20']:,.2f}</code>\n"
                    f"🔹 <b>EMA 50:</b> <code>${ta['ema50']:,.2f}</code>\n\n"
                    f"💡 <b>Ulasan AI:</b>\n{ai_text}"
                )
                send_telegram(msg, chat_id=chat_id)
            else:
                send_telegram(f"❌ Tidak dapat mengambil data TradingView untuk {sym}.", chat_id=chat_id)

        elif text.startswith("/indicator") or text.startswith("/indikator"):
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
                "👉 Taip <code>/strategy</code> untuk melihat cara memasang alert webhook ke Telegram!"
            )
            send_telegram(guide, chat_id=chat_id)

        elif text.startswith("/strategy") or text.startswith("/strategi"):
            guide = (
                "📈 <b>PANDUAN SETUP STRATEGI & ALERT WEBHOOK</b>\n"
                "────────────────────────\n\n"
                "1️⃣ <b>Cipta Alert Baharu</b>\n"
                "Di carta TradingView, klik ikon jam loceng ⏰ atau tekan <b>Alt + A</b>.\n\n"
                "2️⃣ <b>Tetapan Webhook URL</b>\n"
                "Buka tab <b>Notifications</b>, tandakan <b>Webhook URL</b> dan masukkan:\n"
                "<code>https://NAMA_USER.pythonanywhere.com/tradingview</code>\n\n"
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
                "Klik butang <b>Create</b>. Bot akan menghantar notifikasi kemas ke Telegram setiap kali alert berbunyi."
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
                        f"TradingView Technical Data for {ta['symbol']}:\n"
                        f"Harga: ${ta['price']}, Isyarat: {ta['recommendation']} "
                        f"(Buy:{ta['buy']}, Sell:{ta['sell']}, Neutral:{ta['neutral']}), "
                        f"RSI(14): {ta['rsi']}, MACD: {ta['macd']}, EMA20: {ta['ema20']}, EMA50: {ta['ema50']}."
                    )
            prompt = f"Data Pasaran:\n{context}\n\nSoalan: {text}" if context else text
            ai_reply = call_gemini(prompt)
            send_telegram(ai_reply or "Maaf, tiada respon dijana.", chat_id=chat_id)

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
