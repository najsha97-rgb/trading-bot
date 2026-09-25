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

from datetime import datetime, time
try:
    from zoneinfo import ZoneInfo
except ImportError:
    from backports.zoneinfo import ZoneInfo

try:
    from bs4 import BeautifulSoup
except ImportError:
    BeautifulSoup = None

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


# ── Most Active Screener (Bursa Malaysia & NASDAQ) ────────────────────────────

def get_bursa_active_cards(limit: int = 50) -> list[str]:
    """
    Mengambil senarai kaunter paling aktif harian di Bursa Malaysia
    terus daripada ShareInvestor (Top Active Counters DA02).
    Waktu dagangan rasmi Bursa Malaysia: 9:00 AM - 5:00 PM (Sesi Pagi & Petang).
    Menghasilkan senarai mesej (Bhg 1 & Bhg 2 jika > 25) bagi mematuhi had aksara Telegram.
    """
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
        "Accept": "application/json, text/plain, */*",
        "Referer": "https://www.shareinvestor.com/prices/stock_prices",
    }
    url = "https://www.shareinvestor.com/api/v1/prices/stock_prices.json?tab=counters&filter=DA02&type=ranking&layout=trading_data&page=1&market=bursa"
    try:
        r = requests.get(url, headers=headers, timeout=10)
        if r.status_code != 200:
            return [(
                "⚠️ <b>Gagal memuat turun data ShareInvestor buat masa ini.</b>\n"
                "Sila layari terus: <a href=\"https://www.shareinvestor.com/prices/stock_prices\">ShareInvestor Stock Prices</a>\n\n"
                f"{DISCLAIMER_HTML}"
            )]
        data = r.json()
        stocks = data.get("stock_info", [])[:limit]
        if not stocks:
            return ["⚠️ Tiada data kaunter aktif diterima daripada ShareInvestor."]

        tz = ZoneInfo("Asia/Kuala_Lumpur")
        now = datetime.now(tz)
        weekday = now.weekday()
        t = now.time()

        if weekday in range(0, 5):
            if time(9, 0) <= t < time(12, 30):
                status_str = "🟢 <b>Pasaran Dibuka (Sesi Pagi)</b>"
            elif time(12, 30) <= t < time(14, 30):
                status_str = "🟡 <b>Rehat Tengah Hari (Buka semula 2:30 PM)</b>"
            elif time(14, 30) <= t < time(17, 0):
                status_str = "🟢 <b>Pasaran Dibuka (Sesi Petang)</b>"
            else:
                status_str = "🔴 <b>Pasaran Ditutup (Waktu Dagangan: 9:00 AM - 5:00 PM)</b>"
        else:
            status_str = "🔴 <b>Pasaran Ditutup (Hujung Minggu)</b>"

        time_str = now.strftime("%d %b %Y, %I:%M %p")
        rank_emojis = ["1️⃣", "2️⃣", "3️⃣", "4️⃣", "5️⃣", "6️⃣", "7️⃣", "8️⃣", "9️⃣", "🔟"]

        def format_row(i, s):
            rk = rank_emojis[i] if i < len(rank_emojis) else f"<b>{i+1}.</b>"
            name = s.get("Name", "").strip()
            code = s.get("Symbol", "").strip()
            price = s.get("Last Done", "0.00").strip()
            chg = s.get("Chg", "").strip()
            pct = s.get("% Chg", "").strip()
            vol = s.get("Vol", "0").strip()
            shariah = " ☪️" if s.get("Is Shariah") else ""

            badge = "⚪"
            if chg.startswith("+"):
                badge = "🟢"
            elif chg.startswith("-"):
                badge = "🔴"
            elif pct and pct != "-":
                try:
                    val = float(pct.replace("+", ""))
                    if val > 0:
                        badge = "🟢"
                    elif val < 0:
                        badge = "🔴"
                except Exception:
                    pass

            chg_display = f"{pct}%" if pct != "-" else "0.00%"
            return f"{rk} <b>{name}</b> (<code>{code}</code>){shariah} — RM{price} | {badge} {chg_display} | Vol: <code>{vol}</code>"

        if len(stocks) <= 25:
            lines = [
                f"📊 <b>TOP {len(stocks)} KAUNTER AKTIF BURSA MALAYSIA</b>",
                "🏛 Sumber: <a href=\"https://www.shareinvestor.com/prices/stock_prices\">ShareInvestor Stock Prices</a>",
                f"⏱ Status: {status_str}",
                f"🕒 Dikemaskini: <code>{time_str} MYT</code>",
                "────────────────────────",
            ]
            for i, s in enumerate(stocks):
                lines.append(format_row(i, s))
            lines.append("\n💡 <i>Tip: Taip kod atau nama kaunter (cth: <code>zetrix</code> atau <code>0138</code>) untuk melihat ulasan & carta lilin teknikal.</i>\n")
            lines.append(DISCLAIMER_HTML)
            return ["\n".join(lines)]

        p1 = [
            f"📊 <b>TOP {len(stocks)} KAUNTER AKTIF BURSA MALAYSIA (Bhg 1: #1 - #25)</b>",
            "🏛 Sumber: <a href=\"https://www.shareinvestor.com/prices/stock_prices\">ShareInvestor Stock Prices</a>",
            f"⏱ Status: {status_str}",
            f"🕒 Dikemaskini: <code>{time_str} MYT</code>",
            "────────────────────────",
        ]
        for i, s in enumerate(stocks[:25]):
            p1.append(format_row(i, s))

        p2 = [
            f"📊 <b>TOP {len(stocks)} KAUNTER AKTIF BURSA MALAYSIA (Bhg 2: #26 - #{len(stocks)})</b>",
            "────────────────────────",
        ]
        for i, s in enumerate(stocks[25:], start=25):
            p2.append(format_row(i, s))

        p2.append("\n💡 <i>Tip: Taip kod atau nama mana-mana kaunter (cth: <code>zetrix</code> atau <code>0138</code>) untuk melihat ulasan & carta lilin teknikal.</i>\n")
        p2.append(DISCLAIMER_HTML)

        return ["\n".join(p1), "\n".join(p2)]
    except Exception as e:
        app.logger.error("Ralat get_bursa_active_cards: %s", e)
        return [f"⚠️ Ralat memproses data kaunter aktif Bursa: {e}"]


