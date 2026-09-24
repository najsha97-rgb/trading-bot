"""
Telegram AI Bot + TradingView Webhook Receiver for PythonAnywhere (WSGI/Flask)
- Receives alerts from TradingView via POST /tradingview
- Sends alerts directly to Telegram with Gemini AI analysis
- Handles user chats from Telegram via POST /webhook
- 100% compatible with PythonAnywhere free tier
"""

import json
import logging
import os
import requests
from flask import Flask, jsonify, request

# Load .env if present
try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

app = Flask(__name__)
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")

# ── Config ────────────────────────────────────────────────────────────────────
TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT_ID   = os.environ.get("TELEGRAM_CHAT_ID", "")
GEMINI_API_KEY     = os.environ.get("GEMINI_API_KEY", "")

GEMINI_MODEL = "gemini-3-flash-preview"

# In-memory history for Telegram chats (up to 20 turns)
conversations = {}


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
        # Support both JSON payload and raw text
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

        # Format visual indicator
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

        # Get AI quick advice
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

        if text == "/start":
            send_telegram(
                "👋 <b>Helo! Saya Trading Bot anda.</b>\n\n"
                "Saya sedia menerima isyarat dari <b>TradingView</b> dan menjawab soalan trading anda.\n"
                "Taip sebarang soalan pasaran untuk mula!",
                chat_id=chat_id,
            )
        elif text == "/ping":
            send_telegram("🏓 Pong! PythonAnywhere Webhook aktif.", chat_id=chat_id)
        else:
            ai_reply = call_gemini(text, "You are a helpful AI crypto & trading assistant.")
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
