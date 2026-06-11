#!/usr/bin/env python3
"""
LFSR Sürekliliği Karşılaştırma Testi
=====================================
Single buffer vs Double buffer modlarını aynı koşullarda çalıştırıp
LFSR hata sayılarını karşılaştırır.

Kullanım:
    sudo python3 test_lfsr.py
"""

import os
import sys
import struct
import time
from pathlib import Path
from datetime import datetime

import matplotlib
matplotlib.use("TkAgg")          # headless sunucu yoksa Agg kullan
import matplotlib.pyplot as plt
import numpy as np

# Kendi modüllerimiz
sys.path.insert(0, str(Path(__file__).resolve().parent))
from lfsr_analysis_utils import (
    parse_bytes, verify_lfsr, plot_fft, lfsr_next, write_skip_report,
)
from generic import (
    dbuf_start, dbuf_stop,
    run_command, ask_input, is_positive_int,
    LOAD_DRIVER_ARGS, TESTS_DIR, OUTPUTS_DIR
)

# ---------------------------------------------------------------
# Sabitler
# ---------------------------------------------------------------
WORD_SIZE    = 32      # 256-bit / sample
TAP_POSITIONS = [32, 31, 29, 28, 27, 26, 25, 24, 1]


# ---------------------------------------------------------------
# Veri toplama
# ---------------------------------------------------------------

def collect_single_buffer(c2h_dev: str, buf_size: int,
                           transfer_count: int) -> bytes:
    """
    Eski tek-buffer yolu: her read() ayrı bir DMA transferi başlatır.
    Transferler arası boşluk → potansiyel LFSR kopukluğu.
    """
    SAMPLE_SIZE = 32
    READ_EXTRA  = SAMPLE_SIZE * 2   # 64 byte güvenlik payı

    print(f"\n[SINGLE] {transfer_count} × {buf_size} byte okunuyor...")
    fd = os.open(c2h_dev, os.O_RDONLY)
    chunks = []
    try:
        for i in range(transfer_count):
            data = os.read(fd, buf_size + READ_EXTRA)
            actual = len(data)
            if actual < buf_size:
                raise IOError(f"Transfer {i}: beklenen en az {buf_size} byte, alınan {actual}")
            if actual > buf_size:
                print(f"  [WARN] T{i}: {actual - buf_size} byte fazla geldi, truncate edildi.")
            chunks.append(data[:buf_size])
            if (i + 1) % 10 == 0:
                print(f"  [{i+1}/{transfer_count}]", end="\r", flush=True)
    finally:
        os.close(fd)
    print(f"\n[SINGLE] Toplam {sum(len(c) for c in chunks):,} byte toplandı.")
    return b"".join(chunks)


def collect_double_buffer(c2h_dev: str, buf_size: int,
                           transfer_count: int) -> bytes:
    """
    Double buffer yolu: kernel pipeline'ı sürekli dolu tutar.
    Transferler arası boşluk → yok (FPGA LFSR kesmeden akar).
    """
    print(f"\n[DBUF]   {transfer_count} × {buf_size} byte okunuyor...")
    fd = os.open(c2h_dev, os.O_RDONLY)
    chunks = []
    try:
        dbuf_start(fd, buf_size, ep_addr=0)
        for i in range(transfer_count):
            data = os.read(fd, buf_size)
            if len(data) != buf_size:
                raise IOError(f"Transfer {i}: beklenen {buf_size}, alınan {len(data)}")
            chunks.append(data)
            if (i + 1) % 10 == 0:
                print(f"  [{i+1}/{transfer_count}]", end="\r", flush=True)
    finally:
        dbuf_stop(fd)
        os.close(fd)
    print(f"\n[DBUF]   Toplam {sum(len(c) for c in chunks):,} byte toplandı.")
    return b"".join(chunks)


# ---------------------------------------------------------------
# Buffer-sınırı analizi
# ---------------------------------------------------------------

def check_buffer_boundaries(lfsr_values: list, buf_size: int,
                              label: str) -> int:
    """
    Her DMA buffer sınırındaki LFSR geçişini ayrıca kontrol eder.
    Sınırdaki hata = DMA pipeline boşluğundan kaynaklanan kopukluk.
    Sınır-içi hata = FPGA tarafı sorunu.
    """
    samples_per_buf = buf_size // WORD_SIZE
    boundary_errors = 0
    inner_errors     = 0

    for i in range(len(lfsr_values) - 1):
        expected = lfsr_next(lfsr_values[i], TAP_POSITIONS)
        if lfsr_values[i + 1] != expected:
            at_boundary = ((i + 1) % samples_per_buf == 0)
            if at_boundary:
                boundary_errors += 1
            else:
                inner_errors += 1

    total = boundary_errors + inner_errors
    print(f"\n[{label}] Buffer sınırı analizi:")
    print(f"  Buffer başına sample sayısı : {samples_per_buf}")
    print(f"  Sınırda hata (DMA gap)       : {boundary_errors}")
    print(f"  Sınır-içi hata (FPGA)        : {inner_errors}")
    print(f"  Toplam hata                  : {total}")
    return boundary_errors, inner_errors


