# Test Akışları ve Kombinasyonlar

Bu belge, XDMA double buffer driver'ını test etmek için kullanılan scriptlerin rollerini ve hangi kombinasyonlarla çalıştırılacağını açıklar.

---

## Scriptlerin Rolleri

```
generic.py          → sadece ham veri yakalar  (.bin üretir)
continuous_read.py  → sadece ham veri yakalar  (.bin üretir, büyük yakalama için)
plotsincos.py       → .bin alır, grafik çizer + LFSR doğrular
test_lfsr.py        → hem yakalar hem analiz eder (tek komutla)
lfsr_analysis_utils → kütüphane, direkt çalıştırılmaz
```

---

## Kombinasyon 1 — Hızlı Görsel Test

**Ne zaman kullanılır:** SIN/COS dalga formlarını ve LFSR sağlığını hızlıca görmek istediğinizde.

```bash
# 1. Veri yakala
sudo python3 generic.py
#   → mod       : dbuf
#   → kanal     : 4
#   → read size : 4096
#   → read count: 20

# Çıktı: data/outputs/<timestamp>_output/output_ch4_4096B_x20.bin

# 2. Analiz et
python3 plotsincos.py data/outputs/<timestamp>_output/output_ch4_4096B_x20.bin
```

**Üretilen çıktı:**
- SIN / COS / Counter / LFSR grafikleri
- Toplam LFSR hata sayısı (terminal)

---

## Kombinasyon 2 — LFSR Karşılaştırma Testi (Önerilen)

**Ne zaman kullanılır:** Single buffer vs double buffer arasındaki LFSR kayıp farkını ölçmek istediğinizde. `plotsincos.py`'a gerek yok, her şeyi tek komutla yapar.

```bash
sudo python3 test_lfsr.py
#   → transfer size : 4096
#   → transfer count: 20
#   → single buffer : y  (karşılaştırma için) / n
#   → double buffer : y
#   → grafik        : y / n
```

**Üretilen çıktı** (`data/outputs/<timestamp>_lfsr_compare/`):
- `double_buffer.bin` — ham veri
- `double_buffer.skip_report.csv` — her LFSR hatasının boundary analizi
- Terminal: hata sayısı, boundary/inner ayrımı, karşılaştırma tablosu

**Örnek terminal çıktısı:**
```
=======================================================
  KARŞILAŞTIRMA SONUCU
=======================================================
Mod                  Hata       Hata Oranı
-------------------------------------------------------
Single Buffer        1842       71.9531%
Double Buffer          19        0.7461%
=======================================================
  ✓ Double buffer 98.97% iyileştirme sağladı.
```

---

## Kombinasyon 3 — Büyük Sürekli Yakalama + Sonra Analiz

**Ne zaman kullanılır:** 13 MB+ gibi uzun süreli, kesintisiz yakalama gerektiğinde (sunum verisi, uzun FFT penceresi vb.).

```bash
# 1. Veri yakala
sudo python3 continuous_read.py
#   → kanal      : 4
#   → chunk      : 4096
#   → toplam byte: 13421728

# Çıktı: data/outputs/<timestamp>_continuous/ch4_13422592B.bin

# 2a. Grafik
python3 plotsincos.py data/outputs/<timestamp>_continuous/ch4_13422592B.bin

# 2b. CSV skip raporu + snapshot PNG
python3 - <<'EOF'
from lfsr_analysis_utils import write_skip_report, save_signal_snapshot
f = "data/outputs/<timestamp>_continuous/ch4_13422592B.bin"
write_skip_report(f)
save_signal_snapshot(f)
EOF
```

**Üretilen çıktı:**
- `<dosya>.skip_report.csv` — tüm LFSR hatalarının detaylı tablosu
- `<dosya>.snapshot.png` — ilk 8192 sample'ın sinyal görüntüsü

---

## Kombinasyon 4 — Sadece Sonradan Analiz

**Ne zaman kullanılır:** Elinizde zaten yakalanmış bir `.bin` dosyası var, sadece analiz raporu üretmek istiyorsunuz.

```bash
python3 - <<'EOF'
from lfsr_analysis_utils import write_skip_report, save_signal_snapshot
write_skip_report("data/outputs/.../output.bin")
save_signal_snapshot("data/outputs/.../output.bin")
EOF
```

---

## Hangi Script Neye Uygun?

| Amaç | Script |
|------|--------|
| Hızlı LFSR sağlığı kontrolü | `test_lfsr.py` |
| Sinyal şekli görmek (SIN/COS grafik) | `generic.py` → `plotsincos.py` |
| Büyük veri yakalamak (13 MB+) | `continuous_read.py` → `plotsincos.py` |
| Orijinal driver ile karşılaştırma | `test_lfsr.py` (single buffer seçeneğiyle) |
| Sadece skip raporu CSV üretmek | `lfsr_analysis_utils.write_skip_report()` |

---

## Parametre Rehberi

### Neden `buf_size = 4096`?

| buf_size | Boundary Hata | Inner Hata (FPGA FIFO taşması) |
|----------|--------------|-------------------------------|
| 4096 | 2 sample/sınır | **0** ← optimal |
| 32768 | 2 sample/sınır | Var |
| 131072 | 2 sample/sınır | Çok fazla |

FPGA'nın AXI-Stream çıkış FIFO'su büyük transferlerde (>4096 byte) doluyor ve **inner** hatalar üretiyor. `buf_size = 4096` (1 PAGE_SIZE) ile inner hatalar tamamen sıfırlanıyor.

### Boundary Kaybı Neden Önlenemiyor?

Her DMA descriptor geçişinde AXI-Stream engine `tready=0` sinyalini ~2 sample (~80 ns @ 25 MHz) süre tutuyor. Bu donanım sınırı; double buffer ile **kayıp sabitleniyor** (her sınırda tam 2 sample), ama sıfırlanamıyor.

```
sample[127]: counter = N
sample[128]: counter = N+3   ← 2 sample atlandı (N+1, N+2 kayıp)
```
