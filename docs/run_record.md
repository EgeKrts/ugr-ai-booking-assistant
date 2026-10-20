# Çalışma kaydı — Aşama 7 kalite kapısı

Tarih: 20 Ekim 2026 · Ortam: Windows 10, Python 3.12.14, RTX 4060 Laptop (8 GB), Ollama + `qwen3:8b`, sıcaklık 0.

Bu belge, 34 e-postanın **gerçek model çalıştırmasından** alınan sonuçlarını, test durumunu ve bilinen sınırları kaydeder.
Sayılar tek bir çalıştırmaya aittir; e-posta metni içermez, yalnızca ID, niyet, durum ve kodlar vardır.

## 1. Girdi ve toplu sonuç

| Ölçü | Değer |
|---|---|
| E-posta girdisi | **34 / 34** okundu, ayrıştırma hatası **0** |
| İşlenen / başarısız / atlanan | 34 / 0 / 0 |
| Niyet dağılımı | CREATE_BOOKING 23 · UPDATE_BOOKING 5 · CANCEL_BOOKING 2 · NON_BOOKING 2 · STATUS_INQUIRY 1 · UNKNOWN 1 |
| Oluşan booking taslağı | **23** (READY 9, VALIDATION_REQUIRED 14); sonradan 1'i iptal edildi |
| İptal / duplicate / güncelleme | 1 / 1 / 0 |
| Uygulanamayan talep (`FAILED_ACTION`) | 6 (003, 007, 014, 015, 023, 030: ilk kayıt pakette yok) |
| Çakışma (`CONFLICT_REQUIRES_REVIEW`) | 0 |
| İncelemeye giden e-posta | 19 |
| İkinci çalıştırma | 34 atlandı, yeni satır **0** |
| Canlı çalıştırma ile kayıttan oynatma | Rapor **birebir aynı** |
| Model kararlılığı | Aynı istemle iki bağımsız kayıt, 34 e-postanın tamamında aynı çıktı (serbest metin notları hariç). **Garanti değildir:** sonraki bir canlı çalıştırmada EMAIL-002'nin isteğe bağlı `voyage` alanı farklı çıktı (engellemeyen uyarı) |

**Süre ve maliyet.** Model çıkarımı toplam 256 sn, e-posta başına ortalama 7,5 sn (en yavaş 11,1 sn; boş gövde
0 sn, çünkü modele gitmez). Model yerel çalışır, API ücreti yoktur. Token sayısı ölçülmedi.

## 2. Testler

| Komut | Sonuç |
|---|---|
| `python -m pytest -q` | **313 geçti, 22 atlandı** (atlananlar canlı model testleri), sıfır başarısızlık, ~11 sn |
| `python -m pytest --live -q` | **335 / 335 geçti** (canlı testler dahil yaklaşık 2,8 dk) |

Model gerektirmeyen testler sahte çıkarıcı ve geçici veritabanıyla çalışır. `tests/test_scenarios.py` 34 e-postayı gerçek
model çıktısının **kayıttan oynatılmasıyla** tüm hattan geçirir (42 test). Kayıt ve veri paketi Git'te olmadığı için bu
dosya o makinelerde atlanır; kayıt `python -m ugr_booking.cli record-extractions` ile üretilir.

## 3. Aşama 7 sırasında bulunan ve düzeltilen sorunlar

Manuel inceleme ve senaryo testleri, modelin yanlış yaptığı şeyleri ortaya çıkardı:

| Bulgu | Etki | Çözüm |
|---|---|---|
| EMAIL-029 ("book per attached Excel", müşteri ve rota var) `NON_BOOKING` sayılmıştı | Booking talebi iz bırakmadan kayboluyordu | İstemde "eksik ayrıntı yine de CREATE" kuralı |
| Küçük istem değişiklikleri 012 ("RE: ... resend") ve 028 (e-posta içi düzeltme) niyetini `UPDATE`'e kaydırdı | 028 `BOOKING_NOT_FOUND` ile **kaybolurdu** | Kural cümleleri yerine 3 uydurma örnek (resend, e-posta içi düzeltme, gerçek değişiklik) |
| EMAIL-032'de `Requested date: null` satırı seçildi, `Sailing: next available after ...` kayboldu | "25/11'den sonra" bilgisi kaybı | Tarih kuralı: null satırı atla |
| EMAIL-023'te model "reduce to 6" yerine özgün adedi (8) çıkardı | Yanlış değişiklik riski (backend ağı: 6 VIN ile 8 adet çeliştiği için yine uygulanmazdı) | Adet değişikliği örneği |
| EMAIL-018'de "next week" yakalanmıyordu | Bilgi kaybı | Göreli tarih kuralı |

