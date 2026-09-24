"""
Telegram AI Bot — Local Polling Runner + TradingView Live Data
- Mengambil data teknikal langsung (RSI, MACD, EMA, Signal) dari TradingView
- Menjawab soalan analisis crypto menggunakan TradingView + Gemini AI
- Memberikan panduan cara memasukkan indikator & strategi ke dalam TradingView
"""

import asyncio
import io
import json
import logging
import os
import re
import sys
import httpx
from dotenv import load_dotenv
from google import genai
from google.genai import types
from tradingview_ta import TA_Handler, Interval

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

SYSTEM_PROMPT = """You are an expert AI Trading and Crypto Assistant connected with TradingView live technical indicators.
You provide clear, accurate, and insightful technical analysis, crypto market trends, trading strategies, and risk management tips.
Always be polite, concise, and helpful. You understand and can reply in Bahasa Melayu or English depending on user language.
When provided with real-time TradingView technical data (Price, RSI, MACD, EMA20/50/200, Buy/Sell rating), reference those numbers directly to explain the current market structure."""

conversations: dict[str, list] = {}


# ── TradingView TA Helper ──────────────────────────────────────────────────────

INTERVAL_MAP = {
    "1m": Interval.INTERVAL_1_MINUTE,
    "5m": Interval.INTERVAL_5_MINUTES,
    "15m": Interval.INTERVAL_15_MINUTES,
    "1h": Interval.INTERVAL_1_HOUR,
    "4h": Interval.INTERVAL_4_HOURS,
    "1d": Interval.INTERVAL_1_DAY,
    "1w": Interval.INTERVAL_1_WEEK,
}

def clean_crypto_symbol(raw: str) -> str:
    """Normalize input like btc, btc/usdt, bitcoin to BTCUSDT."""
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
    """Fetch live technical analysis indicators from TradingView."""
    sym = clean_crypto_symbol(raw_symbol)
    interval = INTERVAL_MAP.get(interval_key.lower(), Interval.INTERVAL_1_HOUR)

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
                "volume": inds.get("volume"),
            }
        except Exception:
            continue
    return None


# ── Telegram Functions ─────────────────────────────────────────────────────────

async def send_message(client: httpx.AsyncClient, chat_id: int | str, text: str) -> None:
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    try:
        await client.post(url, json={
            "chat_id": chat_id,
            "text": text,
            "parse_mode": "HTML",
            "disable_web_page_preview": True,
        }, timeout=10.0)
    except Exception as e:
        logger.error("Gagal hantar mesej: %s", e)

async def send_typing(client: httpx.AsyncClient, chat_id: int | str) -> None:
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendChatAction"
    try:
        await client.post(url, json={"chat_id": chat_id, "action": "typing"}, timeout=5.0)
    except Exception:
        pass


# ── Gemini AI Helper ───────────────────────────────────────────────────────────

async def ask_gemini(chat_id: str, user_message: str, context: str = "") -> str:
    if not gemini:
        return "⚠️ GEMINI_API_KEY belum dikonfigurasi dalam fail .env."

    prompt_to_send = user_message
    if context:
        prompt_to_send = f"Data Pasaran TradingView Semasa:\n{context}\n\nSoalan Pengguna:\n{user_message}"

    history = conversations.setdefault(chat_id, [])
    history.append(types.Content(role="user", parts=[types.Part(text=prompt_to_send)]))

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
            logger.warning("⚠️ Model %s ralat: %s", model, str(e)[:80])
            last_error = e
            continue

    history.pop()
    return f"⚠️ Semua model sedang sibuk. Sila cuba lagi sebentar. (Ralat: {last_error})"


# ── Status Handler ────────────────────────────────────────────────────────────

