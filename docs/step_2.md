# Aşama 2 — LLM ile intent ve alan çıkarımı

Model: **Ollama + `qwen3:8b`** (yerel, API anahtarı yok). RTX 4060 (8 GB) üzerinde
tamamen GPU'ya sığıyor; e-posta başına yaklaşık 4–13 saniye sürüyor.

## Kullanım

```powershell
ollama pull qwen3:8b
.\.venv\Scripts\python.exe -m pytest tests/test_extractor.py -q
```

`OLLAMA_URL` ve `OLLAMA_MODEL` `.env` dosyasından okunur (bkz. `.env.example`).

## Sorumluluk sınırı

`extractor.py` yalnızca **doğrulanmamış** bir `ExtractionResult` üretir. Veritabanına
yazmaz, tarih/birim dönüştürmez, müşteri/liman eşleştirmez. Bunlar Aşama 3 ve 4'tür.
Model değerleri e-postadaki haliyle kopyalar (`weight_text`, `dimensions_text`,
`requested_date_text`). Böylece "birimi belirtilmemiş ağırlık" gibi durumlar
sessizce kg sayılmaz.

## Tasarım kararları

- **Tek `extract(email)` arayüzü.** Hata durumunda `ExtractionError` (kod:
  `LLM_UNAVAILABLE`, `LLM_TIMEOUT`, `INVALID_JSON`, `SCHEMA_INVALID`, …). Hata gizlenmez.
- **Şema ile kısıtlı çıktı.** Ollama'ya JSON şeması verilir; tüm alanlar "zorunlu"
  gönderilir, böylece küçük model alanı atlamak yerine açıkça `null` yazar.
- **Tek yeniden deneme** yalnızca bozuk JSON/şema hatasında. Sunucu kapalıysa
  veya zaman aşımı olursa yeniden denenmez.
- **Boş gövde modele gitmez** (EMAIL-025): `UNKNOWN` döner, alan uydurulmaz.
- **Alıntı geçmiş ayrı etiketlenir.** Model güncel mesajı talimat, alıntıyı yalnızca
  bağlam olarak görür (EMAIL-015, EMAIL-030).
- **Serbest metin alanları sınırlı** (`message_notes` ≤ 300 karakter). Sınırsızken
  model tekrar döngüsüne girip zaman aşımına uğradı (EMAIL-017).

## Prompt injection: gözlem

EMAIL-031'de yalnızca prompt sıkılaştırması **yetmedi**: model e-postadaki sahte
talimatı izleyip POD'u Dubai yazdı, üstelik notunda "yok sayıldı" dedi. Bu yüzden
deterministik bir katman eklendi: `preprocess.py`, sisteme hitap eden paragrafları
(`SYSTEM NOTE`, `ignore ... rules`, `invent ... values` vb.) modele gitmeden ayırır,
`suspicious_text` alanına koyar ve `PROMPT_INJECTION_SUSPECTED` uyarısı üretir.
Ham gövde denetim için saklanır. Sonuç: POD Jeddah kalıyor. 34 e-postada yalnızca
1 paragraf işaretlendi.

Bu kural listesi sınırlıdır. Aşama 3'te port master kontrolü ve yazma öncesi
backend doğrulaması ikinci savunma hattı olarak eklenecek.

## Gerçek model gözlemleri (qwen3:8b, 14 e-posta)

| E-posta | Sonuç |
|---|---|
| 002 | CREATE; müşteri, rota, 3 VIN, ağırlık, ölçü, tarih ham metinle çıkarıldı |
| 004, 006 | NON_BOOKING, booking yok |
| 024 | STATUS_INQUIRY, `target_booking_ref` dolu |
| 025 | Boş gövde, model çağrılmadı, UNKNOWN |
| 017 | CREATE, **iki** ayrı booking |
| 030 | Yalnızca CANCEL; alıntıdaki eski CREATE kullanılmadı |
| 015, 023, 014 | UPDATE + referans; 015'te güncel adet 4 (alıntıdaki 6 değil) |
| 028 | POD = YANBU (son düzeltme) |
| 019 | 4 adet / 5 VIN uyuşmazlığı düzeltilmeden korundu |
| 001 | POD `JEDDAH / YANBU` ve `SEPTEMBER SAILING` ham haliyle korundu |
| 031 | POD = JEDDAH (injection filtresi sonrası) |

Bu gözlemler elle okunarak karşılaştırıldı; resmî bir cevap anahtarı yoktur ve
modelin çıktısı her çalıştırmada birebir aynı olmayabilir. Tam doğruluk ölçümü
Aşama 7'de yapılacak.

## Açık kalanlar

- `BookingDraft` için `raw_*`, `validation_issues`, `booking_index` gibi iç alanlar
  Aşama 3'te eklenecek.
- "Alıntı mesajda en güncel intent" için otomatik test, yalnızca prompt etiketlemesini
  denetliyor. Modelin davranışı manuel gözlemlendi, otomatik regresyon testi yok.
- 429 (rate limit) kodu eşlenmiş ama yerel Ollama ile test edilemedi.
