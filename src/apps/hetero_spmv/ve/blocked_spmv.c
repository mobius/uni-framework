/*
 * VE 分块 SpMV — 稀疏矩阵×向量 (子块)
 *
 * 读取 Phi 分块后的 CSR 子集 + 完整 x 向量，
 * 计算 y[row] = sum(vals[j] * x[cols[j]]) 对分配给本 VE 的非零元。
 *
 * Compile: ncc -O3 -fopenmp -o blocked_spmv_ve blocked_spmv.c
 *
 * Run: ve_exec -N <id> ./blocked_spmv_ve input_block.bin output.bin
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
        fprintf(stderr, "Usage: %s input_block.bin output.bin\n", argv[0]);
        return 1;
    }

    FILE *fin = fopen(argv[1], "rb");
    if (!fin) { perror(argv[1]); return 1; }

    int N, nnz, col_start, col_end;
    fread(&N, sizeof(int), 1, fin);
    fread(&nnz, sizeof(int), 1, fin);
    fread(&col_start, sizeof(int), 1, fin);
    fread(&col_end, sizeof(int), 1, fin);

    int *row_ptr = (int *)malloc((N + 1) * sizeof(int));
    int *cols    = (int *)malloc(nnz * sizeof(int));
    double *vals = (double *)aligned_alloc(64, nnz * sizeof(double));
    double *x    = (double *)aligned_alloc(64, N * sizeof(double));
    double *y    = (double *)aligned_alloc(64, N * sizeof(double));

    fread(row_ptr, sizeof(int), N + 1, fin);
    fread(cols, sizeof(int), nnz, fin);
    fread(vals, sizeof(double), nnz, fin);
    fread(x, sizeof(double), N, fin);
    fclose(fin);

    /* Initialize y */
    for (int i = 0; i < N; i++) y[i] = 0.0;

    double t0 = wall_sec();

    /* SpMV: y[row] += val * x[col] */
    #pragma omp parallel for schedule(dynamic, 64)
    for (int row = 0; row < N; row++) {
        double sum = 0.0;
        int start = row_ptr[row];
        int end   = row_ptr[row + 1];
        #pragma omp simd reduction(+:sum)
        for (int j = start; j < end; j++) {
            sum += vals[j] * x[cols[j]];
        }
        y[row] = sum;
    }

    double elapsed = wall_sec() - t0;

    /* Checksum */
    double checksum = 0.0;
    for (int i = 0; i < N; i++) checksum += y[i];

    /* GB/s effective */
    double gb = (double)(nnz * (sizeof(int) + sizeof(double)) + N * sizeof(double)) / 1e9;
    double bw = gb / elapsed;

    printf("VE SpMV: N=%d nnz=%d cols=[%d,%d) elapsed=%.4fs checksum=%.3f BW=%.2f_GB/s\n",
           N, nnz, col_start, col_end, elapsed, checksum, bw);

    /* Write output */
    FILE *fout = fopen(argv[2], "wb");
    fwrite(&N, sizeof(int), 1, fout);
    fwrite(y, sizeof(double), N, fout);
    fclose(fout);

    free(row_ptr); free(cols); free(vals); free(x); free(y);
    return 0;
}
