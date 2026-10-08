#!/usr/bin/env python3
"""
Binance USDT-M Futures tarayıcısı.

Son 30 dakikada:
  * hacim artışı YAPMAMIŞ (son 30 dk hacmi, önceki dönemin 30 dk ortalamasını
    belirlenen oranın üzerinde geçmemiş) ve
  * fiyatı %1'den fazla YÜKSELMEMİŞ
olan perpetual kontratları listeler.

Sadece herkese açık (public) endpoint'ler kullanılır, API anahtarı gerekmez.
"""

import argparse
import csv
import os
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime

import requests

BASE_URL = os.environ.get("BINANCE_FAPI_URL", "https://fapi.binance.com")
SESSION = requests.Session()


def get(path, params=None, retries=3):
    for attempt in range(retries):
        try:
            r = SESSION.get(BASE_URL + path, params=params, timeout=10)
            if r.status_code in (418, 429):
                # Rate limit: Binance'in istediği kadar bekle
                time.sleep(int(r.headers.get("Retry-After", 5)))
                continue
            if r.status_code == 451:
                sys.exit("Binance bu bölgeden erişimi engelliyor (HTTP 451). VPN/sunucu konumunu değiştirin.")
            r.raise_for_status()
            return r.json()
        except requests.RequestException:
            if attempt == retries - 1:
                raise
            time.sleep(2 ** attempt)


def usdt_perpetuals():
    info = get("/fapi/v1/exchangeInfo")
    return [
        s["symbol"]
        for s in info["symbols"]
        if s["contractType"] == "PERPETUAL" and s["status"] == "TRADING" and s["quoteAsset"] == "USDT"
    ]


def quote_volumes_24h():
    return {t["symbol"]: float(t["quoteVolume"]) for t in get("/fapi/v1/ticker/24hr")}


def analyze(symbol, window, baseline_windows):
    """1 dakikalık kapanmış mumlarla son `window` dakikayı önceki dönemle karşılaştırır."""
    need = window * (baseline_windows + 1)
    # +1: henüz kapanmamış (canlı) mum atılacak
    klines = get("/fapi/v1/klines", {"symbol": symbol, "interval": "1m", "limit": need + 1})
    now_ms = int(time.time() * 1000)
    closed = [k for k in klines if int(k[6]) < now_ms][-need:]
    if len(closed) < need:
        return None  # yeni listelenmiş coin, yeterli veri yok

    recent = closed[-window:]
    baseline = closed[:-window]

    open_price = float(recent[0][1])
    close_price = float(recent[-1][4])
    price_change = (close_price - open_price) / open_price * 100

    recent_vol = sum(float(k[7]) for k in recent)  # quote volume (USDT)
    baseline_avg = sum(float(k[7]) for k in baseline) / baseline_windows
    vol_ratio = recent_vol / baseline_avg if baseline_avg > 0 else float("inf")

    return {
        "symbol": symbol,
        "price": close_price,
        "price_change_pct": price_change,
        "recent_vol": recent_vol,
        "baseline_avg_vol": baseline_avg,
        "vol_ratio": vol_ratio,
    }


def scan(args):
    symbols = usdt_perpetuals()
    if args.min_24h_volume > 0:
        vols = quote_volumes_24h()
        symbols = [s for s in symbols if vols.get(s, 0) >= args.min_24h_volume]

    results, errors = [], 0
    with ThreadPoolExecutor(max_workers=args.workers) as pool:
        futures = {
            pool.submit(analyze, s, args.window, args.baseline_windows): s for s in symbols
        }
        for f in as_completed(futures):
            try:
                r = f.result()
            except Exception:
                errors += 1
                continue
            if r is None:
                continue
            price_ok = (
                abs(r["price_change_pct"]) <= args.max_price_change
                if args.abs_price
                else r["price_change_pct"] <= args.max_price_change
            )
            if price_ok and r["vol_ratio"] <= args.max_vol_ratio:
                results.append(r)

    results.sort(key=lambda r: r["vol_ratio"])
    return results, len(symbols), errors


def fmt_vol(v):
    for unit, div in (("B", 1e9), ("M", 1e6), ("K", 1e3)):
        if v >= div:
            return f"{v / div:.2f}{unit}"
    return f"{v:.0f}"


