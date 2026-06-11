#!/usr/bin/env python3
import os
import sys
import fcntl
import struct
import subprocess
from pathlib import Path
from datetime import datetime


# ------------------------------------------------------------
# Sabit path tanımları
# ------------------------------------------------------------
BASE_DIR = Path(__file__).resolve().parent
TESTS_DIR = BASE_DIR / "tests"
LOAD_DRIVER_SCRIPT = TESTS_DIR / "load_driver.sh"
TOOLS_DIR = BASE_DIR / "tools"
XDMA_KO_PATH = BASE_DIR / "xdma" / "xdma.ko"
INPUTS_DIR = BASE_DIR / "data" / "inputs"
OUTPUTS_DIR = BASE_DIR / "data" / "outputs"

# load_driver.sh'yi terminalde nasıl çalıştırıyorsan onu burada taklit et.
# Manuelde "sudo bash load_driver.sh" çalışıyorsa boş bırak.
# Manuelde "sudo bash load_driver.sh 2" çalışıyorsa ["2"] yap.
LOAD_DRIVER_ARGS = []
# LOAD_DRIVER_ARGS = ["2"]


# ---------------------------------------------------------------
# Double buffer IOCTL sabitleri
# Kernel tanımı: _IOW('q', 9, struct xdma_dbuf_ioctl *)
#   _IOC_WRITE = 1  (kernel asm-generic/ioctl.h: #define _IOC_WRITE 1U)
#   _IOC_READ  = 2
#   size       = sizeof(pointer) = 8  (çünkü tanımda * kullanıldı)
# ---------------------------------------------------------------
_IOC_NONE  = 0
_IOC_WRITE = 1   # kernel: _IOC_WRITE=1U, _IOC_READ=2U
_IOC_READ  = 2

def _IOC(direction, ioc_type, nr, size):
    return (direction << 30) | (size << 16) | (ord(ioc_type) << 8) | nr

_XDMA_DBUF_STRUCT = struct.Struct("=IIQ")   # version(u32) buf_size(u32) ep_addr(u64)
# size=8: kernel tanımında struct xdma_dbuf_ioctl * (pointer boyutu)
IOCTL_XDMA_DBUF_START = _IOC(_IOC_WRITE, 'q', 9,  8)
IOCTL_XDMA_DBUF_STOP  = _IOC(_IOC_NONE,  'q', 10, 0)
XDMA_DBUF_V1 = 1


def dbuf_start(fd: int, buf_size: int, ep_addr: int = 0) -> None:
    """Kernel tarafında double buffer modunu başlat."""
    payload = _XDMA_DBUF_STRUCT.pack(XDMA_DBUF_V1, buf_size, ep_addr)
    fcntl.ioctl(fd, IOCTL_XDMA_DBUF_START, bytearray(payload))
    print(f"[DBUF] Double buffer başlatıldı: 2 × {buf_size} byte, ep=0x{ep_addr:X}")


def dbuf_stop(fd: int) -> None:
    """Double buffer modunu durdur ve kernel belleklerini serbest bırak."""
    fcntl.ioctl(fd, IOCTL_XDMA_DBUF_STOP)
    print("[DBUF] Double buffer durduruldu.")


def dbuf_read_loop(fd: int, buf_size: int, transfer_count: int,
                   output_file: Path) -> None:
    """
    Double buffer ping-pong okuma döngüsü.
    Her read() çağrısı; kernel tamamlanmış buffer'ı döner ve
    aynı anda yenisini DMA kuyruğuna ekler — pipeline hiç boşalmaz.
    """
    print(f"[DBUF] {transfer_count} × {buf_size} byte okuma başlıyor...")
    with open(output_file, "wb") as f:
        for i in range(transfer_count):
            chunk = os.read(fd, buf_size)
            if len(chunk) != buf_size:
                raise IOError(
                    f"[DBUF] Transfer {i}: beklenen {buf_size} byte, "
                    f"alınan {len(chunk)} byte"
                )
            f.write(chunk)
    print(f"[DBUF] Okuma tamamlandı → {output_file}")


def ask_input(prompt: str, validator=None, default=None):
    while True:
        if default is not None:
            value = input(f"{prompt} [{default}]: ").strip()
            if value == "":
                value = default
        else:
            value = input(f"{prompt}: ").strip()

        if validator is None:
            return value

        try:
            if validator(value):
                return value
        except Exception:
            pass

        print("Geçersiz giriş, tekrar deneyin.")


def is_positive_int(value: str) -> bool:
    return value.isdigit() and int(value) > 0


def ensure_dir(path: Path):
    path.mkdir(parents=True, exist_ok=True)


