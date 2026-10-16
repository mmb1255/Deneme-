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
| Piyasa durumu | Anlık trend (EMA 21/99/200 dizilimi + ADX gücü), 1S ve 4S üst zaman trendi, RSI / StochK / KDJ-J değerleri, aktif işlem ve anlık R'si, son sinyalin kaç bar önce geldiği |
| Performans | TP1 oranı, TP2 tam hedef oranı, net R, işlem başına ortalama R, kâr faktörü, maksimum düşüş (R), maksimum ardışık kayıp, **LONG ve SHORT için ayrı** TP1 oranı ve net R |
| Zaman dilimi | 5 dk / 15 dk / 1 saat / 4 saat için net R, TP1 oranı ve işlem sayısı; **en iyi periyot net R'ye göre** seçilir (min. işlem sayısı ayarlanabilir) |

Ayrıca LONG ve SHORT sinyalleri için `alertcondition` tanımlı; TradingView alarm
menüsünden seçilebilir.

## R hesabı

- Stop: 1R kayıp.
- TP1 vurulunca pozisyonun yarısı kapanır, stop girişe (BE) çekilir.
- Kalan yarı TP2'den çıkarsa toplam `0.5 × TP1 + 0.5 × TP2` R, girişten (BE) çıkarsa `0.5 × TP1` R kazanılır.

## Bilinmesi gerekenler (gerçekçi notlar)

- Bu bir **indikatör**, strateji değil: komisyon, kayma (slippage) ve pozisyon boyutu hesaba katılmaz. Rakamlar yön göstericidir.
- Zaman dilimi satırları `request.security` ile hesaplanır. Grafikten **daha düşük** periyotlar (örn. 1 saatlik grafikte 5 dk satırı) örneklenmiş veriyle hesaplanır; en güvenilir karşılaştırma için grafiği 5 dakikaya alıp panele bak.
- Zaman dilimi versiyonunda likidite süpürme (BSL/SSL) koşulu yoktur; grafik periyodundaki sinyal mantığının biraz sadeleştirilmiş hâlidir.
- Backtest penceresi "şu andan geriye N gün" olduğundan zaman geçtikçe pencere kayar ve sayılar değişir.
- Aynı barda hem stop hem TP görülürse önce stop sayılır (muhafazakâr yaklaşım).
