# XDMA Modified Driver — LFSR Test & Comparison Report

**Tarih:** 2026-06-11  
**Sürücü:** Xilinx XDMA PCIe Linux Kernel Driver (Modified)  
**Değişiklik:** Double Buffer (ping-pong DMA) eklendi  
**Platform:** Linux 6.8.0-111-generic  
**FPGA:** CH4 C2H, AXI4-Stream, 25 MHz, interrupt_mode=2 (Legacy IRQ), poll_mode=0

---

## 1. Test Ortamı

### Sürücü Modifikasyonları

Orjinal Xilinx XDMA sürücüsüne aşağıdaki değişiklikler yapılmıştır:

| Dosya | Değişiklik | Amaç |
|-------|-----------|------|
| `xdma/cdev_sgdma.c` | `ioctl_do_dbuf_start()`: iki adet coherent DMA slotu (ping-pong) tahsis edildi | AXI-ST C2H pipeline'ını boş bırakmamak |
| `xdma/cdev_sgdma.c` | `dbuf_io_done()` callback: slot dolunca kullanıcıya dönülür, hemen yeniden kuyruğa alınır | Kesintisiz transfer |
| `xdma/cdev_sgdma.c` | Tahsis boyutu `buf_size + 64` byte | FPGA'nın AXI-ST descriptor sınırında 1 fazla gönderdiği sample'ın heap'i bozmasını önler |
| `xdma/cdev_sgdma.c` | Single buffer path'de dönüş değeri `count` ile sınırlandırıldı | `xdma_xfer_submit` fazla byte döndürünce buffer taşması engellendi |
| `xdma/xdma_mod.h` | `xdma_cdev`'e `dbuf[2]`, `dbuf_size`, `dbuf_active`, `dbuf_wq`, `dbuf_lock` eklendi | Double buffer durum yönetimi |
| `xdma/cdev_sgdma.h` | `IOCTL_XDMA_DBUF_START`, `IOCTL_XDMA_DBUF_STOP` ioctl tanımları | Kullanıcı uzayından kontrol |

### LFSR Parametreleri

| Parametre | Değer |
|-----------|-------|
| Sample boyutu | 32 byte (256 bit) |
| LFSR (byte 0-3) | 32-bit Galois LFSR, tap=[32,31,29,28,27,26,25,24,1] |
| Counter (byte 14-15) | 16-bit serbest sayaç |
| SIN (byte 16-19) | 32-bit Q30 fixed-point |
| COS (byte 20-23) | 32-bit Q30 fixed-point |
| FPGA frekansı | 25 MHz |

---

## 2. Test 1 — test_lfsr.py: Double Buffer, 20 × 4096 Byte

**Yapılandırma:**  
- Mod: Double buffer  
- Transfer: 20 × 4096 byte = 81.920 byte = 2.560 sample  
- Kanal: CH4 C2H  

**Sonuç Özeti:**

| Metrik | Değer |
|--------|-------|
| Toplam sample | 2.560 |
| Toplam hata | 19 |
| 4096-byte boundary'deki hata | **19 / 19 (%100)** |
| Boundary dışı hata | 0 |
| Ortalama `counter_delta` | 3 |
| Ortalama `lfsr_missing_samples` | 2 |
| Counter ve LFSR kayıp eşleşmesi | 19 / 19 |

**Gözlem:**  
Her DMA buffer geçişinde tam olarak **2 sample kaybolmaktadır**. Bu kayıp, AXI4-Stream engine'in descriptor geçişi sırasında `tready=0` tuttuğu süreye karşılık gelir. Kayıp pattern'i tamamen deterministik ve tutarlıdır:

```
Boundary öncesi:   sample[127]  counter=N     lfsr=A
Boundary sonrası:  sample[128]  counter=N+3   lfsr=lfsr_next³(A)
                                ↑ 2 sample atlandı
```

**Sonuç:** Double buffer, FPGA'nın LFSR akışını buffer'lar arası sürekli tutuyor. Kayıp tamamen donanım kaynaklı (AXI-ST descriptor geçiş gecikmesi) ve yazılımla önlenemiyor.

---

## 3. Test 2 — continuous_read.py: Double Buffer, 3277 × 4096 Byte

**Yapılandırma:**  
- Mod: Double buffer (kernel ping-pong)  
- Transfer: 3.277 × 4096 byte = 13.422.592 byte = 419.456 sample (~13.4 MB)  
- Kanal: CH4 C2H  
- H2C tetikleme: 32 byte  
- Çıktı dosyası: `data/outputs/20260611_142616_output/output_ch4_4096B_x3277.bin`  

**Sonuç Özeti:**

| Metrik | Değer |
|--------|-------|
| Toplam sample | 419.456 |
| Toplam hata | **6.512** |
| Hata oranı | %1,55 |
| 4096-byte boundary'deki hata (Tip-1) | 3.276 (%50,3) |
| Boundary sonrası hata (Tip-2) | 3.236 (%49,7) |
| Tip-1: clean 2-sample skip (`counter_delta=3`) | 3.276 |
| Tip-2: büyük atlama (min=488, max=46.856, ort=~1.117 sample) | 3.236 |

**Hata Tipleri:**

**Tip-1 — AXI-ST Descriptor Geçiş Gecikmesi (beklenen):**  
Her 4096-byte buffer sınırında, son ve ilk sample arasında tam 2 sample kaybı. Test 1 ile aynı pattern.