def get_nasdaq_active_cards(limit: int = 50) -> list[str]:
    """
    Mengambil senarai saham paling aktif di pasaran NASDAQ & US
    terus daripada Yahoo Finance Screener (Most Active).
    Menghasilkan senarai mesej (Bhg 1 & Bhg 2 jika > 25) bagi mematuhi had aksara Telegram.
    """
    headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"}
    url = f"https://query1.finance.yahoo.com/v1/finance/screener/predefined/saved?formatted=false&scrIds=most_actives&count={limit}"
    try:
        quotes = []
        try:
            r = requests.get(url, headers=headers, timeout=10)
            if r.status_code == 200:
                data = r.json()
                quotes = data.get("finance", {}).get("result", [{}])[0].get("quotes", [])
        except Exception:
            quotes = []

        if not quotes and HAS_CHART:
            try:
                res = yf.screen("most_actives")
                quotes = res.get("quotes", [])
            except Exception:
                quotes = []

        if not quotes:
            return [(
                "⚠️ <b>Gagal memuat turun data Yahoo Finance Screener buat masa ini.</b>\n"
                "Sila layari terus: <a href=\"https://finance.yahoo.com/research-hub/screener/most-active?start=0&count=50\">Yahoo Finance Screener</a>\n\n"
                f"{DISCLAIMER_HTML}"
            )]

        nasdaq_quotes = []
        other_quotes = []
        for q in quotes:
            exch = (q.get("fullExchangeName", "") or q.get("exchange", "")).upper()
            if any(k in exch for k in ["NASDAQ", "NMS", "NGS", "NCM"]):
                nasdaq_quotes.append(q)
            else:
                other_quotes.append(q)

        sorted_quotes = (nasdaq_quotes + other_quotes)[:limit]

        tz = ZoneInfo("America/New_York")
        now = datetime.now(tz)
        time_str = now.strftime("%d %b %Y, %I:%M %p")
        rank_emojis = ["1️⃣", "2️⃣", "3️⃣", "4️⃣", "5️⃣", "6️⃣", "7️⃣", "8️⃣", "9️⃣", "🔟"]

        def format_row(i, q):
            rk = rank_emojis[i] if i < len(rank_emojis) else f"<b>{i+1}.</b>"
            sym = q.get("symbol", "").strip()
            name = (q.get("shortName") or q.get("displayName") or sym).strip()
            price = q.get("regularMarketPrice", 0.0)
            chg_pct = q.get("regularMarketChangePercent", 0.0)
            vol = q.get("regularMarketVolume", 0)
            exch = q.get("fullExchangeName") or q.get("exchange") or "NASDAQ"
            badge = "🟢" if chg_pct > 0 else ("🔴" if chg_pct < 0 else "⚪")
            return f"{rk} <b>{sym}</b> — {name} (<i>{exch}</i>) | ${price:,.2f} | {badge} {chg_pct:+.2f}% | Vol: <code>{vol:,}</code>"

        if len(sorted_quotes) <= 25:
            lines = [
                f"📊 <b>TOP {len(sorted_quotes)} KAUNTER AKTIF NASDAQ / US</b>",
                "🏛 Sumber: <a href=\"https://finance.yahoo.com/research-hub/screener/most-active?start=0&count=50\">Yahoo Finance Screener</a>",
                f"🕒 Sesi Pasaran: <code>{time_str} EDT</code>",
                "────────────────────────",
            ]
            for i, q in enumerate(sorted_quotes):
                lines.append(format_row(i, q))
            lines.append("\n💡 <i>Tip: Taip simbol saham (cth: <code>nvda</code> atau <code>tsla</code>) untuk melihat ulasan & carta lilin teknikal.</i>\n")
            lines.append(DISCLAIMER_HTML)
            return ["\n".join(lines)]

        p1 = [
            f"📊 <b>TOP {len(sorted_quotes)} KAUNTER AKTIF NASDAQ / US (Bhg 1: #1 - #25)</b>",
            "🏛 Sumber: <a href=\"https://finance.yahoo.com/research-hub/screener/most-active?start=0&count=50\">Yahoo Finance Screener</a>",
            f"🕒 Sesi Pasaran: <code>{time_str} EDT</code>",
            "────────────────────────",
        ]
        for i, q in enumerate(sorted_quotes[:25]):
            p1.append(format_row(i, q))

        p2 = [
            f"📊 <b>TOP {len(sorted_quotes)} KAUNTER AKTIF NASDAQ / US (Bhg 2: #26 - #{len(sorted_quotes)})</b>",
            "────────────────────────",
        ]
        for i, q in enumerate(sorted_quotes[25:], start=25):
            p2.append(format_row(i, q))

        p2.append("\n💡 <i>Tip: Taip simbol saham (cth: <code>nvda</code> atau <code>tsla</code>) untuk melihat ulasan & carta lilin teknikal.</i>\n")
        p2.append(DISCLAIMER_HTML)

        return ["\n".join(p1), "\n".join(p2)]
    except Exception as e:
        app.logger.error("Ralat get_nasdaq_active_cards: %s", e)
        return [f"⚠️ Ralat memproses data kaunter aktif NASDAQ: {e}"]


