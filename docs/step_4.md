# Aşama 4 — Kalıcılık, kontrollü araçlar ve uçtan uca akış

## Kullanım

```powershell
.\.venv\Scripts\python.exe -m ugr_booking.cli ingest                       # 34 e-posta, sabit kurallarla araç seçimi
.\.venv\Scripts\python.exe -m ugr_booking.cli ingest --planner llm --only EMAIL-008 EMAIL-012 EMAIL-013
.\.venv\Scripts\python.exe -m ugr_booking.cli list-bookings --events
.\.venv\Scripts\python.exe -m pytest tests/test_booking_tools.py tests/test_e2e.py -q
```

## Akış

```
EmailRecord → Extractor (LLM) → validate_extraction → Planner → ToolExecutor → SQLite
                                                        │            ▲
                                       DirectPlanner (kurallar)      └─ tek yazma noktası, her çağrıyı denetler
                                       LLMPlanner (model seçer)
```

Her e-posta **tek bir transaction**'dır. Bir şey patlarsa o e-postanın hiçbir satırı kalmaz,
e-posta `FAILED` olarak (hata metniyle) kaydedilir, sonraki e-postalar işlenmeye devam eder.

## Veritabanı (4 tablo)

| Tablo | İçerik |
|---|---|
| `emails` | Ham ve temiz metin, niyet, durum (`PROCESSED` / `REVIEW_REQUIRED` / `FAILED`), sonuç metni, hata, modelin döndürdüğü JSON |
| `bookings` | Güvenilir alanlar, `status`, VIN listesi ve ölçüler JSON, modelin ham değerleri (`raw`), kaynak e-posta |
| `booking_events` | `CREATE` / `UPDATE` / `CANCEL` / `DUPLICATE` / `FAILED_ACTION`; her biri `source_email_id` taşır |
| `validation_issues` | Kod, alan, önem, mesaj, `review_status` (`OPEN`); booking'e veya sadece e-postaya bağlı |

Aynı booking'e birden çok e-posta, tek e-postadan birden çok booking bu tablolarla izlenir.
Yabancı anahtarlar SQLite'ta açıktır. Dosya veritabanı ilk açılışta oluşturulur ve yeniden açılınca korunur.

## Araç katmanı (`booking_tools.py`)

Kapalı liste: `lookup_customer`, `lookup_booking`, `create_booking`, `update_booking`,
`cancel_booking`, `record_unresolved_action`. Her çağrı `ToolExecutor.execute()` içinden geçer:

1. Araç izin listesinde mi? Değilse `UNKNOWN_TOOL`.
2. Argümanlar şemaya uyuyor mu? (fazla alan, yanlış tür, `true` yerine sayı → `ARGUMENT_INVALID`)
3. Araç e-postanın niyetine uygun mu? (`TOOL_NOT_ALLOWED_FOR_INTENT`)
4. Booking referansı, **e-postada bulunan** referansla aynı mı? (`REFERENCE_MISMATCH`)
5. İşlem: veri her zaman **doğrulanmış taslaktan** gelir, argümandan değil. Argümanlar yalnızca
   kimlik taşır (`booking_index`, `booking_ref`, kısa `reason`). Model bir alan değeri enjekte edemez.

Sonuç `success` / `failure` / `review_required` ve bir kod + gerekçedir.

## İş kuralları

- **CREATE:** kayıt her zaman saklanır; `READY` ya da incelemeye açık `VALIDATION_REQUIRED` taslak olarak.
- **Duplicate:** aynı referans, veya referans yoksa **birebir aynı VIN kümesi** → mevcut kayıt bulunur.
  İki tarafta da dolu olan alanlar çelişmiyorsa `DUPLICATE` olayı yazılır, yeni kayıt açılmaz
  (008 → 012). Çelişiyorsa hiçbir şey ezilmez: `FAILED_ACTION` + `CONFLICT_REQUIRES_REVIEW` sorunu.
  İptal edilmiş booking yeniden canlandırılmaz.
