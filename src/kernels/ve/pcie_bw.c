/*
 * VE PCIe 带宽测试
 *
 * 测量 Host↔VE 数据传输速率:
 *   - H2D: Host → VE (文件读取)
 *   - D2H: VE → Host (文件写入)
 *
 * 原理: VE 的根文件系统就是 Host, fopen/fread 走 PCIe
 *
 * Compile:
 *   ncc -O3 -o pcie_bw_ve pcie_bw.c
 *
 * Run:
 *   ve_exec -N <1|2|3> ./pcie_bw_ve <MB>
 */

#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/time.h>

static double wall_sec(void) {
    struct timeval tv;
    gettimeofday(&tv, NULL);
    return tv.tv_sec + tv.tv_usec * 1e-6;
}

int main(int argc, char *argv[]) {
    int mb = 256;  /* default 256 MB */
    if (argc > 1) mb = atoi(argv[1]);
    if (mb < 1 || mb > 4096) {
        fprintf(stderr, "MB must be 1-4096\n");
        return 1;
    }

    size_t bytes = (size_t)mb * 1024UL * 1024UL;
    size_t nelem = bytes / sizeof(double);

    double *buf = (double *)aligned_alloc(64, bytes);
    if (!buf) {
        fprintf(stderr, "alloc %zu bytes failed\n", bytes);
        return 1;
    }

    /* Fill buffer with known pattern */
    for (size_t i = 0; i < nelem; i++) {
        buf[i] = (double)(i & 0xFFFF);
    }

    const char *path = "/tmp/uni_pcie_bw_test.bin";

    /* ── D2H: VE → Host 写入 ── */
    double t_d2h = wall_sec();
    FILE *f = fopen(path, "wb");
    if (!f) { perror(path); free(buf); return 1; }
    fwrite(buf, 1, bytes, f);
    fclose(f);
    t_d2h = wall_sec() - t_d2h;

    /* 清空 buffer */
    memset(buf, 0, bytes);

    /* ── H2D: Host → VE 读取 ── */
    double t_h2d = wall_sec();
    f = fopen(path, "rb");
    if (!f) { perror(path); free(buf); return 1; }
    size_t got = fread(buf, 1, bytes, f);
    fclose(f);
    t_h2d = wall_sec() - t_h2d;

    /* 清理 */
    remove(path);

    /* 校验: 检查非零元素是否被正确读回 (索引1 → 1.0) */
    int ok = (got == bytes && nelem > 1 && buf[1] == 1.0) ? 1 : 0;

    double h2d_gbs = (bytes / 1e9) / t_h2d;
    double d2h_gbs = (bytes / 1e9) / t_d2h;

    printf("PCIe_BW: MB=%d H2D=%.2f_GB/s D2H=%.2f_GB/s total=%.2f_GB/s verify=%s\n",
           mb, h2d_gbs, d2h_gbs, h2d_gbs + d2h_gbs, ok ? "OK" : "FAIL");

    free(buf);
    return ok ? 0 : 1;
}
