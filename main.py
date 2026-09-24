"""
Telegram AI Bot — Powered by Gemini
- Receives messages via Telegram webhook
- Answers using Google Gemini AI
- Deployable to Render (no PC needed)
"""

import json
import logging
import os
import httpx
from fastapi import FastAPI, Request, Response
from fastapi.responses import JSONResponse
from google import genai
from google.genai import types

# Load .env if present
try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

# ── Logging ────────────────────────────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-8s  %(message)s",
)
logger = logging.getLogger("tg-ai-bot")

# ── Config (loaded from environment variables — set these in Render / .env) ───
TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "")
GEMINI_API_KEY     = os.environ.get("GEMINI_API_KEY", "")
TELEGRAM_CHAT_ID   = os.environ.get("TELEGRAM_CHAT_ID", "")   # optional allowlist

# ── Gemini client ──────────────────────────────────────────────────────────────
gemini = genai.Client(api_key=GEMINI_API_KEY) if GEMINI_API_KEY else None

# ── Model fallback list ────────────────────────────────────────────────────────
# Ordered best→fallback. Auto-switches on quota (429/ResourceExhausted).
# RPM from your account:  Lite models = 15 RPM,  Flash = 5 RPM
GEMINI_MODELS = [
    "gemini-3.8-flash",       # Best / latest — try first (5 RPM)
    "gemini-3.7-flash",       # (5 RPM)
    "gemini-3.6-flash",       # (5 RPM)
    "gemini-3.5-flash",       # (5 RPM)
    "gemini-3.5-flash-lite",  # High quota fallback (15 RPM)
    "gemini-3.1-flash-lite",  # High quota fallback (15 RPM)
    "gemini-3.1-flash",       # (5 RPM)
    "gemini-3-flash",         # (5 RPM)
    "gemini-2.5-flash",       # (5 RPM)
    "gemini-2.5-flash-lite",  # Last resort (10 RPM)
]

SYSTEM_PROMPT = """You are a smart AI trading assistant connected to TradingView.
You can answer questions about:
- Crypto markets (Bitcoin, Ethereum, etc.)
- Trading strategies and technical analysis
- Market news and trends
- General finance questions

Be concise, clear, and helpful. Format responses for Telegram (plain text only, no markdown headers).
If asked about real-time prices, remind the user you work best with TradingView alert data piped to you."""

# ── Conversation history (in-memory per chat_id) ───────────────────────────────
conversations: dict[str, list] = {}

# ── FastAPI app ────────────────────────────────────────────────────────────────
app = FastAPI(title="Telegram AI Bot", version="1.0.0")


# ── Telegram helpers ───────────────────────────────────────────────────────────

async def send_message(chat_id: int | str, text: str) -> None:
    """Send a Telegram message."""
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    async with httpx.AsyncClient(verify=False) as client:
        await client.post(url, json={
            "chat_id": chat_id,
            "text": text,
            "parse_mode": "HTML",
        })


async def send_typing(chat_id: int | str) -> None:
    """Show 'typing...' indicator."""
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendChatAction"
    async with httpx.AsyncClient(verify=False) as client:
        await client.post(url, json={"chat_id": chat_id, "action": "typing"})


async def set_webhook(webhook_url: str) -> dict:
    """Register webhook URL with Telegram."""
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/setWebhook"
    async with httpx.AsyncClient(verify=False) as client:
        resp = await client.post(url, json={"url": webhook_url})
        return resp.json()


# ── Gemini AI helper with auto-fallback ────────────────────────────────────────

