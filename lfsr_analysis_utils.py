#!/usr/bin/env python3

import csv
import struct
import sys
from pathlib import Path

import matplotlib.pyplot as plt
import numpy as np


WORD_SIZE = 32
LFSR_OFFSET = 0
COUNTER_OFFSET = 14
SIN_OFFSET = 16
COS_OFFSET = 20
TAP_POSITIONS = [32, 31, 29, 28, 27, 26, 25, 24, 1]
LFSR_REPORT_SEARCH_STEPS = 64


def _u32_to_signed(value):
    if value == (2**30):
        return value
    if value > (2**30):
        return value - 2**32
    return value


def decode_sample(chunk):
    lfsr = struct.unpack_from("<I", chunk, LFSR_OFFSET)[0]
    counter = struct.unpack_from("<H", chunk, COUNTER_OFFSET)[0]
    sin = _u32_to_signed(struct.unpack_from("<I", chunk, SIN_OFFSET)[0])
    cos = _u32_to_signed(struct.unpack_from("<I", chunk, COS_OFFSET)[0])
    return lfsr, counter, sin, cos


def parse_bytes(data):
    lfsr_values = []
    sin_values = []
    cos_values = []
    counter_values = []

    usable_len = len(data) - (len(data) % WORD_SIZE)
    if usable_len != len(data):
        print(
            f"Warning: incomplete record at offset 0x{usable_len:08X} "
            f"({len(data) - usable_len} byte ignored)",
            file=sys.stderr,
        )

    for offset in range(0, usable_len, WORD_SIZE):
        lfsr, counter, sin, cos = decode_sample(data[offset:offset + WORD_SIZE])
        lfsr_values.append(lfsr)
        sin_values.append(sin)
        cos_values.append(cos)
        counter_values.append(counter)

    return lfsr_values, sin_values, cos_values, counter_values


def lfsr_next(lfsr, tap_positions=TAP_POSITIONS):
    lfsr &= 0xFFFFFFFF
    feedback = 0
    for tap in tap_positions:
        feedback ^= (lfsr >> (tap - 1)) & 1
    return ((lfsr << 1) | feedback) & 0xFFFFFFFF


def verify_lfsr(lfsr_value, tap_positions=TAP_POSITIONS, base_index=0):
    errors = 0
    max_print = 20

    if len(lfsr_value) < 2:
        print("LFSR kontrolü için yeterli veri yok.")
        return 0

    for i in range(len(lfsr_value) - 1):
        current_lfsr = lfsr_value[i] & 0xFFFFFFFF
        next_lfsr_from_file = lfsr_value[i + 1] & 0xFFFFFFFF
        expected_lfsr = lfsr_next(current_lfsr, tap_positions)

        if next_lfsr_from_file != expected_lfsr:
            if errors < max_print:
                absolute_index = base_index + i
                print(
                    f"LFSR error at index {absolute_index}->{absolute_index + 1}, "
                    f"offset 0x{absolute_index * WORD_SIZE:08X}: "
                    f"Current {current_lfsr:08X}, "
                    f"Expected {expected_lfsr:08X}, "
                    f"Got {next_lfsr_from_file:08X}"
                )
            errors += 1

    if errors == 0:
        print("No LFSR errors detected.")
    else:
        print(f"total LFSR Errors : {errors}")
        if errors > max_print:
            print(f"only first {max_print} error printed.")

    return errors


def find_lfsr_steps(current_lfsr, received_lfsr,
                    max_steps=LFSR_REPORT_SEARCH_STEPS):
    value = current_lfsr & 0xFFFFFFFF
    for steps in range(1, max_steps + 1):
        value = lfsr_next(value, TAP_POSITIONS)
        if value == (received_lfsr & 0xFFFFFFFF):
            return steps
    return None