def run_command(cmd, background=False, cwd=None, use_sudo=False):
    cmd = [str(x) for x in cmd]

    if use_sudo and os.geteuid() != 0:
        cmd = ["sudo", "-E"] + cmd

    print(f"\n[CMD] {' '.join(cmd)}")
    if cwd is not None:
        print(f"[CWD] {cwd}")

    if background:
        return subprocess.Popen(cmd, cwd=cwd)
    else:
        return subprocess.run(cmd, cwd=cwd)


def generate_binary_file(file_path: Path, size_bytes: int):
    print(f"[INFO] Binary input oluşturuluyor: {file_path} ({size_bytes} byte)")
    chunk_size = 1024 * 1024
    remaining = size_bytes

    with open(file_path, "wb") as f:
        pattern = bytes([i % 256 for i in range(256)])
        while remaining > 0:
            write_size = min(chunk_size, remaining)
            repeated = (pattern * ((write_size // len(pattern)) + 1))[:write_size]
            f.write(repeated)
            remaining -= write_size


def compare_files(file1: Path, file2: Path, compare_size: int) -> bool:
    print(f"[INFO] Veri doğrulama yapılıyor:")
    print(f"       input : {file1}")
    print(f"       output: {file2}")

    chunk_size = 1024 * 1024
    remaining = compare_size

    with open(file1, "rb") as f1, open(file2, "rb") as f2:
        while remaining > 0:
            read_size = min(chunk_size, remaining)
            b1 = f1.read(read_size)
            b2 = f2.read(read_size)
            if b1 != b2:
                return False
            remaining -= read_size

    return True


def tool_exists(tool_path: Path) -> bool:
    return tool_path.exists() and os.access(tool_path, os.X_OK)


def main():
    print("=== XDMA Generic Test Aracı ===")

    # ------------------------------------------------------------
    # Driver script ve .ko doğrulama
    # ------------------------------------------------------------
    if not LOAD_DRIVER_SCRIPT.exists():
        print(f"[ERROR] load_driver.sh bulunamadı: {LOAD_DRIVER_SCRIPT}")
        sys.exit(1)

    if not XDMA_KO_PATH.exists():
        print(f"[WARN] xdma.ko bulunamadı: {XDMA_KO_PATH}")
        print("[WARN] load_driver.sh relative path kullandığı için çağrı başarısız olabilir.")

    print(f"[INFO] Driver yükleme scripti : {LOAD_DRIVER_SCRIPT}")
    print(f"[INFO] Driver script cwd      : {TESTS_DIR}")

    # ------------------------------------------------------------
    # Driver yükleme
    # Not: load_driver.sh değiştirilmedi.
    # Bu yüzden scripti tests klasöründe çalıştırıyoruz.
    # ------------------------------------------------------------
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

    print("[INFO] Driver başarıyla yüklendi.")

    # ------------------------------------------------------------
    # Tool path kontrolü
    # ------------------------------------------------------------
    tool_dir_input = ask_input(
        "dma araçlarının bulunduğu klasör yolu",
        validator=lambda x: Path(x).exists(),
        default=str(TOOLS_DIR)
    )
    tool_dir = Path(tool_dir_input)

    dma_from_device = tool_dir / "dma_from_device"
    dma_to_device = tool_dir / "dma_to_device"

    # ------------------------------------------------------------
    # Kullanıcı girdileri
    # ------------------------------------------------------------
    input_root = Path(
        ask_input(
            "Input ana klasörü",
            validator=lambda x: len(x) > 0,
            default=str(INPUTS_DIR)
        )
    )
    output_root = Path(
        ask_input(
            "Output ana klasörü",
            validator=lambda x: len(x) > 0,
            default=str(OUTPUTS_DIR)
        )
    )

    mode = ask_input(
        "Test modu seç (read / write / both / dbuf)",
        validator=lambda x: x.lower() in ["read", "write", "both", "dbuf"],
        default="both"
    ).lower()

    channel_user = ask_input(
        "Kanal seç (1, 2, 3, 4)",
        validator=lambda x: x in ["1", "2", "3", "4"]
    )
    channel_user = int(channel_user)
    channel_idx = channel_user - 1

    write_size = write_count = None
    read_size = read_count = None

    if mode in ["write", "both"]:
        write_size = int(
            ask_input(
                "Write/H2C transfer boyutu (byte)",
                validator=is_positive_int,
                default="32" if mode == "both" else "4096"
            )
        )
        write_count = int(
            ask_input(
                "Write/H2C transfer count",
                validator=is_positive_int,
                default="1"
            )
        )

    if mode in ["read", "both", "dbuf"]:
        read_size = int(
            ask_input(
                "Read/C2H transfer boyutu (byte)",
                validator=is_positive_int,
                default="134217728" if mode == "both" else "4096"
            )
        )
        read_count = int(
            ask_input(
                "Read/C2H transfer count",
                validator=is_positive_int,
                default="1"
            )
        )

    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    input_dir = input_root / f"{timestamp}_input"
    output_dir = output_root / f"{timestamp}_output"

    ensure_dir(input_dir)
    ensure_dir(output_dir)

    print(f"[INFO] Input klasörü : {input_dir}")
    print(f"[INFO] Output klasörü: {output_dir}")

    input_file = (
        input_dir / f"input_ch{channel_user}_{write_size}B_x{write_count}.bin"
        if write_size is not None else None
    )
    output_file = (
        output_dir / f"output_ch{channel_user}_{read_size}B_x{read_count}.bin"
        if read_size is not None else None
    )

    # ------------------------------------------------------------
    # Tool doğrulama
    # ------------------------------------------------------------
    if mode in ["read", "both"] and not tool_exists(dma_from_device):
        print(f"[ERROR] dma_from_device bulunamadı veya çalıştırılabilir değil: {dma_from_device}")
        sys.exit(1)

    if mode in ["write", "both"] and not tool_exists(dma_to_device):
        print(f"[ERROR] dma_to_device bulunamadı veya çalıştırılabilir değil: {dma_to_device}")
        sys.exit(1)

    # ------------------------------------------------------------
    # Double buffer modu — LFSR sürekliliği için
    # ------------------------------------------------------------
    if mode == "dbuf":
        c2h_dev = f"/dev/xdma0_c2h_{channel_idx}"
        print(f"[DBUF] Cihaz: {c2h_dev}")
        try:
            fd = os.open(c2h_dev, os.O_RDONLY)
            dbuf_start(fd, read_size, ep_addr=0)
            try:
                dbuf_read_loop(fd, read_size, read_count, output_file)
            finally:
                dbuf_stop(fd)
                os.close(fd)
        except Exception as e:
            print(f"[ERROR] Double buffer okuma hatası: {e}")
            sys.exit(1)
        print("[INFO] Double buffer testi başarıyla tamamlandı.")
        sys.exit(0)

    # ------------------------------------------------------------
    # Input üret
    # ------------------------------------------------------------
    if mode in ["write", "both"]:
        generate_binary_file(input_file, write_size)

    c2h_dev = f"/dev/xdma0_c2h_{channel_idx}"
    h2c_dev = f"/dev/xdma0_h2c_{channel_idx}"

    processes = []

    try:
        # Önce read başlat
        if mode in ["read", "both"]:
            print(f"[INFO] c2h read başlatılıyor, device: {c2h_dev}")
            cmd_read = [
                str(dma_from_device),
                "-d", c2h_dev,
                "-f", str(output_file),
                "-s", str(read_size),
                "-c", str(read_count),
            ]
            processes.append(("read", run_command(cmd_read, background=True)))

        # both için kısa bekleme
        if mode == "both":
            import time
            time.sleep(1)

        # write başlat
        if mode in ["write", "both"]:
            print(f"[INFO] h2c write başlatılıyor, device: {h2c_dev}")
            cmd_write = [
                str(dma_to_device),
                "-d", h2c_dev,
                "-f", str(input_file),
                "-s", str(write_size),
                "-c", str(write_count),
            ]
            processes.append(("write", run_command(cmd_write, background=True)))

        # process bekleme
        test_error = False
        for name, proc in processes:
            ret = proc.wait()
            if ret != 0:
                print(f"[ERROR] {name} işlemi başarısız oldu. return code = {ret}")
                test_error = True

        if test_error:
            sys.exit(1)

        if mode == "both":
            print(f"[INFO] H2C toplam byte: {write_size * write_count}")
            print(f"[INFO] C2H toplam byte: {read_size * read_count}")
            if write_size == read_size and write_count == read_count:
                ok = compare_files(input_file, output_file, write_size * write_count)
                if not ok:
                    print("[ERROR] Yazılan veri ile okunan veri eşleşmedi.")
                    sys.exit(1)
                print("[INFO] Loopback veri doğrulama başarılı.")
            else:
                print("[INFO] H2C tetik ve C2H veri boyutları farklı; loopback karşılaştırması atlandı.")

        print("[INFO] Test başarıyla tamamlandı.")

    except KeyboardInterrupt:
        print("\n[WARN] Kullanıcı testi durdurdu.")
        for _, proc in processes:
            try:
                proc.terminate()
            except Exception:
                pass
        sys.exit(1)


if __name__ == "__main__":
    main()