# ---------------------------------------------------------------
# Rapor ve karşılaştırma
# ---------------------------------------------------------------

def print_comparison(single_errors: int, double_errors: int,
                      single_samples: int, double_samples: int) -> None:
    print("\n" + "=" * 55)
    print("  KARŞILAŞTIRMA SONUCU")
    print("=" * 55)
    fmt = "{:<20} {:>10} {:>12}"
    print(fmt.format("Mod", "Hata", "Hata Oranı"))
    print("-" * 55)

    def rate(e, n):
        return f"{100*e/max(n,1):.4f}%" if n else "N/A"

    print(fmt.format("Single Buffer",
                      single_errors,
                      rate(single_errors, single_samples - 1)))
    print(fmt.format("Double Buffer",
                      double_errors,
                      rate(double_errors, double_samples - 1)))
    print("=" * 55)

    if double_errors < single_errors:
        improvement = 100 * (1 - double_errors / max(single_errors, 1))
        print(f"  ✓ Double buffer {improvement:.1f}% iyileştirme sağladı.")
    elif double_errors == 0 and single_errors == 0:
        print("  ℹ Her iki modda da hata yok.")
    else:
        print("  ✗ Beklenen iyileştirme gözlemlenmedi.")


# ---------------------------------------------------------------
# Plot — hata konumları üstte gösterilen dalga formu
# ---------------------------------------------------------------

def plot_with_errors(sin_values, cos_values, counter_values,
                     lfsr_values, label: str) -> None:
    errors_idx = []
    for i in range(len(lfsr_values) - 1):
        expected = lfsr_next(lfsr_values[i], TAP_POSITIONS)
        if lfsr_values[i + 1] != expected:
            errors_idx.append(i + 1)

    fig, axes = plt.subplots(4, 1, figsize=(14, 10))
    fig.suptitle(f"{label} — LFSR Hata Konumları", fontsize=13)

    x = np.arange(len(sin_values))

    axes[0].plot(x, sin_values, color='blue', linewidth=0.6, label='SIN')
    axes[0].set_title('SIN')

    axes[1].plot(x, cos_values, color='red', linewidth=0.6, label='COS')
    axes[1].set_title('COS')

    axes[2].plot(x, counter_values, color='green', linewidth=0.6, label='Counter')
    axes[2].set_title('Counter')

    axes[3].plot(x[:-1], lfsr_values[:-1], color='purple',
                 linewidth=0.4, label='LFSR', alpha=0.7)
    if errors_idx:
        axes[3].scatter(errors_idx,
                        [lfsr_values[i] for i in errors_idx],
                        color='red', s=30, zorder=5, label=f'Hata ({len(errors_idx)})')
    axes[3].set_title('LFSR + Hata Noktaları')
    axes[3].legend(loc='upper right')

    for ax in axes:
        ax.set_xlabel('Sample Index')
        ax.legend(loc='upper left')

    plt.tight_layout()
    plt.show()


# ---------------------------------------------------------------
# Ana akış
# ---------------------------------------------------------------

