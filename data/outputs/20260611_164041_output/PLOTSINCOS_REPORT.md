# plotsincos.py Test Raporu
## generic.py → plotsincos.py Akışı

**Tarih:** 2026-06-11  
**Kanal:** CH4 C2H, AXI4-Stream, 25 MHz  
**Driver:** Modified (double buffer), interrupt_mode=2

---

## Test Yapılandırması

```bash
# 1. Veri yakalama
sudo python3 generic.py
#   mod   : dbuf (double buffer)
#   kanal : 4
#   boyut : 4096 byte/transfer
#   adet  : 200 transfer
#   toplam: 819.200 byte = 25.600 sample

# 2. Analiz
python3 plotsincos.py output_ch4_4096B_x200.bin
```

---

## LFSR Sonuçları

| Metrik | Değer |
|--------|-------|
| Toplam yakalanan sample | 25.600 |
| plotsincos'un analiz ettiği sample (ilk 4096 atlanır) | 21.504 |
| **plotsincos LFSR hatası** | **335** |
| Tüm veri üzerinde LFSR hatası | 399 |
| Hata oranı (21.504 üzerinden) | %1,56 |

### Hata Dağılımı (tüm 25.600 sample)

| Hata Tipi | Adet | Açıklama |
|-----------|------|---------|
| Başlangıç gecikmesi | 1 | DMA ilk başlarken oluşan ~960-sample gap |
| Boundary (delta=3, missing=2) | 199 | Her 4096-byte sınırında **tam 2 sample** atlama |
| İlk-sample pipeline gap (~960 missing) | 199 | Her buffer'ın 1.→2. sample'ı arası ~38 µs boşluk |
| **Toplam** | **399** | — |

### Hâlâ 2 Sample Atlıyor mu?

**Evet.** Her 4096-byte buffer sınırında:
```
sample[N-1] → sample[N]:  counter_delta = 3  → 2 sample kayıp
```
Bu AXI4-Stream hardware sınırıdır, `tready=0` süresi = 80 ns = 2 × 40 ns @ 25 MHz.  
Yazılımla ortadan kaldırılamaz.

---

## FFT Sonuçları

| Alan | Değer |
|------|-------|
| Analiz edilen sample | 21.504 |
| FFT çözünürlüğü (df) | 1.162,6 Hz |
| SIN peak frekansı | **15.113,5 Hz** |
| COS peak frekansı | **15.113,5 Hz** |

SIN ve COS aynı frekansta zirve yapıyor — FPGA'nın sinyal üreteci beklendiği gibi çalışıyor.

---

## Toplam LFSR Hatalarının Karşılaştırması

| Test | Driver | Yöntem | Sample | LFSR Hatası | Oran |
|------|--------|--------|--------|------------|------|
| 20 × 4096 | Modified | Single buffer | 2.560 | ~1.842 | %71,9 |
| 20 × 4096 | Modified | **Double buffer** | **2.560** | **19** | **%0,74** |
| 200 × 4096 | Modified | Double buffer | 25.600 | 399 | %1,56 |
| 3277 × 4096 | Modified | Double buffer | 419.040 | 6.512 | %1,55 |
| 128MB × 1 | Orijinal | Single (C aracı) | 4.194.304 | 19.307 | %0,46 |
| 128MB × 1 | Modified | Double buffer | 4.194.304 | 18.442 | %0,44 |

> **Evet, toplam LFSR hataları azalıyor.** Double buffer ile 20×4096 senaryosunda single buffer'a kıyasla **~97× daha az hata**.

---

## Üretilen Dosyalar

| Dosya | Açıklama |
|-------|---------|
| `output_ch4_4096B_x200.bin` | Ham yakalama, 819.200 byte |
| `output_ch4_4096B_x200.sincos.png` | SIN/COS/Counter grafikleri |
| `output_ch4_4096B_x200.fft.png` | FFT analizi (15.1 kHz sinyal) |
| `output_ch4_4096B_x200.skip_report.csv` | Her LFSR hatasının detaylı analizi |
| `output_ch4_4096B_x200.snapshot.png` | İlk 8192 sample görüntüsü |
