#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
KM PRO V5 Tarayıcı Botu
=======================
Binance USDT-M vadeli (perpetual) coinlerini tarar, KM PRO V5 indikatöründeki
zaman dilimi backtest mantığını (f_calc_tf_stats) birebir uygular ve
  - net R  >= MIN_R   (varsayılan +10)
  - TP1 oranı >= MIN_TP1 (varsayılan %50)
  - işlem sayısı >= MIN_TRADES
şartlarını sağlayan coinleri Telegram'a yazar. Ayrıca bu coinlerde son kapanan
barda yeni LONG/SHORT açıldıysa ayrıca sinyal mesajı gönderir.

Kurulum:
    pip install -r requirements.txt
    export TG_BOT_TOKEN="BotFather'dan aldığın token"
    export TG_CHAT_ID="1056156546"
    python km_scanner.py --once --dry-run      # Telegram'a göndermeden dene
    python km_scanner.py                       # her 15 dk'da bir tarar, sürekli çalışır

Token'ı asla koda yazma; ortam değişkeni ya da .env dosyası kullan.
"""
import argparse
import json
import logging
import math
import os
import sys
import time
from dataclasses import dataclass, asdict
from datetime import datetime, timezone

import numpy as np
import pandas as pd
import requests

FAPI = "https://fapi.binance.com"
TF_MINUTES = {"1m": 1, "3m": 3, "5m": 5, "15m": 15, "30m": 30, "1h": 60, "2h": 120, "4h": 240, "1d": 1440}
WARMUP_BARS = 320   # EMA200 + ADX ısınması için pencere öncesi ek bar

log = logging.getLogger("km")


# ---------------------------------------------------------------------------
#  Pine eşdeğeri göstergeler
# ---------------------------------------------------------------------------
def _seeded_smooth(x, n, alpha):
    """Pine ta.ema / ta.rma: ilk değer n barlık SMA, sonrası üstel."""
    x = np.asarray(x, dtype=float)
    out = np.full(x.shape, np.nan)
    valid = np.where(np.isfinite(x))[0]
    if len(valid) == 0:
        return out
    f = valid[0]
    if f + n > len(x):
        return out
    out[f + n - 1] = np.mean(x[f:f + n])
    for i in range(f + n, len(x)):
        xi = x[i]
        if not np.isfinite(xi):
            xi = 0.0
        out[i] = alpha * xi + (1.0 - alpha) * out[i - 1]
    return out


def pine_ema(x, n):
    return _seeded_smooth(x, n, 2.0 / (n + 1.0))


def pine_rma(x, n):
    return _seeded_smooth(x, n, 1.0 / n)


def pine_sma(x, n):
    return pd.Series(np.asarray(x, dtype=float)).rolling(n).mean().to_numpy()


def pine_highest(x, n):
    return pd.Series(np.asarray(x, dtype=float)).rolling(n).max().to_numpy()


def pine_lowest(x, n):
    return pd.Series(np.asarray(x, dtype=float)).rolling(n).min().to_numpy()


def pine_rsi(close, n):
    ch = np.diff(close, prepend=np.nan)
    up = np.where(ch > 0, ch, 0.0)
    dn = np.where(ch < 0, -ch, 0.0)
    up[0] = np.nan
    dn[0] = np.nan
    au = pine_rma(up, n)
    ad = pine_rma(dn, n)
    with np.errstate(divide="ignore", invalid="ignore"):
        rsi = np.where(ad == 0, 100.0, np.where(au == 0, 0.0, 100.0 - 100.0 / (1.0 + au / ad)))
    rsi[~np.isfinite(au) | ~np.isfinite(ad)] = np.nan
    return rsi


def pine_tr(high, low, close):
    pc = np.roll(close, 1)
    pc[0] = np.nan
    tr = np.maximum(high - low, np.maximum(np.abs(high - pc), np.abs(low - pc)))
    tr[0] = high[0] - low[0]
    return tr


def pine_atr(high, low, close, n):
    return pine_rma(pine_tr(high, low, close), n)


def pine_dmi(high, low, close, n, adx_n):
    up = np.diff(high, prepend=np.nan)
    dn = -np.diff(low, prepend=np.nan)
    plus_dm = np.where((up > dn) & (up > 0), up, 0.0)
    minus_dm = np.where((dn > up) & (dn > 0), dn, 0.0)
    plus_dm[0] = np.nan
    minus_dm[0] = np.nan
    trur = pine_rma(pine_tr(high, low, close), n)
    with np.errstate(divide="ignore", invalid="ignore"):
        plus = 100.0 * pine_rma(plus_dm, n) / trur
        minus = 100.0 * pine_rma(minus_dm, n) / trur
    plus = pd.Series(plus).ffill().to_numpy()   # fixnan
    minus = pd.Series(minus).ffill().to_numpy()
    s = plus + minus
    with np.errstate(divide="ignore", invalid="ignore"):
        dx = np.abs(plus - minus) / np.where(s == 0, 1.0, s)
    adx = 100.0 * pine_rma(dx, adx_n)
    return plus, minus, adx


# ---------------------------------------------------------------------------
#  Strateji parametreleri ve backtest (Pine f_calc_tf_stats ile aynı)
# ---------------------------------------------------------------------------
@dataclass
class Params:
    lookback_days: int = 30
    use_mtf_trend: bool = True
    sl_atr_mult: float = 1.4
    tp1_rr: float = 1.0
    tp2_rr: float = 2.2


@dataclass
class Stats:
    symbol: str
    tf: str
    trades: int
    tp1_rate: float
    tp2_rate: float
    net_r: float
    long_trades: int
    long_net_r: float
    long_tp1_rate: float
    short_trades: int
    short_net_r: float
    short_tp1_rate: float
    avg_win_min: float
    avg_loss_min: float
    max_dd_r: float
    days_covered: float
    trend_dir: int
    open_dir: int               # şu an açık işlem yönü (0 yok)
    new_signal: int             # son kapanan barda yeni açılan işlem yönü (0 yok)
    last_entry: float
    last_sl: float
    last_tp1: float
    last_tp2: float
    last_bar_close_ms: int


def backtest(df: pd.DataFrame, p: Params, symbol: str, tf: str, now_ms: int) -> Stats:
    o = df["open"].to_numpy(float)
    h = df["high"].to_numpy(float)
    l = df["low"].to_numpy(float)
    c = df["close"].to_numpy(float)
    v = df["volume"].to_numpy(float)
    t_open = df["open_time"].to_numpy(np.int64)
    t_close = df["close_time"].to_numpy(np.int64)
    n = len(df)

    ema21 = pine_ema(c, 21)
    ema99 = pine_ema(c, 99)
    ema200 = pine_ema(c, 200)
    atr = pine_atr(h, l, c, 14)
    _, _, adx = pine_dmi(h, l, c, 14, 14)
    rsi = pine_rsi(c, 14)
    rmin = pine_lowest(rsi, 14)
    rmax = pine_highest(rsi, 14)
    with np.errstate(divide="ignore", invalid="ignore"):
        stoch = np.where((rmax - rmin) == 0, 50.0, (rsi - rmin) / (rmax - rmin) * 100.0)
    stoch_k = pine_sma(stoch, 3)

    l9 = pine_lowest(l, 9)
    h9 = pine_highest(h, 9)
    with np.errstate(divide="ignore", invalid="ignore"):
        rsv = np.where((h9 - l9) == 0, 50.0, (c - l9) / (h9 - l9) * 100.0)
    # KDJ: Pine'daki var k/d = 50, nz(k[1], 50)
    k = np.full(n, np.nan)
    d = np.full(n, np.nan)
    kp, dp = 50.0, 50.0
    for i in range(n):
        if not np.isfinite(rsv[i]):
            kp, dp = 50.0, 50.0
            continue
        kp = (2.0 / 3.0) * kp + (1.0 / 3.0) * rsv[i]
        dp = (2.0 / 3.0) * dp + (1.0 / 3.0) * kp
        k[i], d[i] = kp, dp
    j = 3.0 * k - 2.0 * d
    j1 = np.roll(j, 1); j1[0] = np.nan
    k1 = np.roll(k, 1); k1[0] = np.nan
    jxu = (j > k) & (j1 <= k1)
    jxd = (j < k) & (j1 >= k1)

    vma = pine_sma(v, 20)
    vspk = v / np.where(np.isfinite(vma) & (vma != 0), vma, 1.0)
    vawk = pine_highest(vspk, 10) >= 1.6
    mintick = max(np.nanmin(np.abs(np.diff(c)[np.diff(c) != 0])) if np.any(np.diff(c) != 0) else 1e-8, 1e-12)
    rng = np.maximum(h - l, mintick)
    lwick = (c - l) / rng
    uwick = (h - c) / rng
    sbl = uwick / np.maximum(lwick, 0.15)
    sbs = lwick / np.maximum(uwick, 0.15)
    sbl1 = np.roll(sbl, 1); sbl1[0] = np.nan
    sbs1 = np.roll(sbs, 1); sbs1[0] = np.nan

    mtf_l = (c > ema200) if p.use_mtf_trend else np.ones(n, bool)
    mtf_s = (c < ema200) if p.use_mtf_trend else np.ones(n, bool)
    bull = (c > ema99) & (ema21 > ema99) & mtf_l & (adx >= 16.0)
    bear = (c < ema99) & (ema21 < ema99) & mtf_s & (adx >= 16.0)

    abs_l = bull & vawk & (l <= ema21 * 1.005) & ((c > o) | (lwick >= 0.55)) & ((sbl >= 1.5) | (sbl1 >= 1.6)) & (j <= 30.0) & (stoch_k <= 42.0)
    abs_s = bear & vawk & (h >= ema21 * 0.995) & ((c < o) | (uwick >= 0.55)) & ((sbs >= 1.5) | (sbs1 >= 1.6)) & (j >= 70.0) & (stoch_k >= 58.0)
    mom_l = bull & (vspk >= 1.6) & (c > o) & (lwick > 0.60) & (j >= 48.0) & (j <= 80.0) & jxu & (rsi >= 52) & (rsi <= 68) & (l <= ema21 * 1.012)
    mom_s = bear & (vspk >= 1.6) & (c < o) & (uwick > 0.60) & (j <= 52.0) & (j >= 20.0) & jxd & (rsi <= 48) & (rsi >= 32) & (h >= ema21 * 0.988)
    sig_l = np.nan_to_num(abs_l | mom_l).astype(bool)
    sig_s = np.nan_to_num(abs_s | mom_s).astype(bool)

    ms_limit = p.lookback_days * 86400000
    in_date = (now_ms - t_open) <= ms_limit
    full_win = 0.5 * p.tp1_rr + 0.5 * p.tp2_rr
    half_win = 0.5 * p.tp1_rr

    dir_ = 0; ep = sl = tp1 = tp2 = 0.0; t1_ok = False; entry_t = 0
    w1 = w2 = ls = 0; net_r = 0.0
    lw1 = lls = sw1 = sls = 0; lnet = snet = 0.0
    win_dur = []; loss_dur = []
    peak = 0.0; max_dd = 0.0
    new_signal = 0
    first_in = None

    def close_trade(r, is_win, i, side):
        nonlocal net_r, lnet, snet, peak, max_dd
        net_r += r
        if side == 1:
            lnet += r
        else:
            snet += r
        dur = (t_close[i] - entry_t) / 60000.0
        (win_dur if is_win else loss_dur).append(dur)
        peak = max(peak, net_r)
        max_dd = max(max_dd, peak - net_r)

    for i in range(n):
        if in_date[i] and first_in is None:
            first_in = t_open[i]
        if dir_ == 1:
            if not t1_ok:
                if l[i] <= sl:
                    ls += 1; lls += 1; close_trade(-1.0, False, i, 1); dir_ = 0
                elif h[i] >= tp1:
                    t1_ok = True; w1 += 1; lw1 += 1
                    if h[i] >= tp2:
                        w2 += 1; close_trade(full_win, True, i, 1); dir_ = 0
            else:
                if h[i] >= tp2:
                    w2 += 1; close_trade(full_win, True, i, 1); dir_ = 0
                elif l[i] <= ep:
                    close_trade(half_win, True, i, 1); dir_ = 0
        elif dir_ == -1:
            if not t1_ok:
                if h[i] >= sl:
                    ls += 1; sls += 1; close_trade(-1.0, False, i, -1); dir_ = 0
                elif l[i] <= tp1:
                    t1_ok = True; w1 += 1; sw1 += 1
                    if l[i] <= tp2:
                        w2 += 1; close_trade(full_win, True, i, -1); dir_ = 0
            else:
                if l[i] <= tp2:
                    w2 += 1; close_trade(full_win, True, i, -1); dir_ = 0
                elif h[i] >= ep:
                    close_trade(half_win, True, i, -1); dir_ = 0

        nl = dir_ == 0 and in_date[i] and sig_l[i]
        ns = dir_ == 0 and in_date[i] and sig_s[i] and not nl
        if nl or ns:
            dir_ = 1 if nl else -1
            ep = c[i]
            r = atr[i] * p.sl_atr_mult
            if not np.isfinite(r) or r <= 0:
                dir_ = 0
                continue
            sl = ep - r if nl else ep + r
            tp1 = ep + r * p.tp1_rr if nl else ep - r * p.tp1_rr
            tp2 = ep + r * p.tp2_rr if nl else ep - r * p.tp2_rr
            t1_ok = False
            entry_t = t_close[i]
            new_signal = dir_ if i == n - 1 else 0

    tot = w1 + ls
    ltot = lw1 + lls
    stot = sw1 + sls
    tdir = 0
    if np.isfinite(ema200[-1]):
        if c[-1] > ema99[-1] and ema21[-1] > ema99[-1] and c[-1] > ema200[-1]:
            tdir = 1
        elif c[-1] < ema99[-1] and ema21[-1] < ema99[-1] and c[-1] < ema200[-1]:
            tdir = -1
    days = (now_ms - first_in) / 86400000.0 if first_in is not None else 0.0
    return Stats(
        symbol=symbol, tf=tf, trades=tot,
        tp1_rate=(100.0 * w1 / tot) if tot else 0.0,
        tp2_rate=(100.0 * w2 / tot) if tot else 0.0,
        net_r=net_r,
        long_trades=ltot, long_net_r=lnet, long_tp1_rate=(100.0 * lw1 / ltot) if ltot else 0.0,
        short_trades=stot, short_net_r=snet, short_tp1_rate=(100.0 * sw1 / stot) if stot else 0.0,
        avg_win_min=float(np.mean(win_dur)) if win_dur else 0.0,
        avg_loss_min=float(np.mean(loss_dur)) if loss_dur else 0.0,
        max_dd_r=max_dd, days_covered=days, trend_dir=tdir,
        open_dir=dir_, new_signal=new_signal,
        last_entry=ep, last_sl=sl, last_tp1=tp1, last_tp2=tp2,
        last_bar_close_ms=int(t_close[-1]),
    )


# ---------------------------------------------------------------------------
#  Binance veri
# ---------------------------------------------------------------------------
class Binance:
    def __init__(self, req_per_sec=5.0):
        self.s = requests.Session()
        self.s.headers["User-Agent"] = "km-scanner/1.0"
        self.min_gap = 1.0 / req_per_sec
        self._last = 0.0

    def _get(self, path, params=None, retries=5):
        for attempt in range(retries):
            gap = time.time() - self._last
            if gap < self.min_gap:
                time.sleep(self.min_gap - gap)
            self._last = time.time()
            try:
                r = self.s.get(FAPI + path, params=params, timeout=20)
            except requests.RequestException as e:
                log.warning("istek hatası %s: %s", path, e)
                time.sleep(2 ** attempt)
                continue
            if r.status_code in (418, 429):
                wait = int(r.headers.get("Retry-After", "30"))
                log.warning("rate limit, %s sn bekleniyor", wait)
                time.sleep(wait)
                continue
            if r.status_code != 200:
                log.warning("HTTP %s %s %s", r.status_code, path, r.text[:120])
                time.sleep(2 ** attempt)
                continue
            return r.json()
        raise RuntimeError(f"Binance isteği başarısız: {path}")

    def perpetual_symbols(self, top=150, quote="USDT"):
        info = self._get("/fapi/v1/exchangeInfo")
        ok = {s["symbol"] for s in info["symbols"]
              if s.get("contractType") == "PERPETUAL" and s.get("status") == "TRADING" and s.get("quoteAsset") == quote}
        tick = self._get("/fapi/v1/ticker/24hr")
        ranked = sorted((t for t in tick if t["symbol"] in ok), key=lambda t: float(t.get("quoteVolume", 0)), reverse=True)
        return [t["symbol"] for t in ranked[:top]]

    def klines(self, symbol, tf, bars_needed):
        tf_ms = TF_MINUTES[tf] * 60000
        end = int(time.time() * 1000)
        start = end - bars_needed * tf_ms
        rows = []
        while start < end:
            batch = self._get("/fapi/v1/klines", {"symbol": symbol, "interval": tf, "startTime": start, "limit": 1000})
            if not batch:
                break
            rows.extend(batch)
            last_open = batch[-1][0]
            if len(batch) < 1000 or last_open + tf_ms >= end:
                break
            start = last_open + tf_ms
        if not rows:
            return None
        df = pd.DataFrame(rows, columns=["open_time", "open", "high", "low", "close", "volume", "close_time",
                                         "qv", "n", "tbv", "tbqv", "ign"])
        df = df[["open_time", "open", "high", "low", "close", "volume", "close_time"]].astype(
            {"open_time": np.int64, "close_time": np.int64, "open": float, "high": float, "low": float,
             "close": float, "volume": float})
        df = df.drop_duplicates("open_time").sort_values("open_time").reset_index(drop=True)
        now = int(time.time() * 1000)
        df = df[df["close_time"] <= now]          # oluşmakta olan mumu at
        return df.reset_index(drop=True)


# ---------------------------------------------------------------------------
#  Telegram
# ---------------------------------------------------------------------------
def tg_send(token, chat_id, text, dry_run=False):
    if dry_run or not token or not chat_id:
        print("\n[TELEGRAM MESAJI]\n" + text + "\n")
        return True
    for chunk in [text[i:i + 3900] for i in range(0, len(text), 3900)]:
        r = requests.post(f"https://api.telegram.org/bot{token}/sendMessage",
                          json={"chat_id": chat_id, "text": chunk, "disable_web_page_preview": True}, timeout=20)
        if r.status_code != 200:
            log.error("Telegram hata %s: %s", r.status_code, r.text[:200])
            return False
    return True


def fmt_r(x):
    return f"{x:+.1f} R"


def fmt_min(m):
    m = int(round(m))
    return f"{m} dk" + (f" ({m // 60}s {m % 60}dk)" if m >= 60 else "")


# ---------------------------------------------------------------------------
#  Tarama
# ---------------------------------------------------------------------------
def scan(args, api, p, state):
    now_ms = int(time.time() * 1000)
    symbols = args.symbols.split(",") if args.symbols else api.perpetual_symbols(top=args.top)
    log.info("%d sembol, periyotlar: %s", len(symbols), ",".join(args.tfs))
    results, errors = [], 0
    for idx, sym in enumerate(symbols, 1):
        for tf in args.tfs:
            bars = int(p.lookback_days * 1440 / TF_MINUTES[tf]) + WARMUP_BARS
            try:
                df = api.klines(sym, tf, bars)
                if df is None or len(df) < 260:
                    continue
                st = backtest(df, p, sym, tf, now_ms)
                results.append(st)
            except Exception as e:   # tek coin hatası taramayı durdurmasın
                errors += 1
                log.warning("%s %s: %s", sym, tf, e)
        if idx % 25 == 0:
            log.info("%d/%d tarandı", idx, len(symbols))

    qual = [s for s in results if s.net_r >= args.min_r and s.tp1_rate >= args.min_tp1 and s.trades >= args.min_trades]
    qual.sort(key=lambda s: s.net_r, reverse=True)
    log.info("%d sonuç, %d eşik üstü, %d hata", len(results), len(qual), errors)

    # --- özet mesajı ---
    key_now = sorted(f"{s.symbol}|{s.tf}" for s in qual)
    changed = key_now != state.get("last_qual", [])
    if qual and (changed or args.always_summary):
        ts = datetime.now(timezone.utc).astimezone().strftime("%d.%m %H:%M")
        lines = [f"📋 KM Tarama {ts} · {len(symbols)} coin · son {p.lookback_days} gün",
                 f"Eşik: net R ≥ {args.min_r:+.0f}, TP1 ≥ %{args.min_tp1:.0f}, min {args.min_trades} işlem", ""]
        for s in qual[:args.max_list]:
            tr = "↑" if s.trend_dir == 1 else "↓" if s.trend_dir == -1 else "→"
            pos = f" · AÇIK {'LONG' if s.open_dir == 1 else 'SHORT'}" if s.open_dir else ""
            warn = " ⚠veri" if s.days_covered < p.lookback_days * 0.9 else ""
            lines.append(f"{s.symbol} {s.tf} {tr} · {fmt_r(s.net_r)} · TP1 %{s.tp1_rate:.0f} · {s.trades} işl. · "
                         f"L {fmt_r(s.long_net_r)} / S {fmt_r(s.short_net_r)} · DD -{s.max_dd_r:.1f}R{pos}{warn}")
        if len(qual) > args.max_list:
            lines.append(f"... +{len(qual) - args.max_list} coin daha")
        tg_send(args.token, args.chat_id, "\n".join(lines), args.dry_run)
    elif not qual and changed:
        tg_send(args.token, args.chat_id, "📋 KM Tarama: eşiği geçen coin yok.", args.dry_run)
    state["last_qual"] = key_now

    # --- eşik üstü coinlerde yeni sinyal ---
    sent = state.setdefault("sent_signals", {})
    for s in qual:
        if not s.new_signal:
            continue
        k = f"{s.symbol}|{s.tf}|{s.last_bar_close_ms}"
        if k in sent:
            continue
        side = "LONG" if s.new_signal == 1 else "SHORT"
        emoji = "🚀" if s.new_signal == 1 else "🩸"
        prec = max(2, -int(math.floor(math.log10(abs(s.last_entry)))) + 4) if s.last_entry else 4
        msg = (f"{emoji} KM {side} · {s.symbol} · {s.tf}\n"
               f"Giriş: {s.last_entry:.{prec}f}\nSL: {s.last_sl:.{prec}f}\n"
               f"TP1: {s.last_tp1:.{prec}f}\nTP2: {s.last_tp2:.{prec}f}\n"
               f"Geçmiş: {fmt_r(s.net_r)} · TP1 %{s.tp1_rate:.0f} · {s.trades} işl.\n"
               f"Ort. süre kazanç/kayıp: {fmt_min(s.avg_win_min)} / {fmt_min(s.avg_loss_min)}")
        if tg_send(args.token, args.chat_id, msg, args.dry_run):
            sent[k] = now_ms
    # eski kayıtları temizle (7 gün)
    for k in [k for k, t in sent.items() if now_ms - t > 7 * 86400000]:
        del sent[k]
    return qual


def selftest():
    """Sentetik rastgele yürüyüş verisiyle mantığın hatasız döndüğünü kontrol eder."""
    rng = np.random.default_rng(7)
    n = 4000
    tf_ms = 15 * 60000
    now = int(time.time() * 1000)
    close = 100 * np.exp(np.cumsum(rng.normal(0, 0.004, n)))
    high = close * (1 + np.abs(rng.normal(0, 0.003, n)))
    low = close * (1 - np.abs(rng.normal(0, 0.003, n)))
    open_ = np.roll(close, 1); open_[0] = close[0]
    vol = rng.lognormal(10, 0.6, n)
    ot = now - (n - np.arange(n)) * tf_ms
    df = pd.DataFrame({"open_time": ot, "open": open_, "high": np.maximum(high, open_), "low": np.minimum(low, open_),
                       "close": close, "volume": vol, "close_time": ot + tf_ms - 1})
    st = backtest(df, Params(), "TEST", "15m", now)
    print(json.dumps(asdict(st), indent=2, ensure_ascii=False))
    assert st.trades >= 0 and np.isfinite(st.net_r)
    print("selftest OK")


def main():
    ap = argparse.ArgumentParser(description="KM PRO V5 coin tarayıcı + Telegram")
    ap.add_argument("--tfs", default="15m,1h", help="virgülle periyotlar: 5m,15m,1h,4h")
    ap.add_argument("--days", type=int, default=30, help="backtest penceresi (gün)")
    ap.add_argument("--min-r", type=float, default=10.0, help="min net R")
    ap.add_argument("--min-tp1", type=float, default=50.0, help="min TP1 oranı %%")
    ap.add_argument("--min-trades", type=int, default=5, help="min işlem sayısı")
    ap.add_argument("--top", type=int, default=150, help="hacme göre ilk N coin")
    ap.add_argument("--symbols", default="", help="sadece bu semboller: BTCUSDT,ETHUSDT")
    ap.add_argument("--interval", type=int, default=15, help="taramalar arası dakika")
    ap.add_argument("--max-list", type=int, default=25, help="özet mesajında en fazla kaç coin")
    ap.add_argument("--always-summary", action="store_true", help="liste değişmese de her taramada özet gönder")
    ap.add_argument("--once", action="store_true", help="tek tarama yap ve çık")
    ap.add_argument("--dry-run", action="store_true", help="Telegram'a göndermeden ekrana yaz")
    ap.add_argument("--selftest", action="store_true", help="sentetik veriyle mantık testi")
    ap.add_argument("--state", default="km_state.json")
    ap.add_argument("--rps", type=float, default=5.0, help="saniyede en fazla istek")
    ap.add_argument("--no-ema200", action="store_true", help="EMA200 trend filtresini kapat")
    args = ap.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

    if args.selftest:
        selftest()
        return

    args.tfs = [t.strip() for t in args.tfs.split(",") if t.strip()]
    for t in args.tfs:
        if t not in TF_MINUTES:
            sys.exit(f"bilinmeyen periyot: {t}")
    args.token = os.environ.get("TG_BOT_TOKEN", "")
    args.chat_id = os.environ.get("TG_CHAT_ID", "")
    if not args.dry_run and (not args.token or not args.chat_id):
        sys.exit("TG_BOT_TOKEN ve TG_CHAT_ID ortam değişkenlerini ayarla ya da --dry-run kullan")

    p = Params(lookback_days=args.days, use_mtf_trend=not args.no_ema200)
    api = Binance(req_per_sec=args.rps)
    state = {}
    if os.path.exists(args.state):
        try:
            with open(args.state, encoding="utf-8") as f:
                state = json.load(f)
        except Exception:
            state = {}

    while True:
        t0 = time.time()
        try:
            scan(args, api, p, state)
        except Exception as e:
            log.exception("tarama hatası: %s", e)
        with open(args.state, "w", encoding="utf-8") as f:
            json.dump(state, f)
        if args.once:
            break
        wait = max(30, args.interval * 60 - (time.time() - t0))
        log.info("sonraki tarama %.0f sn sonra", wait)
        time.sleep(wait)


if __name__ == "__main__":
    main()
