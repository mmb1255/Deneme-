# Futures Hacim/Fiyat Tarayıcı

Binance USDT-M perpetual futures'ta **son 30 dakikada hacim artışı yapmamış** ve
**fiyatı %1'den fazla yükselmemiş** coinleri bulur. API anahtarı gerekmez.

## Kurulum

```bash
pip install -r requirements.txt
python futures_scanner.py
```

## Nasıl hesaplıyor?

- 1 dakikalık **kapanmış** mumlar kullanılır (canlı mum atılır, yarım mum hacmi yanıltmasın diye).
- **Fiyat değişimi:** son 30 mumun ilk açılışı → son kapanışı.
- **Hacim oranı:** son 30 dk USDT hacmi ÷ önceki 3 saatin 30 dk'lık ortalaması.
  `1.0x` ve altı = hacim artışı yok.
- 24s hacmi 5M USDT altındaki coinler varsayılan olarak atlanır (ölü/likiditesiz coinler).

## Seçenekler

| Parametre | Varsayılan | Açıklama |
|---|---|---|
| `--window` | 30 | İncelenen pencere (dk) |
| `--max-price-change` | 1.0 | Maks. fiyat artışı (%) |
| `--abs-price` | kapalı | Düşüşü de sınırla (sadece yatay coinler) |
| `--max-vol-ratio` | 1.0 | Maks. hacim oranı (ör. 1.2 = %20'ye kadar artışa izin ver) |
| `--baseline-windows` | 6 | Karşılaştırma için önceki pencere sayısı |
| `--min-24h-volume` | 5000000 | Min. 24s USDT hacmi (0 = kapalı) |
| `--loop` | 0 | Her N saniyede bir tekrar tara |
| `--csv` | - | Sonuçları CSV'ye ekle |
| `--top` | 50 | Gösterilecek maks. sonuç |

Örnekler:

```bash
# Her 5 dakikada bir tara, sadece yatay seyredenler, CSV'ye kaydet
python futures_scanner.py --loop 300 --abs-price --csv sonuclar.csv

# Hacim %20'ye kadar artmış olabilir, 24s hacim filtresi kapalı
python futures_scanner.py --max-vol-ratio 1.2 --min-24h-volume 0
```

## Telegram bildirimi (opsiyonel)

```bash
export TELEGRAM_BOT_TOKEN=...
export TELEGRAM_CHAT_ID=...
python futures_scanner.py --loop 300
```

## Notlar

- Binance bölgenizde engelliyse (HTTP 451) script bunu söyleyip çıkar.
  Farklı bir uç nokta için `BINANCE_FAPI_URL` ortam değişkenini kullanabilirsiniz.
- Bu bir filtre, al/sat sinyali değil. Sonuçları kendi analizinizle değerlendirin.
