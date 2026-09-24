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
    if not HAS_TV_TA:
        return None

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
                "🔹 <code>/status BTC</code> | <code>/indicator</code> | <code>/strategy</code>",
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
                "🔹 <b>Tanya Soalan Terbuka:</b> Taip apa sahaja seperti <i>'adakah bagus beli btc sekarang?'</i>",
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
                "👉 Taip <i>'strategi'</i> untuk melihat cara memasang alert webhook ke Telegram!"
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
                "Klik butang <b>Create</b>. Bot akan menghantar notifikasi kemas ke Telegram setiap kali alert berbunyi."
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
                f"🌐 <a href=\"{ta_data['chart_url']}\">Buka Carta di TradingView</a>"
            )
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

        # 4. Semakan Ticker Pantas Tanpa '/' (Contoh: 'btc', 'maybank', 'sol 4h', 'nvda')
        status_handled = False
        parts = text.split()
        if len(parts) in [1, 2]:
            cand_sym = parts[0]
            cand_tf = parts[1] if len(parts) == 2 and parts[1].lower() in INTERVAL_MAP else "1h"
            ta = get_tradingview_ta(cand_sym, cand_tf)
            if ta:
                send_ta_card(ta)
                status_handled = True

        # 5. Soalan Status / Analisis dalam Bahasa Biasa (Contoh: 'apa status cimb', 'tengok harga tesla')
        if not status_handled and any(k in clean_lower for k in [
            "status", "harga", "price", "analis", "analisis", "analisa", "trend",
            "tengok", "check", "semak", "macam mana", "bagaimana", "view"
        ]):
            words = re.findall(r'[A-Za-z0-9]+', text)
            timeframe = "1h"
            for w in words:
                if w.lower() in INTERVAL_MAP:
                    timeframe = w.lower()
                    break
            stopwords = {
                "STATUS", "HARGA", "PRICE", "TREND", "DAN", "SAYA", "KAU", "INI",
                "ITU", "HARI", "MACAM", "MANA", "TAK", "TENGOK", "BAGAIMANA", "DI",
                "KE", "DARI", "UNTUK", "TENTANG", "NAK", "CHECK", "ANALISIS",
                "ANALISA", "VIEW", "PASARAN", "BOLEH", "TOLONG", "BERIKAN", "SEMAK",
                "APA", "APAKAH", "BERAPA"
            }
            for w in words:
                if len(w) >= 2 and w.upper() not in stopwords:
                    ta = get_tradingview_ta(w, timeframe)
                    if ta:
                        send_ta_card(ta)
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
