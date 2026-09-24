"""
Telegram AI Bot — Local Polling Runner
Menjalankan bot terus dari komputer / Antigravity IDE tanpa perlukan server atau webhook.
"""

import asyncio
import io
import logging
import os
import sys
import httpx
from dotenv import load_dotenv
from google import genai
from google.genai import types

if sys.platform == "win32":
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8", errors="replace")

# ── Load Environment Variables ────────────────────────────────────────────────
load_dotenv()

TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "")
GEMINI_API_KEY     = os.environ.get("GEMINI_API_KEY", "")
TELEGRAM_CHAT_ID   = os.environ.get("TELEGRAM_CHAT_ID", "")

# ── Logging ────────────────────────────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-8s  %(message)s",
)
logger = logging.getLogger("tg-ai-bot-local")

# ── Gemini Client ──────────────────────────────────────────────────────────────
gemini = genai.Client(api_key=GEMINI_API_KEY) if GEMINI_API_KEY else None

GEMINI_MODELS = [
    "gemini-3-flash-preview",
    "gemini-3.6-flash",
    "gemini-3.5-flash-lite",
    "gemini-flash-latest",
    "gemini-3.5-flash",
    "gemini-3.7-flash",
    "gemini-3.8-flash",
]

SYSTEM_PROMPT = """You are an expert AI Trading and Crypto Assistant.
You provide clear, accurate, and insightful technical analysis, crypto market trends, trading strategies, and risk management tips.
Always be polite, concise, and helpful. You understand and can reply in Bahasa Melayu or English depending on user language.
If asked about real-time prices, remind the user you work best with TradingView alert data piped to you."""

conversations: dict[str, list] = {}

# ── Telegram Functions ─────────────────────────────────────────────────────────

async def send_message(client: httpx.AsyncClient, chat_id: int | str, text: str) -> None:
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    try:
        await client.post(url, json={
            "chat_id": chat_id,
            "text": text,
            "parse_mode": "HTML",
        }, timeout=10.0)
    except Exception as e:
        logger.error("Gagal hantar mesej: %s", e)

async def send_typing(client: httpx.AsyncClient, chat_id: int | str) -> None:
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendChatAction"
    try:
        await client.post(url, json={"chat_id": chat_id, "action": "typing"}, timeout=5.0)
    except Exception:
        pass

async def ask_gemini(chat_id: str, user_message: str) -> str:
    if not gemini:
        return "⚠️ GEMINI_API_KEY belum dikonfigurasi dalam fail .env."

    history = conversations.setdefault(chat_id, [])
    history.append(types.Content(role="user", parts=[types.Part(text=user_message)]))

    last_error = None
    for model in GEMINI_MODELS:
        try:
            logger.info("Mencuba model: %s", model)
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
            history.append(types.Content(role="model", parts=[types.Part(text=reply)]))
            if len(history) > 40:
                conversations[chat_id] = history[-40:]
            logger.info("✅ Berjaya respon melalui %s", model)
            return reply
        except Exception as e:
            err = str(e).lower()
            logger.warning("⚠️ Model %s ralat: %s", model, str(e)[:80])
            last_error = e
            continue

    history.pop()
    return f"⚠️ Semua model sibuk. Sila cuba sebentar lagi. (Ralat: {last_error})"

async def handle_command(client: httpx.AsyncClient, chat_id: str, command: str) -> None:
    if command == "/start":
        await send_message(client, chat_id,
            "👋 <b>Helo! Saya AI Trading Assistant anda.</b>\n\n"
            "Saya boleh bantu anda dengan:\n"
            "📊 Analisis teknikal & trend pasaran crypto\n"
            "💡 Strategi trading & pengurusan risiko\n"
            "📰 Soalan umum pasaran kewangan\n\n"
            "Taip sebarang soalan sekarang untuk mula berbual!"
        )
    elif command == "/help":
        await send_message(client, chat_id,
            "🤖 <b>Senarai Arahan:</b>\n\n"
            "/start — Mesej selamat datang\n"
            "/help — Senarai arahan\n"
            "/clear — Kosongkan ingatan perbualan\n"
            "/ping — Uji status bot"
        )
    elif command == "/clear":
        conversations.pop(chat_id, None)
        await send_message(client, chat_id, "🧹 Sejarah perbualan telah dikosongkan.")
    elif command == "/ping":
        await send_message(client, chat_id, "🏓 Pong! Bot aktif dan bersedia di Antigravity IDE.")
    else:
        await send_message(client, chat_id, f"❓ Arahan tidak dikenali: {command}\nTaip /help untuk bantuan.")

# ── Main Polling Loop ──────────────────────────────────────────────────────────

async def main():
    if not TELEGRAM_BOT_TOKEN:
        print("❌ Ralat: TELEGRAM_BOT_TOKEN tiada dalam fail .env!")
        return

    print("🚀 Memulakan Telegram AI Bot dalam mod Polling...")
    print(f"🤖 Bot sedang mendengar mesej dari Telegram...")
    print("👉 Tekan Ctrl+C di terminal ini untuk berhenti.\n")

    async with httpx.AsyncClient(timeout=35.0) as client:
        # Padam sebarang webhook lama supaya Telegram hantar mesej secara polling
        await client.post(f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/deleteWebhook")

        offset = 0
        while True:
            try:
                url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/getUpdates"
                resp = await client.get(url, params={"offset": offset, "timeout": 20})
                data = resp.json()

                if not data.get("ok"):
                    await asyncio.sleep(2)
                    continue

                for update in data.get("result", []):
                    offset = update["update_id"] + 1
                    message = update.get("message")
                    if not message:
                        continue

                    chat_id = str(message["chat"]["id"])
                    text = message.get("text", "").strip()
                    first_name = message.get("from", {}).get("first_name", "Pengguna")

                    if not text:
                        continue

                    print(f"📩 Mesej diterima daripada {first_name} ({chat_id}): {text}")

                    # Tunjuk indikator 'typing...'
                    await send_typing(client, chat_id)

                    if text.startswith("/"):
                        cmd = text.split()[0].lower()
                        await handle_command(client, chat_id, cmd)
                    else:
                        reply = await ask_gemini(chat_id, text)
                        await send_message(client, chat_id, reply)

            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error("Ralat gelung polling: %s", e)
                await asyncio.sleep(3)

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\n🛑 Bot dimatikan.")
