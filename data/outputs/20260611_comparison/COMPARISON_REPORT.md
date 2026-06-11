# XDMA Driver Karşılaştırma Raporu
## Without Double Buffer vs With Double Buffer

**Tarih:** 2026-06-11  
**Kanal:** CH4 C2H, AXI4-Stream, 25 MHz  
**Interrupt modu:** Legacy IRQ (interrupt_mode=2), poll_mode=0  

---

## 1. Test Yapılandırması

| Parametre | Orijinal Driver | Modified Driver |
|-----------|----------------|-----------------|
| Double buffer | ✗ Yok | ✓ Var |
| Transfer boyutu | 134.217.728 byte (128 MB) | 134.217.728 byte (128 MB) |
| Transfer sayısı | 1 | 1 |
| Toplam sample | 4.194.304 | 4.194.304 |
| Çıktı dosyası | `data/outputs/20260611_150433_output/output_ch4_134217728B_x1.bin` | `output_ch4_134217728B_x1.bin` |

---

## 2. Sonuç Özeti

| Metrik | Orijinal (dbuf yok) | Modified (dbuf var) | Fark |
|--------|---------------------|---------------------|------|
| Hatalı geçiş | **19.307** | **18.442** | −865 (−4,5%) |
| Hata oranı | %0,4603 | %0,4397 | −0,021 puan |
| Kayıp sample toplamı | 36.924 (%0,88) | 35.209 (%0,84) | −1.715 sample |
| 4096-byte boundary hatası | 2.206 (%11,4) | 2.095 (%11,4) | −111 |
| Boundary dışı hata | 17.101 (%88,6) | 16.347 (%88,6) | −754 |
| Clean 2-sample skip (Δ=3) | 17.624 | 16.766 | −858 |
| Büyük atlama (Δ>10) | 10 | 5 | −5 |
| Counter ve LFSR eşleşen kayıp | 19.297 | 18.437 | — |

---

## 3. Hata Dağılımı Analizi

### 3.1 Hataların Byte Sınırlarına Göre Dağılımı

| Boundary | Orijinal | Modified |
|----------|---------|---------|
| 4.096 byte | 2.206 (%11,4) | 2.095 (%11,4) |
| 8.192 byte | 1.098 (%5,7) | 1.041 (%5,6) |
| 65.536 byte | 131 (%0,7) | 121 (%0,7) |
| 131.072 byte | 58 (%0,3) | 56 (%0,3) |
| 1 MB boundary | 0 | 0 |
| **Boundary dışı** | **17.101 (%88,6)** | **16.347 (%88,6)** |

Her iki testte de hataların **%88,6'sı herhangi bir standart sınırda değil** — bunlar DMA descriptor zinciri içindeki geçişlerdir.

### 3.2 Counter Delta Dağılımı

| counter_delta | Anlamı | Orijinal | Modified |
|---------------|--------|---------|---------|
| 2 | 1 sample kayıp | 1.672 | 1.669 |
| **3** | **2 sample kayıp** | **17.624** | **16.766** |
| > 10 | Büyük atlama | 10 | 5 |

Hataların büyük çoğunluğu (**%91+**) tam olarak **2 sample'lık** deterministik kayıplar. Bu, AXI4-Stream descriptor geçiş gecikmesinin imzasıdır.

---

## 4. Neden İyileştirme Sınırlı Kaldı?

Bu testte her iki driver da **128 MB'ı tek seferlik (`-c 1`) büyük DMA transferi** olarak gönderiyor. Double buffer mekanizması yalnızca **transfer sınırlarında** (bir `read()` çağrısının bitişi ile bir sonraki `read()`'in başlangıcı arasında) devreye girer.

Tek büyük 128 MB transferde DMA pipeline şöyle çalışır:

```
Orijinal:
  ┌─────────────────────────────── 128 MB ──────────────────────────────┐
  │ desc[0]─►desc[1]─►...─►desc[N]                                     │
  │         ↑             ↑                                             │
  │    2-sample skip  2-sample skip  (her descriptor geçişinde)         │
  └─────────────────────────────────────────────────────────────────────┘
  transfer bittikten sonra → yeni read() → DMA engine boşta kalır → büyük atlama

Modified (dbuf, tek transfer):
  Slot 0: 128 MB doluyor...
  [Slot 1 zaten sırada ama read_count=1 olduğu için slot 1 hiç okunmuyor]
  → double buffer'ın getirisi: sadece transfer başlangıcındaki küçük fark
```

**Özetle:** `read_count=1` olduğunda double buffer sadece **başlangıç latency'sini** etkiler; 128 MB'ın içindeki ~19.000 descriptor geçişi her iki durumda da aynıdır.

