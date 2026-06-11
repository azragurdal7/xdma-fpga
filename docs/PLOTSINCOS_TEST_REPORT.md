# plotsincos.py Test Raporu
## generic.py → plotsincos.py Akışı ile LFSR ve FFT Analizi

**Tarih:** 2026-06-11  
**Kanal:** CH4 C2H, AXI4-Stream, 25 MHz  
**Driver:** Modified (double buffer), interrupt_mode=2, poll_mode=0

---

## 1. Test Akışı

```bash
# Adım 1 — Veri yakala
sudo python3 generic.py
#   mod   : dbuf  (double buffer)
#   kanal : 4
#   boyut : 4096 byte/transfer
#   adet  : 200 transfer
#   toplam: 819.200 byte = 25.600 sample

# Adım 2 — Analiz et
python3 plotsincos.py data/outputs/20260611_164041_output/output_ch4_4096B_x200.bin
```

Çıktılar: `output_ch4_4096B_x200.sincos.png`, `.fft.png`, `.skip_report.csv`, `.snapshot.png`

---

## 2. LFSR Hata Sonuçları

| Metrik | Değer |
|--------|-------|
| Yakalanan toplam sample | 25.600 |
| plotsincos'un analiz ettiği sample¹ | 21.504 |
| **plotsincos LFSR hata sayısı** | **335** |
| Tüm veri üzerinde LFSR hata sayısı | 399 |
| Hata oranı (21.504 üzerinden) | %1,56 |

¹ plotsincos ilk 4096 sample'ı LFSR analizinden atlıyor (`lfsr_value[4096:]`).  
FPGA açılış stabilizasyonu için tasarlanmış bir güvenlik marjı.

### Hata Dağılımı

| Hata Tipi | Adet | delta | Kayıp Sample |
|-----------|------|-------|-------------|
| Başlangıç gecikmesi (DMA ilk başlangıç) | 1 | ~960 | ~959 |
| **Boundary skip (donanım)** | **199** | **3** | **2 × 199 = 398** |
| Pipeline gap (buffer ilk sample) | 199 | ~960 | ~958 × 199 |
| **Toplam** | **399** | — | — |

### Hâlâ 2 Sample Atlıyor mu?

**Evet — her 4096-byte sınırında tam 2 sample atlama devam ediyor:**

```
sample[127]: counter = N
sample[128]: counter = N+3   ← N+1 ve N+2 kayıp (2 sample)
```

`counter_delta = 3` → `missing = 2`  
Fiziksel neden: AXI4-Stream DMA engine descriptor geçişinde `tready=0` sinyalini **80 ns = 2 × 40 ns** tutuyor.  
**Bu donanım sınırıdır; yazılımla ortadan kaldırılamaz.**

---

## 3. FFT Sonuçları

| Alan | Değer |
|------|-------|
| Analiz edilen sample sayısı | 21.504 |
| FFT çözünürlüğü (df) | 1.162,6 Hz |
| **SIN peak frekansı** | **15.113,5 Hz** |
| **COS peak frekansı** | **15.113,5 Hz** |

SIN ve COS aynı frekansta zirve yapıyor. FPGA sinyal üreteci doğru çalışıyor.

---

## 4. Toplam LFSR Hatalarının Karşılaştırması

Toplam hata sayısı double buffer ile belirgin şekilde azalıyor:

| Test | Driver | Mod | Sample | LFSR Hatası | Oran |
|------|--------|-----|--------|------------|------|
| 20 × 4096 | Modified | Single buffer | 2.560 | ~1.842 | %71,9 |
| **20 × 4096** | **Modified** | **Double buffer** | **2.560** | **19** | **%0,74** |
| **200 × 4096** | **Modified** | **Double buffer** | **25.600** | **399** | **%1,56** |
| 3277 × 4096 | Modified | Double buffer | 419.040 | 6.512 | %1,55 |
| 128 MB × 1 | Orijinal | Single (C aracı) | 4.194.304 | 19.307 | %0,46 |
| 128 MB × 1 | Modified | Double buffer | 4.194.304 | 18.442 | %0,44 |

**Double buffer, 20×4096 senaryosunda single buffer'a göre ~97× daha az hata üretiyor.**

---

## 5. plotsincos.py ile lfsr_analysis_utils Farkı

| Özellik | plotsincos.py | lfsr_analysis_utils |
|---------|--------------|---------------------|
| LFSR doğrulama | İlk 4096 sample atlar | Tüm sample'ları kontrol eder |
| Çıktı | PNG grafik + terminal özet | CSV skip raporu + PNG snapshot |
| Boundary analizi | ✗ | ✓ (4096-byte sınırında mı?) |
| Counter delta detayı | ✗ | ✓ (kaç sample atlandı) |
| FFT | ✓ | ✗ |

---

## 6. Üretilen Dosyalar

| Dosya | Konum |
|-------|-------|
| Ham veri | `data/outputs/20260611_164041_output/output_ch4_4096B_x200.bin` |
| SIN/COS/Counter grafik | `output_ch4_4096B_x200.sincos.png` |
| FFT grafik | `output_ch4_4096B_x200.fft.png` |
| Detaylı skip raporu | `output_ch4_4096B_x200.skip_report.csv` |
| İlk 8192 sample snapshot | `output_ch4_4096B_x200.snapshot.png` |