async def ask_gemini(chat_id: str, user_message: str) -> str:
    """
    Send to Gemini with automatic model fallback.
    Tries each model in GEMINI_MODELS order.
    Switches on quota exhaustion (429 / ResourceExhausted / not_found).
    """
    if not gemini:
        return "⚠️ GEMINI_API_KEY is not configured."

    history = conversations.setdefault(chat_id, [])
    history.append(types.Content(role="user", parts=[types.Part(text=user_message)]))

    last_error = None
    for model in GEMINI_MODELS:
        try:
            logger.info("Trying model: %s", model)
            response = gemini.models.generate_content(
                model=model,
                contents=history,
                config=types.GenerateContentConfig(
                    system_instruction=SYSTEM_PROMPT,
                    temperature=0.7,
                    max_output_tokens=1024,
                ),
            )
            reply = response.text or "Maaf, saya tidak dapat menjana respons."

            # Save reply to history (keep last 40 turns)
            history.append(types.Content(role="model", parts=[types.Part(text=reply)]))
            if len(history) > 40:
                conversations[chat_id] = history[-40:]

            logger.info("✅ Response via %s", model)
            return reply

        except Exception as e:
            err = str(e).lower()
            if any(k in err for k in ["quota", "resource_exhausted", "429", "rate", "limit", "not_found", "404", "no longer available"]):
                logger.warning("⚠️ %s unavailable (%s), trying next model...", model, str(e)[:60])
                last_error = e
                continue
            else:
                logger.error("❌ Gemini error on %s: %s", model, e)
                history.pop()
                return f"⚠️ Error: {e}"

    # All models exhausted
    history.pop()
    logger.error("❌ All models exhausted. Last: %s", last_error)
    return "⚠️ Semua model sedang penuh (quota exhausted). Cuba lagi dalam beberapa minit."



# ── Command handlers ───────────────────────────────────────────────────────────


async def handle_command(chat_id: str, command: str, text: str) -> None:
    """Handle Telegram slash commands."""
    if command == "/start":
        await send_message(chat_id,
            "👋 <b>Helo! Saya AI Trading Assistant anda.</b>\n\n"
            "Saya boleh bantu dengan:\n"
            "📊 Analisis teknikal crypto\n"
            "📰 Berita pasaran\n"
            "💡 Strategi trading\n"
            "❓ Soalan umum tentang finance\n\n"
            "Taip sebarang soalan untuk mula!"
        )
    elif command == "/help":
        await send_message(chat_id,
            "🤖 <b>Arahan tersedia:</b>\n\n"
            "/start — Mesej selamat datang\n"
            "/help — Tunjuk senarai arahan\n"
            "/clear — Kosongkan sejarah perbualan\n"
            "/ping — Semak sama ada bot aktif\n\n"
            "Atau taip sebarang soalan terus!"
        )
    elif command == "/clear":
        conversations.pop(chat_id, None)
        await send_message(chat_id, "🧹 Sejarah perbualan telah dikosongkan.")
    elif command == "/ping":
        await send_message(chat_id, "🏓 Pong! Bot aktif dan bersedia.")
    else:
        await send_message(chat_id, f"❓ Arahan tidak dikenali: {command}\nTaip /help untuk senarai arahan.")


# ── Webhook endpoint ───────────────────────────────────────────────────────────

@app.post("/webhook")
async def telegram_webhook(request: Request):
    """Handle incoming Telegram updates."""
    try:
        update = await request.json()
    except Exception:
        return Response(status_code=200)

    message = update.get("message") or update.get("edited_message")
    if not message:
        return Response(status_code=200)

    chat_id  = str(message["chat"]["id"])
    text     = message.get("text", "").strip()
    username = message.get("from", {}).get("first_name", "User")

    if not text:
        return Response(status_code=200)

    logger.info("Message from %s (%s): %s", username, chat_id, text[:80])

    # Show typing indicator
    await send_typing(chat_id)

    # Handle commands
    if text.startswith("/"):
        command = text.split()[0].lower()
        await handle_command(chat_id, command, text)
    else:
        # Ask Gemini
        reply = await ask_gemini(chat_id, text)
        await send_message(chat_id, reply)

    return Response(status_code=200)


# ── Health check & webhook setup ───────────────────────────────────────────────

@app.get("/")
async def health():
    return {"status": "ok", "bot": "Telegram AI Bot"}


@app.get("/set-webhook")
async def setup_webhook(url: str):
    """Call this once after deploying to Render to register the webhook URL.
    Example: https://your-app.onrender.com/set-webhook?url=https://your-app.onrender.com/webhook
    """
    result = await set_webhook(url)
    return result
