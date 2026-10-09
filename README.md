# Coin LONG/SHORT Botu

Her dakika Binance USDT-M Futures coinlerini tarar, her coin için son 60 dakikalık
1dk ve 15dk mum verisinden göstergeler (RSI, EMA9/21, hacim, funding) hesaplar ve
Claude'a **LONG / SHORT / BEKLE** diye sorar. Sonuçlar ekrana ve `sinyaller.csv`'ye yazılır.

## Kurulum

Linux / macOS:
```bash
pip install -r requirements.txt
export ANTHROPIC_API_KEY=sk-ant-...
python coin_bot.py
```

Windows PowerShell (proje klasörünün içindeyken):
```powershell
pip install -r requirements.txt
$env:ANTHROPIC_API_KEY = "sk-ant-..."
python coin_bot.py
```

## Seçenekler

| Parametre | Varsayılan | Açıklama |
|---|---|---|
| `--top N` | 20 | 24s hacme göre ilk N coin |
| `--symbols A,B` | – | Belirli coinler, örn. `BTCUSDT,ETHUSDT` |
| `--interval S` | 60 | Tur aralığı (saniye) |
| `--concurrency K` | 5 | Aynı anda kaç coin sorulsun |
| `--once` | – | Tek tur çalıştır ve çık |

Model `CLAUDE_MODEL` ortam değişkeniyle değiştirilebilir (varsayılan `claude-opus-5-5`).

## Gerçekçi notlar

- **Maliyet:** 20 coin × her dakika = günde ~28.800 çağrı. Opus 5.5 ile bu kabaca
  günde birkaç yüz dolar eder. `CLAUDE_MODEL=claude-haiku-5-5` çok daha ucuzdur.
  Önce `--once` ile deneyip Console'dan maliyete bak.
- **Doğruluk:** 1 dakikalık veride yön tahmini büyük oranda gürültüdür. Bu bot emir
  göndermez; `sinyaller.csv` ile sinyalleri sonradan fiyatla karşılaştırıp test et.
- Binance bazı ülkelerde (ör. ABD) engellidir; bu durumda VPN/başka borsa gerekir.
