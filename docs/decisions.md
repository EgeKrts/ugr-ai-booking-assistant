# Teknik karar ve öğrenme notları

Bu dosya, yaptığımız işi sunumda gerekçeleriyle anlatabilmek için tutulur.
İlk kayıt: 20 Ekim 2026, takip planının yalnızca **1.1 — Başlangıç** adımı.

## Proje kökü: D:\UGR

Kaynak kod, testler ve belgeler burada bulunur. Sanal ortam ve özel veri de
yerelde bu klasörün altındadır ancak Git'e eklenmez. Önceki çalışma klasöründeki
deneme kodları bu projeye aktarılmadı; buradaki ilerleme yeni plana göre izlenir.

## Python 3.12 ve ayrı sanal ortam

Python dili proje gereksinimidir. Mevcut Python 3.12.14 kurulumu kullanılarak bu
projeye ait `.venv` oluşturuldu. Paketler bu ortama kurulur; başka projelerin
paketleriyle karışmaz. Python genel PATH üzerinde bulunmasa da tam çalıştırılabilir
dosya yolu ile sanal ortam kurulabilir. README, ayrı bir Python 3.11/3.12 kurulumu
olan değerlendiricinin projeyi yeniden kurmasını açıklar.

PowerShell'de aktivasyon ayarı değiştirmek yerine `.venv\Scripts\python.exe`
kullanırız. Böylece hangi Python ve hangi paketlerin kullanıldığı açık olur.

## Paketlerin görevleri

| Paket | Neden var? | Kullanılacağı sonraki adım |
|---|---|---|
| Pydantic | AI çıktısının türlerini ve yapısını doğrulamak | Şema/çıkarım |
| SQLAlchemy | Veritabanı erişimi ve transaction yönetimi | Kalıcı kayıt |
| Streamlit | Python ile dashboard ve chatbot ekranı | Arayüz |
| pytest | Kritik kuralların ve hata yollarının otomatik testi | İşlevlerle birlikte |
| python-dotenv | Yerel ayar ve API anahtarlarını koddan ayırmak | Yapılandırma/LLM |

SQLite desteği Python ile gelir; ayrıca SQLite sunucusu kurmayız. SQLAlchemy,
SQLite'ın kendisi değil, veritabanına erişim katmanıdır. Bu adımda paketleri
kurmak, ilgili uygulama özelliklerinin tamamlandığı anlamına gelmez.

LLM sağlayıcısı ve SDK henüz seçilmedi. Gerçek model bağlantısı ilgili adımda
yapılacak; bu aşamada API anahtarı gerekmiyor.

## Kaynak paket düzeni ve kurulum

`src/ugr_booking` uygulamanın Python paketidir. `pip install -e .` komutu
paketi sanal ortama bağlar; kaynak kod değişince kopyalama yapmaya gerek kalmaz.
Bu düzen, import edilen kodun nereden geldiğini açık tutar. `ui`, `tests` ve
`docs` klasörleri arayüzü, doğrulamayı ve açıklamaları ayırır. Henüz kullanılmayan
işlev modüllerini boş uygulamalarla doldurmayız; ilgili adımda oluştururuz.

## Bağımlılık sürümleri

`requirements.txt` doğrudan seçtiğimiz paketleri ve doğrulanan sürümlerini tutar.
`requirements-lock.txt` geçişli bağımlılıklar dahil kurulu ortamı kaydeder. Amaç,
aynı Windows/Python ortamını daha sonra yeniden kurabilmektir. Sürüm sabitlemek
tek başına doğruluk kanıtı değildir; kurulum sonrası paket tutarlılığı kontrolü
de yapılır. Farklı platform desteği ayrıca doğrulanmalıdır.

## Gizli veri ve Git

`.gitignore`, API anahtarı içeren `.env`, sanal ortam, ham veri seti dosyaları,
veritabanları ve logların normal `git add` ile depoya alınmasını önler.
`.env.example` yalnızca boş/örnek ayarları gösterir; gerçek bir sır içermez.
Bu dosya kalıpları tam kapsamlı bir secret tarayıcısı değildir; commit içeriği
yine de kontrol edilir. Git yerel sürüm geçmişidir; GitHub'a yayın ayrı adımdır.

## Bu adımda doğruladığımız sınır

Python sürümü, kullanılan sanal ortam, kurulu bağımlılıklar, paket import'u,
`pip check`, Git dışlama kuralları ve ilk commit kontrol edilir. Bunlar kurulum
kontrolleridir. E-posta okuma, LLM kalitesi veya booking doğruluğu henüz test
edilmiş sayılmaz. **1.2 başlamadan durulur.**

## 1.2 — Manifest, ana veri ve tip sözleşmesi

Manifest hangi girdilerin mevcut olduğunu ve alınma zamanını belirler. Dosya adı
sırasına güvenmeyiz; parse edilmiş zamanla sıralarız. Kaynak zamanlarda saat
dilimi bulunmadığından kendimiz UTC veya yerel saat dilimi eklemeyiz.

Klasör ve ZIP aynı DatasetSource arayüzünden okunur. ZIP açılmaz; yalnızca
bellekte izinli dosyalar okunur. Mutlak yollar, üst klasöre geçiş ve kök dışına
çözülen yollar reddedilir. Müşteri ve liman alias listeleri aynen korunur;
eşleştirme/normalizasyon kuralları ilgili validation adımında eklenecektir.

BookingDraft, verilen 20 alanın Pydantic karşılığıdır. String miktar veya bool
miktar sessizce sayıya çevrilmez. Geçersiz ya da göreli tarih kesin tarihe
çevrilmez; bilinmeyen iş alanları null kalır. Bu yapı tek başına booking
onayı değildir. İş kuralları ve kontrollü yazma sonraki adımlara aittir.

Harici şema kod olarak çalıştırılmaz. Uygulamanın desteklediği alan ve enum
sözleşmesiyle karşılaştırılır; beklenmeyen değişiklik açık hata verir.
Paket yapısındaki bir sorun inceleme komutunu kontrollü hatayla bitirir.
E-posta bazlı hatadan sonra devam etme, gövde ayrıştırma adımında uygulanacaktır.
