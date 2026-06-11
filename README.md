# xdma-fpga — Xilinx XDMA PCIe Driver with Double Buffer

Bu repo, Xilinx'in resmi XDMA PCIe DMA Linux kernel sürücüsünü temel alır ve üzerine **Double Buffer (ping-pong DMA)** mekanizması ekler. Amaç, FPGA'dan gelen AXI4-Stream veri akışını (LFSR, SIN, COS) kayıpsız ve sürekli olarak host'a aktarmaktır.

---

## İçindekiler

- [Proje Amacı](#proje-amacı)
- [LFSR Veri Akışı](#lfsr-veri-akışı)
- [Sorun: Tek Buffer'da Veri Kaybı](#sorun-tek-bufferda-veri-kaybı)
- [Çözüm: Double Buffer (Ping-Pong DMA)](#çözüm-double-buffer-ping-pong-dma)
- [Yapılan Kod Değişiklikleri](#yapılan-kod-değişiklikleri)
- [Kullanım](#kullanım)
- [Test Sonuçları](#test-sonuçları)
- [Dosya Yapısı](#dosya-yapısı)

---

## Proje Amacı

FPGA, PCIe üzerinden 25 MHz hızında AXI4-Stream formatında sürekli veri üretiyor:

- **LFSR** (Linear Feedback Shift Register) — 32-bit pseudo-random sequence, sürekliliği test için kullanılır
- **Counter** — 16-bit monoton sayaç, kaç sample'ın kaybolduğunu ölçer
- **SIN / COS** — 32-bit Q30 fixed-point sinüs ve kosinüs sinyalleri

Her sample **32 byte** (256 bit), FPGA 25 MHz'de üretiyor → bant genişliği ≈ 800 MB/s.

Hedef: Bu akışı host'ta kesintisiz yakalamak, veri kaybını minimize etmek.

---

## LFSR Veri Akışı

Her 32-byte sample'ın byte düzeni:

```
Byte  0- 3 : LFSR (32-bit, little-endian)
Byte  4-13 : (reserved / diğer sinyaller)
Byte 14-15 : Counter (16-bit, little-endian)
Byte 16-19 : SIN (32-bit Q30, little-endian)
Byte 20-23 : COS (32-bit Q30, little-endian)
Byte 24-31 : (reserved)
```

LFSR'ın tap pozisyonları: `[32, 31, 29, 28, 27, 26, 25, 24, 1]`

Bir sonraki LFSR değeri şöyle hesaplanır:

```python
def lfsr_next(lfsr, taps=[32,31,29,28,27,26,25,24,1]):
    lfsr &= 0xFFFFFFFF
    feedback = 0
    for tap in taps:
        feedback ^= (lfsr >> (tap - 1)) & 1
    return ((lfsr << 1) | feedback) & 0xFFFFFFFF
```

Eğer alınan `sample[i+1].lfsr != lfsr_next(sample[i].lfsr)` ise veri kaybı var demektir.

---

## Sorun: Tek Buffer'da Veri Kaybı

Orijinal sürücüde, kullanıcı uzayından her `read()` çağrısı **ayrı bir DMA transferi** başlatır:

```
read() → DMA transfer 0 başlat → bekleniyor... → veri geldi → kullanıcıya kopyala
read() → DMA transfer 1 başlat → bekleniyor... → veri geldi → kullanıcıya kopyala
         ↑                                           ↑
         bu süre zarfında DMA engine boşta           FPGA LFSR durmadan akıyor
```

Transfer 0 bittikten sonra transfer 1 kuyruğa alınana kadar DMA engine boşta kalır.
Bu süre zarfında FPGA'nın AXI-Stream FIFO'su dolar ve **yüzlerce–binlerce sample** düşer.

### Ek Sorun: FPGA +1 Sample Overflow

AXI4-Stream modunda FPGA, descriptor sınırında istenen boyuttan **1 sample (32 byte) fazla** gönderebilir. Bu, kullanıcı buffer'ının bitişik bellek alanını bozar ve `malloc(): corrupted top size` hatasına yol açar.

---

## Çözüm: Double Buffer (Ping-Pong DMA)

İki adet kernel DMA buffer'ı (slot) **her zaman eş zamanlı** DMA engine kuyruğunda tutulur:

```
Başlangıç:
  Slot 0 ──► DMA engine kuyruğu  (doluyor...)
  Slot 1 ──► DMA engine kuyruğu  (sırada bekliyor)

Slot 0 dolunca (dbuf_io_done callback):
  Slot 0 ──► kullanıcı okur      (copy_to_user)
  Slot 1 ──► DMA engine kuyruğu  (doluyor...)   ← pipeline boşalmadı!
  Slot 0 ──► hemen yeniden kuyruğa eklendi      (sırada bekliyor)

Slot 1 dolunca:
  Slot 1 ──► kullanıcı okur
  Slot 0 ──► DMA engine kuyruğu  (doluyor...)
  Slot 1 ──► hemen yeniden kuyruğa eklendi
```

Sonuç: DMA engine **hiçbir zaman boşta kalmaz** → FPGA LFSR kesintisiz akar.

### Donanım Limiti: 2-Sample Boundary Kaybı

AXI4-Stream DMA engine, descriptor geçişi sırasında `tready=0` sinyalini yaklaşık **2 sample** (~80 ns @ 25 MHz) süre boyunca assert eder. Bu sürede FPGA veri üretmeye devam ettiğinden tam olarak **2 sample** kaybolur. Bu kayıp yazılımla önlenemez; FPGA tarafında multi-descriptor chain kullanılarak azaltılabilir.

---

## Yapılan Kod Değişiklikleri

### 1. `xdma/xdma_mod.h` — Yeni Veri Yapıları

```c
#define XDMA_DBUF_NUM 2   /* ping-pong slot sayısı */

struct xdma_dbuf_slot {
    void                *virt;   /* CPU erişim adresi */
    dma_addr_t           bus;    /* DMA (PCIe) adresi */
    size_t               size;   /* buf_size (byte) */
    struct sg_table      sgt;    /* scatter-gather tablosu */
    struct xdma_io_cb    cb;     /* DMA tamamlanma callback */
    volatile int         filled; /* 1 = veri hazır */
};
```

`xdma_cdev` yapısına eklenenler:

```c
struct xdma_dbuf_slot  dbuf[XDMA_DBUF_NUM];
size_t                 dbuf_size;
u64                    dbuf_ep_addr;
int                    dbuf_read_idx;    /* kullanıcı hangi slot'u okuyacak */
wait_queue_head_t      dbuf_wq;          /* okuma bekleme kuyruğu */
spinlock_t             dbuf_lock;
bool                   dbuf_active;
```

### 2. `xdma/cdev_sgdma.h` — IOCTL Tanımları

```c
#define XDMA_DBUF_V1 1

struct xdma_dbuf_ioctl {
    __u32 version;    /* XDMA_DBUF_V1 */
    __u32 buf_size;   /* her slot'un byte cinsinden boyutu */
    __u64 ep_addr;    /* AXI-ST'de kullanılmaz, 0 geçin */
};

#define IOCTL_XDMA_DBUF_START  _IOW('q', 9,  struct xdma_dbuf_ioctl *)
#define IOCTL_XDMA_DBUF_STOP   _IO ('q', 10)
```

### 3. `xdma/cdev_sgdma.c` — Ana Değişiklikler

#### a) `ioctl_do_dbuf_start()` — Buffer Tahsisi

```c
/* AXI-ST'de FPGA descriptor sınırında 1 sample (32 byte) fazla gönderebilir.
 * buf_size + 64 byte ayırarak taşmanın bitişik kernel belleğine yazmasını önlüyoruz. */
xcdev->dbuf[i].virt = dma_alloc_coherent(&pdev->dev,
    io.buf_size + 64,
    &xcdev->dbuf[i].bus, GFP_KERNEL);
```

İki slot tahsis edildikten sonra her ikisi de `dbuf_submit_slot()` ile DMA engine kuyruğuna eklenir.

#### b) `dbuf_io_done()` — DMA Tamamlanma Callback'i (IRQ bağlamı)

```c
static void dbuf_io_done(unsigned long cb_hndl, int err)
{
    struct xdma_cdev *xcdev = ...;
    int idx = ...;   /* hangi slot doldu */

    xcdev->dbuf[idx].filled = 1;
    wake_up(&xcdev->dbuf_wq);   /* okuyan thread'i uyandır */
}
```

#### c) `char_sgdma_read()` — Ping-Pong Okuma

```c
if (xcdev->dbuf_active) {
    int idx = xcdev->dbuf_read_idx;

    /* Slot dolana kadar bekle */
    wait_event(xcdev->dbuf_wq, xcdev->dbuf[idx].filled || !xcdev->dbuf_active);

    /* Veriyi kullanıcıya kopyala */
    copy_to_user(buf, xcdev->dbuf[idx].virt, count);

    /* Slot'u sıfırla ve hemen yeniden kuyruğa al */
    xcdev->dbuf[idx].filled = 0;
    dbuf_submit_slot(xcdev, idx);

    /* Diğer slot'a geç */
    xcdev->dbuf_read_idx ^= 1;
}
```

#### d) `ioctl_do_dbuf_stop()` ve `release()` — Temizlik

```c
/* Tahsis boyutu buf_size + 64 olduğu için free de aynı boyutla yapılmalı */
dma_free_coherent(&pdev->dev,
    xcdev->dbuf[i].size + 64,
    xcdev->dbuf[i].virt,
    xcdev->dbuf[i].bus);
```

#### e) Single Buffer Overflow Koruması

```c
/* xdma_xfer_submit AXI-ST'de fazla byte döndürebilir, count ile sınırla */
if (res > 0 && (size_t)res > count)
    res = (ssize_t)count;
```

### 4. `generic.py` — Python IOCTL Sarmalayıcı

```python
_XDMA_DBUF_STRUCT = struct.Struct("=IIQ")  # version, buf_size, ep_addr

def dbuf_start(fd, buf_size, ep_addr=0):
    payload = _XDMA_DBUF_STRUCT.pack(XDMA_DBUF_V1, buf_size, ep_addr)
    fcntl.ioctl(fd, IOCTL_XDMA_DBUF_START, payload)

def dbuf_stop(fd):
    fcntl.ioctl(fd, IOCTL_XDMA_DBUF_STOP, 0)
```

---

## Kullanım

### Gereksinimler

```bash
# Kernel başlıkları
sudo apt install linux-headers-$(uname -r) build-essential

# Python kütüphaneleri
pip3 install numpy matplotlib
```

### Driver Derleme ve Yükleme

```bash
cd xdma
make

# Yükleme (interrupt_mode: 0=auto, 1=MSI, 2=Legacy, 3=MSI-X, 4=poll)
cd ../tests
sudo bash load_driver.sh 2   # Legacy IRQ
```

### Sürekli Okuma (Double Buffer)

```bash
sudo python3 continuous_read.py
# Kanal: 4
# Chunk boyutu: 4096
# Toplam byte: 13421728
```

### LFSR Karşılaştırma Testi

```bash
sudo python3 test_lfsr.py
# Transfer boyutu: 4096
# Transfer sayısı: 20
# Double buffer: y
```

### Sinyal Analizi

```bash
python3 plotsincos.py <output.bin>
```

---

## Test Sonuçları

Tüm testler CH4 C2H, AXI4-Stream, interrupt_mode=2 (Legacy IRQ), buf_size=4096 ile yapılmıştır.

### test_lfsr — 20 × 4096 Byte (2.560 Sample)

| Metrik | Değer |
|--------|-------|
| Toplam hata | 19 |
| Boundary hatası (4096-byte sınırı) | 19 (%100) |
| Inner hata (buffer içi) | **0** |
| Her boundary'de kayıp sample | **2** (sabit) |
| `counter_delta` | 3 (deterministik) |

### continuous_read — 3277 × 4096 Byte (~13.4 MB, 419.456 Sample)

| Metrik | Değer |
|--------|-------|
| Toplam hata | 6.512 |
| Tip-1: clean 2-sample boundary skip | 3.276 |
| Tip-2: buffer başlangıç anomalisi | 3.236 |
| Inner hata | **0** |

> **Tip-2 notu:** Uzun capture'da her buffer'ın 0.→1. sample geçişinde büyük sayaç sıçraması gözlemleniyor. IRQ resubmit gecikmesinin birikmesinden kaynaklandığı düşünülüyor; araştırılmaya devam ediliyor.

### Buffer Boyutu Karşılaştırması

| buf_size | Inner Hata | Sonuç |
|----------|-----------|-------|
| 4096 | 0 | ✓ Optimal |
| 32768 | Var | ✗ FPGA FIFO taşması |
| 131072 | Çok fazla | ✗ |

---

## Dosya Yapısı

```
.
├── xdma/                        # Kernel driver kaynak kodu
│   ├── cdev_sgdma.c             # ★ Ana değişiklik — double buffer implementasyonu
│   ├── cdev_sgdma.h             # ★ IOCTL tanımları (DBUF_START, DBUF_STOP)
│   ├── xdma_mod.h               # ★ xdma_dbuf_slot ve xdma_cdev eklentileri
│   ├── libxdma.c                # DMA engine (değiştirilmedi)
│   ├── libxdma.h                # DMA yapıları (değiştirilmedi)
│   └── Makefile
├── generic.py                   # Python IOCTL sarmalayıcı + dbuf_start/stop
├── test_lfsr.py                 # Single vs double buffer LFSR karşılaştırma testi
├── continuous_read.py           # Büyük sürekli okuma scripti
├── plotsincos.py                # Sinyal görselleştirme
├── lfsr_analysis_utils.py       # LFSR analiz kütüphanesi (parse, verify, report)
├── tests/
│   ├── load_driver.sh           # Driver yükleme scripti
│   └── data/
├── data/outputs/
│   ├── REPORT_modified_driver.md              # Detaylı test raporu
│   ├── 20260610_185646_lfsr_compare/
│   │   └── double_buffer.skip_report.csv      # test_lfsr sonuçları
│   └── 20260611_142616_output/
│       ├── output_ch4_4096B_x3277.snapshot.png
│       └── output_ch4_4096B_x3277.skip_report.csv
└── include/
```

---

## Lisans

Orijinal Xilinx XDMA sürücüsü GPL-2.0 lisansı ile dağıtılmaktadır. Bu repodaki değişiklikler de aynı lisans kapsamındadır. Bkz. [LICENSE](LICENSE) ve [COPYING](COPYING).
