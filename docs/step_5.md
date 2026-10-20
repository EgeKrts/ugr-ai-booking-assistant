# Aşama 5 — Operasyon dashboard'u

## Çalıştırma

```powershell
.\.venv\Scripts\python.exe -m ugr_booking.cli ingest        # veritabanı yoksa önce bu
.\.venv\Scripts\python.exe -m streamlit run ui/app.py       # http://localhost:8501
.\.venv\Scripts\python.exe -m pytest tests/test_query_tools.py tests/test_dashboard.py -q
```

Veritabanı yolu kenar çubuğundaki **Database** kutusundan değiştirilebilir (varsayılan `DATABASE_URL`
ya da `sqlite:///data/booking.sqlite3`). Dosya yoksa dashboard boş bir dosya **oluşturmaz**, uyarı verir.

> Streamlit çalışırken `ui/app.py` değişikliklerini yeniden yükler, ama `src/ugr_booking/` altındaki
> modülleri yüklemez. Bu modülleri düzenlediysen sunucuyu yeniden başlat.

## Sayfa düzeni

- **Overview:** Toplam, Ready, Needs validation, Cancelled (kenar çubuğu filtrelerine uyar) ve ayrı bir
  e-posta satırı: işlenen e-posta, booking olmayan e-posta, durum sorgusu, uygulanamayan talep.
  Grafikler: durum (yeşil = hazır, amber = inceleme, gri = iptal), müşteri, yükleme/boşaltma limanı, en yoğun rotalar.
- **Bookings:** Tüm booking'ler tabloda. Amber satır = doğrulama gerekli, gri italik = iptal. Satır seçince
  alt tarafta detay: alanlar, VIN listesi, doğrulama sorunları, işlem geçmişi (olaylar) ve kaynak e-posta metni.
- **Needs attention:** İncelenecek booking'ler **nedenleriyle** (sorun kodları), uygulanamayan UPDATE/CANCEL
  talepleri ve niyete göre e-posta sayıları.
- **Filtreler:** müşteri, POL, POD, durum (çoklu seçim), arama (referans, `BKG-00004`, e-posta no, kargo),
  istenen tarih aralığı, sıralama, sıfırla. Filtreler birbirini daraltır (AND) ve **hem listeyi hem
  tüm grafik ve sayıları** etkiler.

## Tek sorgu katmanı

Dashboard ve (Aşama 6) chatbot aynı `query_tools.py` fonksiyonlarını kullanır: `search_bookings`,
`count_bookings`, `count_by`, `latest_bookings`, `bookings_requiring_review`, `get_booking_detail`.
Böylece aynı filtre, aynı booking kümesini verir. Hiçbir sorgu kullanıcı metninden SQL kurmaz;
metin parametre olarak bağlanır, `%` ve `_` düz karakter sayılır.

## Tanımlar (dashboard ve chatbot için aynı)

- **Toplam booking**, iptal edilmişleri de içerir; iptal bir durumdur, kayıt silinmez.
- **En son (latest)** = kaynak e-postası en geç gelen booking (eşitlikte büyük id). İşlem zamanı kullanılmaz,
  çünkü bir toplu çalıştırma her şeyi birkaç saniyede yazar.
- **İnceleme gerekli** = durumu `VALIDATION_REQUIRED`.
- **Booking olmayan e-posta** (fiyat, yükleme listesi, boş/okunamayan) booking sayısına karışmaz.
- **Sorun nedeni:** e-posta düzeyindeki sorunlar (örneğin `PROMPT_INJECTION_SUSPECTED`) o e-postadan çıkan
  tüm booking'lerin nedeni sayılır ve detayda `scope = e-mail` olarak görünür.

## Doğrulama (gerçek veri, 34 e-posta)

- Dashboard toplamı = `SELECT COUNT(*) FROM bookings` = 22; durum dağılımı SQL `GROUP BY` ile aynı.
- D100 `CANCELLED`; EMAIL-019 `VIN_COUNT_MISMATCH` ile bulunuyor; EMAIL-017 iki ayrı booking gösteriyor.
- `ABC Shipping` + `READY` filtresi: liste, durum dağılımı ve POD grafiği aynı 2 kaydı yansıtıyor.
- Tarayıcıda elle denendi (Overview, Bookings, satır detayı, Needs attention). Testler Streamlit'i başsız
  çalıştırır (`AppTest`): KPI'lar SQL ile aynı, filtre ve sıfırlama, sıfır sonuç, boş veritabanı, eksik dosya.

Elle denerken bir hata yakalandı ve düzeltildi: e-posta düzeyindeki sorunlar booking detayında ve "neden"
sütununda görünmüyordu, yani bir kaydın neden incelemede olduğu kaybolmuştu. Artık görünüyor ve testi var.

## Sınırlar

- Tek kullanıcı, yerel veritabanı. Yazma/onay arayüzü yok; dashboard salt okunurdur.
- Limanlar kod + isim olarak gösterilir. İsimler yerel veri paketinden okunur; paket yoksa yalnızca kod görünür.
- Tablo, Streamlit'in yerleşik bileşenidir; çok geniş ekranlarda yatay kaydırma gerekebilir.
- Tarayıcıda 1400 px genişlikte denendi; dar/mobil görünüm optimize edilmedi.