_crypto_trending_cache = {"data": None, "timestamp": 0}

_crypto_active_cache = {"data": None, "timestamp": 0}

def fmt_crypto_price(p) -> str:
    try:
        val = float(str(p).replace(",", "").replace("$", ""))
        if val >= 1000:
            return f"${val:,.2f}"
        elif val >= 1:
            return f"${val:,.2f}"
        elif val >= 0.01:
            return f"${val:,.4f}"
        elif val >= 0.0001:
            return f"${val:,.6f}"
        else:
            return f"${val:.8f}"
    except Exception:
        return f"${p}"

def get_crypto_active_cards(limit: int = 50) -> list[str]:
    """
    Mengambil senarai 50 kripto paling aktif berdasarkan bilangan transaksi tertinggi
    (most transaction activity) terus daripada TradingView Most Transactions:
    https://www.tradingview.com/markets/cryptocurrencies/prices-most-transactions/
    Digabungkan jadi satu bersama CoinGecko Trending Highlights jika ada kaunter yang bertindan.
    Menghasilkan 2 bahagian (Bhg 1: #1-#25, Bhg 2: #26-#50) bagi mematuhi had aksara Telegram.
    """
    import time as _pytime
    global _crypto_active_cache
    now_ts = _pytime.time()

    # Semak cache dalam memori (60 saat)
    if _crypto_active_cache["data"] and (now_ts - _crypto_active_cache["timestamp"] < 60):
        return _crypto_active_cache["data"]

    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "en-US,en;q=0.9"
    }

    # 1. Dapatkan senarai Trending CoinGecko untuk semakan pertindihan (overlap)
    cg_trending_map = {}
    try:
        r_cg = requests.get(
            "https://api.coingecko.com/api/v3/search/trending",
            headers={"User-Agent": headers["User-Agent"], "Accept": "application/json"},
            timeout=8
        )
        if r_cg.status_code == 200:
            cg_data = r_cg.json()
            for rank_idx, c in enumerate(cg_data.get("coins", []), 1):
                item = c.get("item", {})
                sym = item.get("symbol", "").strip().upper()
                nm = item.get("name", "").strip().lower()
                cg_trending_map[sym] = {"rank": rank_idx, "name": item.get("name", "")}
                cg_trending_map[nm] = {"rank": rank_idx, "name": item.get("name", ""), "sym": sym}
    except Exception as e:
        app.logger.warning("Ralat CoinGecko trending fetch: %s", e)

    # 2. Ambil data Most Transactions daripada TradingView
    items = []
    tv_url = "https://www.tradingview.com/markets/cryptocurrencies/prices-most-transactions/"
    try:
        r_tv = requests.get(tv_url, headers=headers, timeout=12)
        if r_tv.status_code == 200 and BeautifulSoup:
            soup = BeautifulSoup(r_tv.text, "html.parser")
            rows = soup.find_all("tr", attrs={"data-rowkey": True})
            for r in rows:
                rowkey = r.get("data-rowkey", "")
                tds = r.find_all("td")
                if not tds or len(tds) < 5:
                    continue

                cell0 = tds[0]
                t_link = cell0.find("a")
                ticker = t_link.get_text(strip=True) if t_link else ""
                all_text0 = cell0.get_text(separator="|", strip=True).split("|")
                if len(all_text0) >= 2:
                    ticker = ticker or all_text0[0].strip()
                    name = all_text0[1].strip()
                elif len(all_text0) == 1:
                    ticker = ticker or all_text0[0].strip()
                    name = ticker
                else:
                    name = ticker

                if not ticker and ":" in rowkey:
                    ticker = rowkey.split(":")[1].replace("USD", "").replace("USDT", "")

                txs = tds[2].get_text(strip=True) if len(tds) > 2 else "-"
                price = tds[3].get_text(strip=True) if len(tds) > 3 else "-"
                chg = tds[4].get_text(strip=True) if len(tds) > 4 else "-"
                vol = tds[6].get_text(strip=True) if len(tds) > 6 else "-"
                tech_rating = tds[-1].get_text(strip=True) if len(tds) > 7 else "-"

                # Bersihkan format
                chg = chg.replace("\u2212", "-")
                price = price.replace("USD", "").replace("$", "").strip()
                txs = txs.replace("\u202f", "")
                vol = vol.replace("\u202f", "").replace("USD", "").strip()

                # Semak pertindihan (overlap) dengan CoinGecko
                cg_overlap = None
                sym_clean = ticker.upper()
                name_clean = name.lower()
                if sym_clean in cg_trending_map:
                    cg_overlap = cg_trending_map[sym_clean]
                elif name_clean in cg_trending_map:
                    cg_overlap = cg_trending_map[name_clean]

                items.append({
                    "ticker": ticker,
                    "name": name,
                    "price": price,
                    "chg": chg,
                    "txs": txs,
                    "vol": vol,
                    "rating": tech_rating,
                    "cg_overlap": cg_overlap
                })
                if len(items) >= limit:
                    break
    except Exception as e:
        app.logger.error("Ralat fetch TradingView Most Transactions: %s", e)

    # 3. Fallback jika TradingView disekat atau gagal
    if not items:
        # Fallback kepada CoinGecko Trending
        try:
            r_cg_fb = requests.get("https://api.coingecko.com/api/v3/search/trending", headers=headers, timeout=10)
            if r_cg_fb.status_code == 200:
                raw_coins = r_cg_fb.json().get("coins", [])
                for c in raw_coins[:limit]:
                    item = c.get("item", {})
                    d = item.get("data", {})
                    items.append({
                        "ticker": item.get("symbol", "").strip().upper(),
                        "name": item.get("name", "").strip(),
                        "price": str(d.get("price", "0")),
                        "chg": str(d.get("price_change_percentage_24h", {}).get("usd", "0")),
                        "txs": "-",
                        "vol": str(d.get("total_volume", "-")),
                        "rating": "-",
                        "cg_overlap": {"rank": item.get("market_cap_rank", 1)}
                    })
        except Exception:
            pass

    if not items:
        return [(
            "⚠️ <b>Gagal memuat turun data transaksi kripto TradingView buat masa ini.</b>\n"
            "Sila layari terus: <a href=\"https://www.tradingview.com/markets/cryptocurrencies/prices-most-transactions/\">TradingView Most Transactions</a>\n\n"
            f"{DISCLAIMER_HTML}"
        )]

    overlap_count = sum(1 for it in items if it["cg_overlap"])

    tz = ZoneInfo("Asia/Kuala_Lumpur")
    now = datetime.now(tz)
    time_str = now.strftime("%d %b %Y, %I:%M %p")
    rank_emojis = ["1️⃣", "2️⃣", "3️⃣", "4️⃣", "5️⃣", "6️⃣", "7️⃣", "8️⃣", "9️⃣", "🔟"]

    def format_row(i, it):
        rk = rank_emojis[i] if i < len(rank_emojis) else f"<b>{i+1}.</b>"
        tk = it["ticker"]
        nm = it["name"]
        p = it["price"]
        c = it["chg"]
        tx = it["txs"]
        v = it["vol"]
        rating = it["rating"]

        badge = "⚪"
        if c.startswith("+"):
            badge = "🟢"
        elif c.startswith("-"):
            badge = "🔴"

        cg_badge = ""
        if it["cg_overlap"]:
            cg_rank = it["cg_overlap"].get("rank")
            cg_badge = f" | 🔥 <i>CG #{cg_rank}</i>"

        rating_str = f" | <i>{rating}</i>" if rating and rating != "-" else ""
        return f"{rk} <b>{nm}</b> (<code>{tk}</code>) — <b>${p}</b> | {badge} {c} | Txs: {tx} | Vol: ${v}{cg_badge}{rating_str}"

    p1 = [
        f"⚡ <b>TOP 50 KRIPTO AKTIF & TRANSAKSI TERTINGGI (Bhg 1: #1 - #25)</b>",
        "🌐 Sumber: <a href=\"https://www.tradingview.com/markets/cryptocurrencies/prices-most-transactions/\">TradingView Most Transactions</a> + <a href=\"https://www.coingecko.com/en/highlights/trending-crypto\">CoinGecko Trending</a>",
        "⏱ Pasaran: 🟢 <b>Dibuka 24/7 (Pasaran Global Kripto)</b>",
        f"🕒 Dikemaskini: <code>{time_str} MYT</code>",
        f"🔥 <i>{overlap_count} kaunter aktif turut trending di CoinGecko!</i>",
        "────────────────────────",
    ]
    for i, it in enumerate(items[:25]):
        p1.append(format_row(i, it))

    p2 = [
        f"⚡ <b>TOP 50 KRIPTO AKTIF & TRANSAKSI TERTINGGI (Bhg 2: #26 - #{len(items)})</b>",
        "────────────────────────",
    ]
    for i, it in enumerate(items[25:], start=25):
        p2.append(format_row(i, it))

    p2.append("\n💡 <i>Tip: Taip nama syiling atau kod (cth: <code>sol 1h</code> atau <code>chart xrp</code>) untuk melihat ulasan & carta lilin teknikal TradingView.</i>\n")
    p2.append(DISCLAIMER_HTML)

    result = ["\n".join(p1), "\n".join(p2)]
    _crypto_active_cache["data"] = result
    _crypto_active_cache["timestamp"] = now_ts
    return result