def print_table(results, total, errors, args):
    ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    print(f"\n[{ts}] {total} kontrat tarandı, {len(results)} eşleşme"
          + (f", {errors} hata" if errors else ""))
    print(f"Kriter: son {args.window} dk fiyat değişimi "
          f"{'|x|' if args.abs_price else ''}<= %{args.max_price_change}, "
          f"hacim oranı <= {args.max_vol_ratio}x (önceki {args.baseline_windows} x {args.window} dk ortalamasına göre)")
    if not results:
        return
    print(f"{'SEMBOL':<16}{'FİYAT':>14}{'30dk %':>9}{'30dk HACİM':>13}{'ORT. HACİM':>13}{'ORAN':>7}")
    for r in results[: args.top]:
        print(f"{r['symbol']:<16}{r['price']:>14.6g}{r['price_change_pct']:>+8.2f}%"
              f"{fmt_vol(r['recent_vol']):>13}{fmt_vol(r['baseline_avg_vol']):>13}{r['vol_ratio']:>6.2f}x")
    if len(results) > args.top:
        print(f"... ve {len(results) - args.top} tane daha (--top ile artırın)")


def save_csv(results, path):
    new = not os.path.exists(path)
    ts = datetime.now().isoformat(timespec="seconds")
    with open(path, "a", newline="") as f:
        w = csv.writer(f)
        if new:
            w.writerow(["time", "symbol", "price", "price_change_pct", "recent_vol", "baseline_avg_vol", "vol_ratio"])
        for r in results:
            w.writerow([ts, r["symbol"], r["price"], round(r["price_change_pct"], 4),
                        round(r["recent_vol"], 2), round(r["baseline_avg_vol"], 2), round(r["vol_ratio"], 4)])


def send_telegram(results, args):
    token, chat = os.environ.get("TELEGRAM_BOT_TOKEN"), os.environ.get("TELEGRAM_CHAT_ID")
    if not (token and chat and results):
        return
    lines = [f"{len(results)} coin: hacim artışı yok, 30dk değişim <= %{args.max_price_change}"]
    lines += [f"{r['symbol']}  {r['price_change_pct']:+.2f}%  hacim {r['vol_ratio']:.2f}x"
              for r in results[: args.top]]
    try:
        requests.post(f"https://api.telegram.org/bot{token}/sendMessage",
                      json={"chat_id": chat, "text": "\n".join(lines)}, timeout=10)
    except requests.RequestException as e:
        print(f"Telegram gönderilemedi: {e}", file=sys.stderr)


def main():
    p = argparse.ArgumentParser(description="Hacim artışı olmayan ve 30 dk'da %1'den fazla yükselmemiş futures coinlerini bulur.")
    p.add_argument("--window", type=int, default=30, help="İncelenen pencere (dakika). Varsayılan: 30")
    p.add_argument("--max-price-change", type=float, default=1.0, help="Pencerede izin verilen maks. fiyat artışı (%%). Varsayılan: 1.0")
    p.add_argument("--abs-price", action="store_true", help="Düşüşü de sınırla: |değişim| <= limit (yatay coinler)")
    p.add_argument("--max-vol-ratio", type=float, default=1.0,
                   help="Son pencere hacmi / önceki ortalama hacim üst sınırı. 1.0 = hiç artış yok. Varsayılan: 1.0")
    p.add_argument("--baseline-windows", type=int, default=6,
                   help="Karşılaştırma için kaç önceki pencere kullanılsın (6 x 30dk = 3 saat). Varsayılan: 6")
    p.add_argument("--min-24h-volume", type=float, default=5_000_000,
                   help="24s USDT hacmi bunun altındaki ölü coinleri atla. 0 = kapalı. Varsayılan: 5M")
    p.add_argument("--top", type=int, default=50, help="Ekranda gösterilecek maks. sonuç")
    p.add_argument("--workers", type=int, default=8, help="Paralel istek sayısı")
    p.add_argument("--loop", type=int, default=0, help="Her N saniyede bir tekrar tara (0 = tek sefer)")
    p.add_argument("--csv", help="Sonuçları bu CSV dosyasına ekle")
    args = p.parse_args()

    if args.window * (args.baseline_windows + 1) > 1499:
        p.error("window * (baseline_windows + 1) en fazla 1499 olabilir (Binance kline limiti)")

    while True:
        try:
            results, total, errors = scan(args)
            print_table(results, total, errors, args)
            if args.csv:
                save_csv(results, args.csv)
            send_telegram(results, args)
        except requests.RequestException as e:
            print(f"Tarama başarısız: {e}", file=sys.stderr)
        if not args.loop:
            break
        time.sleep(args.loop)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        pass
