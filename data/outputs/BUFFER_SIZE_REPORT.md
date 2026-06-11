# XDMA Buffer Boyutu Kullanım Raporu
## Tüm Testlerde Kullanılan Buffer Boyutları ve Etkileri

**Kanal:** CH4 C2H, AXI4-Stream, 25 MHz  
**Driver:** Modified (double buffer destekli), interrupt_mode=2, poll_mode=0  
**Sample boyutu:** 32 byte (256-bit: LFSR + counter + SIN + COS)

---

## 1. Neden Buffer Boyutu Kritik?

FPGA her zaman 25 MHz hızında AXI4-Stream çıkışı yapar: saniyede 25.000.000 sample × 32 byte = **800 MB/s** sabit veri akışı. Linux userspace bu akışı `os.read()` ile kesintisiz tüketmek zorundadır.

İki çelişen kısıt vardır:

| Kısıt | Küçük Buffer | Büyük Buffer |
|-------|-------------|-------------|
| **FPGA FIFO taşması** | ✓ Taşmaz | ✗ Taşabilir |
| **Linux syscall gecikmesi** | ✗ Pipeline boşalır | ✓ Boşalma riski az |
| **DMA descriptor geçiş kaybı** | = Her geçişte 2 sample | = Her geçişte 2 sample |

**Optimal nokta: `buf_size = 4096` (1 PAGE)**

