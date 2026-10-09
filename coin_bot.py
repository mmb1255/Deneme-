"""Her dakika Binance Futures coinlerini tarayıp her biri için Claude'a LONG/SHORT soran bot.

Kullanım:
    pip install -r requirements.txt
    export ANTHROPIC_API_KEY=sk-ant-...
    python coin_bot.py                 # hacme göre ilk 20 coin, her 60 sn
    python coin_bot.py --top 50        # ilk 50 coin
    python coin_bot.py --symbols BTCUSDT,ETHUSDT,SOLUSDT
    python coin_bot.py --once          # tek tur çalıştır ve çık

UYARI: Bu bir yatırım tavsiyesi aracı değildir. Çıktılar 1 dakikalık mum
verisine dayalı tahminlerdir; otomatik emir göndermez.
"""

import argparse
import asyncio
import csv
import json
import os
import sys
import time
from datetime import datetime, timezone

import anthropic
import httpx2 as httpx

BINANCE = "https://fapi.binance.com"
MODEL = os.environ.get("CLAUDE_MODEL", "claude-opus-5-5")
LOG_FILE = "sinyaller.csv"

SYSTEM_PROMPT = """Sen kısa vadeli kripto futures işlem analistisin.
Sana bir coinin son 1 dakikalık ve 15 dakikalık mum verilerinden hesaplanmış göstergeler verilecek.
Önümüzdeki 5-15 dakika için yön kararı ver: LONG, SHORT veya BEKLE.
Sinyal net değilse BEKLE de; zorla yön seçme. Güveni 0-100 arası ver.
Gerekçeyi Türkçe, en fazla bir cümle yaz."""

DECISION_SCHEMA = {
    "type": "object",
    "properties": {
        "karar": {"type": "string", "enum": ["LONG", "SHORT", "BEKLE"]},
        "guven": {"type": "integer"},
        "gerekce": {"type": "string"},
    },
    "required": ["karar", "guven", "gerekce"],
    "additionalProperties": False,
}


# ---------------------------------------------------------------- göstergeler

def ema(values, period):
    k = 2 / (period + 1)
    out = values[0]
    for v in values[1:]:
        out = v * k + out * (1 - k)
    return out


def rsi(closes, period=14):
    if len(closes) <= period:
        return 50.0
    gains, losses = 0.0, 0.0
    for i in range(1, period + 1):
        diff = closes[i] - closes[i - 1]
        gains += max(diff, 0)
        losses += max(-diff, 0)
    avg_gain, avg_loss = gains / period, losses / period
    for i in range(period + 1, len(closes)):
        diff = closes[i] - closes[i - 1]
        avg_gain = (avg_gain * (period - 1) + max(diff, 0)) / period
        avg_loss = (avg_loss * (period - 1) + max(-diff, 0)) / period
    if avg_loss == 0:
        return 100.0
    return 100 - 100 / (1 + avg_gain / avg_loss)


def pct(a, b):
    return (a / b - 1) * 100 if b else 0.0


def summarize(symbol, k1m, k15m, ticker, funding):
    """Ham mumlardan Claude'a gidecek kısa özeti üretir."""
    c1 = [float(k[4]) for k in k1m]
    v1 = [float(k[5]) for k in k1m]
    c15 = [float(k[4]) for k in k15m]
    price = c1[-1]
    avg_vol = sum(v1[-31:-1]) / 30 if len(v1) > 30 else sum(v1) / len(v1)
    last5 = [
        f"O={float(k[1]):.6g} H={float(k[2]):.6g} L={float(k[3]):.6g} C={float(k[4]):.6g} V={float(k[5]):.4g}"
        for k in k1m[-5:]
    ]
    return "\n".join([
        f"Coin: {symbol}",
        f"Fiyat: {price:.6g}",
        f"Değişim 1dk: {pct(c1[-1], c1[-2]):+.2f}% | 5dk: {pct(c1[-1], c1[-6]):+.2f}% | "
        f"15dk: {pct(c1[-1], c1[-16]):+.2f}% | 60dk: {pct(c1[-1], c1[0]):+.2f}% | "
        f"24s: {float(ticker['priceChangePercent']):+.2f}%",
        f"1dk RSI(14): {rsi(c1):.1f} | 15dk RSI(14): {rsi(c15):.1f}",
        f"1dk EMA9/EMA21: {ema(c1, 9):.6g} / {ema(c1, 21):.6g}",
        f"15dk EMA9/EMA21: {ema(c15, 9):.6g} / {ema(c15, 21):.6g}",
        f"Son 1dk hacim / 30dk ort.: {v1[-1] / avg_vol if avg_vol else 0:.2f}x",
        f"60dk aralık: düşük {min(float(k[3]) for k in k1m):.6g} - yüksek {max(float(k[2]) for k in k1m):.6g}",
        f"Funding: {funding * 100:+.4f}%",
        "Son 5 adet 1dk mum:",
        *last5,
    ])


# ---------------------------------------------------------------- Binance

async def get_json(http, path, **params):
    r = await http.get(BINANCE + path, params=params)
    r.raise_for_status()
    return r.json()


async def top_symbols(http, n):
    tickers = await get_json(http, "/fapi/v1/ticker/24hr")
    info = await get_json(http, "/fapi/v1/exchangeInfo")
    trading = {
        s["symbol"] for s in info["symbols"]
        if s["status"] == "TRADING" and s["quoteAsset"] == "USDT" and s["contractType"] == "PERPETUAL"
    }
    tickers = [t for t in tickers if t["symbol"] in trading]
    tickers.sort(key=lambda t: float(t["quoteVolume"]), reverse=True)
    return [t["symbol"] for t in tickers[:n]]


