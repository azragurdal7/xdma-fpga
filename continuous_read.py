#!/usr/bin/env python3
"""
Kesintisiz C2H Okuma — Double Buffer ile
==========================================
Sunumdaki gibi büyük, tek akış halinde okuma yapar.
Double buffer sayesinde DMA pipeline hiç boşalmaz → LFSR kesintisiz akar.

Kullanım:
    sudo python3 continuous_read.py

Sonra analiz (değişmiyor):
    python3 plotsincos.py <output.bin>
"""

import os
import sys
import time
import threading
from pathlib import Path
from datetime import datetime

sys.path.insert(0, str(Path(__file__).resolve().parent))
from generic import (
    dbuf_start, dbuf_stop,
    run_command, ask_input, is_positive_int,
    LOAD_DRIVER_SCRIPT, LOAD_DRIVER_ARGS,
    TESTS_DIR, OUTPUTS_DIR, TOOLS_DIR,
)

# Sunum Slayt 27 — exact parametreler:
# CH4 C2H: 13,421,728 byte = 4,194,304 sample × 32 byte
# H2C tetikleme: 32 byte
DEFAULT_CHANNEL     = "4"
DEFAULT_TOTAL_BYTES = 13_421_728
DEFAULT_CHUNK_SIZE  = 4096         # test: küçük chunk iç FIFO overflow'u önler
DEFAULT_TRIGGER_SIZE = 32          # Slayt 27: H2C tetikleme = 32 byte


def send_h2c_trigger(h2c_dev: str, trigger_size: int, delay: float = 1.0):
    """C2H okuma başladıktan delay saniye sonra H2C tetikleme gönderir."""
    time.sleep(delay)
    trigger_file = TESTS_DIR / "data" / "datafile0_4K.bin"
    dma_to_device = TOOLS_DIR / "dma_to_device"

    print(f"\n[H2C]  Tetikleme gönderiliyor → {h2c_dev} ({trigger_size} byte)")
    result = run_command(
        [str(dma_to_device), "-d", h2c_dev,
         "-f", str(trigger_file),
         "-s", str(trigger_size), "-c", "1"],
        use_sudo=True,
    )
    if result.returncode != 0:
        print("[H2C]  UYARI: tetikleme başarısız olabilir.")
    else:
        print("[H2C]  Tetikleme gönderildi.")


def read_c2h(c2h_dev: str, chunk_size: int, n_chunks: int,
             out_file: Path, result_box: list):
    """Double buffer ile C2H okuma — ayrı thread'de çalışır."""
    fd = os.open(c2h_dev, os.O_RDONLY)
    dbuf_start(fd, chunk_size, ep_addr=0)
    bytes_read = 0
    try:
        with open(out_file, "wb") as f:
            for i in range(n_chunks):
                chunk = os.read(fd, chunk_size)
                if len(chunk) != chunk_size:
                    raise IOError(
                        f"Chunk {i}: beklenen {chunk_size}, "
                        f"alınan {len(chunk)} byte"
                    )
                f.write(chunk)
                bytes_read += len(chunk)
                pct = 100 * (i + 1) / n_chunks
                print(f"  {i+1:4d}/{n_chunks}  {bytes_read:>12,} byte  "
                      f"{pct:5.1f}%", end="\r", flush=True)
        result_box.append(bytes_read)
    except Exception as e:
        result_box.append(e)
    finally:
        dbuf_stop(fd)
        os.close(fd)


def main():
    print("=" * 50)
    print("  Kesintisiz C2H Okuma (Double Buffer)")
    print("=" * 50)

    # ── Driver yükle ──────────────────────────────
    print("[INFO] Driver yükleniyor...")
    result = run_command(
        ["bash", str(LOAD_DRIVER_SCRIPT)] + LOAD_DRIVER_ARGS,
        cwd=TESTS_DIR,
        use_sudo=True,
    )
    if result.returncode != 0:
        print("[ERROR] Driver yükleme başarısız.")
        sys.exit(1)

    # ── Parametreler ──────────────────────────────
    channel_user = int(ask_input(
        "Kanal seç (1-4)",
        validator=lambda x: x in ["1", "2", "3", "4"],
        default=DEFAULT_CHANNEL,
    ))
    channel_idx = channel_user - 1
    c2h_dev = f"/dev/xdma0_c2h_{channel_idx}"
    h2c_dev = f"/dev/xdma0_h2c_{channel_idx}"

    total_bytes = int(ask_input(
        "Toplam okunacak byte",
        validator=is_positive_int,
        default=str(DEFAULT_TOTAL_BYTES),
    ))

    chunk_size = int(ask_input(
        "Chunk boyutu (PAGE_SIZE=4096 katı)",
        validator=lambda x: is_positive_int(x) and int(x) % 4096 == 0,
        default=str(DEFAULT_CHUNK_SIZE),
    ))

    use_trigger = ask_input(
        "H2C tetikleme gönderilsin mi? (y/n)",
        validator=lambda x: x.lower() in ["y", "n"],
        default="y",
    ).lower() == "y"

    # ── Hesapla ───────────────────────────────────
    n_chunks     = (total_bytes + chunk_size - 1) // chunk_size
    actual_bytes = n_chunks * chunk_size
    n_samples    = actual_bytes // 32
    print(f"\n[INFO] Plan   : {n_chunks} × {chunk_size:,} byte "
          f"= {actual_bytes:,} byte = {n_samples:,} sample")
    print(f"[INFO] C2H    : {c2h_dev}")
    if use_trigger:
        print(f"[INFO] H2C    : {h2c_dev}  ({DEFAULT_TRIGGER_SIZE} byte tetikleme)")

    # ── Çıktı dosyası ─────────────────────────────
    ts      = datetime.now().strftime("%Y%m%d_%H%M%S")
    out_dir = OUTPUTS_DIR / f"{ts}_continuous"
    out_dir.mkdir(parents=True, exist_ok=True)
    out_file = out_dir / f"ch{channel_user}_{actual_bytes}B.bin"
    print(f"[INFO] Çıktı  : {out_file}")

    # ── Çalıştır ──────────────────────────────────
    print("\n[INFO] Okuma başlıyor...\n")

    result_box = []

    # C2H okuma thread'i başlat
    c2h_thread = threading.Thread(
        target=read_c2h,
        args=(c2h_dev, chunk_size, n_chunks, out_file, result_box),
        daemon=True,
    )
    c2h_thread.start()

    # H2C tetikleme — C2H hazır olduktan 1 sn sonra
    if use_trigger:
        send_h2c_trigger(h2c_dev, DEFAULT_TRIGGER_SIZE, delay=1.0)

    # C2H bitmesini bekle
    c2h_thread.join()

    # ── Sonuç ─────────────────────────────────────
    print()
    if result_box and isinstance(result_box[0], Exception):
        print(f"[ERROR] Okuma hatası: {result_box[0]}")
        sys.exit(1)

    bytes_read = result_box[0] if result_box else 0
    print(f"\n[INFO] Tamamlandı : {bytes_read:,} byte okundu.")
    print(f"[INFO] Dosya      : {out_file}")
    print(f"\n[INFO] Analiz için:")
    print(f"       python3 plotsincos.py {out_file}")


if __name__ == "__main__":
    main()
