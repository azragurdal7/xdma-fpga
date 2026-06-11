# Sistem Bant Genişliği Analizi
## RAM, PCIe ve Gerçek Bottleneck Hesapları

**Sistem:** HP Workstation, Intel 100 Series Chipset  
**BIOS:** HP N51 Ver. 01.21 (2016)  
**Tarih:** 2026-06-11

---

## 1. RAM Bilgileri (dmidecode çıktısından)

```
Slot  : DIMM1 (ChannelB) + DIMM3 (ChannelA)   ← iki kanal dolu
Tip   : DDR4, Samsung M378A2K43BB1-CPB
Boyut : 16 GB + 16 GB = 32 GB toplam
Hız   : 2133 MT/s (Configured: 2133 MT/s)
Voltaj: 1.2 V
```

DIMM2 ve DIMM4 boş → **Dual Channel** (iki farklı kanalda birer DIMM).

---

## 2. RAM Bant Genişliği Hesabı

### Formül

```
Bant genişliği = Transfer hızı × Veri yolu genişliği × Kanal sayısı
```

### Adım adım

```
DDR = Double Data Rate → clock'un hem yükselen hem düşen kenarında veri
2133 MT/s = saniyede 2.133 × 10⁹ transfer

Veri yolu: 64 bit = 8 byte (her DIMM)
Kanal sayısı: 2 (dual channel)

Bant genişliği = 2133 × 10⁶  ×  8 byte  ×  2 kanal
               = 2133 × 10⁶  ×  16 byte
               = 34.128 × 10⁹ byte/s
               ≈ 34.1 GB/s
```

| Parametre | Değer |
|-----------|-------|
| Transfer hızı | 2133 MT/s |
| Veri yolu (tek kanal) | 64 bit = 8 byte |
| Kanal sayısı | 2 (dual channel) |
| **Teorik bant genişliği** | **34.1 GB/s** |

---

## 3. PCIe Bilgileri (lspci çıktısından)

```bash
BDF=$(lspci | grep -Ei "xilinx" | awk '{print $1}')
sudo lspci -s "$BDF" -vv | grep -E "LnkCap:|LnkSta:"
```

```
Cihaz   : 05:00.0  Xilinx Corporation Device 9038  (XDMA)
LnkCap  : Speed 8GT/s (Gen3), Width x8   ← slot kapasitesi
LnkSta  : Speed 8GT/s (ok),   Width x4   ← gerçekte aktif lane
```

Width x4 — slot x8 destekliyor ama kart yalnızca 4 lane kullanıyor.

---

## 4. PCIe Bant Genişliği Hesabı

### PCIe Gen3 kodlama

PCIe Gen3, **128b/130b** kodlama kullanır:  
Her 130 bit iletimde 128 bit faydalı veri → verimlilik = 128/130 ≈ %98,5

### Formül

```
Bant genişliği = GT/s × lane sayısı × (128/130) ÷ 8 bit/byte
```

### Adım adım

```
Raw bit hızı   = 8 GT/s × 4 lane = 32 Gbps

Kodlama kaybı  = 32 × (128/130) = 31.508 Gbps

Byte/s         = 31.508 × 10⁹ / 8 = 3.938 × 10⁹ byte/s
               ≈ 3.94 GB/s
```

| Parametre | Değer |
|-----------|-------|
| PCIe nesli | Gen3 (8 GT/s/lane) |
| Aktif lane | x4 (slot x8, downgraded) |
| Kodlama | 128b/130b (~%98,5 verim) |
| **Teorik bant genişliği** | **~3.94 GB/s** |

---

## 5. FPGA Veri Üretim Hızı

```
Örnekleme hızı : 25 MHz = 25 × 10⁶ sample/s
Sample boyutu  : 32 byte (256 bit: LFSR + Counter + SIN + COS)

FPGA veri hızı = 25 × 10⁶ × 32 = 800 × 10⁶ byte/s = 800 MB/s
```

---

## 6. Python Efektif Yakalama Hızı

`os.read()` syscall'inin ölçülen gecikmesi ~50 µs (sistem yükü, context switch, copy_to_user).

```
Dolum süresi  = 4096 byte / 800 MB/s = 5.12 µs
İşlem süresi  = ~50 µs  (Python syscall + copy_to_user overhead)

Efektif hız   = 4096 byte / (5.12 + 50) µs
              = 4096 / 55.12 µs
              ≈ 74.3 MB/s
```

---

## 7. Bottleneck Karşılaştırma Tablosu

| Katman | Teorik Kapasite | FPGA'nın Gerektirdiği | Kullanım | Bottleneck? |
|--------|----------------|----------------------|---------|-------------|
| RAM (DDR4-2133, dual ch) | **34.1 GB/s** | 800 MB/s | %2,3 | ✗ Hayır |
| PCIe Gen3 x4 | **3.94 GB/s** | 800 MB/s | %20,3 | ✗ Hayır |
| Python `os.read()` | **~74 MB/s** | 800 MB/s | %100+ | **✓ EVET** |

```
FPGA üretiyor  : 800 MB/s
Python yakalar : ~74 MB/s
Kayıp          : 726 MB/s = %90.7
```

Bu kayıp doğrudan LFSR hata raporlarındaki **Tip-2 pipeline stall** hatalarına dönüşüyor.

---

## 8. Hızı Artırmak İçin Ne Gerekir?

Python `os.read()` darboğazını aşmak için seçenekler:

| Yöntem | Beklenen Hız | Açıklama |
|--------|-------------|---------|
| **C ile read()** | ~400–600 MB/s | Syscall overhead çok düşük |
| **Kernel ring buffer** | ~800 MB/s | DMA→mmap, sıfır kopya |
| **Büyük buf_size** | — | FPGA FIFO taşıyor, işe yaramaz |
| Double buffer (mevcut) | ~74 MB/s | Python sınırına ulaşıldı |

RAM ve PCIe hiçbir senaryoda bottleneck olmayacak.

---

## 9. Komutlar (Referans)

```bash
# RAM hız bilgisi
sudo dmidecode --type memory | grep -E "Speed|Type|Size|Manufacturer"

# PCIe link hızı (XDMA kartı)
BDF=$(lspci | grep -Ei "xilinx" | awk '{print $1}' | head -n1)
sudo lspci -s "$BDF" -vv | grep -E "LnkCap:|LnkSta:"

# Anlık RAM kullanımı
free -h

# Detaylı bellek bilgisi
cat /proc/meminfo | grep -E "MemTotal|MemFree|MemAvail|Cached"
```