async def handle_status_command(client: httpx.AsyncClient, chat_id: str, args: list[str]) -> None:
    """Handles /status <symbol> [timeframe]."""
    symbol = args[0] if args else "BTC"
    interval = args[1] if len(args) > 1 else "1h"

    await send_typing(client, chat_id)
    ta = get_tradingview_ta(symbol, interval)

    if not ta:
        await send_message(client, chat_id,
            f"❌ <b>Simbol tidak dijumpai di TradingView:</b> <code>{symbol}</code>\n"
            "Contoh penggunaan: <code>/status BTC</code> atau <code>/status SOL 4h</code>"
        )
        return

    # Visual badge
    rec = ta["recommendation"]
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

    price_str = f"${ta['price']:,.2f}" if isinstance(ta['price'], (int, float)) else str(ta['price'])
    rsi_str = f"{ta['rsi']}"
    if ta['rsi'] >= 70:
        rsi_str += " (⚠️ Overbought)"
    elif ta['rsi'] <= 30:
        rsi_str += " (💡 Oversold)"

    ta_context = (
        f"Pair: {ta['symbol']} ({ta['exchange']}), Timeframe: {ta['interval']}\n"
        f"Harga: {price_str}\n"
        f"Rating TradingView: {rec} (Buy: {ta['buy']}, Sell: {ta['sell']}, Neutral: {ta['neutral']})\n"
        f"RSI(14): {ta['rsi']}, MACD: {ta['macd']}, EMA20: {ta['ema20']}, EMA50: {ta['ema50']}, EMA200: {ta['ema200']}"
    )

    # Ask Gemini for quick tactical breakdown
    ai_comment = await ask_gemini(
        chat_id,
        f"Berikan ulasan teknikal 3-4 ayat berdasarkan data TradingView ini untuk {ta['symbol']}. Nyatakan cadangan support, resistance, dan pengurusan risiko.",
        context=ta_context
    )

    msg = (
        f"📊 <b>STATUS TRADINGVIEW: {ta['symbol']}</b> (TF: <code>{ta['interval']}</code>)\n"
        f"━━━━━━━━━━━━━━━━━━━━\n"
        f"💰 <b>Harga Semasa:</b> <code>{price_str}</code>\n"
        f"🎯 <b>Isyarat Teknikal:</b> {badge}\n"
        f"📈 <b>Skor Indikator:</b> 🟢 {ta['buy']} | ⚪ {ta['neutral']} | 🔴 {ta['sell']}\n\n"
        f"<b>Indikator Utama:</b>\n"
        f"• <b>RSI (14):</b> <code>{rsi_str}</code>\n"
        f"• <b>MACD:</b> <code>{ta['macd']}</code>\n"
        f"• <b>EMA 20:</b> <code>${ta['ema20']:,.2f}</code>\n"
        f"• <b>EMA 50:</b> <code>${ta['ema50']:,.2f}</code>\n"
        f"• <b>EMA 200:</b> <code>${ta['ema200']:,.2f}</code>\n\n"
        f"🧠 <b>Ulasan AI Gemini:</b>\n{ai_comment}"
    )

    await send_message(client, chat_id, msg)


# ── Indicator & Strategy Guide ────────────────────────────────────────────────

def get_indicator_guide() -> str:
    return (
        "🛠 <b>PANDUAN MEMASUKKAN INDIKATOR DI TRADINGVIEW:</b>\n"
        "━━━━━━━━━━━━━━━━━━━━\n"
        "<b>Langkah 1 — Buka Menu Indikator:</b>\n"
        "Di bahagian atas carta TradingView, klik butang <b>Indicators</b> (ikon <b>fx</b> atau tekan kekunci <code>/</code>).\n\n"
        "<b>Langkah 2 — Cari & Masukkan Indikator Asas:</b>\n"
        "1. <b>Relative Strength Index (RSI):</b>\n"
        "   • Taip 'RSI' → Klik <i>Relative Strength Index</i>.\n"
        "   • <i>Setting:</i> Length 14 (Zon 30 = Oversold / Potensi Buy, Zon 70 = Overbought).\n\n"
        "2. <b>Exponential Moving Average (EMA Cross):</b>\n"
        "   • Taip 'EMA' → Masukkan 2 kali.\n"
        "   • <i>Setting EMA 1:</i> Length <b>20</b> (Warna Kuning/Biru).\n"
        "   • <i>Setting EMA 2:</i> Length <b>50</b> (Warna Merah).\n"
        "   • <i>Strategi:</i> Bila EMA 20 silang ke atas EMA 50 = Golden Cross (Buy).\n\n"
        "3. <b>MACD (Moving Average Convergence Divergence):</b>\n"
        "   • Mengesan momentum trend dan pembalikan arah harga.\n\n"
        "👉 Taip <code>/strategy</code> untuk melihat cara memasang strategi & alert webhook!"
    )

def get_strategy_guide() -> str:
    return (
        "📈 <b>PANDUAN SETUP STRATEGI & ALERT WEBHOOK:</b>\n"
        "━━━━━━━━━━━━━━━━━━━━\n"
        "<b>Cara Pasang Alert Webhook ke Bot Telegram:</b>\n\n"
        "1. Klik kanan pada indikator / carta → pilih <b>Add Alert</b> (atau <kbd>Alt</kbd> + <kbd>A</kbd>).\n"
        "2. Di tab <b>Notifications</b>:\n"
        "   • Tandakan kotak <b>Webhook URL</b>.\n"
        "   • Masukkan URL bot anda: <code>https://NAMA_USER.pythonanywhere.com/tradingview</code>\n\n"
        "3. Di tab <b>Settings</b> (Message Box), masukkan format JSON ini:\n"
        "<code>{\n"
        '  "ticker": "{{ticker}}",\n'
        '  "action": "{{strategy.order.action}}",\n'
        '  "price": "{{close}}",\n'
        '  "time": "{{time}}",\n'
        '  "message": "Signal triggered!"\n'
        "}</code>\n\n"
        "4. Klik <b>Create</b>. Selesai! Bot akan terima isyarat secara automatik."
    )