async def market_snapshot(http, symbol):
    k1m, k15m, ticker, prem = await asyncio.gather(
        get_json(http, "/fapi/v1/klines", symbol=symbol, interval="1m", limit=61),
        get_json(http, "/fapi/v1/klines", symbol=symbol, interval="15m", limit=40),
        get_json(http, "/fapi/v1/ticker/24hr", symbol=symbol),
        get_json(http, "/fapi/v1/premiumIndex", symbol=symbol),
    )
    # Son 1dk mumu henüz kapanmadı; analizde sadece kapanmış mumları kullan.
    return summarize(symbol, k1m[:-1], k15m, ticker, float(prem["lastFundingRate"]))


# ---------------------------------------------------------------- Claude

async def ask_claude(client, snapshot):
    resp = await client.beta.messages.create(
        model=MODEL,
        max_tokens=4000,
        system=SYSTEM_PROMPT,
        messages=[{"role": "user", "content": snapshot}],
        output_config={"effort": "low", "format": {"type": "json_schema", "schema": DECISION_SCHEMA}},
        betas=["server-side-fallback-2026-07-01"],
        fallbacks="default",
    )
    if resp.stop_reason == "refusal":
        return {"karar": "BEKLE", "guven": 0, "gerekce": "Model yanıt vermeyi reddetti."}
    text = next((b.text for b in resp.content if b.type == "text"), "")
    return json.loads(text)


# ---------------------------------------------------------------- döngü

COLORS = {"LONG": "\033[92m", "SHORT": "\033[91m", "BEKLE": "\033[93m"}
RESET = "\033[0m"


async def analyze(sem, http, client, symbol):
    async with sem:
        try:
            snap = await market_snapshot(http, symbol)
            return symbol, await ask_claude(client, snap)
        except anthropic.RateLimitError:
            return symbol, {"hata": "Claude rate limit (429) - --concurrency düşür"}
        except anthropic.APIStatusError as e:
            return symbol, {"hata": f"Claude API {e.status_code}: {e.message}"}
        except anthropic.APIConnectionError:
            return symbol, {"hata": "Claude bağlantı hatası"}
        except httpx.HTTPError as e:
            return symbol, {"hata": f"Binance hatası: {e}"}
        except (json.JSONDecodeError, KeyError, ValueError, IndexError) as e:
            return symbol, {"hata": f"Veri/çıktı hatası: {e}"}


def log_rows(ts, results):
    new = not os.path.exists(LOG_FILE)
    with open(LOG_FILE, "a", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        if new:
            w.writerow(["zaman", "coin", "karar", "guven", "gerekce"])
        for sym, r in results:
            if "hata" not in r:
                w.writerow([ts, sym, r["karar"], r["guven"], r["gerekce"]])


async def run_cycle(http, client, symbols, concurrency):
    ts = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
    sem = asyncio.Semaphore(concurrency)
    results = await asyncio.gather(*(analyze(sem, http, client, s) for s in symbols))
    print(f"\n===== {ts} =====")
    for sym, r in results:  # coin listesi sırasıyla yazdırılır
        if "hata" in r:
            print(f"{sym:<14} HATA   {r['hata']}")
        else:
            c = COLORS.get(r["karar"], "")
            print(f"{sym:<14} {c}{r['karar']:<6}{RESET} %{r['guven']:<3} {r['gerekce']}")
    log_rows(ts, results)


async def main():
    p = argparse.ArgumentParser(description="Her dakika coinleri tarayıp Claude'a LONG/SHORT soran bot")
    p.add_argument("--top", type=int, default=20, help="24s hacme göre ilk N coin (varsayılan 20)")
    p.add_argument("--symbols", help="Virgülle ayrılmış coin listesi, örn. BTCUSDT,ETHUSDT")
    p.add_argument("--interval", type=int, default=60, help="Tur aralığı, saniye (varsayılan 60)")
    p.add_argument("--concurrency", type=int, default=5, help="Aynı anda kaç coin sorulsun (varsayılan 5)")
    p.add_argument("--once", action="store_true", help="Tek tur çalıştır ve çık")
    args = p.parse_args()

    client = anthropic.AsyncAnthropic()
    async with httpx.AsyncClient(timeout=15) as http:
        if args.symbols:
            symbols = [s.strip().upper() for s in args.symbols.split(",") if s.strip()]
        else:
            symbols = await top_symbols(http, args.top)
        print(f"Model: {MODEL} | {len(symbols)} coin: {', '.join(symbols)}")
        calls_day = len(symbols) * 86400 // args.interval
        print(f"Not: günde ~{calls_day:,} Claude çağrısı yapılacak. Maliyeti Console'dan takip et.")

        while True:
            start = time.monotonic()
            await run_cycle(http, client, symbols, args.concurrency)
            if args.once:
                return
            elapsed = time.monotonic() - start
            if elapsed > args.interval:
                print(f"Uyarı: tur {elapsed:.0f} sn sürdü (> {args.interval} sn). "
                      f"--concurrency artır veya --top düşür.")
            await asyncio.sleep(max(0, args.interval - elapsed))


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        sys.exit(0)
