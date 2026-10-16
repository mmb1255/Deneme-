# KM PRO V5 – TradingView Pine Script İndikatörü

`KM_PRO_V5.pine` dosyası, KM PRO V4 sinyal mantığının üzerine daha detaylı bir
istatistik paneli ve **net R bazında** zaman dilimi karşılaştırması ekler.

## TradingView'a yükleme

1. TradingView'da bir grafik aç, alt menüden **Pine Editor**'ü aç.
2. `KM_PRO_V5.pine` dosyasının tamamını kopyalayıp editöre yapıştır (eski içeriği sil).
3. **Save** (Kaydet) → **Add to chart** (Grafiğe ekle).
4. İndikatör ayarlarından panel konumunu, backtest süresini ve R hedeflerini değiştirebilirsin.

## Panelde neler var

| Bölüm | İçerik |
|---|---|
| Piyasa durumu | Anlık trend (EMA 21/99/200 dizilimi + ADX gücü), 1S ve 4S üst zaman trendi, RSI / StochK / KDJ-J değerleri, aktif işlem ve anlık R'si, son sinyalin kaç bar önce geldiği, **son kapanan işlemin sonucu ve süresi**, kazanan / kaybeden işlemlerin ortalama süresi |
| Performans | TP1 oranı, TP2 tam hedef oranı, net R, işlem başına ortalama R, kâr faktörü, maksimum düşüş (R), maksimum ardışık kayıp, **LONG ve SHORT için ayrı** TP1 oranı ve net R |
| Zaman dilimi | 5 dk / 15 dk / 1 saat / 4 saat için net R, TP1 oranı ve işlem sayısı; **en iyi periyot net R'ye göre** seçilir (min. işlem sayısı ayarlanabilir) |

Ayrıca LONG ve SHORT sinyalleri için `alertcondition` tanımlı; TradingView alarm
menüsünden seçilebilir.

## Grafikteki sonuç etiketleri

İşlem kapandığında bar üzerine bir etiket çizilir:

- `🎯 TP2 · +1.6 R · 95 dk (1s 35dk)` → tam hedef vuruldu
- `✔ TP1 · stop BE · 40 dk` → TP1 vuruldu, stop girişe çekildi, işlem sürüyor
- `↩ BE ÇIKIŞ · +0.5 R · 70 dk` → TP1 sonrası kalan yarı girişten çıktı
- `❌ STOP · -1.0 R · 15 dk` → stop oldu

Süre, giriş barının kapanışından çıkış barının kapanışına kadar geçen zamandır ve dakika cinsindendir.
Bar içinde tam olarak hangi dakikada vurulduğu bilinemez; hassasiyet grafik periyodu kadardır.

## R hesabı

- Stop: 1R kayıp.
- TP1 vurulunca pozisyonun yarısı kapanır, stop girişe (BE) çekilir.
- Kalan yarı TP2'den çıkarsa toplam `0.5 × TP1 + 0.5 × TP2` R, girişten (BE) çıkarsa `0.5 × TP1` R kazanılır.

## Bilinmesi gerekenler (gerçekçi notlar)

- Bu bir **indikatör**, strateji değil: komisyon, kayma (slippage) ve pozisyon boyutu hesaba katılmaz. Rakamlar yön göstericidir.
- **Grafikteki veri miktarı periyoda göre değişir.** TradingView ücretsiz planda yaklaşık 5000 bar yükler; 5 dakikalık grafikte bu ~17 gün eder, yani "son 30 gün" penceresi dolmaz. Aynı sembolde 15 dk grafikten istenen 5 dk verisi ise 30 günün tamamını kapsayabilir. Bu yüzden 5 dk satırı farklı grafiklerde farklı çıkabilir. Panel her satırda gerçekten kaç günlük veri olduğunu (`17g veri`) gösterir ve pencere dolmadıysa `⚠` koyar. Periyotların gün sayısı farklıysa "En İyi Periyot Ölçütü" ayarını **Günlük R** yap, karşılaştırma adil olur.
- PERFORMANS bloğu ile zaman dilimi satırları aynı sayıyı vermez: PERFORMANS bloğu TP1 şans filtresi ve likidite süpürme koşulu dahil tam mantığı kullanır, zaman dilimi satırları filtresiz sadeleştirilmiş mantığı kullanır.
- Zaman dilimi versiyonunda likidite süpürme (BSL/SSL) koşulu yoktur; grafik periyodundaki sinyal mantığının biraz sadeleştirilmiş hâlidir.
- Backtest penceresi "şu andan geriye N gün" olduğundan zaman geçtikçe pencere kayar ve sayılar değişir.
- Aynı barda hem stop hem TP görülürse önce stop sayılır (muhafazakâr yaklaşım).