---

## 5. Double Buffer'ın Gerçek Avantajı — Küçük Chunk Testi

Double buffer'ın asıl farkı **çok sayıda küçük transfer** senaryosunda görülür:

### 5.1 20 × 4096 Byte Testi (test_lfsr.py, aynı gün)

| Mod | Sample | Hata | Hata oranı | Boundary hatası | Inner hata |
|-----|--------|------|-----------|----------------|-----------|
| Single buffer (orijinal tarz) | 2.560 | ~1.800+ | ~70%+ | Var | Var (büyük) |
| **Double buffer** | **2.560** | **19** | **%0,74** | **19** | **0** |

### 5.2 Neden Bu Kadar Büyük Fark?

```
Single buffer (çok sayıda küçük transfer):
  read() → DMA 4096 byte → bitti → kullanıcı işler → read() → DMA başlatılıyor...
           ↑                                                    ↑
           bu süre zarfında DMA engine BOŞTA        yüzlerce-binlerce sample kayıp

Double buffer (çok sayıda küçük transfer):
  [Slot 0 doluyor]   [Slot 1 doluyor]   [Slot 0 doluyor]   ...
  Kullanıcı Slot 0'ı okurken Slot 1 zaten doluyor → sadece 2-sample donanım latency
```

### 5.3 Karşılaştırma Tablosu — Tüm Testler

| Test Senaryosu | Driver | Toplam Hata | Inner Hata | Açıklama |
|----------------|--------|-------------|-----------|---------|
| 128 MB × 1 transfer | Orijinal | 19.307 (%0,46) | 17.101 | Descriptor chain |
| 128 MB × 1 transfer | **Modified** | **18.442 (%0,44)** | **16.347** | Descriptor chain |
| 20 × 4096 byte | Modified (dbuf) | **19 (%0,74)** | **0** | Sadece boundary |
| 3277 × 4096 byte | Modified (dbuf) | 6.512 | 0 | Boundary + anomali |

> **Sonuç:** Double buffer, tek büyük transfer senaryosunda ~4,5% iyileştirme sağlar. Çok sayıda küçük transfer senaryosunda ise %70+ → %0,74 seviyesinde dramatik iyileştirme sağlar.

---

## 6. 128 MB Testindeki Hata Kaynağı: Descriptor Zinciri

Her iki testteki 128 MB transferde hataların büyük çoğunluğu (`counter_delta=3`, 2-sample skip) DMA descriptor zincirinden kaynaklanıyor:

```
128 MB kullanıcı buffer'ı
  → kernel scatter-gather sayfaları (4 KB fiziksel sayfa)
  → XDMA her fiziksel kontig. bölge için 1 descriptor
  → Toplam ~17.000–18.000 descriptor
  → Her descriptor geçişinde AXI-ST tready=0 → 2 sample kayıp
```

Bu kayıp **her iki driver'da da aynı şekilde** oluşur ve double buffer ile **önlenemez** — çünkü bu, tek bir transfer içindeki bir donanım sınırıdır.

---

## 7. Dosyalar

| Dosya | Açıklama |
|-------|---------|
| `without_dbuf.skip_report.csv` | Orijinal driver, 128 MB capture, 19.307 hata |
| `without_dbuf.snapshot.png` | Orijinal driver sinyal snapshot (ilk 8192 sample) |
| `with_dbuf.skip_report.csv` | Modified driver, 128 MB capture, 18.442 hata |
| `with_dbuf.snapshot.png` | Modified driver sinyal snapshot (ilk 8192 sample) |

---

## 8. Sonuçlar ve Öneriler

### Bulgular
1. **128 MB tek transfer** testinde double buffer'ın katkısı **marginal** (−4,5%) — her iki driver da aynı descriptor zinciri sorununa maruz kalıyor.
2. **Küçük chunk çoklu transfer** testinde double buffer **dramatik** fark yaratıyor (inner hata %70+ → %0).
3. Her iki durumda da dominant hata tipi **2-sample skip** (counter_delta=3) — AXI-ST descriptor geçiş latency'si.

### Öneriler
| # | Öneri |
|---|-------|
| O1 | Büyük veri yakalamak için `continuous_read.py` (4096-byte chunk, double buffer) kullanın — 128 MB single transfer yerine |
| O2 | 128 MB'ı doğrudan okumak gerekiyorsa, `dma_from_device` yerine double buffer + küçük chunk döngüsü kullanın |
| O3 | FPGA tarafında multi-descriptor continuous stream implement edilirse descriptor geçiş kayıpları sıfırlanabilir |