# Alias untuk keserasian ke belakang
get_crypto_trending_cards = get_crypto_active_cards


# ── Fear & Greed Index (US Stocks, Crypto, Bursa Malaysia) ────────────────────

def render_gauge_bar(score: int, length: int = 10) -> str:
    filled = max(0, min(length, round(score / 100 * length)))
    empty = length - filled
    return "█" * filled + "░" * empty

def get_sentiment_label(score: int) -> str:
    if score <= 20:
        return "EXTREME FEAR"
    elif score <= 40:
        return "FEAR"
    elif score <= 60:
        return "NEUTRAL"
    elif score <= 80:
        return "GREED"
    else:
        return "EXTREME GREED"

def fetch_cnn_fgi() -> dict | None:
    headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"}
    url = "https://production.dataviz.cnn.io/index/fearandgreed/graphdata"
    try:
        r = requests.get(url, headers=headers, timeout=8)
        if r.status_code == 200:
            data = r.json()
            fng = data.get("fear_and_greed", {})
            score = round(fng.get("score", 0))
            rating = fng.get("rating", "").upper()
            prev_1w = round(fng.get("previous_1_week", 0))
            prev_1m = round(fng.get("previous_1_month", 0))
            prev_1y = round(fng.get("previous_1_year", 0))
            return {
                "score": score,
                "rating": rating,
                "prev_1w": prev_1w,
                "prev_1m": prev_1m,
                "prev_1y": prev_1y,
            }
    except Exception as e:
        app.logger.error("Ralat fetch_cnn_fgi: %s", e)
    return None