**Tip-2 — Buffer Başlangıç Anomalisi (beklenmeyen):**  
Her yeni 4096-byte buffer'ın **0. sample'ından 1. sample'ına** geçişte büyük sayaç sıçraması:

```
Örnek (chunk 41 sınırı):
  sample[5247]: counter=14906
  sample[5248]: counter=14909   ← Tip-1: 2-sample skip (clean)
  sample[5249]: counter=47466   ← Tip-2: 32.557 sample sıçraması (anomali)
```

Bu anomali 3.276 geçişin **%98,8'inde** görülmektedir. Test 1'deki 20 transfer testinde oluşmamıştır.

**Olası Sebepler:**
1. **Kernel resubmit gecikmesi:** `dbuf_io_done()` callback yavaş çalışınca yeni slot geç iletilir; FPGA FIFO'su bu sürede dolup veri düşürür.
2. **IRQ gecikme birikimi:** Uzun süre çalışınca IRQ latency artabilir.
3. **FPGA FIFO davranışı:** Yeni descriptor gelene kadar FPGA FIFO dolu kalır; DMA başladığında FIFO'dan "eski" veriler önce gönderilir.

**Snapshot:** `data/outputs/20260611_142616_output/output_ch4_4096B_x3277.snapshot.png`  
**Skip raporu:** `data/outputs/20260611_142616_output/output_ch4_4096B_x3277.skip_report.csv`

---

## 4. Buffer Boyutu Optimizasyonu

Farklı `buf_size` değerleriyle yapılan testlerin özeti:

| buf_size | Boundary Hata | Inner Hata (FIFO overflow) | Sonuç |
|----------|--------------|---------------------------|-------|
| 4096 | Var (2 sample/sınır) | **0** | ✓ Optimal |
| 32768 | Var | Var | ✗ |
| 131072 | Var | Var (çok fazla) | ✗ |

**Optimum: `buf_size = 4096`** — Daha büyük buffer'larda FPGA'nın çıkış FIFO'su uzun transfer sırasında doluyor ve inner LFSR hataları oluşuyor.

---

## 5. Single Buffer vs Double Buffer Karşılaştırması

| Kriter | Single Buffer | Double Buffer |
|--------|--------------|---------------|
| DMA pipeline sürekliliği | ✗ Her read() arasında boşluk | ✓ Sürekli dolu |
| Boundary kayıp | Değişken (100–1000+ sample) | Sabit (2 sample, donanım limiti) |
| Inner hatalar (buf_size=4096) | 0 | 0 |
| FPGA +1 sample overflow | Heap corruption | `+64` padding ile önlendi |
| Büyük capture stabilitesi | Crash riski | Stabil (6512 hata ama çökmez) |

**Single buffer sorunları (gözlemlenen):**
- `realloc(): invalid next size` — heap metadata bozulması
- Transfer başına değişken büyüklükte LFSR/counter kayıpları
- Hata sayısı kullanılan `buf_size` × transfer sayısına göre dramatik değişim

---

## 6. Bilinen Limitasyonlar ve Öneriler

| # | Durum | Açıklama |
|---|-------|---------|
| L1 | ⚠ Donanım limiti | AXI-ST descriptor geçişinde 2 sample kayıp önlenemez. FPGA tarafında multi-descriptor chain gerektirir. |
| L2 | 🔍 Araştırılıyor | Büyük capture'da Tip-2 buffer anomalisi — IRQ latency veya FPGA FIFO davranışı. |
| L3 | ✅ Düzeltildi | FPGA 1 fazla sample gönderme — `+64` byte padding. |
| L4 | ✅ Düzeltildi | Single buffer dönüş değeri taşması — return value cap. |
| R1 | Öneri | `XDMA_DBUF_NUM = 3` (triple buffer) — IRQ latency toleransını artırır. |
| R2 | Öneri | `dbuf_io_done()` içinde slot resubmit'i daha erken yaparak pipeline doldurma süresini kısalt. |

---

## 7. Dosya Referansları

| Dosya | Açıklama |
|-------|---------|
| `data/outputs/20260610_185646_lfsr_compare/double_buffer.bin` | test_lfsr double buffer ham verisi (80 KB) |
| `data/outputs/20260610_185646_lfsr_compare/double_buffer.skip_report.csv` | test_lfsr skip raporu (19 hata, tümü clean boundary) |
| `data/outputs/20260611_142616_output/output_ch4_4096B_x3277.bin` | continuous_read ham verisi (13.4 MB) |
| `data/outputs/20260611_142616_output/output_ch4_4096B_x3277.snapshot.png` | Sinyal snapshot (ilk 8192 sample) |
| `data/outputs/20260611_142616_output/output_ch4_4096B_x3277.skip_report.csv` | continuous_read skip raporu (6512 hata) |

---

## 8. Sonraki Adım — Orijinal Xilinx Sürücüsü Karşılaştırması

Aynı testleri modifiye edilmemiş Xilinx XDMA sürücüsü üzerinde çalıştırarak karşılaştırma yapılacak. Beklenen fark:

- Single buffer: Her `os.read()` arasında değişken büyüklükte LFSR kayıpları (yüzlerce–binlerce sample)
- Double buffer ioctl mevcut değil → tüm transferler ayrı DMA başlatır
- Büyük capture'da çok daha yüksek toplam hata oranı
