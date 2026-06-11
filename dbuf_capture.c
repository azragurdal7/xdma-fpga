/*
 * dbuf_capture.c — XDMA Double Buffer C2H Capture
 *
 * Kullanım:
 *   sudo ./dbuf_capture -d /dev/xdma0_c2h_3 -n 200 -o capture.bin
 *
 * Seçenekler:
 *   -d <cihaz>   C2H cihazı (varsayılan: /dev/xdma0_c2h_3)
 *   -n <adet>    Transfer sayısı (varsayılan: 200)
 *   -s <boyut>   Buffer boyutu byte (varsayılan: 4096, PAGE_SIZE katı olmalı)
 *   -o <dosya>   Çıktı dosyası (varsayılan: capture.bin)
 */

#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <stdint.h>
#include <fcntl.h>
#include <unistd.h>
#include <sys/ioctl.h>
#include <sys/time.h>
#include <linux/ioctl.h>

/* ---- driver ioctl tanımları (cdev_sgdma.h'tan) ---- */
#define XDMA_DBUF_V1  1

struct xdma_dbuf_ioctl {
    uint32_t version;
    uint32_t buf_size;
    uint64_t ep_addr;
};

#define IOCTL_XDMA_DBUF_START  _IOW('q', 9,  struct xdma_dbuf_ioctl *)
#define IOCTL_XDMA_DBUF_STOP   _IO('q',  10)
/* ---------------------------------------------------- */

#define DEFAULT_DEV    "/dev/xdma0_c2h_3"
#define DEFAULT_COUNT  200
#define DEFAULT_SIZE   4096
#define DEFAULT_OUT    "capture.bin"

static double elapsed_ms(struct timeval *t0, struct timeval *t1)
{
    return (t1->tv_sec - t0->tv_sec) * 1000.0
         + (t1->tv_usec - t0->tv_usec) / 1000.0;
}

int main(int argc, char *argv[])
{
    const char *dev  = DEFAULT_DEV;
    const char *out  = DEFAULT_OUT;
    int         n    = DEFAULT_COUNT;
    int         size = DEFAULT_SIZE;

    /* argüman ayrıştırma */
    for (int i = 1; i < argc; i++) {
        if (!strcmp(argv[i], "-d") && i+1 < argc) dev  = argv[++i];
        else if (!strcmp(argv[i], "-o") && i+1 < argc) out  = argv[++i];
        else if (!strcmp(argv[i], "-n") && i+1 < argc) n    = atoi(argv[++i]);
        else if (!strcmp(argv[i], "-s") && i+1 < argc) size = atoi(argv[++i]);
        else { fprintf(stderr, "Bilinmeyen argüman: %s\n", argv[i]); return 1; }
    }

    printf("Cihaz  : %s\n", dev);
    printf("Boyut  : %d byte/transfer\n", size);
    printf("Adet   : %d transfer\n", n);
    printf("Toplam : %ld byte = %d sample\n", (long)size * n, (size * n) / 32);
    printf("Çıktı  : %s\n\n", out);

    /* tüm veriyi RAM'e al, döngü içinde disk I/O yok */
    long total_size = (long)size * n;
    char *buf = malloc(total_size);
    if (!buf) { perror("malloc"); return 1; }

    /* cihaz aç */
    int fd = open(dev, O_RDONLY);
    if (fd < 0) { perror("open"); free(buf); return 1; }

    /* çıktı dosyası aç */
    FILE *fp = fopen(out, "wb");
    if (!fp) { perror("fopen"); close(fd); free(buf); return 1; }

    /* double buffer başlat */
    struct xdma_dbuf_ioctl dbuf = {
        .version  = XDMA_DBUF_V1,
        .buf_size = (uint32_t)size,
        .ep_addr  = 0,
    };
    if (ioctl(fd, IOCTL_XDMA_DBUF_START, &dbuf) < 0) {
        perror("ioctl DBUF_START");
        fclose(fp); close(fd); free(buf);
        return 1;
    }
    printf("[DBUF] Double buffer başlatıldı: 2 × %d byte\n", size);

    /* yakalama döngüsü */
    struct timeval t0, t1;
    gettimeofday(&t0, NULL);

    /* --- kritik döngü: sadece read(), disk I/O yok --- */
    long total = 0;
    for (int i = 0; i < n; i++) {
        ssize_t r = read(fd, buf + (long)i * size, size);
        if (r != size) {
            fprintf(stderr, "\n[HATA] Transfer %d: beklenen %d, alınan %zd\n",
                    i, size, r);
            break;
        }
        total += size;
    }
    /* ------------------------------------------------- */

    gettimeofday(&t1, NULL);

    /* double buffer durdur */
    if (ioctl(fd, IOCTL_XDMA_DBUF_STOP) < 0)
        perror("ioctl DBUF_STOP");
    close(fd);

    /* hız raporu */
    double ms  = elapsed_ms(&t0, &t1);
    double mbs = (total / 1024.0 / 1024.0) / (ms / 1000.0);
    printf("\n[TAMAMLANDI — yakalama]\n");
    printf("  Süre          : %.2f ms\n", ms);
    printf("  Yakalanan     : %ld byte (%.2f MB)\n", total, total / 1024.0 / 1024.0);
    printf("  Efektif hız   : %.1f MB/s\n", mbs);
    printf("  FPGA hızı     : 800 MB/s\n");
    printf("  Kullanım oranı: %.1f%%\n", mbs / 800.0 * 100.0);

    /* diske yaz (döngü dışında) */
    printf("\nDiske yazılıyor: %s ...\n", out);
    fwrite(buf, 1, total, fp);

    fclose(fp);
    free(buf);
    printf("  Çıktı dosyası : %s\n", out);

    return 0;
}