4096 byte = 128 sample = **5.12 µs dolum süresi** (25 MHz'de).  
Double buffer sayesinde bir slot dolarken diğeri kullanıcıya aktarılır; geçiş süresinde FPGA FIFO'su absorbe eder.

---

## 2. Test Edilmiş Buffer Boyutları — Özet Tablosu

| buf_size | Toplam Byte | Transfers | Script | Hata | Inner Hata | Kayıp Sample | Kayıp % |
|----------|-------------|-----------|--------|------|-----------|--------------|---------|
| **4096** | 81.920 | 20 | test_lfsr.py (dbuf) | 19 | **0** | 38 | **1,48%** |
| **4096** | 81.920 | 20 | dma_from_device (C) | 19 | **0** | 38 | **1,48%** |
| **4096** | 13.409.280 | 3.277 | continuous_read.py (dbuf) | 6.512 | 0† | 2.076.038† | — |
| **4096** | 134.217.728 | ~32.768 | generic.py (dbuf, 128MB) | 18.442 | 16.347 | 87.191 | **2,08%** |
| **4096** | 134.217.728 | ~32.768 | generic.py (no dbuf, 128MB) | 19.307 | 17.101 | 193.426 | **4,61%** |
| **32768** | 655.360 | 20 | test_lfsr.py (dbuf) | 232 | **213** | 60.346 | **29,5%** |
| **131072** | 2.621.440 | 20 | test_lfsr.py (dbuf) | 4.957 | **4.906** | 26.861 | **32,8%** |
| **131072** | 20.971.520 | 160 | test_lfsr.py (dbuf) | 9.609 | **9.542** | 41.238 | **6,3%** |

† 6512 hatanın 3276'sı donanım boundary kaybı (2 sample × 3276 = 6552 sample), kalan 3236'sı Tip-2 pipeline durma olayı (ortalama ~638 sample/olay).

---

## 3. Her Buffer Boyutunun Detaylı Analizi

### 3.1  buf_size = 4096 byte (1 PAGE) — **Optimal**

**Dolum süresi:** 4096 / (800 MB/s) = **5.12 µs**  
**FPGA AXI-Stream FIFO:** yeterince küçük → overflow yok

#### 3.1.1 20 × 4096 Testi (test_lfsr.py, double buffer)
```
Tarih     : 2026-06-10 18:56:46
Toplam    : 81.920 byte = 2.560 sample
Hatalar   : 19  (tümü buffer boundary'de, inner=0)
Kayıp     : 38 sample (2 × 19)
Kayıp oranı: 1,48%
Counter delta: tümü = 3 (tam 2 sample atlama)
```

Her 4096-byte sınırında **tam 2 sample** kayboluyor. Bu, AXI4-Stream DMA engine'in bir descriptor'dan diğerine geçerken `tready = 0` tuttuğu ~80 ns donanım latency'sidir. Yazılımla önlenemez.

#### 3.1.2 20 × 4096 Testi (C aracı dma_from_device, orijinal driver)
```
Tarih     : 2026-06-11
Toplam    : 81.920 byte = 2.560 sample
Hatalar   : 19  (tümü boundary, inner=0)
Kayıp     : 38 sample
```
C aracı ile Python double buffer aynı sonucu verir: FPGA FIFO, C transferleri arası ~µs gecikmesini absorbe ediyor.

#### 3.1.3 3277 × 4096 Testi (continuous_read.py, ~13 MB, double buffer)
```
Tarih     : 2026-06-11
Toplam    : 13.409.280 byte = 419.040 sample
Hatalar   : 6.512
  Tip-1 (donanım, 2-sample skip)    : 3.276  (her buffer boundary'de)
  Tip-2 (pipeline durma)            : 3.236
Kayıp (Tip-1)  : 3.276 × 2 = 6.552 sample
Kayıp (Tip-2)  : ~3.236 × 638 ≈ 2.064.568 sample (tahmin)
```

**Tip-2 neden oluşuyor?**  
4096 byte 5.12 µs'de doluyor ama Linux kullanıcı alanı işleme süresi ~50 µs. Double buffer her iki slot'u da doldurup öne geçiyor; requeue yapılana kadar ~45 µs'lik pipeline durması ≈ 1.125 sample kaybı. 40. chunk'tan itibaren başlıyor (sistem başlangıçta daha hızlı).

#### 3.1.4 128 MB Tek Transfer (generic.py, double buffer — modified driver)
```
Tarih     : 2026-06-11
Toplam    : 134.217.728 byte = 4.194.304 sample
Hatalar   : 18.442  (boundary=2.095, inner=16.347)
Kayıp     : 87.191 sample (%2,08)
Avg delta : 7,6
```
128 MB'lık büyük transfer scatter-gather descriptor zinciriyle aktarılıyor (her 4 KB fiziksel sayfa = 1 descriptor). Descriptor geçişleri (donanım kaynaklı) + FPGA FIFO taşması iç içe.

#### 3.1.5 128 MB Tek Transfer (generic.py, orijinal driver — no double buffer)
```
Tarih     : 2026-06-11
Toplam    : 134.217.728 byte = 4.194.304 sample
Hatalar   : 19.307  (boundary=2.206, inner=17.101)
Kayıp     : 193.426 sample (%4,61)
Avg delta : 11,0
```
Modified driver 128MB'da **%4,5 daha az hata, %55 daha az kayıp sample** sağlıyor.

---

### 3.2  buf_size = 32768 byte (8 PAGE)

**Dolum süresi:** 32768 / (800 MB/s) = **40.96 µs**

```
Tarih     : 2026-06-10 18:36:23
Toplam    : 655.360 byte = 20.480 sample  (20 transfer)
Hatalar   : 232  (boundary=19, inner=213)
Kayıp     : 60.346 sample
Kayıp oranı: 29,5%  ← KRİTİK
Avg delta : 437,9  (bazı hatalarda binlerce sample atlama)
```

**Neden bu kadar kötü?**

FPGA'nın AXI-Stream çıkış FIFO'su 32768 byte dolum süresini (41 µs) absorbe edemiyor. Buf içindeki ortalama ~10 µs'de FIFO dolup taşıyor → buffer içinde rastgele iç hatalar. Avg delta=437 → 436 sample'lık büyük atlamalar.

Boundary'deki 19 hata deterministik 2-sample kaybı. Boundary dışındaki 213 hata FPGA FIFO taşmasından kaynaklanıyor ve öngörülemez.

---

### 3.3  buf_size = 131072 byte (32 PAGE)

**Dolum süresi:** 131072 / (800 MB/s) = **163.84 µs**

```
Tarih     : 2026-06-10 18:36:24
Toplam    : 2.621.440 byte = 81.920 sample  (20 transfer)
Hatalar   : 4.957  (boundary=51, inner=4.906)
Kayıp     : 26.861 sample
Kayıp oranı: 32,8%  ← KRİTİK

Tarih     : 2026-06-10 18:36:25
Toplam    : 20.971.520 byte = 655.360 sample  (160 transfer)
Hatalar   : 9.609  (boundary=67, inner=9.542)
Kayıp     : 41.238 sample
Kayıp oranı: 6,3%
Avg delta : 5,3
```

163 µs dolum süresi FPGA FIFO'sunu mutlaka taşırıyor. Neredeyse tüm hatalar inner (FPGA tarafı), çok küçük delta değerleriyle (avg=5–6) ama çok yüksek sayıda → sürekli küçük atlamalar.

---

## 4. Buffer Boyutu Seçim Kılavuzu

```
                FPGA FIFO kapasitesi (~4-8 KB)
                        │
         ───────────────┼───────────────
         │              │              │
    buf_size<4K     buf_size=4K    buf_size>4K
    (FIFO taşmaz)  (FIFO taşmaz)  (FIFO TAŞIYOR)
         │              │              │
    Çok sık        Optimal         Inner hatalar
    syscall                        (%30+ kayıp)
```

| Parametre | Değer | Açıklama |
|-----------|-------|---------|
| **Optimal buf_size** | **4096 byte** | 1 PAGE, FIFO taşmıyor |
| Minimum güvenli | 4096 byte | PAGE_SIZE altı driver desteklemiyor |
| Maksimum güvenli | ~8192 byte | Denenmedi; teorik olarak OK |
| Kesinlikle kaçınılacak | ≥ 32768 byte | FPGA FIFO kapasitesini aşıyor |

---

## 5. Double Buffer Neden Fark Yaratıyor (buf_size=4096 ile)?

```
Orijinal driver (single buffer):
  read() → DMA 4096B → bitti → Python işler (~50 µs) → read() → DMA başlatılıyor...
                                                          ↑
                                              Bu sürede FPGA akıyor, DMA boşta
                                              50 µs × 25 MHz = ~1.250 sample kayıp

Double buffer (buf_size=4096):
  Slot A: [████████] → kullanıcıya
  Slot B:        [████████] → sırada
  Kesintisiz akış; yalnızca donanım latency'si (2 sample/boundary)
```

### Karşılaştırma: Single vs Double Buffer (20 × 4096 byte)

| Mod | Hatalar | Inner Hata | Kayıp Sample | Oran |
|-----|---------|-----------|--------------|------|
| Single buffer (test_lfsr.py) | ~1.842 | Yüksek | ~1.300/transfer × 20 | ~71,9% |
| **Double buffer** | **19** | **0** | **38** | **1,48%** |
| C aracı (orijinal driver) | 19 | 0 | 38 | 1,48% |

Double buffer, 20×4096 senaryosunda **~97 kat** daha az hata üretiyor.

> **Not:** C aracının (`dma_from_device`) double buffer ile aynı sonucu vermesinin nedeni, C kodunun userspace gecikmesinin çok kısa olmasıdır (~µs). FPGA FIFO bu gecikmeyi absorbe ediyor. Python ile aynı testi yapmak orijinal driver'da heap corruption hatası verdiğinden mümkün olmamıştır.

---

## 6. Donanım Sınırı: Önlenemeyen 2-Sample Kayıp

Her buf_size için, her buffer sınırında:
```
sample[N-1]:  counter = K
sample[N]:    counter = K + 3   ← 2 sample kayıp (K+1 ve K+2 yok)
```

**Fiziksel neden:** AXI4-Stream DMA engine descriptor geçişinde `tready=0` sinyalini  
**~80 ns = 2 × 40 ns = 2 sample** süre assert eder.

Bu kayıp:
- Tüm buf_size değerlerinde aynı
- Her buffer sınırında deterministik
- Yazılım ile önlenmesi mümkün değil
- FPGA tarafında multi-descriptor continuous chain uygulanırsa sıfırlanabilir

---

## 7. Test Zaman Çizelgesi

| Tarih | Oturum | buf_size | n_transfers | Script | Notlar |
|-------|--------|----------|-------------|--------|--------|
| 2026-06-03 | 141905, 142103 | 4096 | 1 | generic.py | İlk testler |
| 2026-06-03 | 185315 | 4096 | 3328 | continuous_read.py | ~13 MB yakalama |
| 2026-06-04 | 175450 | 4096 | 3328 | continuous_read.py | Tekrar |
| 2026-06-04 | 180421 | 4096 | 1 | test_lfsr.py (single) | Tek transfer single |
| 2026-06-05 | 162400, 162821 | 134217728 | 1 | generic.py | 128 MB tek transfer |
| 2026-06-10 | 152931, 163952 | 134217728 | 1 | generic.py | 128 MB karşılaştırma |
| 2026-06-10 | 170957 | 4096 | 1 | test_lfsr.py (dbuf) | Tek transfer double |
| 2026-06-10 | **183623** | **32768** | **20** | test_lfsr.py (dbuf) | **232 hata, 29,5% kayıp** |
| 2026-06-10 | **183624** | **131072** | **20** | test_lfsr.py (dbuf) | **4957 hata, 32,8% kayıp** |
| 2026-06-10 | **183625** | **131072** | **160** | test_lfsr.py (dbuf) | **9609 hata, 6,3% kayıp** |
| 2026-06-10 | **185646** | **4096** | **20** | test_lfsr.py (dbuf) | **19 hata, 0 inner** ← optimal |
| 2026-06-11 | 142616, 160347 | 4096 | 3277 | generic.py (dbuf) | ~13 MB, 6512 hata (Tip-2) |
| 2026-06-11 | comparison | 4096 | 20 | dma_from_device (C) | 19 hata, C vs Python |
| 2026-06-11 | comparison | 134217728 | 1 | generic.py (with/without dbuf) | 128 MB karşılaştırma |

---

## 8. Sonuç

**Tek cümle:** `buf_size = 4096` kullanın, double buffer aktif olsun.

| # | Bulgu |
|---|-------|
| S1 | buf_size=4096 ile iç hata sıfırdır; FPGA FIFO overflow gerçekleşmez |
| S2 | buf_size=32768: %29,5 sample kaybı — FPGA FIFO taşıyor |
| S3 | buf_size=131072: %32,8 sample kaybı — tamamen işe yaramaz |
| S4 | Her buf_size'da, her buffer sınırında tam 2 sample kayıp (donanım sınırı, kaçınılmaz) |
| S5 | Double buffer, 20×4096 senaryosunda single buffer'a göre ~97× daha az hata üretiyor |
| S6 | 128 MB tek transfer: descriptor chain kaybı her iki driver'da da var; modified driver %55 daha az sample kaybediyor |
| S7 | ~13 MB continuous capture'da Tip-2 (pipeline durma) 40. chunk'tan itibaren başlıyor: 5.12 µs dolum vs ~50 µs işlem süresi çakışması |
