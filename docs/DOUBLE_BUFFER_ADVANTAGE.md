# Double Buffer Avantajı: Senaryo Analizi ve Hesap

## Hangi Senaryoda Avantajlı?

**Senaryo: Çok sayıda küçük transfer (N × 4096 byte)**

Double buffer, her `read()` çağrısı arasında DMA engine'i boşta bırakmayan tek yöntemdir.
Bu nedenle avantajı yalnızca **transferler arası boşluğun** hakim olduğu durumda ortaya çıkar.

---

## Karşılaştırma Testi

**Yapılandırma:** CH4 C2H, AXI4-Stream, 25 MHz, 20 × 4096 byte transfer

| Mod | Hata Sayısı | Kayıp Sample | Hata Oranı |
|-----|-------------|-------------|------------|
| Single buffer | ~1.842 | ~36.000 | %71,9 |
| **Double buffer** | **19** | **38** | **%0,74** |
| **Fark** | **~97×** | **~947×** | — |

---

## Hesap: Single Buffer'da Neden %71,9 Kayıp?

FPGA sabit 25 MHz hızında veri üretiyor:

```
Veri hızı = 25.000.000 sample/s → 1 sample = 40 ns
```

Single buffer akışı:

```
read() → DMA 4096B doluyor (5,12 µs)
→ DMA bitti → Python veriyi işliyor (~50 µs)
→ DMA BOŞTA: 50 µs × 25 MHz = ~1.250 sample kayıp
→ read() → DMA 4096B doluyor ...
```

20 transfer için toplam kayıp tahmini:

```
20 transfer × 1.250 kayıp/transfer = ~25.000 sample kayıp
Toplam sample = 20 × 128 = 2.560
Kayıp oranı ≈ 25.000 / (2.560 + 25.000) ≈ %90
```

Gerçek ölçüm %71,9 çıktı — tahminle aynı mertebeye yakın.

---

## Hesap: Double Buffer'da Neden Sadece %0,74 Kayıp?

Double buffer akışı:

```
Slot A: [████████] → kullanıcıya aktarılıyor
Slot B:      [████████] → DMA engine zaten dolduruyor
→ Slot A okunurken Slot B sırada, DMA hiç boşa düşmüyor
```

Kalan tek kayıp kaynağı: AXI4-Stream DMA descriptor geçişi

```
Her descriptor geçişinde tready=0 süresi ≈ 80 ns = 2 sample
20 transfer → 19 boundary → 19 × 2 = 38 sample kayıp
Kayıp oranı = 38 / 2.560 = %1,48
```

Ölçülen: **19 hata, 38 kayıp sample, %0,74** — tahminle örtüşüyor.

---

## Nasıl Ölçüldü?

FPGA her sample'a şu alanları gömüyor (32 byte / sample):

| Byte | Alan |
|------|------|
| 0–3 | 32-bit LFSR değeri |
| 14–15 | 16-bit artan sayaç |
| 16–23 | SIN / COS (Q30) |

`test_lfsr.py` aynı koşullarda önce single buffer, sonra double buffer ile veri topladı.
`lfsr_analysis_utils.py` → `write_skip_report()` her ardışık sample çifti için:

1. Beklenen LFSR değeri hesaplandı (tap `[32,31,29,28,27,26,25,24,1]`)
2. Counter delta ölçüldü: `delta = counter[i+1] - counter[i]`
3. `delta = 3` → 2 sample atlama; `delta >> 3` → büyük pipeline boşluğu
4. Hatanın 4096-byte boundary'de mi yoksa buffer içinde mi olduğu kaydedildi

Ham veriler `data/outputs/` altındaki `.bin` ve `.skip_report.csv` dosyalarında.

---

## Özet: Double Buffer Ne Zaman Fark Yaratır, Ne Zaman Yaratmaz?

| Senaryo | Double Buffer Etkisi | Açıklama |
|---------|---------------------|---------|
| **N × 4096 byte (küçük chunk, çok transfer)** | **~97× iyileştirme** | Transferler arası boşluk ortadan kalktı |
| 128 MB tek büyük transfer | ~4,5× az hata | Hatalar transfer *içinde*, descriptor zincirinden kaynaklanıyor |
| ~13 MB sürekli akış (3277 × 4096) | Kısmi | ~40. chunk'tan sonra Tip-2 pipeline durması başlıyor |

**Sonuç:** Double buffer avantajı "streaming küçük chunk" modunda maksimum.
Tek büyük transfer için getirisi marjinal; o senaryodaki hatalar donanım/kernel katmanında.
