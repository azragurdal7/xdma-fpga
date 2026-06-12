# RAM Throughput Analizi
## Teorik Hesap + Gerçek Ölçüm

**Sistem:** DDR4-2133, Dual Channel (Samsung M378A2K43BB1-CPB × 2)  
**Tarih:** 2026-06-12

---

## 1. Teorik RAM Bant Genişliği Hesabı

### Formül

```
Bant genişliği = Transfer hızı × Veri yolu genişliği × Kanal sayısı
```

### DDR Nedir?

DDR = **Double Data Rate** — clock'un hem yükselen hem düşen kenarında veri iletilir.  
"2133 MT/s" zaten bu çarpanı içeriyor (gerçek clock = 1066 MHz, her clock'ta 2 transfer).

### Adım Adım Hesap

```
Veri hızı      = 2133 × 10⁶ transfer/s       (her transfer = 1 clock kenarı)
Veri yolu      = 64 bit = 8 byte              (DIMM başına)
Kanal sayısı   = 2                            (DIMM1 ChannelB + DIMM3 ChannelA)

Bant genişliği = 2133 × 10⁶ × 8 × 2
               = 34.128 × 10⁹ byte/s
               ≈ 34.1 GB/s  (teorik maksimum)
```

---

## 2. Gerçek Ölçümler

### Ölçüm 1 — dd ile /dev/shm (tmpfs, saf RAM)

```bash
dd if=/dev/zero of=/dev/shm/ramtest bs=4096 count=262144
# 262144 × 4096 = 1 GB
```

```
Sonuç: 1,2 GB/s
```

Neden düşük? Her `write()` çağrısı bir syscall — 262.144 syscall'in overhead'i bant genişliğini kısıtlıyor. Bu `dd` ile RAM değil, **syscall hızını** ölçüyor.

---

### Ölçüm 2 — Bulk memcpy (512 MB, tek seferde)

```python
src = bytearray(512 * 1024 * 1024)
dst = bytearray(512 * 1024 * 1024)
dst[:] = src   # tek bulk kopyalama
```

```
Sonuç: 12.314 MB/s ≈ 12.3 GB/s
```

Syscall overhead yok. L3 cache + RAM birlikte ölçülüyor.

---

### Ölçüm 3 — 4096-byte chunk yazma (DMA buffer boyutunda)

```python
CHUNK = 4096
for i in range(N):
    dst[i*CHUNK:(i+1)*CHUNK] = buf   # her seferinde 4096 byte
```

```
Sonuç : 8.657 MB/s ≈ 8.7 GB/s
Süre  : 0.45 µs / chunk
```

**Bu bizim için en anlamlı ölçüm** — DMA'nın her transferde yaptığı şeyin tam karşılığı.

---

## 3. Teorik vs Ölçülen Karşılaştırma

| Ölçüm | Sonuç | Açıklama |
|-------|-------|---------|
| Teorik (DDR4-2133 dual ch) | **34.1 GB/s** | Formül hesabı |
| dd /dev/shm (1 GB, 4096-chunk) | **1.2 GB/s** | Syscall overhead yüzünden düşük |
| Bulk memcpy (512 MB) | **12.3 GB/s** | Cache + RAM, gerçeğe en yakın |
| 4096-byte chunk yazma | **8.7 GB/s** | DMA senaryo ile birebir |

**Teorik ile ölçülen arasındaki fark:**
- Bellek kontrolcüsü overhead'i
- L3 cache doygunluğu
- Tek thread dual-channel'ı tam dolduramıyor
- OS bellek yönetimi gecikmesi

---

## 4. DMA Senaryosu İçin Ne Anlama Geliyor?

```
FPGA → DMA → RAM yazma süresi:
  FPGA 4096 byte üretir   : 5.12 µs   (25 MHz × 32 byte)
  RAM  4096 byte alabilir : 0.45 µs   (8.7 GB/s ölçümü)

  RAM, FPGA'dan 11× daha hızlı → RAM kesinlikle bottleneck değil
```

### Neden Hâlâ Hata Var?

```
read() süresi dökümü:
  IRQ handler gecikmesi   : ~10 µs   ← asıl darboğaz
  copy_to_user (4096 byte): ~0.5 µs  ← RAM hızlıca kaldırıyor
  Syscall giriş/çıkış     : ~2 µs
  ─────────────────────────────────
  Toplam read()           : ~16 µs   >  5.12 µs dolum süresi
```

IRQ gecikmesi (~10 µs), RAM hızıyla değil interrupt işleme süresiyle ilgili.

---

## 5. Tüm Katmanların Özeti

| Katman | Kapasite | Gereken | Kullanım | Bottleneck? |
|--------|---------|---------|---------|-------------|
| RAM teorik | 34.1 GB/s | 800 MB/s | %2.3 | ✗ |
| RAM ölçülen (4K chunk) | 8.7 GB/s | 800 MB/s | %9.2 | ✗ |
| PCIe Gen3 x4 | 3.94 GB/s | 800 MB/s | %20.3 | ✗ |
| C `read()` efektif | ~237 MB/s | 800 MB/s | %29.7 | ✗ |
| **IRQ handler gecikmesi** | **~100 MB/s** | **800 MB/s** | **%100+** | **✓ EVET** |

**Gerçek darboğaz: IRQ handler gecikmesi (~10 µs)**  
Çözüm: `interrupt_mode=0` (polling) — IRQ bekleme olmaz, `read()` anında döner.

---

## 6. Ölçüm Komutları

```bash
# RAM yazma hızı (syscall dahil)
dd if=/dev/zero of=/dev/shm/ramtest bs=4096 count=262144 && rm /dev/shm/ramtest

# Bulk memcpy hızı
python3 -c "
import time
SIZE = 512*1024*1024
src = bytearray(SIZE); dst = bytearray(SIZE)
t0 = time.monotonic(); dst[:] = src; t1 = time.monotonic()
print(f'{SIZE/1024/1024/(t1-t0):.0f} MB/s')
"

# 4096-byte chunk yazma hızı
python3 -c "
import time
CHUNK=4096; N=131072; buf=bytearray(CHUNK); dst=bytearray(CHUNK*N)
t0=time.monotonic()
for i in range(N): dst[i*CHUNK:(i+1)*CHUNK]=buf
t1=time.monotonic()
print(f'{CHUNK*N/1024/1024/(t1-t0):.0f} MB/s  —  {(t1-t0)/N*1e6:.2f} µs/chunk')
"
```
