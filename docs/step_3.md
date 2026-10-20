# Aşama 3 — Deterministik doğrulama ve güvenlik

Model bir **iddia** üretir; bu aşama iddiayı kodla denetler. Çıktı `ValidationResult`:
her booking için `draft` (yalnızca güvenilir değerler), `raw` (modelin yazdığı) ve
`issues` (kod + alan + açıklama + önem derecesi).

## Kullanım

```powershell
.\.venv\Scripts\python.exe -m ugr_booking.cli validate EMAIL-019   # model + kurallar, hiçbir şey kaydedilmez
.\.venv\Scripts\python.exe -m pytest tests/test_validation.py tests/test_security.py -q
```

## Dosyalar

| Dosya | Görev |
|---|---|
| `normalize.py` | Saf dönüşümler: yerel sayı biçimleri, ağırlık, ölçü, tarih, metin katlama |
| `master_data.py` (`MasterIndex`) | Müşteri/liman eşleştirmesi: tam eşleşme, büyük-küçük harf ve aksan duyarsız, bulanık tahmin yok |
| `validators.py` | `validate_extraction()`: tüm iş kuralları |
| `schemas.py` | `ValidationIssue`, `Severity`, `ValidatedBooking`, `ValidationResult` |

## Temel ilkeler

1. **Bir değer `draft`'a ancak şu koşullarla girer:** e-postada gerçekten geçiyorsa,
   master data ile eşleşiyorsa veya tahmin gerektirmeden ayrıştırılabiliyorsa.
   Aksi halde `draft` alanı boş kalır, sorun `issues`'a yazılır, ham değer `raw`'da durur.
2. **Kaynakta doğrulama (grounding).** Modelin döndürdüğü liman, VIN, booking referansı,
   tarih ve sayılar e-posta metninde aranır. Bulunmazsa reddedilir. Ağırlık/ölçüde
   cümle değil *sayı* aranır, çünkü model "Weight each: 900 KG" gibi bir ifadeyi
   "900 KG each" diye yeniden sıralayabiliyor.
3. **Önem derecesi.** `ERROR` kaydı `READY` yapmaz; `WARNING` yapar ama gösterilir.
   `READY` ≠ `ASSIGNED`: sefer ataması hiçbir yerde üretilmez.
4. **UPDATE / CANCEL / STATUS_INQUIRY booking yaratmaz.** Referans e-postada
   yoksa `MISSING_REFERENCE` döner ve `action_unresolved` doğru olur.
   NON_BOOKING / UNKNOWN / CANCEL için model booking döndürse bile yok sayılır.

## Kurallar

**CREATE için zorunlu (eksikse `MISSING_FIELD`, hata):** müşteri, POL, POD, kargo,
adet, istenen tarih. **Opsiyonel:** iletişim, vessel, voyage, ağırlık (uyarı), ölçü (uyarı), VIN (uyarı).

| Kod | Ne zaman |
|---|---|
| `UNKNOWN_CUSTOMER`, `AMBIGUOUS_CUSTOMER` | Müşteri master'da yok / birden fazla kayıtla eşleşiyor |
| `UNKNOWN_PORT`, `AMBIGUOUS_PORT`, `SAME_POL_POD` | Liman eşleşmiyor / `JEDDAH / YANBU` gibi iki liman / POL = POD |
| `VALUE_NOT_IN_SOURCE`, `VIN_NOT_IN_SOURCE`, `REFERENCE_NOT_IN_SOURCE` | Model e-postada olmayan bir değer verdi |
| `INVALID_QUANTITY`, `VIN_COUNT_MISMATCH`, `DUPLICATE_VIN`, `VIN_FORMAT_UNUSUAL` | Adet ≤ 0 / adet ≠ VIN sayısı / aynı VIN iki kez / VIN diğerlerinden farklı biçimde |
| `INVALID_DATE` | `31/02/2026` gibi gerçek olmayan tarih (düzeltilmez) |
| `AMBIGUOUS_DATE` | `SEPTEMBER SAILING`, `next week`: kesin tarih yok |
| `DATE_NOT_EXACT` | `next available after 25/11/2026`: yalnızca alt sınır, sefer tarihi değil |
| `UNIT_UNRESOLVED`, `UNIT_UNSUPPORTED` | Birimsiz ağırlık/ölçü (`Weight: 7200`) / kg dışı birim (dönüştürülmez) |
| `DIMENSION_FORMAT_MIXED`, `DIMENSION_OUT_OF_RANGE`, `DIMENSION_UNPARSEABLE` | `6,20 x 2.450 x 3,10 m` karışık ondalık / metre karşılığı 0,1–30 dışında / okunamıyor |
| `WEIGHT_BASIS_UNCLEAR` (uyarı) | Adet > 1 ve "each" yok: toplam olabilir, `weight_kg_per_unit` boş bırakılır |
| `PROMPT_INJECTION_SUSPECTED` | Sisteme hitap eden metin ayrıldı; kayıt hiçbir zaman otomatik `READY` olmaz |
| `MISSING_REFERENCE`, `NO_BOOKING_EXTRACTED`, `BOOKINGS_IGNORED` | Aksiyon e-postası referanssız / CREATE ama booking yok / booking üretmemesi gereken niyette booking geldi |

## Varsayımlar (kasıtlı)

- **Sayısal tarihler gün-ay-yıl okunur.** Veri setinde `22/11`, `28/10`, `27/11/26`
  var; bunlar ay-gün olarak anlamsız. İki haneli yıl 2000'lere tamamlanır.
- **Ağırlık yalnızca kg.** `7.650`, `12,400`, `7 450` binlik ayraç sayılır (üç hane + bir
  ayraç); `12.500,50` ve `7.330,50` için son ayraç ondalıktır; `7,5` ondalıktır.
- **Ölçü sırası uzunluk × genişlik × yükseklik**; `cm`/`mm` metreye çevrilir.
- **Karşılaştırma e-postanın alınma gününe göre:** geçmiş tarih `DATE_IN_PAST` uyarısıdır, engel değildir.
- `voyage` rakam içermiyorsa saklanmaz (`VOYAGE_UNRECOGNISED`); model EMAIL-002'de "ex SAG" yazmıştı.

## Gerçek veride sonuç (34 e-posta, qwen3:8b + bu kurallar)

13 booking taslağı `VALIDATION_REQUIRED`, 10 `READY`, 5 UPDATE kısmi taslak (`PARSED`).
Takip planındaki senaryo matrisinin tamamı tutuyor: 001, 005, 010, 011, 017, 019, 020, 021,
026, 031, 032 incelemeye; 002, 008, 009, 016, 022, 027, 033, 034 `READY`; 013 ve 030 iptal
(referanslı); 024 sorgu; 004, 006, 029 NON_BOOKING; 025 UNKNOWN.

Matris bir cevap anahtarı değil, e-postalar okunarak çıkarılmış hipotezdir.

## Açık kalanlar

- **Duplicate (008 ↔ 012) ve çelişki (`CONFLICT_REQUIRES_REVIEW`)** mevcut booking'lere
  bakmayı gerektirir; Aşama 4'te veritabanıyla birlikte yapılacak. Şu an 008 ve 012 ikisi de `READY`.
- `_INSTRUCTION_LIKE` kalıp listesi sınırlıdır. Ona takılmayan bir enjeksiyon
  modele ulaşabilir; yine de uydurma değerler `VALUE_NOT_IN_SOURCE` ile reddedilir.
- Modelin gerçek bir değeri yanlış kopyalaması (örneğin bir VIN'in bir hanesi) yakalanır
  ama kaynakta var olan *başka* bir değeri seçmesi yakalanamaz.