Bu döngü her istem değişikliğinde **tüm 34 e-postanın** yeniden çalıştırılması gerektiğini gösterdi; bunun için kayıt/oynatma
ve canlı regresyon testleri eklendi. İstemde veri setinden alınmış metin yoktur, örnekler uydurmadır.

## 4. Manuel inceleme: şüpheli sınıflandırmalar

| E-posta | Değerlendirme |
|---|---|
| 004, 006 | `NON_BOOKING` doğru: e-posta açıkça "rate review only" / "not a new booking request" diyor |
| 012 | `CREATE` → `DUPLICATE` doğru (D100'ün yeniden gönderimi) |
| 028 | `CREATE` doğru (konu "Booking request", düzeltme mesaj içinde); POD son değer Yanbu |
| 029 | `CREATE`, `VALIDATION_REQUIRED`: ek dosya okunamadığı için adet/VIN/ağırlık/tarih uydurulmadı |
| 018 | `CREATE` taslağı, liman ve tarih eksik; "as usual" bilgisi uydurulmadı |
| 007 | `UPDATE`; **değer çıkarımı güvenilir değil** (adet 25 geldi: kaldırılan B/L adedi, toplam hacim değil). Hedef booking yok, uygulanmadı |
| 003 | `UPDATE`, müşteri ve VIN sayısı hatalı; zaten uygulanamaz (kaynak ref yok) |

## 5. Her e-postanın son durumu

| E-mail | Intent | Status | Bookings | Events | Blocking issues | Warnings |
|---|---|---|---|---|---|---|
| EMAIL-006 | NON_BOOKING | PROCESSED | - | - | - | - |
| EMAIL-007 | UPDATE_BOOKING | REVIEW_REQUIRED | - | FAILED_ACTION | - | - |
| EMAIL-001 | CREATE_BOOKING | REVIEW_REQUIRED | BKG-00001 (VALIDATION_REQUIRED) | CREATE->BKG-00001 | AMBIGUOUS_DATE, AMBIGUOUS_PORT, UNIT_UNRESOLVED | - |
| EMAIL-004 | NON_BOOKING | PROCESSED | - | - | - | - |
| EMAIL-005 | CREATE_BOOKING | REVIEW_REQUIRED | BKG-00002 (VALIDATION_REQUIRED) | CREATE->BKG-00002 | MISSING_FIELD | - |
| EMAIL-002 | CREATE_BOOKING | PROCESSED | BKG-00003 (READY) | CREATE->BKG-00003 | - | - |
| EMAIL-003 | UPDATE_BOOKING | REVIEW_REQUIRED | - | FAILED_ACTION | UNKNOWN_CUSTOMER, VIN_COUNT_MISMATCH | - |
| EMAIL-008 | CREATE_BOOKING | PROCESSED | BKG-00004 (CANCELLED) | CREATE->BKG-00004 | - | - |
| EMAIL-012 | CREATE_BOOKING | PROCESSED | - | DUPLICATE->BKG-00004 | - | - |
| EMAIL-009 | CREATE_BOOKING | PROCESSED | BKG-00005 (READY) | CREATE->BKG-00005 | - | - |
| EMAIL-010 | CREATE_BOOKING | REVIEW_REQUIRED | BKG-00006 (VALIDATION_REQUIRED) | CREATE->BKG-00006 | UNKNOWN_CUSTOMER | - |
| EMAIL-011 | CREATE_BOOKING | REVIEW_REQUIRED | BKG-00007 (VALIDATION_REQUIRED) | CREATE->BKG-00007 | VIN_FORMAT_UNUSUAL | - |
| EMAIL-013 | CANCEL_BOOKING | PROCESSED | - | CANCEL->BKG-00004 | - | - |
| EMAIL-014 | UPDATE_BOOKING | REVIEW_REQUIRED | - | FAILED_ACTION | - | - |
| EMAIL-015 | UPDATE_BOOKING | REVIEW_REQUIRED | - | FAILED_ACTION | - | - |
| EMAIL-016 | CREATE_BOOKING | PROCESSED | BKG-00008 (READY) | CREATE->BKG-00008 | - | - |
| EMAIL-017 | CREATE_BOOKING | REVIEW_REQUIRED | BKG-00009 (VALIDATION_REQUIRED), BKG-00010 (VALIDATION_REQUIRED) | CREATE->BKG-00009, CREATE->BKG-00010 | AMBIGUOUS_DATE | - |
| EMAIL-018 | CREATE_BOOKING | REVIEW_REQUIRED | BKG-00011 (VALIDATION_REQUIRED) | CREATE->BKG-00011 | AMBIGUOUS_DATE, MISSING_FIELD | - |
| EMAIL-019 | CREATE_BOOKING | REVIEW_REQUIRED | BKG-00012 (VALIDATION_REQUIRED) | CREATE->BKG-00012 | VIN_COUNT_MISMATCH | - |
| EMAIL-020 | CREATE_BOOKING | REVIEW_REQUIRED | BKG-00013 (VALIDATION_REQUIRED) | CREATE->BKG-00013 | INVALID_DATE | - |
| EMAIL-021 | CREATE_BOOKING | REVIEW_REQUIRED | BKG-00014 (VALIDATION_REQUIRED) | CREATE->BKG-00014 | UNIT_UNRESOLVED | - |
| EMAIL-022 | CREATE_BOOKING | PROCESSED | BKG-00015 (READY) | CREATE->BKG-00015 | - | - |
| EMAIL-023 | UPDATE_BOOKING | REVIEW_REQUIRED | - | FAILED_ACTION | - | - |
| EMAIL-024 | STATUS_INQUIRY | PROCESSED | - | - | - | - |
| EMAIL-025 | UNKNOWN | PROCESSED | - | - | - | EMPTY_BODY |
| EMAIL-026 | CREATE_BOOKING | REVIEW_REQUIRED | BKG-00016 (VALIDATION_REQUIRED) | CREATE->BKG-00016 | UNKNOWN_PORT | MISSING_SENDER, SUSPECT_ENCODING |
| EMAIL-027 | CREATE_BOOKING | PROCESSED | BKG-00017 (READY) | CREATE->BKG-00017 | - | - |
| EMAIL-028 | CREATE_BOOKING | PROCESSED | BKG-00018 (READY) | CREATE->BKG-00018 | - | - |
| EMAIL-029 | CREATE_BOOKING | REVIEW_REQUIRED | BKG-00019 (VALIDATION_REQUIRED) | CREATE->BKG-00019 | MISSING_FIELD | - |
| EMAIL-030 | CANCEL_BOOKING | REVIEW_REQUIRED | - | FAILED_ACTION | - | - |
| EMAIL-031 | CREATE_BOOKING | REVIEW_REQUIRED | BKG-00020 (VALIDATION_REQUIRED) | CREATE->BKG-00020 | PROMPT_INJECTION_SUSPECTED | PROMPT_INJECTION_SUSPECTED |
| EMAIL-032 | CREATE_BOOKING | REVIEW_REQUIRED | BKG-00021 (VALIDATION_REQUIRED) | CREATE->BKG-00021 | DATE_NOT_EXACT, DIMENSION_FORMAT_MIXED | - |
| EMAIL-033 | CREATE_BOOKING | PROCESSED | BKG-00022 (READY) | CREATE->BKG-00022 | - | - |
| EMAIL-034 | CREATE_BOOKING | PROCESSED | BKG-00023 (READY) | CREATE->BKG-00023 | - | - |

## 6. Bilinen sınırlar

1. **UPDATE değer çıkarımı karmaşık mesajlarda güvenilir değil** (007). Üretimde UPDATE için insan onayı gerekir. Mevcut korumalar: doğrulamadan geçmeyen değişiklik uygulanmaz, adet–VIN çelişkisi kaydı incelemeye döndürür.
2. **Model istem hassasiyeti.** Belirsiz e-postalarda küçük istem değişiklikleri niyeti çevirdi. Savunma: örnekler, senaryo testleri, canlı regresyon testi. İstem veya model değişince `record-extractions` ve `pytest --live` yeniden çalıştırılmalıdır; kayıt eski istemle yapıldıysa `RecordedExtractor.stale` bunu bildirir.
3. **Senaryo matrisi bir cevap anahtarı değildir**, e-postalar okunarak çıkarılmış hipotezlerdir. Veri seti sentetik ve 34 e-postadan ibarettir; genel bir doğruluk iddiası yoktur.
4. UPDATE/CANCEL e-postalarının ilk kaydı pakette bulunmadığı için hiçbir güncelleme uygulanamadı; güncelleme yolu yalnızca testlerde ve sentetik senaryolarda kanıtlandı.
5. Duplicate tespiti referansa ve birebir aynı VIN kümesine dayanır; referanssız ve VIN'siz tekrarlar yakalanmaz.
6. Prompt injection filtresi sınırlı bir kalıp listesidir; ikinci hat, kaynakta doğrulama ve backend politikasıdır.
7. Ek dosyalar (029) okunmaz; sistem bunu uydurmadan `VALIDATION_REQUIRED` olarak bırakır.
8. SQLite tek kullanıcı içindir; eşzamanlı yazma desteklenmez.
9. Senaryo testleri yerel veri paketi ve kayıt gerektirir (Git'te yok); başka makinede atlanır.
10. Token kullanımı ölçülmedi.

## 7. Sonradan eklenen: araç seçimini modelin yapması ve takip e-postası demosu

PDF'in "kontrollü AI → tool etkileşimi" maddesi için `ingest` varsayılanı **modelin araç seçmesi** olarak değiştirildi
(`GuardedLLMPlanner`; ayrıntı: [step_4](step_4.md)). Aynı 34 e-posta yeniden canlı çalıştırıldı:

| Ölçü | Değer |
|---|---|
| Araç çağrısını seçen: model / kurallar | **49 / 0** |
| Politika kapısının reddettiği model çağrıları | 15 |
| Toplu rapor (her e-postanın durumu, booking ve olayları) | Sabit kurallı çalıştırmayla **birebir aynı** |
| Sayılar | 34/34 işlendi, 0 hata, 23 oluşan (9 READY, 14 VALIDATION_REQUIRED), 1 iptal, 1 duplicate, 6 uygulanamayan |

`update_booking` gerçek veride, `examples/demo_inbox/` içindeki uydurma takip e-postalarıyla gösterildi (gerçek model, gerçek
booking'ler): tarih güncellemesi uygulandı, adet güncellemesi kaydı incelemeye döndürdü, bilinmeyen müşteri değişikliği
uygulanmadı, iptal ve durum sorgusu çalıştı, olmayan referans booking oluşturmadı (tablo: [step_4](step_4.md)).

Chat cevapları artık dashboard'u otomatik filtreler ve vurgular ([step_6](step_6.md)).

## 8. Temiz klonda doğrulama

Kaynak: `git clone` ile yalnızca commit'li dosyalar, yeni Python 3.12.14 sanal ortamı, README'nin adımları.

| Adım | Sonuç |
|---|---|
| `pip install -r requirements.txt` | Sorunsuz, yaklaşık 49 sn |
| `pip install -e .` (ek bayrak yok), `pip check` | Sorunsuz, "No broken requirements found" |
| Python 3.10 ile aynı adımlar | Pip açık mesaj verir: `requires a different Python: 3.10.9 not in '<3.13,>=3.11'` |
| `pytest -q`, veri paketi ve kayıt **olmadan** | 255 geçti, 39 atlandı, 0 başarısız |
| Veri paketi yerleştirildikten sonra `ingest` (gerçek model) | 34/34, 0 hata, 23 booking, model 49 / kurallar 0 / reddedilen 15, süre **9 dk 5 sn** |
| `pytest -q`, veri paketi **ile** | 275 geçti, 23 atlandı (senaryo kaydı ve canlı testler), 0 başarısız |
| `streamlit run ui/app.py` | Sunucu açıldı, sağlık ve ana sayfa HTTP 200, temiz log |

Bu denemenin bulduğu ve düzeltilen sorunlar: veri paketi yokken hata mesajı yolu ve çözümü söylemiyordu; `list-bookings` ve `report`
olmayan bir veritabanında boş bir dosya oluşturuyordu; README süre tahmini (5 dk) gerçeğin (5–10 dk) altındaydı.
