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
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
import pandas as pd
import requests
import yfinance as yf
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

def get_fallback_ta(raw_symbol: str, interval_key: str = "1h") -> dict | None:
    """Fallback engine jika TradingView API rate-limit (429) atau offline."""
    try:
        sym = raw_symbol.upper().strip().replace("/", "").replace("-", "").replace("PERP", "")
        df = None
        curr = "$"
        market_type = "Kripto"
        exchange = "BINANCE"

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
        # 2. Crypto via Binance
        elif sym.endswith("USDT") or sym in ["BTC", "ETH", "SOL", "XRP", "DOGE", "BNB", "ADA", "AVAX", "LINK", "SUI", "PEPE", "NEAR"]:
            c_sym = sym if sym.endswith("USDT") else f"{sym}USDT"
            binance_tf = interval_key.lower() if interval_key.lower() in ["1m", "5m", "15m", "1h", "4h", "1d", "1w"] else "1h"
            try:
                r = requests.get(f"https://api.binance.com/api/v3/klines?symbol={c_sym}&interval={binance_tf}&limit=60", timeout=5.0)
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
        logger.error("Ralat fallback TA: %s", e)
        return None

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

    # Fallback automatik jika TradingView rate-limit / block
    return get_fallback_ta(raw_symbol, interval_key)


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

async def send_photo_card(client: httpx.AsyncClient, chat_id: int | str, photo_bytes: bytes, text: str) -> None:
    """Hantar gambar carta TradingView bersama ulasan teks analisis."""
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendPhoto"
    try:
        # Telegram had caption ialah 1024 aksara
        if len(text) <= 1024:
            await client.post(url, data={
                "chat_id": chat_id,
                "caption": text,
                "parse_mode": "HTML",
            }, files={
                "photo": ("chart.png", photo_bytes, "image/png")
            }, timeout=20.0)
        else:
            first_line = text.split("\n")[0]
            await client.post(url, data={
                "chat_id": chat_id,
                "caption": first_line,
                "parse_mode": "HTML",
            }, files={
                "photo": ("chart.png", photo_bytes, "image/png")
            }, timeout=20.0)
            await send_message(client, chat_id, text)
    except Exception as e:
        logger.error("Gagal send_photo_card: %s", e)
        await send_message(client, chat_id, text)


# ── Chart Engine (Candlestick + EMAs) ─────────────────────────────────────────

def generate_candlestick_chart(raw_symbol: str, interval: str = "1h", market_type: str = "") -> bytes | None:
    """Menjana carta candlestick dark mode berkualiti tinggi sebagai gambar PNG."""
    try:
        sym = raw_symbol.upper().strip().replace("/", "").replace("-", "").replace("PERP", "")
        df = None
        curr = "$"

        # 1. Saham Bursa Malaysia
        if market_type == "Bursa Malaysia" or sym in BURSA_CODE_MAP or (len(sym) == 4 and sym.isdigit()):
            b_sym = BURSA_CODE_MAP.get(sym, f"{sym}.KL")
            yf_df = yf.Ticker(b_sym).history(period="1mo", interval="1d")
            if not yf_df.empty:
                df = yf_df.reset_index()
                df.columns = [c.lower() for c in df.columns]
                curr = "RM"

        # 2. Kripto (Guna Binance API untuk kelajuan maksima)
        elif "crypto" in market_type.lower() or sym.endswith("USDT") or sym in ["BTC", "ETH", "SOL", "XRP", "DOGE", "BNB", "ADA", "AVAX", "NEAR", "SUI"]:
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
            except Exception as e:
                logger.warning("Ralat Binance chart: %s", e)

        # 3. Fallback ke yfinance (US Stocks & Forex)
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

        # Ambil 35 lilin terkini
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

        # Tambah garis EMA 20 & EMA 50
        df["ema20"] = df["close"].ewm(span=20).mean()
        ax.plot(x, df["ema20"], color="#00F0FF", label="EMA 20", linewidth=1.5)
        if len(df) >= 25:
            df["ema50"] = df["close"].ewm(span=50).mean()
            ax.plot(x, df["ema50"], color="#FFA500", label="EMA 50", linewidth=1.3)

        ax.grid(True, color="#1E222D", linestyle="--", alpha=0.7)
        ax.tick_params(colors="#848E9C")
        for spine in ax.spines.values():
            spine.set_color("#1E222D")

        # Format label paksi X
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
        logger.error("Ralat penjanaan carta untuk %s: %s", raw_symbol, e)
        return None


