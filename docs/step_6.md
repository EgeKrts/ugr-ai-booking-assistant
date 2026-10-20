# Aşama 6 — Veritabanına dayalı chatbot

Dashboard'un **Chat** sekmesi. Sorular doğal dilde sorulur; cevap yalnızca SQLite'taki kayıtlardan üretilir.

## Çalıştırma

```powershell
.\.venv\Scripts\python.exe -m streamlit run ui/app.py       # Chat sekmesi
$env:UGR_CHAT_PLANNER = "keywords"                          # isteğe bağlı: modelsiz, kural tabanlı yorumlayıcı
.\.venv\Scripts\python.exe -m pytest tests/test_chatbot.py tests/test_dashboard.py -q
```

## Nasıl çalışır

```
soru → QueryPlan (model ya da anahtar kelime yedeği) → resolve() → sabit salt-okunur sorgular → cevap şablonu
```

1. **Yorumlama.** Model yalnızca küçük bir `QueryPlan` üretir (niyet, müşteri/liman metni, durum, referans, adet,
   gruplama). Şema Ollama'ya JSON şeması olarak verilir; şemaya uymayan çıktı reddedilir (1 yeniden deneme).
   Model **SQL yazmaz** ve **cevabı yazmaz**.
2. **Doğrulama (`resolve`).** Plan bir *öneridir*, karar kodundadır:
   - Müşteri ve limanlar ana veriyle eşleştirilir (`Izmir`/`İZMİR`→`TRIZM`, `Genova`→`ITGOA`, `JEDAH`→`SAJED`).
   - Modelin yazdığı bir varlık **soruda geçmiyorsa** yok sayılır ve cevaba not düşülür (uydurma müşteri, liman, referans).
   - **Bilinmeyen müşteri/liman "hepsi" demek değildir:** `Foo Corp` ana veride yoksa sonuç boştur. Model müşteriyi
     kaçırsa bile "customer called Foo Corp" gibi ifadeleri kod yakalar (`customer_mention`).
   - Gruplama (`group_by`) yalnızca soruda "per / by / each / breakdown" gibi bir ipucu varsa kabul edilir;
     model "yardımcı olmak için" gereksiz gruplama ekleyebiliyor.
3. **Sorgular.** Dashboard ile aynı `query_tools.py` fonksiyonları: `count_bookings`, `count_by`, `search_bookings`,
   `latest_bookings`, `bookings_requiring_review`, `get_booking_detail`. Sorgu metinden SQL kurmaz.
4. **Cevap.** Satırlardan şablonla üretilir: sayı, kullanılan filtreler, booking ID'leri, tablo. Kayıt yoksa
   "bulunamadı" der.

## Salt-okunur garanti (üç katman)