## Telegram bildirimi

İndikatör sinyal ve kapanışlarda `alert()` çağırır. Telegram'a düşmesi için:

1. **TradingView'da Webhook gerekir.** Webhook sadece ücretli planlarda (Essential ve üstü) vardır. Ücretsiz planda Telegram'a gönderim mümkün değildir; sadece uygulama/e-posta bildirimi çalışır.
2. BotFather'dan bot oluştur, token al. **Token'ı kimseyle paylaşma ve koda yazma.** Yanlışlıkla paylaştıysan BotFather'da `/revoke` ile yenisini al.
3. Telegram'da botunu aç ve **Start**'a bas (bot sana ilk mesajı atamaz, önce sen başlatmalısın).
4. İndikatör ayarlarında "Telegram Chat ID" alanına kendi kullanıcı ID'ni yaz.
5. Grafikte alarm oluştur:
   - Koşul: **KM PRO V5 → Any alert() function call**
   - Webhook URL: `https://api.telegram.org/bot<TOKEN>/sendMessage`
   - Mesaj kutusunu boş bırak ya da olduğu gibi bırak; metin indikatörden gelir.
6. Test etmek için tarayıcıdan şunu aç, Telegram'a mesaj düşmeli:
   `https://api.telegram.org/bot<TOKEN>/sendMessage?chat_id=<CHAT_ID>&text=test`

Chat ID boşsa mesajlar düz metin üretilir; bu durumda uygulama bildirimi ve e-posta için kullanılabilir.

## Tarayıcı bot (bot/km_scanner.py)

TradingView'dan bağımsız çalışan Python botu. Binance USDT-M vadeli coinlerini
hacme göre sıralar, her coin için indikatördeki zaman dilimi backtest mantığını
birebir uygular ve eşiği geçenleri Telegram'a yazar. **Webhook ve ücretli
TradingView planı gerekmez.**

Varsayılan eşik: net R ≥ +10, TP1 oranı ≥ %50, en az 5 işlem, son 30 gün, 15m ve 1h.

Gönderdikleri:
- Eşiği geçen coin listesi (net R, TP1 %, işlem sayısı, LONG/SHORT R, maks. düşüş, açık işlem varsa). Liste değişince gönderilir; `--always-summary` ile her taramada.
- Eşiği geçen bir coinde son kapanan barda yeni LONG/SHORT açıldıysa giriş, SL, TP1, TP2 ve geçmiş istatistikle sinyal mesajı. Aynı sinyal iki kez gönderilmez.

### Kurulum (bilgisayar ya da ucuz bir VPS)

```bash
cd bot
pip install -r requirements.txt
export TG_BOT_TOKEN="BotFather'dan aldığın token"   # Windows: set TG_BOT_TOKEN=...
export TG_CHAT_ID="1056156546"
python km_scanner.py --selftest                    # mantık testi, internet gerekmez
python km_scanner.py --once --dry-run --top 20     # 20 coin tara, Telegram'a göndermeden ekrana yaz
python km_scanner.py --once                        # tek tarama, Telegram'a gönder
python km_scanner.py                               # sürekli çalış, 15 dk'da bir tara
```

Sık kullanılan seçenekler: `--tfs 5m,15m,1h,4h`, `--days 30`, `--min-r 10`, `--min-tp1 50`,
`--min-trades 5`, `--top 150`, `--symbols BTCUSDT,ETHUSDT`, `--interval 15`.

Notlar:
- Botun çalışması için bilgisayar açık kalmalı; sürekli bildirim istiyorsan aylık birkaç dolara bir VPS'e kur.
- Önce Telegram'da botuna **Start** yazmalısın, yoksa bot sana mesaj atamaz.
- 150 coin × 2 periyot bir tarama yaklaşık 2-3 dakika sürer (Binance istek sınırına uyar).
- Bot, indikatörün "zaman dilimi satırı" mantığını kullanır (likidite süpürme ve TP1 şans filtresi yok). PERFORMANS bloğuyla değil, panelin alt kısmındaki periyot satırlarıyla karşılaştır.
- Pine ve Python göstergeleri aynı formülle yazıldı ama ufak yuvarlama farkları olabilir; birebir aynı sayıyı beklemeden önce birkaç coinde karşılaştır.