def write_skip_report(file_path, output_path=None):
    path = Path(file_path).expanduser()
    if not path.is_file():
        print(f"Error: '{path}' dosyası bulunamadı.", file=sys.stderr)
        return None

    output = Path(output_path) if output_path else path.with_suffix(".skip_report.csv")
    fields = [
        "error_number",
        "current_index",
        "next_index",
        "current_byte_offset",
        "next_byte_offset",
        "counter_current",
        "counter_next",
        "counter_delta",
        "counter_missing_samples",
        "lfsr_current",
        "lfsr_expected_next",
        "lfsr_received",
        "lfsr_steps_to_received",
        "lfsr_missing_samples",
        "next_sample_at_4096_boundary",
    ]

    error_count = 0
    counter_error_count = 0
    lfsr_error_count = 0
    matching_loss_count = 0
    sample_count = 0

    def generate_rows():
        nonlocal error_count, counter_error_count, lfsr_error_count
        nonlocal matching_loss_count, sample_count

        with path.open("rb") as source:
            previous = source.read(WORD_SIZE)
            if len(previous) != WORD_SIZE:
                return

            previous_lfsr, previous_counter, _, _ = decode_sample(previous)
            sample_count = 1

            while True:
                current = source.read(WORD_SIZE)
                if not current:
                    break
                if len(current) != WORD_SIZE:
                    print(
                        f"Warning: incomplete record at offset "
                        f"0x{sample_count * WORD_SIZE:08X}",
                        file=sys.stderr,
                    )
                    break

                current_lfsr, current_counter, _, _ = decode_sample(current)
                expected_lfsr = lfsr_next(previous_lfsr, TAP_POSITIONS)
                counter_delta = (current_counter - previous_counter) & 0xFFFF
                counter_error = counter_delta != 1
                lfsr_error = current_lfsr != expected_lfsr

                if counter_error or lfsr_error:
                    error_count += 1
                    counter_error_count += int(counter_error)
                    lfsr_error_count += int(lfsr_error)

                    lfsr_steps = find_lfsr_steps(previous_lfsr, current_lfsr)
                    counter_missing = (
                        counter_delta - 1 if 1 < counter_delta < 0x8000 else None
                    )
                    lfsr_missing = lfsr_steps - 1 if lfsr_steps is not None else None
                    if (
                        counter_missing is not None
                        and lfsr_missing is not None
                        and counter_missing == lfsr_missing
                    ):
                        matching_loss_count += 1

                    next_offset = sample_count * WORD_SIZE
                    yield {
                        "error_number": error_count,
                        "current_index": sample_count - 1,
                        "next_index": sample_count,
                        "current_byte_offset": f"0x{next_offset - WORD_SIZE:08X}",
                        "next_byte_offset": f"0x{next_offset:08X}",
                        "counter_current": previous_counter,
                        "counter_next": current_counter,
                        "counter_delta": counter_delta,
                        "counter_missing_samples": (
                            counter_missing
                            if counter_missing is not None
                            else "unknown/reset"
                        ),
                        "lfsr_current": f"0x{previous_lfsr:08X}",
                        "lfsr_expected_next": f"0x{expected_lfsr:08X}",
                        "lfsr_received": f"0x{current_lfsr:08X}",
                        "lfsr_steps_to_received": (
                            lfsr_steps
                            if lfsr_steps is not None
                            else f">{LFSR_REPORT_SEARCH_STEPS}"
                        ),
                        "lfsr_missing_samples": (
                            lfsr_missing if lfsr_missing is not None else "unknown"
                        ),
                        "next_sample_at_4096_boundary": next_offset % 4096 == 0,
                    }

                previous_lfsr = current_lfsr
                previous_counter = current_counter
                sample_count += 1

    try:
        destination = output.open("w", newline="")
    except PermissionError:
        output = Path.cwd() / output.name
        destination = output.open("w", newline="")

    with destination:
        writer = csv.DictWriter(destination, fieldnames=fields)
        writer.writeheader()
        writer.writerows(generate_rows())

    print(f"Skip raporu kaydedildi: {output}")
    print(f"Toplam sample: {sample_count:,}")
    print(f"Hatalı geçiş: {error_count:,}")
    print(f"Counter hatası: {counter_error_count:,}; LFSR hatası: {lfsr_error_count:,}")
    print(f"Counter ve LFSR'ın aynı kayıp sayısını gösterdiği geçiş: {matching_loss_count:,}")
    return output


