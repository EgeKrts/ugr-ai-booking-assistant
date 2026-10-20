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

## LLM tool-calling (varsayılan mod)

`ingest` varsayılan olarak **araçları modelin seçtiği** `GuardedLLMPlanner` ile çalışır (`--planner direct` sabit kurallar,
kayıttan oynatırken varsayılan). Model sistem istemi + doğrulanmış özet (e-posta metni **değil**) ve araç tanımlarını alır;
`lookup_booking` sonucunu görüp `cancel_booking` ya da `record_unresolved_action` seçer (en çok 4 tur).

Üç güvence katmanı:

1. **Politika kapısı** (`ToolExecutor`): modelin her çağrısı allowlist, argüman şeması, niyet uyumu ve referans bağlama
   denetiminden geçer. Reddedilen çağrılar veriye dokunmaz ve e-postanın durumunu bozmaz; sonuç metninde
   `[refused: ...]` olarak denetim için kalır.
2. **Kuralların tamamlaması**: model hiç araç çağırmazsa, bir adayı atlarsa ya da yanlış araç seçip reddedilirse, yapılmayan
   iş sabit kurallarla tamamlanır. Böylece sessiz ya da zayıf bir model bir booking talebini **kaybettiremez**.
3. **Kim seçti, kayıtlı**: her sonuç `data["via"] = model | rules` taşır; `ingest` çıktısındaki `[model]` / `[rules]` etiketleri
   ve özetteki `tool_calls_chosen_by_model / _by_rules / model_calls_refused_by_policy` bunu gösterir.

Model servisi kapalıysa e-posta `FAILED` olur, hiçbir şey saklanmaz ve sonraki çalıştırmada yeniden denenir.

**Gerçek ölçüm (qwen3:8b, 34 e-posta):** model **49** araç çağrısı seçti, kurallar **0** çağrı tamamladı, politika modelin **15**
gereksiz çağrısını reddetti (örneğin NON_BOOKING e-postasında `record_unresolved_action`). Toplu rapor, sabit kurallı
çalıştırmayla **birebir aynı** çıktı. 031'de model salt-okunur `lookup_customer` aracını da kendisi çağırdı.

`reason` alanını model yazar (≤ 200 karakter); yalnızca açıklama olarak saklanır, hiçbir karar ona bağlı değildir.

## Takip e-postaları: `update_booking` gerçek veride

Paketteki UPDATE/CANCEL e-postalarının ilk kaydı olmadığı için hiçbiri uygulanamaz. Güncelleme yolunu gerçek veride göstermek için
`examples/demo_inbox/` içinde **uydurma** altı takip e-postası var (veri setinden metin içermez). Aynı biçimdeki bir klasör
`ingest --inbox` ile işlenir (yerel dosya girişi; manifest gerekmez):

```powershell
Copy-Item data\booking.sqlite3 data\demo.sqlite3
.\.venv\Scripts\python.exe -m ugr_booking.cli ingest --inbox examples/demo_inbox --db sqlite:///data/demo.sqlite3
.\.venv\Scripts\python.exe -m ugr_booking.cli list-bookings --events --db sqlite:///data/demo.sqlite3
```

Gerçek model ve gerçek booking'ler üzerinde sonuç:

| E-posta | İçerik | Sonuç |
|---|---|---|
| DEMO-001 | A400'ün sefer tarihini değiştir | `UPDATED`; olay: `requested_date 2026-11-10 → 2026-12-01` |
| DEMO-002 | D200'ü 4 adet yap (3 VIN var) | `UPDATED_NEEDS_REVIEW`; adet 3 → 4, kayıt `VALIDATION_REQUIRED`, `VIN_COUNT_MISMATCH` |
| DEMO-003 | A400'ün müşterisini bilinmeyen bir firmaya çevir | `UPDATE_NOT_APPLIED` (`UNKNOWN_CUSTOMER`); kayıt değişmedi. Model "çözülemedi" demeye çalıştı, backend booking var olduğu için reddetti (`ACTION_IS_RESOLVABLE`) |
| DEMO-004 | A400'ün durumu? | `lookup_booking` → `FOUND`, booking oluşmadı |
| DEMO-005 | XYZ-REF-2026-1109'u iptal et | `CANCELLED`; sebep olay kaydında |
| DEMO-006 | Olmayan BOOKINGREF-NOPE'u değiştir | `BOOKING_NOT_FOUND`, `FAILED_ACTION`, booking oluşmadı |

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
