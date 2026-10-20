# 1.2 — Veri paketi ve ana veriler

Bu adımın amacı, sonraki işlemlerin hangi girdilere dayanacağını doğrulamaktır.
E-posta gövdelerinden alan çıkarımı 1.3 ve LLM adımlarında ele alınacaktır.

## Kullanım

Proje kökünde PowerShell:

```powershell
.\.venv\Scripts\python.exe -m ugr_booking.cli inspect-dataset
.\.venv\Scripts\python.exe -m pytest tests/test_dataset.py -q
```

Farklı klasör veya orijinal ZIP için `--dataset "tam yol"` verilebilir.
Varsayılan kaynak: `data/private/dataset`.
Kaynak yalnızca okunur; ZIP açılıp diske çıkarılmaz.

## Dosyaların görevleri

| Dosya | Görev |
|---|---|
| config.py | Komut, ortam değişkeni ve varsayılan klasör arasından veri yolunu seçer. |
| dataset.py | Klasör/ZIP erişimi, yol sınırları, manifest, benzersiz kimlik ve tarih sıralaması. |
| master_data.py | Müşteri/liman kayıtları ve önerilen şemanın desteklenen sözleşmeyle kontrolü. |
| schemas.py | Manifest, müşteri, liman, mesaj niyeti, işlem durumu ve booking adayı tipleri. |
| cli.py | Kontrolleri tek komutla çalıştırıp gerçek metadata özetini üretir. |

## Neden bu kontroller var?

- Manifestte aynı kimlik veya aynı kaynak dosyası iki kez bulunursa sessiz çoğaltma önlenir.
- Dosya yolları gerçek kaynakla eşleşir; kök dışındaki dosyalara gidilemez.
- Tarihler dosya numarasına göre değil, alınma zamanına göre sıralanır.
- Müşteri/liman alias listeleri korunur; eşleştirme kuralları henüz uygulanmaz.
- 34 e-posta, 4 müşteri ve 12 liman bu veri setinin kabul ölçütüdür.
  Bunlar uygulamadaki booking sayıları değildir.
- Pydantic, çıkarılan verinin yapısını kontrol etmek için hazırlanır.
  `quantity="3"` veya `quantity=True` sessizce adet kabul edilmez.
- Tarih yalnızca gerçek bir YYYY-MM-DD değeri veya null olabilir.
  Göreli tarihler burada yorumlanmaz.
- Yapısal olarak geçerli booking adayı, iş kurallarından geçmiş booking değildir.
- Önerilen harici şema çalıştırılabilir kod olarak yorumlanmaz; desteklenen
  alan/tip/enum listesiyle karşılaştırılır.

## Gözlemlenen sonuçlar

Hazırlanan kod üzerinde 21 otomatik kontrol geçti. Testler yalnızca bağımsız,
uydurulmuş örnekler kullanır; gerçek e-posta gövdeleri testlere kopyalanmaz.

Kullanıcının D:\UGR içindeki gerçek klasörüyle:
- 34 manifest kaydı, 34 benzersiz e-posta kimliği, 34 eşleşen dosya.
- 4 müşteri, 12 liman.
- 20 booking alanı, 6 mesaj niyeti ve 6 işlem durumu.
- İlk kronolojik e-posta: EMAIL-006.
- Son kronolojik e-posta: EMAIL-034.

Hazırlık kontrolleri çalışma klasöründe yapıldı. Uygulama komutu dosyaları proje
köküne yerleştirdikten sonra aynı testleri ve gerçek veri kontrolünü tekrar çalıştırır.
Yalnızca bunlar geçerse takip listesindeki sekiz adet 1.2 maddesini işaretler.

## Sınır

Bu komut paketin metadata doğrulamasıdır. Booking oluşturmaz; veritabanına
yazmaz; AI çağırmaz. E-posta gövdesi hatalarından sonra devam etme, HTML temizliği
ve alıntı mesaj bölümlerinin ayrılması 1.3'ün konusudur. 1.3 başlamadan durulur.