def main():
    print("=" * 55)
    print("  XDMA LFSR Karşılaştırma Testi")
    print("=" * 55)

    # --- Driver yükle ---
    from generic import LOAD_DRIVER_SCRIPT
    print("[INFO] Driver yükleniyor...")
    result = run_command(
        ["bash", str(LOAD_DRIVER_SCRIPT)] + LOAD_DRIVER_ARGS,
        background=False,
        cwd=TESTS_DIR,
        use_sudo=True
    )
    if result.returncode != 0:
        print("[ERROR] Driver yükleme başarısız.")
        sys.exit(1)

    # --- Parametreler ---
    channel_user = int(ask_input(
        "Kanal seç (1-4)",
        validator=lambda x: x in ["1", "2", "3", "4"]
    ))
    channel_idx = channel_user - 1
    c2h_dev = f"/dev/xdma0_c2h_{channel_idx}"

    # Orijinal dma_from_device gibi: kullanıcı toplam byte ve kaç parça okuyacağını girer.
    # transfer_count=1 → tek büyük transfer (sunum'daki 13.4 MB gibi)
    # transfer_count>1 → birden fazla ardışık transfer (buffer sınırları test edilir)
    buf_size = int(ask_input(
        "Tek transfer boyutu (byte, PAGE_SIZE=4096 katı olmalı)",
        validator=lambda x: is_positive_int(x) and int(x) % 4096 == 0,
        default="4096"
    ))

    transfer_count = int(ask_input(
        "Transfer sayısı [-c parametresi] (orijinal default=1)",
        validator=is_positive_int,
        default="1"
    ))

    total_bytes = buf_size * transfer_count
    total_samples = total_bytes // WORD_SIZE
    print(f"[INFO] Toplam: {total_bytes:,} byte = {total_samples:,} sample "
          f"({transfer_count} × {buf_size} byte)")

    run_single = ask_input(
        "Single buffer testi çalıştırılsın mı? (y/n)",
        validator=lambda x: x.lower() in ["y", "n"],
        default="y"
    ).lower() == "y"

    run_double = ask_input(
        "Double buffer testi çalıştırılsın mı? (y/n)",
        validator=lambda x: x.lower() in ["y", "n"],
        default="y"
    ).lower() == "y"

    show_plots = ask_input(
        "Sonuçları grafikle göster? (y/n)",
        validator=lambda x: x.lower() in ["y", "n"],
        default="y"
    ).lower() == "y"

    # --- Çıktı klasörü ---
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    out_dir = OUTPUTS_DIR / f"{ts}_lfsr_compare"
    out_dir.mkdir(parents=True, exist_ok=True)

    results = {}

    # -------------------------------------------------------
    # SINGLE BUFFER
    # -------------------------------------------------------
    if run_single:
        t0 = time.monotonic()
        raw = collect_single_buffer(c2h_dev, buf_size, transfer_count)
        elapsed = time.monotonic() - t0

        lfsr, sin, cos, counter = parse_bytes(raw)
        print(f"\n[SINGLE] {len(lfsr):,} sample parse edildi ({elapsed:.2f}s)")

        errors = verify_lfsr(lfsr, TAP_POSITIONS)
        b_err, i_err = check_buffer_boundaries(lfsr, buf_size, "SINGLE")

        out_file = out_dir / "single_buffer.bin"
        out_file.write_bytes(raw)
        print(f"[SINGLE] Ham veri kaydedildi: {out_file}")
        write_skip_report(out_file)

        results["single"] = {
            "errors": errors, "boundary": b_err, "inner": i_err,
            "samples": len(lfsr),
            "lfsr": lfsr, "sin": sin, "cos": cos, "counter": counter,
        }

    # -------------------------------------------------------
    # DOUBLE BUFFER
    # -------------------------------------------------------
    if run_double:
        t0 = time.monotonic()
        raw = collect_double_buffer(c2h_dev, buf_size, transfer_count)
        elapsed = time.monotonic() - t0

        lfsr, sin, cos, counter = parse_bytes(raw)
        print(f"\n[DBUF]   {len(lfsr):,} sample parse edildi ({elapsed:.2f}s)")

        errors = verify_lfsr(lfsr, TAP_POSITIONS)
        b_err, i_err = check_buffer_boundaries(lfsr, buf_size, "DBUF")

        out_file = out_dir / "double_buffer.bin"
        out_file.write_bytes(raw)
        print(f"[DBUF]   Ham veri kaydedildi: {out_file}")
        write_skip_report(out_file)

        results["double"] = {
            "errors": errors, "boundary": b_err, "inner": i_err,
            "samples": len(lfsr),
            "lfsr": lfsr, "sin": sin, "cos": cos, "counter": counter,
        }

    # -------------------------------------------------------
    # Karşılaştırma
    # -------------------------------------------------------
    if "single" in results and "double" in results:
        print_comparison(
            results["single"]["errors"], results["double"]["errors"],
            results["single"]["samples"], results["double"]["samples"],
        )

    # -------------------------------------------------------
    # Plotlar
    # -------------------------------------------------------
    if show_plots:
        for mode, label in [("single", "Single Buffer"), ("double", "Double Buffer")]:
            if mode in results:
                r = results[mode]
                plot_with_errors(r["sin"], r["cos"], r["counter"],
                                 r["lfsr"], label)
                plot_fft(r["sin"], r["cos"])

    print(f"\n[INFO] Tüm çıktılar: {out_dir}")


if __name__ == "__main__":
    main()
