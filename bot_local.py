"""
Telegram AI Bot — Local Polling Runner + Multi-Market TradingView Engine
Menyokong:
1. Semua Crypto (BTC, ETH, SOL, XRP, dll)
2. Saham Bursa Malaysia (MAYBANK, TENAGA, CIMB, YTL, GAMUDA, dll)
3. Saham Global & US / NASDAQ / NYSE (NVDA, TSLA, AAPL, MSFT, dll)
4. Pasaran Forex & Komoditi (USDMYR, EURUSD, GBPUSD, dll)
Format kemas, bersih, tiada simbol bintang (*) yang berselerak.
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
    format="%(asctime)s [%(levelname)s] %(message)s",
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

SYSTEM_PROMPT = """Anda adalah pembantu analisis pasaran kewangan profesional (Crypto, Bursa Malaysia, NASDAQ/US Stocks, & Forex).
ARAHAN FORMAT PENTING:
1. DILARANG KERAS menggunakan simbol bintang (*) atau (**). JANGAN gunakan markdown asterisk.
2. Gunakan ikon dan emoji yang kemas (seperti 🔹, 📈, 📉, 🎯, 💡, 🛡️, 💰) untuk senarai dan poin utama.
3. Susun jawapan dengan tajuk ringkas, perenggan pendek (2-3 baris), dan ruang kosong yang selesa dibaca.
4. Terangkan dengan Bahasa Melayu yang ringkas, tepat, padat, dan mudah difahami.
5. Nyatakan aras sokongan (Support), aras rintangan (Resistance), dan pesanan kawalan risiko."""

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
            logger.warning("Gagal membaca brain.md: %s", e)
    return SYSTEM_PROMPT

conversations: dict[str, list] = {}


# ── Text Cleaner & Formatter ──────────────────────────────────────────────────

def format_clean_telegram(text: str) -> str:
    """Membersihkan sebarang simbol bintang (*) dan formatkan dengan kemas untuk Telegram."""
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


# ── Multi-Market TradingView TA Helper ─────────────────────────────────────────

INTERVAL_MAP = {
    "1m": Interval.INTERVAL_1_MINUTE,
    "5m": Interval.INTERVAL_5_MINUTES,
    "15m": Interval.INTERVAL_15_MINUTES,
    "1h": Interval.INTERVAL_1_HOUR,
    "4h": Interval.INTERVAL_4_HOURS,
    "1d": Interval.INTERVAL_1_DAY,
    "1w": Interval.INTERVAL_1_WEEK,
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

def get_tradingview_ta(raw_symbol: str, interval_key: str = "1h") -> dict | None:
    """
    Auto-resolves and fetches live indicators from TradingView for:
    - Crypto (Binance / Bybit)
    - Bursa Malaysia (MYX)
    - US Stocks / Global (NASDAQ / NYSE)
    - Forex (FX_IDC / Oanda)
    """
    sym = raw_symbol.upper().strip().replace("/", "").replace("-", "").replace("PERP", "")
    interval = INTERVAL_MAP.get(interval_key.lower(), Interval.INTERVAL_1_HOUR)

    candidates = []

    # Check known names first
    if sym in KNOWN_NAMES:
        candidates.append(KNOWN_NAMES[sym])

    # 1. Forex candidates
    if len(sym) == 6 and any(fx in sym for fx in ["MYR", "EUR", "GBP", "JPY", "AUD", "SGD", "CAD", "CHF"]):
        candidates.append((sym, "forex", "FX_IDC"))
        candidates.append((sym, "forex", "OANDA"))

    # 2. Crypto candidates
    crypto_sym = sym if (sym.endswith("USDT") or sym.endswith("USD") or sym.endswith("BUSD")) else f"{sym}USDT"
    candidates.append((crypto_sym, "crypto", "BINANCE"))
    candidates.append((crypto_sym, "crypto", "BYBIT"))
    candidates.append((crypto_sym, "crypto", "OKX"))

    # 3. US Stocks (NASDAQ & NYSE)
    candidates.append((sym, "america", "NASDAQ"))
    candidates.append((sym, "america", "NYSE"))

    # 4. Bursa Malaysia (MYX)
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
                    currency_prefix = "$"
                elif screener == "malaysia":
                    market_type = "Bursa Malaysia"
                    currency_prefix = "RM"
                elif screener == "america":
                    market_type = f"Saham US ({exchange})"
                    currency_prefix = "$"
                else:
                    market_type = "Forex / Mata Wang"
                    currency_prefix = ""

                price_val = round(price, 4) if price < 1 else (round(price, 2) if price < 1000 else round(price, 2))
                chart_url = f"https://www.tradingview.com/chart/?symbol={exchange}:{symbol_candidate}"

                return {
                    "symbol": symbol_candidate,
                    "market": market_type,
                    "exchange": exchange,
                    "interval": interval_key,
                    "currency": currency_prefix,
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
    return None


# ── Telegram Functions ─────────────────────────────────────────────────────────

async def send_message(client: httpx.AsyncClient, chat_id: int | str, text: str) -> None:
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    try:
        await client.post(url, json={
            "chat_id": chat_id,
            "text": text,
            "parse_mode": "HTML",
            "disable_web_page_preview": False,
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
        prompt_to_send = (
            f"Data Pasaran TradingView:\n{context}\n\n"
            f"Soalan Pengguna: {user_message}\n"
            f"Peringatan: JANGAN gunakan simbol bintang (*). Gunakan ikon (🔹, 📈, 📉, 💡) untuk senarai."
        )

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
                    system_instruction=load_brain_prompt(),
                    temperature=0.7,
                    max_output_tokens=1024,
                ),
            )
            raw_reply = response.text or "Maaf, tiada respon dijana."
            clean_reply = format_clean_telegram(raw_reply)

            history.append(types.Content(role="model", parts=[types.Part(text=clean_reply)]))
            if len(history) > 40:
                conversations[chat_id] = history[-40:]
            logger.info("✅ Respon berjaya via %s", model)
            return clean_reply
        except Exception as e:
            logger.warning("Model %s ralat: %s", model, str(e)[:80])
            last_error = e
            continue

    history.pop()
    return f"⚠️ Model AI sedang sibuk. Sila cuba sebentar lagi."


# ── Status Handler ────────────────────────────────────────────────────────────

async def handle_status_command(client: httpx.AsyncClient, chat_id: str, args: list[str]) -> None:
    symbol = args[0] if args else "BTC"
    interval = args[1] if len(args) > 1 else "1h"

    await send_typing(client, chat_id)
    ta = get_tradingview_ta(symbol, interval)

    if not ta:
        await send_message(client, chat_id,
            f"❌ <b>Simbol tidak ditemui di TradingView:</b> <code>{symbol}</code>\n\n"
            "<b>Contoh Carian Pelbagai Pasaran:</b>\n"
            "🔹 Kripto: <code>/status BTC</code> atau <code>/status SOL 4h</code>\n"
            "🔹 Bursa Malaysia: <code>/status MAYBANK</code> atau <code>/status CIMB</code>\n"
            "🔹 Saham Global/NASDAQ: <code>/status NVDA</code> atau <code>/status TSLA 1d</code>\n"
            "🔹 Forex: <code>/status USDMYR</code> atau <code>/status EURUSD</code>"
        )
        return

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

    curr = ta["currency"]
    price_str = f"{curr}{ta['price']:,.2f}" if curr else f"{ta['price']:,.4f}"
    rsi_str = f"{ta['rsi']}"
    if ta['rsi'] >= 70:
        rsi_str += " ⚠️ Overbought"
    elif ta['rsi'] <= 30:
        rsi_str += " 💡 Oversold"

    ta_context = (
        f"Aset: {ta['symbol']} ({ta['market']} - {ta['exchange']}), Timeframe: {ta['interval']}\n"
        f"Harga: {price_str}, Rating: {rec} (Buy: {ta['buy']}, Sell: {ta['sell']}, Neutral: {ta['neutral']})\n"
        f"RSI(14): {ta['rsi']}, MACD: {ta['macd']}, EMA20: {curr}{ta['ema20']}, EMA50: {curr}{ta['ema50']}, EMA200: {curr}{ta['ema200']}"
    )

    ai_comment = await ask_gemini(
        chat_id,
        f"Ulas data pasaran {ta['market']} ini untuk {ta['symbol']}. Berikan sokongan, rintangan, dan kawalan risiko dalam 3-4 baris.",
        context=ta_context
    )

    msg = (
        f"📊 <b>ANALISIS PASARAN: {ta['symbol']}</b>\n"
        f"🏛 Pasaran: <b>{ta['market']}</b> ({ta['exchange']})\n"
        f"⏱ Timeframe: <code>{ta['interval']}</code>\n"
        f"────────────────────────\n"
        f"💰 <b>Harga Semasa:</b> <code>{price_str}</code>\n"
        f"🎯 <b>Isyarat Teknikal:</b> {badge}\n"
        f"📈 <b>Skor Indikator:</b> 🟢 {ta['buy']} Beli | ⚪ {ta['neutral']} Neutral | 🔴 {ta['sell']} Jual\n\n"
        f"📋 <b>Indikator Utama:</b>\n"
        f"🔹 <b>RSI (14):</b> <code>{rsi_str}</code>\n"
        f"🔹 <b>MACD:</b> <code>{ta['macd']}</code>\n"
        f"🔹 <b>EMA 20:</b> <code>{curr}{ta['ema20']:,.2f}</code>\n"
        f"🔹 <b>EMA 50:</b> <code>{curr}{ta['ema50']:,.2f}</code>\n"
        f"🔹 <b>EMA 200:</b> <code>{curr}{ta['ema200']:,.2f}</code>\n\n"
        f"💡 <b>Ulasan AI:</b>\n"
        f"{ai_comment}\n\n"
        f"🌐 <a href=\"{ta['chart_url']}\">Buka Carta di TradingView</a>"
    )

    await send_message(client, chat_id, msg)


# ── Guides (Clean & Readable) ─────────────────────────────────────────────────

def get_indicator_guide() -> str:
    return (
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

def get_strategy_guide() -> str:
    return (
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


# ── Command & Message Router ──────────────────────────────────────────────────

async def handle_command(client: httpx.AsyncClient, chat_id: str, command: str, args: list[str]) -> None:
    if command == "/start":
        await send_message(client, chat_id,
            "👋 <b>Selamat Datang ke AI Trading Assistant!</b>\n"
            "Disambungkan ke TradingView untuk Kripto, Saham Bursa Malaysia & NASDAQ.\n"
            "────────────────────────\n\n"
            "📌 <b>Contoh Carian Pelbagai Pasaran:</b>\n"
            "🔹 <code>/status BTC</code> — Kripto Bitcoin\n"
            "🔹 <code>/status MAYBANK</code> — Saham Bursa Malaysia\n"
            "🔹 <code>/status NVDA 1d</code> — Saham NASDAQ (Nvidia)\n"
            "🔹 <code>/status USDMYR</code> — Kadar Tukaran Forex\n\n"
            "🛠 <b>Panduan:</b>\n"
            "🔹 <code>/indicator</code> — Panduan masukkan indikator\n"
            "🔹 <code>/strategy</code> — Panduan setup alert webhook\n"
            "🔹 <code>/clear</code> — Kosongkan ingatan perbualan\n\n"
            "💬 Anda juga boleh menaip soalan biasa seperti:\n"
            "<i>'Bagaimana status Maybank?'</i> atau <i>'Trend Tesla hari ini'</i>"
        )
    elif command in ["/status", "/ta", "/analisa"]:
        await handle_status_command(client, chat_id, args)
    elif command in ["/indicator", "/indikator"]:
        await send_message(client, chat_id, get_indicator_guide())
    elif command in ["/strategy", "/strategi"]:
        await send_message(client, chat_id, get_strategy_guide())
    elif command == "/help":
        await send_message(client, chat_id,
            "🤖 <b>Senarai Arahan Tersedia:</b>\n"
            "────────────────────────\n"
            "🔹 <code>/status &lt;simbol&gt; [tf]</code> — Analisis teknikal live (Kripto, Bursa, US, Forex)\n"
            "🔹 <code>/indicator</code> — Panduan indikator TradingView\n"
            "🔹 <code>/strategy</code> — Panduan alert strategi webhook\n"
            "🔹 <code>/clear</code> — Kosongkan ingatan perbualan\n"
            "🔹 <code>/ping</code> — Semak status bot"
        )
    elif command == "/clear":
        conversations.pop(chat_id, None)
        await send_message(client, chat_id, "🧹 Ingatan perbualan telah dikosongkan.")
    elif command == "/ping":
        await send_message(client, chat_id, "🏓 Pong! Bot aktif dengan sambungan TradingView pelbagai pasaran.")
    else:
        await send_message(client, chat_id, f"❓ Arahan tidak dikenali: {command}\nTaip /help untuk senarai arahan.")


# ── Main Polling Loop ──────────────────────────────────────────────────────────

async def main():
    if not TELEGRAM_BOT_TOKEN:
        print("Ralat: TELEGRAM_BOT_TOKEN tiada dalam fail .env!")
        return

    print("Memulakan Telegram AI Bot (Multi-Market: Kripto, Bursa, NASDAQ, Forex)...")
    print("Bot sedang mendengar mesej dari Telegram...")

    async with httpx.AsyncClient(timeout=35.0) as client:
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
                        # Extract ticker if user asks in natural language
                        context = ""
                        # Try to find ticker in text
                        words = re.findall(r'[A-Za-z0-9]+', text)
                        for word in words:
                            if len(word) >= 2 and word.upper() not in ["APA", "BILA", "CARA", "BOLEH", "STATUS", "HARGA", "PRICE", "TREND", "DAN", "SAYA", "KAU", "INI", "ITU", "HARI"]:
                                ta = get_tradingview_ta(word, "1h")
                                if ta:
                                    context = (
                                        f"TradingView Technical Data for {ta['symbol']} ({ta['market']} - {ta['exchange']}):\n"
                                        f"Harga: {ta['currency']}{ta['price']}, Isyarat: {ta['recommendation']} "
                                        f"(Buy:{ta['buy']}, Sell:{ta['sell']}, Neutral:{ta['neutral']}), "
                                        f"RSI(14): {ta['rsi']}, MACD: {ta['macd']}, EMA20: {ta['ema20']}, EMA50: {ta['ema50']}."
                                    )
                                    break

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