DISCLAIMER_HTML = (
    "⚠️ <i>Penafian: Maklumat dan analisis ini adalah untuk tujuan pembelajaran dan rujukan teknikal sahaja, "
    "bukan nasihat pelaburan atau kewangan. Sentiasa lakukan kajian anda sendiri (DYOR).</i>"
)

def attach_disclaimer(text: str) -> str:
    """Sertakan penafian bukan nasihat kewangan jika belum wujud."""
    lower = text.lower()
    if any(k in lower for k in ["bukan nasihat kewangan", "penafian:", "dyor", "not financial advice"]):
        return text
    return f"{text.rstrip()}\n\n{DISCLAIMER_HTML}"


# ── Gemini AI Helper ───────────────────────────────────────────────────────────

async def ask_gemini(chat_id: str, user_message: str, context: str = "", add_disclaimer: bool = True) -> str:
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
            if add_disclaimer:
                clean_reply = attach_disclaimer(clean_reply)
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
        context=ta_context,
        add_disclaimer=False,
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
        f"🌐 <a href=\"{ta['chart_url']}\">Buka Carta di TradingView</a>\n\n"
        f"{DISCLAIMER_HTML}"
    )

    # Jana carta candlestick secara latar (non-blocking)
    chart_bytes = await asyncio.to_thread(generate_candlestick_chart, ta["symbol"], ta["interval"], ta["market"])
    if chart_bytes:
        await send_photo_card(client, chat_id, chart_bytes, msg)
    else:
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
        "👉 Taip <code>/strategy</code> untuk melihat cara memasang alert webhook ke Telegram!\n\n"
        f"{DISCLAIMER_HTML}"
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
        "Klik butang <b>Create</b>. Bot akan menghantar notifikasi kemas ke Telegram setiap kali alert berbunyi.\n\n"
        f"{DISCLAIMER_HTML}"
    )


# ── Command & Message Router ──────────────────────────────────────────────────