def fetch_crypto_fgi() -> dict | None:
    headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"}
    now = int(datetime.now().timestamp())
    start = now - (35 * 86400)
    chart_url = f"https://api.coinmarketcap.com/data-api/v3/fear-greed/chart?start={start}&end={now}"
    try:
        r = requests.get(chart_url, headers=headers, timeout=8)
        if r.status_code == 200:
            d_list = r.json().get("data", {}).get("dataList", [])
            if d_list:
                latest = d_list[-1]
                score = round(int(latest.get("score", 0)))
                rating = latest.get("name", "").upper()
                target_1w = now - (7 * 86400)
                target_1m = now - (30 * 86400)
                prev_1w_item = min(d_list, key=lambda x: abs(int(x.get("timestamp", 0)) - target_1w))
                prev_1w_score = round(int(prev_1w_item.get("score", 0)))
                prev_1w_rating = prev_1w_item.get("name", "").title()
                prev_1m_item = min(d_list, key=lambda x: abs(int(x.get("timestamp", 0)) - target_1m))
                prev_1m_score = round(int(prev_1m_item.get("score", 0)))
                prev_1m_rating = prev_1m_item.get("name", "").title()
                return {
                    "score": score,
                    "rating": rating,
                    "prev_1w_score": prev_1w_score,
                    "prev_1w_rating": prev_1w_rating,
                    "prev_1m_score": prev_1m_score,
                    "prev_1m_rating": prev_1m_rating,
                }
    except Exception as e:
        app.logger.error("Ralat fetch_crypto_fgi chart: %s", e)

    # Fallback 1: Scrape CMC page
    try:
        r_page = requests.get("https://coinmarketcap.com/charts/fear-and-greed-index/", headers=headers, timeout=8)
        if r_page.status_code == 200:
            m = re.search(r'\"currentIndex\":\{"score\":(\d+)[^}]*\"name\":\"([^\"]+)\"', r_page.text)
            if m:
                score = int(m.group(1))
                rating = m.group(2).upper()
                return {
                    "score": score,
                    "rating": rating,
                    "prev_1w_score": score,
                    "prev_1w_rating": rating.title(),
                    "prev_1m_score": None,
                    "prev_1m_rating": None,
                }
    except Exception as e:
        app.logger.error("Ralat fetch_crypto_fgi scrape: %s", e)

    # Fallback 2: Alternative.me
    try:
        r_alt = requests.get("https://api.alternative.me/fng/?limit=31", timeout=5)
        if r_alt.status_code == 200:
            items = r_alt.json().get("data", [])
            if items:
                score = int(items[0].get("value", 0))
                rating = items[0].get("value_classification", "").upper()
                w_item = items[6] if len(items) >= 7 else items[-1]
                w_score = int(w_item.get("value", 0))
                w_rating = w_item.get("value_classification", "").title()
                m_item = items[-1] if len(items) >= 30 else items[-1]
                m_score = int(m_item.get("value", 0))
                m_rating = m_item.get("value_classification", "").title()
                return {
                    "score": score,
                    "rating": rating,
                    "prev_1w_score": w_score,
                    "prev_1w_rating": w_rating,
                    "prev_1m_score": m_score,
                    "prev_1m_rating": m_rating,
                }
    except Exception as e:
        app.logger.error("Ralat fetch_crypto_fgi alt.me: %s", e)

    return None

def fetch_bursa_sentiment() -> dict | None:
    headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"}
    urls = [
        "https://www.malaysiastock.biz/Market-Gauge-New.aspx",
        "https://www.malaysiastock.biz/Market-Watch.aspx",
    ]
    for url in urls:
        try:
            r = requests.get(url, headers=headers, timeout=8)
            if r.status_code == 200:
                if BeautifulSoup:
                    soup = BeautifulSoup(r.text, "html.parser")
                    gainers_el = soup.find(id="lbIndice_GainerNo")
                    losers_el = soup.find(id="lbIndice_LosersNo")
                    if gainers_el and losers_el:
                        g_txt = gainers_el.text.strip().replace(",", "")
                        l_txt = losers_el.text.strip().replace(",", "")
                        if g_txt.isdigit() and l_txt.isdigit():
                            g = int(g_txt)
                            l = int(l_txt)
                            if (g + l) > 0:
                                # Formula rasmi MalaysiaStock.Biz Market Sentiment: Math.floor(Gainer / (Gainer + Loser) * 100)
                                score = int((g / (g + l)) * 100)
                                return {
                                    "score": score,
                                    "rating": get_sentiment_label(score),
                                    "gainers": f"{g:,}",
                                    "losers": f"{l:,}",
                                }
                    gauge_val = soup.find(id="lbGaugeIndexVal")
                    if gauge_val and gauge_val.text.strip().isdigit():
                        score = int(gauge_val.text.strip())
                        return {
                            "score": score,
                            "rating": get_sentiment_label(score),
                            "gainers": "-",
                            "losers": "-",
                        }
                m = re.search(r'id=\"lbGaugeIndexVal\"[^>]*>(\d+)<', r.text)
                if m:
                    score = int(m.group(1))
                    return {
                        "score": score,
                        "rating": get_sentiment_label(score),
                        "gainers": "-",
                        "losers": "-",
                    }
        except Exception as e:
            app.logger.error("Ralat fetch_bursa_sentiment: %s", e)
    return None

def get_sentiment_commentary(market: str, score: int, rating: str) -> str:
    """Ulasan ringkas psikologi pasaran berdasarkan skor dan zon sentimen."""
    r = str(rating).upper()
    if "EXTREME FEAR" in r or score <= 24:
        return "Fasa panik pasaran melampau — peluang potensi zon diskaun / oversold bagi pelabur berdisiplin."
    elif "FEAR" in r or score <= 44:
        if market == "bursa":
            return "Pasaran defensif — sentimen berhati-hati dengan tekanan kaunter rugi mendominasi pasaran."
        return "Pasaran defensif — tekanan jualan masih membayangi pergerakan harga."
    elif "NEUTRAL" in r or score <= 55:
        return "Keseimbangan pembeli & penjual — pasaran berkonsolidasi mencari arah seterusnya."
    elif "EXTREME GREED" in r or score >= 76:
        return "Euforia belian memuncak — sentiasa berwaspada potensi pembetulan teknikal (overbought)."
    else:
        return "Optimisme belian kukuh — momentum pasaran disokong aliran belian aktif."