# ── Command & Message Router ──────────────────────────────────────────────────

async def handle_command(client: httpx.AsyncClient, chat_id: str, command: str, args: list[str]) -> None:
    if command == "/start":
        await send_message(client, chat_id,
            "👋 <b>Helo! Saya AI Trading Assistant anda yang disambung ke TradingView.</b>\n\n"
            "<b>Arahan yang boleh anda cuba:</b>\n"
            "📊 <code>/status BTC</code> — Semak status teknikal live TradingView\n"
            "📊 <code>/status SOL 4h</code> — Semak status pada timeframe 4 Jam\n"
            "🛠 <code>/indicator</code> — Panduan memasukkan indikator dalam TradingView\n"
            "📈 <code>/strategy</code> — Panduan setup alert strategi ke Telegram\n"
            "🧹 <code>/clear</code> — Kosongkan ingatan perbualan\n\n"
            "Atau anda boleh terus bertanya dalam bahasa biasa, contohnya:\n"
            "<i>'Apa status pasaran ETH sekarang?'</i>"
        )
    elif command in ["/status", "/ta", "/analisa"]:
        await handle_status_command(client, chat_id, args)
    elif command in ["/indicator", "/indikator"]:
        await send_message(client, chat_id, get_indicator_guide())
    elif command in ["/strategy", "/strategi"]:
        await send_message(client, chat_id, get_strategy_guide())
    elif command == "/help":
        await send_message(client, chat_id,
            "🤖 <b>Senarai Arahan Tersedia:</b>\n\n"
            "/status &lt;crypto&gt; [tf] — Data langsung TradingView (RSI, MACD, EMA, Signal)\n"
            "/indicator — Panduan masukkan indikator TradingView\n"
            "/strategy — Panduan buat strategi alert webhook\n"
            "/clear — Kosongkan sejarah perbualan\n"
            "/ping — Uji status bot"
        )
    elif command == "/clear":
        conversations.pop(chat_id, None)
        await send_message(client, chat_id, "🧹 Sejarah perbualan telah dikosongkan.")
    elif command == "/ping":
        await send_message(client, chat_id, "🏓 Pong! Bot aktif dengan sambungan TradingView.")
    else:
        await send_message(client, chat_id, f"❓ Arahan tidak dikenali: {command}\nTaip /help untuk bantuan.")


# ── Main Polling Loop ──────────────────────────────────────────────────────────

async def main():
    if not TELEGRAM_BOT_TOKEN:
        print("Ralat: TELEGRAM_BOT_TOKEN tiada dalam fail .env!")
        return

    print("Memulakan Telegram AI Bot + TradingView Live Engine...")
    print("Bot sedang mendengar mesej dari Telegram...")
    print("Tekan Ctrl+C di terminal ini untuk berhenti.\n")

    async with httpx.AsyncClient(timeout=35.0) as client:
        # Padam webhook lama supaya Telegram hantar mesej secara polling
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

                    print(f"Mesej daripada {first_name} ({chat_id}): {text}")
                    await send_typing(client, chat_id)

                    if text.startswith("/"):
                        parts = text.split()
                        cmd = parts[0].lower()
                        args = parts[1:]
                        await handle_command(client, chat_id, cmd, args)
                    else:
                        # Semak jika user bertanya tentang status sesuatu crypto dalam teks biasa
                        match = re.search(r'\b(btc|eth|sol|xrp|doge|ada|bnb|avax|link|near|sui|pepe|bitcoin|ethereum|solana)\b', text, re.IGNORECASE)
                        context = ""
                        if match and any(k in text.lower() for k in ["status", "harga", "price", "analis", "analisis", "signal", "trend", "tengok"]):
                            detected_coin = match.group(1)
                            ta = get_tradingview_ta(detected_coin, "1h")
                            if ta:
                                context = (
                                    f"TradingView Real-Time Technical Data for {ta['symbol']}:\n"
                                    f"Harga: ${ta['price']}, Signal TradingView: {ta['recommendation']} "
                                    f"(Buy:{ta['buy']}, Sell:{ta['sell']}, Neutral:{ta['neutral']}), "
                                    f"RSI(14): {ta['rsi']}, MACD: {ta['macd']}, EMA20: {ta['ema20']}, EMA50: {ta['ema50']}, EMA200: {ta['ema200']}"
                                )

                        reply = await ask_gemini(chat_id, text, context=context)
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
        print("\nBot dimatikan.")