async def handle_command(client: httpx.AsyncClient, chat_id: str, command: str, args: list[str]) -> None:
    if command == "/start":
        await send_message(client, chat_id,
            "👋 <b>Selamat Datang ke AI Trading Assistant!</b>\n"
            "Disambungkan terus ke TradingView untuk Kripto, Saham Bursa Malaysia & Global (NASDAQ/NYSE).\n"
            "────────────────────────\n\n"
            "💡 <b>Paling Mudah:</b> Anda <b>TIDAK PERLU</b> taip simbol '/' langsung! Boleh taip nama saham atau tanya soalan macam biasa.\n\n"
            "📌 <b>Contoh Taip Terus (Tanpa '/'):</b>\n"
            "🔹 <code>btc</code> atau <code>sol 4h</code> — Analisis Kripto\n"
            "🔹 <code>maybank</code> atau <code>cimb</code> — Saham Bursa Malaysia\n"
            "🔹 <code>nvda</code> atau <code>tsla 1d</code> — Saham US / NASDAQ\n"
            "🔹 <code>usdmyr</code> — Pasaran Mata Wang / Forex\n"
            "🔹 <i>'tengok harga maybank'</i> — Analisis automatik\n"
            "🔹 <i>'panduan indikator'</i> — Cara pasang RSI & EMA\n"
            "🔹 <i>'setup alert'</i> — Cara sambung webhook TradingView\n\n"
            "🤖 Boleh juga gunakan arahan standard:\n"
            "🔹 <code>/status BTC</code> | <code>/indicator</code> | <code>/strategy</code> | <code>/clear</code>"
        )
    elif command in ["/status", "/ta", "/analisa"]:
        await handle_status_command(client, chat_id, args)
    elif command in ["/indicator", "/indikator"]:
        await send_message(client, chat_id, get_indicator_guide())
    elif command in ["/strategy", "/strategi"]:
        await send_message(client, chat_id, get_strategy_guide())
    elif command == "/help":
        await send_message(client, chat_id,
            "🤖 <b>Panduan Penggunaan Bot:</b>\n"
            "────────────────────────\n"
            "💡 <i>Tip: Anda boleh taip terus tanpa simbol '/'!</i>\n\n"
            "🔹 <b>Carian Ticker Pantas:</b> Taip <code>btc</code>, <code>maybank</code>, <code>nvda</code>, atau <code>sol 4h</code>\n"
            "🔹 <b>Panduan Indikator:</b> Taip <i>'indikator'</i> atau <code>/indicator</code>\n"
            "🔹 <b>Setup Alert:</b> Taip <i>'alert'</i>, <i>'strategi'</i> atau <code>/strategy</code>\n"
            "🔹 <b>Kosongkan Memori AI:</b> Taip <i>'clear'</i> atau <code>/clear</code>\n"
            "🔹 <b>Tanya Soalan Terbuka:</b> Taip apa sahaja seperti <i>'adakah bagus beli btc sekarang?'</i>"
        )
    elif command == "/clear":
        conversations.pop(chat_id, None)
        await send_message(client, chat_id, "🧹 Ingatan perbualan telah dikosongkan.")
    elif command == "/ping":
        await send_message(client, chat_id, "🏓 Pong! Bot aktif dengan sambungan TradingView pelbagai pasaran.")
    else:
        await send_message(client, chat_id, f"❓ Arahan tidak dikenali: {command}\nTaip /help untuk panduan penggunaan.")


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

                    # Jika bermula dengan '/' -> Jalankan arahan standard
                    if text.startswith("/"):
                        parts = text.split()
                        cmd = parts[0].lower()
                        args = parts[1:]
                        await handle_command(client, chat_id, cmd, args)
                    else:
                        # PROSES SOALAN BIASA TANPA FORMAT '/'
                        clean_lower = text.lower().strip()

                        # 0. Menu Pantas (Help, Clear, Ping)
                        if clean_lower in ["help", "bantuan", "menu", "arahan"]:
                            await handle_command(client, chat_id, "/help", [])
                            continue
                        if clean_lower in ["clear", "reset", "padam"]:
                            await handle_command(client, chat_id, "/clear", [])
                            continue
                        if clean_lower in ["ping", "test"]:
                            await handle_command(client, chat_id, "/ping", [])
                            continue

                        # 1. Panduan Indikator secara bahasa biasa
                        if clean_lower in ["indikator", "indicator"] or any(k in clean_lower for k in [
                            "masuk indikator", "pasang indikator", "cara indikator", "panduan indikator",
                            "setting rsi", "setting ema", "guna indikator"
                        ]):
                            await send_message(client, chat_id, get_indicator_guide())
                            continue

                        # 2. Panduan Strategi & Alert secara bahasa biasa
                        if clean_lower in ["strategi", "strategy", "alert", "webhook"] or any(k in clean_lower for k in [
                            "cara buat alert", "pasang alert", "setup alert", "buat webhook",
                            "sambung webhook", "panduan strategi"
                        ]):
                            await send_message(client, chat_id, get_strategy_guide())
                            continue

                        # 3. Permintaan Carta & Analisis Pasaran
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
                                        await handle_status_command(client, chat_id, [w, timeframe])
                                        status_handled = True
                                        break

                        # 5. Soalan Terbuka Umum (Serta auto-suntik data TradingView jika nama aset disebut)
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

                            reply = await ask_gemini(chat_id, text, context=context_ta)
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