def generate_fgi_triple_gauge_chart(cnn: dict | None, cmc: dict | None, bursa: dict | None) -> bytes | None:
    """Menjana gambar grafik tolok separa bulat 3-dalam-1 (US, Crypto, Bursa) berkualiti tinggi."""
    try:
        import numpy as np
        from matplotlib.patches import Wedge, Circle, Polygon

        fig, axes = plt.subplots(1, 3, figsize=(13, 4.2), facecolor='#0B0E14')

        cnn_score = cnn.get("score", 50) if cnn else 50
        cnn_rating = cnn.get("rating", "NEUTRAL") if cnn else "N/A"

        cmc_score = cmc.get("score", 50) if cmc else 50
        cmc_rating = cmc.get("rating", "NEUTRAL") if cmc else "N/A"

        bursa_score = bursa.get("score", 50) if bursa else 50
        bursa_rating = bursa.get("rating", "NEUTRAL") if bursa else "N/A"

        markets = [
            ("US STOCKS (CNN)", cnn_score, cnn_rating),
            ("CRYPTO (CMC)", cmc_score, cmc_rating),
            ("BURSA MALAYSIA", bursa_score, bursa_rating),
        ]

        zones = [
            (136.8, 180, '#EA3943'),   # Extreme Fear
            (100.8, 136.8, '#F6851B'),  # Fear
            (81.0, 100.8, '#F3D42F'),   # Neutral
            (45.0, 81.0, '#93D900'),    # Greed
            (0.0, 45.0, '#16C784')      # Extreme Greed
        ]

        for ax, (m_title, m_score, m_sent) in zip(axes, markets):
            ax.set_facecolor('#0B0E14')
            cx, cy = 0, 0
            for t1, t2, col in zones:
                w = Wedge((cx, cy), 1.0, t1, t2, width=0.30, facecolor=col, edgecolor='#0B0E14', linewidth=1.5, alpha=0.95)
                ax.add_patch(w)

            angle_deg = 180.0 - (m_score * 1.8)
            angle_rad = np.radians(angle_deg)
            arrow_x = cx + 0.82 * np.cos(angle_rad)
            arrow_y = cy + 0.82 * np.sin(angle_rad)

            perp_angle = angle_rad + np.pi / 2
            base_w = 0.035
            b_x1 = cx + base_w * np.cos(perp_angle)
            b_y1 = cy + base_w * np.sin(perp_angle)
            b_x2 = cx - base_w * np.cos(perp_angle)
            b_y2 = cy - base_w * np.sin(perp_angle)

            needle = Polygon([
                (b_x1, b_y1), (arrow_x, arrow_y), (b_x2, b_y2),
                (cx - 0.05 * np.cos(angle_rad), cy - 0.05 * np.sin(angle_rad))
            ], facecolor='#FFFFFF', edgecolor='#0B0E14', linewidth=1.2, zorder=10)
            ax.add_patch(needle)

            p_out = Circle((cx, cy), 0.10, facecolor='#1E222D', edgecolor='#FFFFFF', linewidth=1.5, zorder=11)
            p_in = Circle((cx, cy), 0.04, facecolor='#00F0FF', zorder=12)
            ax.add_patch(p_out)
            ax.add_patch(p_in)

            sent_upper = str(m_sent).upper()
            if "EXTREME GREED" in sent_upper:
                s_col = '#16C784'
            elif "GREED" in sent_upper:
                s_col = '#93D900'
            elif "EXTREME FEAR" in sent_upper:
                s_col = '#EA3943'
            elif "FEAR" in sent_upper:
                s_col = '#F6851B'
            else:
                s_col = '#F3D42F'

            ax.text(cx, cy - 0.28, f'{m_score}/100', fontsize=18, fontweight='bold', color='#FFFFFF', ha='center', va='center')
            ax.text(cx, cy - 0.46, m_sent, fontsize=12, fontweight='bold', color=s_col, ha='center', va='center')
            ax.text(cx, cy + 1.18, m_title, fontsize=11, fontweight='bold', color='#E1E3E6', ha='center', va='center')
            ax.text(cx - 1.05, cy - 0.02, '0', fontsize=8, color='#848E9C', ha='center', va='top')
            ax.text(cx + 1.05, cy - 0.02, '100', fontsize=8, color='#848E9C', ha='center', va='top')

            ax.set_xlim(-1.25, 1.25)
            ax.set_ylim(-0.6, 1.35)
            ax.set_aspect('equal')
            ax.axis('off')

        plt.suptitle('Market Sentiment & Fear & Greed Gauges | LangkahTrade AI', color='#848E9C', fontsize=11, y=0.98)
        plt.tight_layout()

        buf = io.BytesIO()
        plt.savefig(buf, format='png', bbox_inches='tight', dpi=150)
        plt.close(fig)
        buf.seek(0)
        return buf.getvalue()
    except Exception as e:
        app.logger.error("Ralat generate_fgi_triple_gauge_chart: %s", e)
        return None