def save_signal_snapshot(file_path, output_path=None, max_samples=8192):
    path = Path(file_path).expanduser()
    if not path.is_file():
        print(f"Error: '{path}' dosyası bulunamadı.", file=sys.stderr)
        return None

    out_png = Path(output_path) if output_path else path.with_suffix(".snapshot.png")

    with path.open("rb") as f:
        raw = f.read(max_samples * WORD_SIZE)
    lfsr_vals, sin_vals, cos_vals, counter_vals = parse_bytes(raw)

    errors_idx = []
    for i in range(len(lfsr_vals) - 1):
        if lfsr_vals[i + 1] != lfsr_next(lfsr_vals[i]):
            errors_idx.append(i + 1)

    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as _plt

    fig, axes = _plt.subplots(4, 1, figsize=(14, 10))
    fig.suptitle(f"Signal Snapshot — {path.name} (first {len(sin_vals):,} samples)", fontsize=12)

    x = np.arange(len(sin_vals))
    axes[0].plot(x, sin_vals, color="blue", linewidth=0.5)
    axes[0].set_title("SIN")
    axes[0].set_xlabel("Sample Index")

    axes[1].plot(x, cos_vals, color="red", linewidth=0.5)
    axes[1].set_title("COS")
    axes[1].set_xlabel("Sample Index")

    axes[2].plot(x, counter_vals, color="green", linewidth=0.5)
    axes[2].set_title("Counter")
    axes[2].set_xlabel("Sample Index")

    axes[3].plot(x[:-1], lfsr_vals[:-1], color="purple", linewidth=0.3, alpha=0.7)
    if errors_idx:
        axes[3].scatter(
            errors_idx,
            [lfsr_vals[i] for i in errors_idx],
            color="red", s=25, zorder=5, label=f"LFSR error ({len(errors_idx)})",
        )
        axes[3].legend(loc="upper right")
    axes[3].set_title("LFSR + Error Points")
    axes[3].set_xlabel("Sample Index")

    _plt.tight_layout()
    _plt.savefig(str(out_png), dpi=120)
    _plt.close(fig)
    print(f"Snapshot kaydedildi: {out_png}")
    return out_png


def plot_fft(sin_values, cos_values, sampling_rate=25e6, skip=4096):
    sin_arr = np.asarray(sin_values, dtype=float)[skip:]
    cos_arr = np.asarray(cos_values, dtype=float)[skip:]

    sin_arr -= np.mean(sin_arr)
    cos_arr -= np.mean(cos_arr)

    n_samples = len(sin_arr)
    df = sampling_rate / n_samples

    print(f"N = {n_samples}")
    print(f"FFT resolution df = {df:.6f} Hz")

    window = np.hanning(n_samples)
    sin_fft = np.fft.rfft(sin_arr * window)
    cos_fft = np.fft.rfft(cos_arr * window)
    freq = np.fft.rfftfreq(n_samples, d=1.0 / sampling_rate)

    sin_mag = np.abs(sin_fft)
    cos_mag = np.abs(cos_fft)
    sin_mag[0] = 0
    cos_mag[0] = 0

    sin_k = np.argmax(sin_mag)
    cos_k = np.argmax(cos_mag)

    print(f"SIN peak bin = {sin_k}, f_bin = {freq[sin_k]:.6f} Hz")
    print(f"COS peak bin = {cos_k}, f_bin = {freq[cos_k]:.6f} Hz")

    plt.figure(figsize=(12, 6))

    plt.subplot(2, 1, 1)
    plt.plot(freq, sin_mag, label="FFT(SIN)", color="RED")
    plt.title("FFT of SIN")
    plt.xlabel("Frequency (Hz)")
    plt.ylabel("Magnitude")
    plt.legend()

    plt.subplot(2, 1, 2)
    plt.plot(freq, cos_mag, label="FFT(COS)", color="BLUE")
    plt.title("FFT of COS")
    plt.xlabel("Frequency (Hz)")
    plt.ylabel("Magnitude")
    plt.legend()

    plt.tight_layout()
    plt.show()