1. Plan şeması yalnızca okuma niyetleri içerir; yazma isteği `UNSUPPORTED` olur ("Delete all bookings", "Cancel ...", "DROP TABLE" dahil).
2. Sorgu fonksiyonlarının hiçbiri yazmaz.
3. Cevap verilirken SQLite bağlantısı `PRAGMA query_only = ON` ile açılır; bir hata olsa bile yazma mümkün değildir.
   Cevap bitince bağlantı eski haline döndürülür (testle doğrulandı: aynı engine'de sonra yazma çalışıyor).

## Model yoksa

Model erişilemez ya da zaman aşımına uğrarsa kural tabanlı `KeywordPlanner` devreye girer ve cevapta
"The language model could not be used (...); the keyword interpreter answered instead." notu çıkar.
Konu dışı sorular ("What is the weather in Genoa?") için "booking" kelimesi şarttır; aksi halde yardım metni döner.

## Zorunlu sorular (qwen3:8b, gerçek veri)

| Soru | Sonuç |
|---|---|
| How many bookings does ABC Shipping have? | **6** (VALIDATION_REQUIRED 4, READY 2), ID'ler listelenir |
| Show bookings with Izmir as the port of loading. | 2 booking (BKG-00021, BKG-00022), filtre `TRIZM Izmir` |
| Which bookings require validation? | 13 booking, her biri için neden kodları |
| Summarize the latest three bookings. | BKG-00022, 00021, 00020 + özet satırı |
| What is the status of BOOKINGREF-D100? | BKG-00004 **CANCELLED**; geçmiş: CREATE (008), DUPLICATE (012), CANCEL (013) |
| Show cancelled bookings. | 1 booking (BKG-00004) |
| Show bookings for a non-existing customer called Foo Corp. | "No bookings: customer 'Foo Corp' is not in the customer master data" |

Ayrıca: olmayan bir referans sorulursa ve bir e-postada geçiyorsa ("update ... BOOKING_NOT_FOUND") cevap bunu söyler;
hiç geçmiyorsa "no booking with reference X in the database" der.

## Tanımlar (dashboard ile aynı)

- **En son (latest)** = kaynak e-postası en geç gelen booking, eşitlikte büyük id; işlem zamanı kullanılmaz.
- **İptal edilmiş booking'ler sayıya dahildir**; cevap "The count includes N cancelled booking(s)" diye belirtir.
- **İnceleme gerekli** = durum `VALIDATION_REQUIRED`. Limanı yönü belirtilmemiş sorularda ("Yanbu'daki booking'ler")
  hem yükleme hem boşaltma limanı eşleşir.

## Tutarlılık

Sohbet cevabındaki ID kümesi, aynı filtreyle `search_bookings` sonucuna eşittir ve sayı `count_bookings` ile aynıdır
(testlerde karşılaştırılıyor).

## Dashboard cevabı otomatik yansıtır

Her cevap dashboard'u da günceller (gereksinim: "istenen bilgiyi dashboard'da filtreleme, vurgulama veya güncel özet ile yansıt"):

- **Filtre:** cevabın filtresi kenar çubuğuna otomatik uygulanır (müşteri, POL, POD, durum, referans/ID araması; yönü
  belirtilmemiş liman için yeni **Port (loading or discharge)** filtresi). KPI'lar, grafikler ve tablo birlikte güncellenir.
- **Vurgu:** cevaptaki booking'ler Bookings ve Needs attention tablolarında mavi satır ve **★** ile işaretlenir
  (filtre olmayan sorularda, örneğin "latest three", tüm tablo görünür kalır ve ilgili satırlar vurgulanır).
- **Bant:** sekmelerin üstünde son soru, cevap ve "dashboard bu cevabı izliyor" notu durur (uzun cevaplar kapalı bir
  bölümdedir). Streamlit, yeniden çalışınca sekmeyi ilk sekmeye döndürdüğü için cevap burada da görünür.
- **Sonuç yoksa** filtreler değiştirilmez, bant bunu söyler. **Clear chat focus** filtreleri ve vurguyu temizler.

## Elle denerken ve testle bulunan hatalar

- Model "How many bookings does ABC Shipping have?" sorusuna gereksiz `group_by=customer` ekledi → artık ipucu şartı var.
- Yedek yorumlayıcı "non-existing customer called Foo Corp" için **tüm booking'leri** listeledi → bilinmeyen müşteri artık boş sonuç.
- Yedek yorumlayıcı "What is the weather in Genoa?" sorusunu (liman adı + "what") booking listesi sandı → "booking" kelimesi şart.

## Sınırlar

- Desteklenen soru türleri sabittir (sayma, listeleme, son booking'ler, inceleme, referansla durum). Karmaşık sorular
  ("son 30 günde ... ortalama") `UNSUPPORTED` olur; cevap uydurulmaz.
- Çok turlu bağlam yoktur: her soru bağımsız yorumlanır ("peki ya Izmir'dekiler?" gibi takip soruları anlaşılmaz).
- Tarih filtresi sohbetten desteklenmez; dashboard'dan yapılır.
- Müşteri/liman eşleştirmesi tam eşleşmedir; yazım hatalı adlar ("Genoaa") bilinmeyen sayılır ve boş sonuç döner.
- Model bazen aynı soruya biraz farklı plan üretebilir; sonuç yine de `resolve` ve sabit sorgulardan geçer.