def format_fgi_card(cnn: dict | None, cmc: dict | None, bursa: dict | None) -> str:
    lines = [
        "😱 <b>Fear & Greed Index (Sentimen Pasaran)</b>\n"
    ]

    # 1. US Stocks (CNN)
    if cnn:
        lines.append(f"🇺🇸 <b>US STOCKS (CNN):</b> <code>{cnn['score']}/100</code> — <b>{cnn['rating']}</b>")
        lines.append(f"<code>{render_gauge_bar(cnn['score'])}</code>")
        lines.append(f"<i>1 week: {cnn['prev_1w']}  |  1 month: {cnn['prev_1m']}  |  1 year: {cnn['prev_1y']}</i>")
        lines.append(f"💡 <i>Ulasan: {get_sentiment_commentary('us', cnn['score'], cnn['rating'])}</i>\n")
    else:
        lines.append("🇺🇸 <b>US STOCKS (CNN):</b> <i>Data tidak tersedia buat masa ini.</i>\n")

    # 2. Crypto (CoinMarketCap)
    if cmc:
        lines.append(f"🪙 <b>CRYPTO (CoinMarketCap):</b> <code>{cmc['score']}/100</code> — <b>{cmc['rating']}</b>")
        lines.append(f"<code>{render_gauge_bar(cmc['score'])}</code>")
        history_parts = [f"1 week: {cmc['prev_1w_score']} ({cmc['prev_1w_rating']})"]
        if cmc.get("prev_1m_score") is not None:
            history_parts.append(f"Last month: {cmc['prev_1m_score']} ({cmc['prev_1m_rating']})")
        lines.append(f"<i>{'  |  '.join(history_parts)}</i>")
        lines.append(f"💡 <i>Ulasan: {get_sentiment_commentary('crypto', cmc['score'], cmc['rating'])}</i>\n")
    else:
        lines.append("🪙 <b>CRYPTO (CoinMarketCap):</b> <i>Data tidak tersedia buat masa ini.</i>\n")

    # 3. Bursa Malaysia
    if bursa:
        lines.append(f"🇲🇾 <b>BURSA MALAYSIA — Market Sentiment:</b> <code>{bursa['score']}/100</code> — <b>{bursa['rating']}</b>")
        lines.append(f"<code>{render_gauge_bar(bursa['score'])}</code>")
        if bursa.get("gainers") != "-" and bursa.get("losers") != "-":
            lines.append(f"📊 <i>Statistik Pasaran: 🟢 Gainer {bursa['gainers']}  |  🔴 Loser {bursa['losers']}</i>")
        lines.append(f"💡 <i>Ulasan: {get_sentiment_commentary('bursa', bursa['score'], bursa['rating'])}</i>\n")
    else:
        lines.append("🇲🇾 <b>BURSA MALAYSIA:</b> <i>Data sentimen tidak tersedia buat masa ini.</i>\n")

    lines.append("📌 <i>0 = Extreme Fear, 100 = Extreme Greed. Not financial advice.</i>")
    lines.append("⚠️ <i>Penafian: Rujukan teknikal sahaja, bukan nasihat kewangan atau pelaburan (DYOR).</i>")

    return "\n".join(lines)