- **UPDATE:** referans kayıtlıysa ve değişiklikler doğrulamadan geçtiyse yalnızca belirtilen alanlar
  uygulanır; eski/yeni değerler olay `details`'ında. Güncelleme adet–VIN uyumunu bozarsa kayıt
  `VALIDATION_REQUIRED`'a döner. Doğrulamadan geçmeyen değişiklik uygulanmaz (`UPDATE_NOT_APPLIED`).
- **CANCEL:** `CANCELLED` + olay. Zaten iptalse `DUPLICATE` olayı.
- **Referans yok / booking yok:** booking uydurulmaz; `FAILED_ACTION` olayı kaydedilir.
- **STATUS_INQUIRY, NON_BOOKING, UNKNOWN:** yalnızca e-posta satırı; booking yazılmaz.
- E-postalar `received_at` sırasıyla işlenir.
- **Idempotency:** `PROCESSED` / `REVIEW_REQUIRED` e-postalar atlanır (model bile çağrılmaz);
  `FAILED` olanlar bir sonraki çalıştırmada yeniden denenir.

## LLM tool-calling

`--planner llm` ile araçları model seçer: sistem istemi + doğrulanmış özet (e-posta metni **değil**)
ve araç tanımları gönderilir; model `lookup_booking` sonucunu görüp `cancel_booking` ya da
`record_unresolved_action` seçer (en çok 4 tur). Seçimleri yine `ToolExecutor` denetler.
Reddedilen çağrılar veriye dokunmaz ve e-postanın durumunu bozmaz; sonuç metninde
`[refused: ...]` olarak denetim için kalır.

Gerçek `qwen3:8b` gözlemi (008, 012, 013, 017, 024, 030, 004): 013'te `lookup_booking → cancel_booking`
zincirini model kurdu; 030'da kayıt bulunamayınca `record_unresolved_action`'ı seçti; 017'de iki
`create_booking`. Model bazen gereksiz `record_unresolved_action` çağırdı (NON_BOOKING, duplicate sonrası);
politika hepsini reddetti. Bu, politika katmanının neden gerekli olduğunu gösteren somut örnektir.
`reason` alanını model yazar (≤ 200 karakter); yalnızca açıklama olarak saklanır, hiçbir karar
ona bağlı değildir.

## Gerçek veride sonuç (qwen3:8b, `ingest`, 34 e-posta)

```
processed 34, failed 0 · created 22 (ready 9, validation_required 13) · cancelled 1
duplicated 1 · failed_actions 6 · conflicts 0 · 18 e-posta incelemede
```

- 008 oluşturuldu → 012 `DUPLICATE` → 013 `CANCELLED`: D100 için tek booking, son durum iptal.
- 017 iki ayrı booking üretti, ikisi de kaynak olarak EMAIL-017'ye bağlı.
- 003, 007, 014, 015, 023 (UPDATE) ve 030 (CANCEL): paketteki hiçbir e-posta bu referansları
  oluşturmadığı için `BOOKING_NOT_FOUND`; hiçbiri booking açmadı.
- İkinci çalıştırma: 34 e-posta atlandı, yeni satır yok.

Bu sayılar bu çalıştırmaya aittir; model çıktısı her seferinde birebir aynı olmayabilir.

## Sınırlar ve açık kalanlar

- Paket dışı bir ilk kaydı olan UPDATE/CANCEL'lar (A220, A310, X002, X300…) uygulanamaz; bu bir
  veri sınırıdır, hata değil.
- Duplicate eşlemesi referans ve birebir VIN kümesine dayanır. VIN'siz, referanssız tekrarlar yakalanmaz.
- Eşzamanlı yazma (iki süreç aynı anda) desteklenmez; SQLite tek kullanıcı için seçildi.
- Ayrıştırılamayan dosyalar (bu veri setinde yok) `emails` tablosuna değil, toplu rapora `failed` olarak yazılır.
