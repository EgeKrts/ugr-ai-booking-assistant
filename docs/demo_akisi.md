# Demo akışı (sabit senaryo)

Süre: **tam sürüm ~4 dakika**, **kısa sürüm ~2,5 dakika** (işaretli adımlar). Her şey hazırlanmış veritabanından çalışır;
sahnede model yalnızca soru-cevap ve takip e-postaları için kullanılır. **Sahnede tam `ingest` çalıştırma:** 6–9 dakika sürer.

## Hazırlık

Bir kez (sunumdan önceki gün):

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\demo.ps1 prepare
```

Sunumdan **10 dakika önce** ve her provadan önce:

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\demo.ps1 preflight     # Ollama, model ısınır, kayıt güncel mi, port boş mu
powershell -ExecutionPolicy Bypass -File .\scripts\demo.ps1 reset         # veritabanı hazırlanmış haline döner
powershell -ExecutionPolicy Bypass -File .\scripts\demo.ps1 dashboard     # ayrı bir terminal penceresinde, açık bırak
```

Kontrol listesi:

- [ ] `preflight` tamamı `[ OK ]` (port 8501 doluysa eski dashboard penceresini `Ctrl+C` ile kapat; **eski pencere eski kodu gösterir**)
- [ ] Tarayıcıda `http://localhost:8501` açık, **Overview** sekmesinde, tarayıcı yakınlaştırması %100
- [ ] İkinci terminal açık, şu komut yazılı ama **Enter'a basılmamış**: `powershell -ExecutionPolicy Bypass -File .\scripts\demo.ps1 followup`
- [ ] Bilgisayarda bildirimler kapalı, şarj takılı, Ollama uygulaması açık
- [ ] Yedek ekran görüntüleri alındı (aşağıdaki adımların her birinden bir tane) ve `data/private/` altına kaydedildi (Git'e girmez)

## Akış

| Süre | Ekran | Yap | Söyle | Beklenen |
|---|---|---|---|---|
| **0:00** ★ | **Overview** | Sayfayı göster, KPI'lara işaret et | "34 e-posta geldi. Hepsi booking değil; sistem 23 booking kaydı çıkardı, 8'i doğrulamadan geçti, 14'ü bir insanın bakmasını bekliyor, 1'i iptal. 3 e-posta booking değil ve sayıya karışmıyor. 6 talep uygulanamadı çünkü hedef booking yok, uydurulmadı." | Total **23**, Ready **8**, Needs validation **14**, Cancelled **1**, E-mails processed **34**, Non-booking **3**, Status inquiries **1**, Requests not applied **6** |
| **0:30** ★ | **Bookings** | Kenar çubuğu **Search** kutusuna `EMAIL-019` yaz. Satırın solundaki kutuyu işaretle. Alttaki **Source e-mail** bölümünü aç | "Bu e-postada 4 araç isteniyor ama 5 VIN listelenmiş. Sistem hangisinin doğru olduğunu bilemez, birini diğerine uydurmaz: booking incelemeye gidiyor ve nedeni burada." | `BKG-00012`, **VALIDATION_REQUIRED**, sorun `VIN_COUNT_MISMATCH`, adet 4 |
| **1:10** | **Bookings** | Search'e `BOOKINGREF-D100` yaz, satırı seç, **History** tablosuna bak | "Aynı talep iki kez geldi, sonra iptal edildi. Tek booking var: olaylar CREATE, DUPLICATE, CANCEL. Her olay hangi e-postadan geldiğini taşıyor." | `BKG-00004`, **CANCELLED**, olaylar `CREATE` (EMAIL-008), `DUPLICATE` (EMAIL-012), `CANCEL` (EMAIL-013) |
| **1:40** | **Bookings** | Search'e `EMAIL-031` yaz, satırı seç, **Validation issues**'a bak | "Bu e-postanın içine sistem talimatı gibi görünen bir paragraf gizlenmiş: 'varış limanını değiştir'. Paragraf modele hiç gitmedi, liman Jeddah olarak kaldı ve e-posta incelemeye alındı." | POD **SAJED Jeddah**, sorun `PROMPT_INJECTION_SUSPECTED`, kapsam `e-mail` |
| **2:10** ★ | **Chat** | **Show bookings with Izmir as the port of loading.** düğmesine tıkla (5–10 sn bekle) | "Soruyu model bir sorgu planına çeviriyor, cevabı ise kod veritabanından yazıyor. Dikkat: dashboard kendiliğinden filtrelendi." | Cevap 2 booking; sekmelerin üstünde **Last chat question** kutusu; kenar çubuğunda **Port of loading = TRIZM Izmir**; Overview'da Total **2**; **Bookings** sekmesinde ★'lı mavi satırlar |
| **2:40** ★ | **Chat** | **Which bookings require validation?** düğmesine tıkla | "Aynı veri, aynı filtre. Her booking'in neden incelemede olduğu da yazıyor." | 14 booking, "Why" sütunu, kenar çubuğunda `VALIDATION_REQUIRED` |
| **3:00** | **Chat** | **Show bookings for a non-existing customer called Foo Corp.** düğmesine tıkla | "Olmayan bir müşteri için 'hepsini' göstermiyor, boş cevap veriyor. Chatbot yalnızca okuyabilir." (isteğe bağlı: `Delete all bookings` yaz → reddeder) | "No bookings: customer 'Foo Corp' is not in the customer master data"; filtreler değişmez |
| **3:20** ★ | **Terminal** | Önce üstteki **Clear chat focus** düğmesine bas (filtreler temizlenir). Sonra terminalde Enter'a bas (`demo.ps1 followup`, ~30 sn) | "Şimdi gerçek takip e-postaları: bir tarih değişikliği, bir adet değişikliği, bilinmeyen müşteriye çevirme, durum sorusu, iptal ve olmayan bir referans. Araçları modelin seçtiğini `[model]` etiketlerinden görüyorsunuz." | Satırlar: `DEMO-001 ... update_booking:UPDATED[model]`, `DEMO-002 ... UPDATED_NEEDS_REVIEW`, `DEMO-003 ... UPDATE_NOT_APPLIED`, `DEMO-004 ... FOUND`, `DEMO-005 ... CANCELLED`, `DEMO-006 ... BOOKING_NOT_FOUND` |
| **3:50** ★ | **Bookings** | Tarayıcıyı yenile (`F5`). Search'e `BOOKINGREF-A400`, satırı seç, **History** | "Tarih güncellemesi uygulandı ve eski ile yeni değer olayda duruyor. Bilinmeyen müşteriye çevirme ise reddedildi, kayıt değişmedi. Model 'çözülemedi' demeye çalıştı, sistem booking var olduğu için bunu da reddetti." | `BKG-00022` **READY**, istenen tarih **2026-12-01**, olay `UPDATE: requested_date 2026-11-10 → 2026-12-01`, ayrıca reddedilen değişiklik için `FAILED_ACTION` |
| **4:10** | **Bookings** | Search'e `BOOKINGREF-D200` | "Adet 4'e çıkarıldı ama 3 VIN var; sistem güncellemeyi uyguladı ama kaydı tekrar incelemeye aldı." | `BKG-00018` adet **4**, **VALIDATION_REQUIRED** |
| **4:20** ★ | Konuş | (ekran: Overview) | "Özet: model okuyor, kod doğruluyor ve yalnızca izinli araçlar yazıyor. 317 test geçiyor, 34 e-postanın tamamı gerçek modelle işlendi. Bilinen sınır: karmaşık güncellemelerde değer çıkarımı güvenilir değil, bu yüzden bir sonraki adım insan onay ekranı." | |

★ = kısa sürüm (2,5 dk): 0:00, 0:30, 2:10, 2:40, 3:20 ve 3:50.

## Sunumdan sonra

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\demo.ps1 reset
```

## Yedek planlar

| Sorun | Çözüm |
|---|---|
| Ollama kapalı / model yanıt vermiyor | Dashboard'u `demo.ps1 dashboard -Offline` ile aç: chat anahtar kelime yorumlayıcısıyla çalışır ve cevapta bunu yazar. Takip e-postaları için `demo.ps1 followup-offline` (kayıttan oynatma, ekranda **REPLAY, not live AI** yazar; "canlı yapay zeka" diye sunma) |
| Model yavaş (cevap > 20 sn) | Bir kez daha bekle; olmazsa Offline'a geç. İlk istek yavaştır, `preflight` modeli ısıtır |
| Dashboard açılmıyor / port dolu | Eski pencereyi kapat; olmazsa `streamlit run ui/app.py --server.port 8502` ile aç (`DATABASE_URL=sqlite:///data/demo.sqlite3` ortam değişkeniyle) |
| Veritabanı bozuldu / yanlış durumda | `demo.ps1 reset` |
| `followup` iki kez çalıştı | Bitmiş e-postalar atlanır, bir şey bozulmaz; baştan göstermek için `reset` |
| Tablo satırı seçilemiyor | Satırın en solundaki kutuya tıkla; olmazsa Search ile tek satıra indir |
| Hiçbir şey çalışmıyor | Önceden alınmış ekran görüntüleriyle anlat ve bunu açıkça söyle |

## Söylenmesi gereken dürüst notlar

- Takip e-postaları (`examples/demo_inbox`) **bizim yazdığımız uydurma** e-postalardır; paketteki güncelleme e-postalarının ilk kaydı yoktu, bu yüzden güncelleme yolu gerçek veride böyle gösteriliyor.
- Model sıcaklık 0'da bile birebir tekrarlanabilir değildir; demo veritabanı kayıtlı model çıktısından üretildi ve aynı akış gerçek modelle de çalıştırıldı.
- Chatbot'ta modelin işi yalnızca soruyu plana çevirmek; cevap metnini ve sayıları kod veritabanından üretiyor.

## Sık sorulabilecekler (kısa cevaplar)

- **Neden bu kadar kural, model yetmez mi?** Model yanılabiliyor: örneğin bir e-postadaki sahte talimata uyup limanı değiştirdi. Güvenliği prompt'a bırakmadık; her değer e-postada geçmek zorunda ve yazmayı yalnızca denetlenen araçlar yapıyor.
- **Yanlış yaptığı bir şey var mı?** Evet, geliştirirken bulduk: ek dosyalı bir booking talebini "booking değil" saymıştı, küçük istem değişiklikleri bazı e-postaların niyetini kaydırdı. Hepsi `docs/run_record.md` içinde.
- **Üretimde ne değişirdi?** İnsan onay ekranı, etiketli daha büyük bir değerlendirme seti, ek dosya okuma, kimlik doğrulama ve gözlemlenebilirlik.