def get_fear_and_greed_card() -> str:
    cnn = fetch_cnn_fgi()
    cmc = fetch_crypto_fgi()
    bursa = fetch_bursa_sentiment()
    return format_fgi_card(cnn, cmc, bursa)


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
        if (df is None or df.empty) and yf:
            try:
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
            except Exception:
                pass

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
                "Disambungkan terus ke TradingView, ShareInvestor (Bursa), Yahoo Finance (NASDAQ) & CoinGecko.\n"
                "────────────────────────\n\n"
                "💡 <b>Paling Mudah:</b> Anda <b>TIDAK PERLU</b> taip simbol '/' langsung! Boleh taip nama saham atau tanya soalan macam biasa.\n\n"
                "📌 <b>Contoh Taip Terus (Tanpa '/'):</b>\n"
                "🔹 <code>fgi</code> — Fear & Greed Index (US Stocks, Kripto & Bursa Malaysia)\n"
                "🔹 <code>aktif bursa</code> — Top 50 Kaunter Aktif Harian (ShareInvestor 9am-5pm)\n"
                "🔹 <code>aktif nasdaq</code> — Top 50 Saham Paling Aktif US (Yahoo Finance)\n"
                "🔹 <code>aktif kripto</code> @ <code>trending kripto</code> — Top 50 Kripto Transaksi Tertinggi (TradingView + CoinGecko)\n"
                "🔹 <code>chart xrp</code> atau <code>sol 4h</code> — Analisis Kripto & Carta Lilin\n"
                "🔹 <code>maybank</code> atau <code>cimb</code> — Saham Bursa Malaysia\n"
                "🔹 <code>nvda</code> atau <code>tsla 1d</code> — Saham US / NASDAQ\n"
                "🔹 <code>usdmyr</code> — Pasaran Forex\n"
                "🔹 <i>'panduan indikator'</i> — Cara pasang RSI & EMA\n"
                "🔹 <i>'setup alert'</i> — Cara sambung webhook TradingView\n\n"
                "🤖 Boleh juga gunakan arahan biasa:\n"
                f"🔹 <code>/fgi</code> | <code>/aktif bursa</code> | <code>/aktif nasdaq</code> | <code>/aktif kripto</code> | <code>/trending</code> | <code>/status BTC</code>\n\n"
                f"{DISCLAIMER_HTML}",
                chat_id=chat_id,
            )
            return "OK", 200

        if text.startswith("/help") or clean_lower in ["help", "bantuan", "menu", "arahan"]:
            send_telegram(
                "🤖 <b>Panduan Penggunaan Bot:</b>\n"
                "────────────────────────\n"
                "💡 <i>Tip: Anda boleh taip terus tanpa simbol '/'!</i>\n\n"
                "🔹 <b>Fear & Greed Index:</b> Taip <i>'fgi'</i>, <i>'sentimen'</i> atau <code>/fgi</code>\n"
                "🔹 <b>Kaunter Aktif Bursa:</b> Taip <i>'kaunter aktif bursa'</i> atau <code>/aktif bursa</code>\n"
                "🔹 <b>Kaunter Aktif NASDAQ:</b> Taip <i>'kaunter aktif nasdaq'</i> atau <code>/aktif nasdaq</code>\n"
                "🔹 <b>Kripto Transaksi Aktif:</b> Taip <i>'aktif kripto'</i>, <i>'trending kripto'</i> atau <code>/trending</code>\n"
                "🔹 <b>Carian Ticker Pantas:</b> Taip <code>btc</code>, <code>maybank</code>, <code>nvda</code>, atau <code>sol 4h</code>\n"
                "🔹 <b>Carta Teknikal:</b> Taip <i>'chart xrp'</i>, <i>'carta btc'</i> atau <i>'graf maybank'</i>\n"
                "🔹 <b>Panduan Indikator:</b> Taip <i>'indikator'</i> atau <code>/indicator</code>\n"
                "🔹 <b>Setup Alert:</b> Taip <i>'alert'</i>, <i>'strategi'</i> atau <code>/strategy</code>\n\n"
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

        # 2.4 Fear & Greed Index (Arahan /fgi & Bahasa Biasa)
        if text.startswith("/fgi") or text.startswith("/sentiment") or text.startswith("/sentimen") or any(k in clean_lower for k in [
            "fear and greed", "fear & greed", "fgi", "fear greed",
            "market sentiment", "sentimen pasaran", "sentimen bursa",
            "sentimen crypto", "sentimen kripto", "sentimen us",
            "index sentimen", "indeks sentimen", "sentimen semasa"
        ]):
            cnn = fetch_cnn_fgi()
            cmc = fetch_crypto_fgi()
            bursa = fetch_bursa_sentiment()
            fgi_text = format_fgi_card(cnn, cmc, bursa)
            gauge_img = generate_fgi_triple_gauge_chart(cnn, cmc, bursa)
            if gauge_img:
                send_telegram_photo(gauge_img, fgi_text, chat_id=chat_id)
            else:
                send_telegram(fgi_text, chat_id=chat_id)
            return "OK", 200

        # 2.5 Arahan Standard /aktif, /active, atau /trending
        if text.startswith("/aktif") or text.startswith("/active") or text.startswith("/top") or text.startswith("/mostactive") or text.startswith("/trending"):
            parts = text.split()
            sub = parts[1].lower() if len(parts) > 1 else ""
            if "bursa" in sub or "my" in sub or "malaysia" in sub:
                for card in get_bursa_active_cards(50):
                    send_telegram(card, chat_id=chat_id)
            elif "nasdaq" in sub or "us" in sub or "nyse" in sub:
                for card in get_nasdaq_active_cards(50):
                    send_telegram(card, chat_id=chat_id)
            elif "crypto" in sub or "kripto" in sub or "coin" in sub or text.startswith("/trending"):
                for card in get_crypto_active_cards(50):
                    send_telegram(card, chat_id=chat_id)
            else:
                for card in get_bursa_active_cards(50):
                    send_telegram(card, chat_id=chat_id)
                for card in get_nasdaq_active_cards(50):
                    send_telegram(card, chat_id=chat_id)
                for card in get_crypto_active_cards(50):
                    send_telegram(card, chat_id=chat_id)
            return "OK", 200

        # 2.6 Kaunter Aktif Pasaran (Bursa Malaysia, NASDAQ US & Trending Crypto TradingView + CoinGecko)
        if any(k in clean_lower for k in [
            "kaunter aktif", "top aktif", "saham aktif", "most active", "top volume",
            "aktif bursa", "bursa aktif", "aktif nasdaq", "nasdaq aktif",
            "aktif kripto", "aktif crypto", "kripto aktif", "crypto aktif",
            "trending kripto", "trending crypto", "kripto trending", "crypto trending",
            "top trending", "trending coin", "trending coins",
            "50 kaunter", "senarai kaunter", "kaunter paling aktif"
        ]):
            is_bursa = any(b in clean_lower for b in ["bursa", "malaysia", "klse", "my"])
            is_nasdaq = any(n in clean_lower for n in ["nasdaq", "us", "amerika", "nyse"])
            is_crypto = any(c in clean_lower for c in ["kripto", "crypto", "coin", "coins", "coingecko"])

            if is_crypto and not is_bursa and not is_nasdaq:
                for card in get_crypto_active_cards(50):
                    send_telegram(card, chat_id=chat_id)
            elif is_bursa and not is_nasdaq and not is_crypto:
                for card in get_bursa_active_cards(50):
                    send_telegram(card, chat_id=chat_id)
            elif is_nasdaq and not is_bursa and not is_crypto:
                for card in get_nasdaq_active_cards(50):
                    send_telegram(card, chat_id=chat_id)
            else:
                if is_bursa or (not is_nasdaq and not is_crypto):
                    for card in get_bursa_active_cards(50):
                        send_telegram(card, chat_id=chat_id)
                if is_nasdaq or (not is_bursa and not is_crypto):
                    for card in get_nasdaq_active_cards(50):
                        send_telegram(card, chat_id=chat_id)
                if is_crypto or (not is_bursa and not is_nasdaq):
                    for card in get_crypto_active_cards(50):
                        send_telegram(card, chat_id=chat_id)
            return "OK", 200

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
                "BERAPA", "TF", "TIMEFRAME", "1M", "5M", "15M", "1H", "4H", "1D", "1W",
                "AKTIF", "ACTIVE", "TOP", "MOST", "VOLUME", "SAHAM", "KAUNTER"
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
