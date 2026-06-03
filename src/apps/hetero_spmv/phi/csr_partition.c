/*
 * Phi SpMV 内核
 *
 * 读取 CSR 分块 + x 向量, 计算 y[row] = sum(vals[j] * x[cols[j]])
 * 利用 Phi 244 线程并行处理不规则访存 (SpMV 天然不规则)。
 *
 * Run: micnativeloadex spmv_phi.mic -d 0 -t 60 -a "block.bin y_out.bin"
 *
 * Input format: N, nnz, col_start, col_end, row_ptr[N+1], cols[nnz], vals[nnz], x[N]
 * Output: N, y[N]
 */

#include <stdio.h>
#include <stdlib.h>
#include <omp.h>
#include <sys/time.h>

static double wall_sec(void) {
    struct timeval tv;
    gettimeofday(&tv, NULL);
    return tv.tv_sec + tv.tv_usec * 1e-6;
}

int main(int argc, char *argv[]) {
    if (argc != 3) {
        fprintf(stderr, "Usage: %s block.bin y_out.bin\n", argv[0]);
        return 1;
    }

    FILE *fin = fopen(argv[1], "rb");
    if (!fin) { perror(argv[1]); return 1; }

    int N, nnz, cs, ce;
    fread(&N, sizeof(int), 1, fin);
    fread(&nnz, sizeof(int), 1, fin);
    fread(&cs, sizeof(int), 1, fin);
    fread(&ce, sizeof(int), 1, fin);

    int *row_ptr = (int *)malloc((N + 1) * sizeof(int));
    int *cols    = (int *)malloc(nnz * sizeof(int));
    double *vals = (double *)malloc(nnz * sizeof(double));
    double *x    = (double *)malloc(N * sizeof(double));
    double *y    = (double *)calloc(N, sizeof(double));

    fread(row_ptr, sizeof(int), N + 1, fin);
    fread(cols, sizeof(int), nnz, fin);
    fread(vals, sizeof(double), nnz, fin);
    fread(x, sizeof(double), N, fin);
    fclose(fin);

    double t0 = wall_sec();

    /* SpMV: y[row] += val * x[col] — irregular, Phi's 244 threads handle well */
    #pragma omp parallel for schedule(dynamic, 32)
    for (int row = 0; row < N; row++) {
        double sum = 0.0;
        int start = row_ptr[row], end = row_ptr[row + 1];
        for (int j = start; j < end; j++) {
            sum += vals[j] * x[cols[j]];
        }
        y[row] = sum;
    }

    double elapsed = wall_sec() - t0;

    double checksum = 0.0;
    for (int i = 0; i < N; i++) checksum += y[i];

    double flops = 2.0 * (double)nnz;
    double gflops = flops / elapsed / 1e9;

    printf("Phi SpMV: N=%d nnz=%d cols=[%d,%d) elapsed=%.4fs GFLOPS=%.1f checksum=%.3f\n",
           N, nnz, cs, ce, elapsed, gflops, checksum);

    FILE *fout = fopen(argv[2], "wb");
    fwrite(&N, sizeof(int), 1, fout);
    fwrite(y, sizeof(double), N, fout);
    fclose(fout);

    free(row_ptr); free(cols); free(vals); free(x); free(y);
    return 0;
}
