#!/usr/bin/env python3

import sys
from pathlib import Path
import struct
import matplotlib.pyplot as plt
import numpy as np

def hexdump_file(file_path, bytes_per_line=32):
    path = Path (file_path)

    if not path.exists():
        print(f"Error: THE '{path}' does not exist.")
        return
    if not path.is_file():
        print(f"Error:'{path} is not a regule file")
        return
    
    sin_values = []
    cos_values = []
    counter_values = []
    Sample_value = 0
    lfsr_value = []
    
    with open(path, "rb") as f:
        offset = 0
        while True:
            chunk = f.read(bytes_per_line)

            if not chunk:
                break

            if len (chunk) < 8:
                print(f"Warning: incomplete record at offset {offset:08X}")
                break

            sin = struct.unpack('<I',chunk [16:20])[0]
            if sin == (2**30):
                sin = sin
            elif sin > (2**30):
                sin = sin - 2**32
            
            cos = struct.unpack('<I',chunk [20:24])[0]
            if cos == (2**30):
                cos = cos
            elif cos > (2**30):
                cos = cos - 2**32

            counter = struct.unpack('<H',chunk [14:16])[0]
            lfsr = struct.unpack('<I',chunk[0:4])[0]

            Sample_value+=1


            sin_values.append(sin)
            cos_values.append(cos)
            counter_values.append(counter)
            lfsr_value.append(lfsr)


            print(f"{offset:08X} SIN={sin} COS={cos} Counter={counter} SampleNo={Sample_value}")
            offset += len(chunk)
    return sin_values,cos_values,counter_values,lfsr_value

def plot_sin_cos(sin_values,cos_values,counter_values):
    plt.figure(figsize=(12,6))

    plt.subplot(3,1,1)
    plt.plot(sin_values, label='SIN',color='blue')
    plt.title('SIN')
    plt.xlabel('Record Index')
    plt.ylabel('Value')
    plt.legend()

    plt.subplot(3,1,2)
    plt.plot(cos_values, label='COS',color='red')
    plt.title('COS')
    plt.xlabel('Record Index')
    plt.ylabel('Value')
    plt.legend()

    plt.subplot(3,1,3)
    plt.plot(counter_values, label='Counter',color='green')
    plt.title('counter')
    plt.xlabel('Record Index')
    plt.ylabel('Value')
    plt.legend()


    plt.tight_layout()
    plt.show()

def lfsr_next(lfsr,tap_positions):
    lfsr &= 0xFFFFFFFF
    feedback = 0
    for tap in tap_positions:
        feedback ^= (lfsr >> (tap-1)) &1
    return ((lfsr << 1) | feedback) & 0xFFFFFFFF

def verify_lfsr(lfsr_value,tap_positions=[32,31,29,28,27,26,25,24,1]):    
    errors = 0
    max_print = 20

    if len(lfsr_value) < 2:
        print("LFSR kontrolü için yeterli veri yok.")
        return 0
    

    for i in range(len(lfsr_value)-1):
        current_lfsr=lfsr_value[i] & 0xFFFFFFFF
        next_lfsr_from_file=lfsr_value[i+1] & 0xFFFFFFFF

        expected_lfsr=lfsr_next(current_lfsr,tap_positions)

        if next_lfsr_from_file != expected_lfsr:
            if errors < max_print:
                print(
                    f"LFSR error at index {i}->{i+1}: "
                    f"Cuurent {current_lfsr:08X}, "
                    f"Expected {expected_lfsr:08X}, "
                    f"Got {next_lfsr_from_file:08X}"
                )
            errors +=1

    if errors == 0:
        print("No LFSR errors detected.")
    else:
        print(f"total LFSR Errors : {errors}")
        if errors > max_print:
            print(f"only first {max_print} error printed.")    

    return errors

    


def plot_fft (sin_values,cos_values,sampling_rate=25e6,skip=4096):

    sin_arr = np.asarray(sin_values, dtype=float)[skip:]
    cos_arr = np.asarray(cos_values, dtype=float)[skip:]


    sin_arr -= np.mean(sin_arr)
    cos_arr -= np.mean(cos_arr)

    N = len(sin_arr)
    df = sampling_rate / N

    print(f"N = {N}")
    print(f"FFT resolution df = {df:.6f} Hz")

    window = np.hanning(N)


    sin_fft = np.fft.rfft(sin_arr * window)
    cos_fft = np.fft.rfft(cos_arr * window)
    freq = np.fft.rfftfreq(N, d=1.0 / sampling_rate)

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
    plt.plot(freq, sin_mag, label="FFT(SIN)",color='RED')
    plt.title("FFT of SIN")
    plt.xlabel("Frequency (Hz)")
    plt.ylabel("Magnitude")
    plt.legend()

    plt.subplot(2, 1, 2)
    plt.plot(freq, cos_mag, label="FFT(COS)",color='BLUE')
    plt.title("FFT of COS")
    plt.xlabel("Frequency (Hz)")
    plt.ylabel("Magnitude")
    plt.legend()

    plt.tight_layout()
    plt.show()


if __name__ == "__main__":
    if len(sys.argv) !=2:
        print(f"Kullanım: {sys.argv[0]} dosya.bin")
        sys.exit(1)

    sin_values,cos_values,counter_values,lfsr_value=hexdump_file(sys.argv[1])

    verify_lfsr(lfsr_value[4096:])
    plot_sin_cos(sin_values,cos_values,counter_values)
    plot_fft(sin_values,cos_values)