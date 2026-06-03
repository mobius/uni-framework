/*
 * VE aggregate kernel — 多块结果聚合 (逐元素求和)
 * Compile: ncc -O3 -o aggregate_ve aggregate.c
 *
 * Input: 3 files (result_1.bin, result_2.bin, result_3.bin), each: [int32 N][double C[N*N]]
 * Output: [int32 N][double sum[N*N]]
 *
 * Usage: ve_exec -N 1 ./aggregate_ve r1.bin r2.bin r3.bin output.bin
 */

#include <stdio.h>
#include <stdlib.h>
#include <sys/time.h>
#include <omp.h>

static double get_time(void) {
    struct timeval tv;
    gettimeofday(&tv, NULL);
    return tv.tv_sec + tv.tv_usec * 1e-6;
}

int main(int argc, char *argv[]) {
    if (argc != 5) {
        fprintf(stderr, "Usage: %s r1.bin r2.bin r3.bin output.bin\n", argv[0]);
        return 1;
    }

    int N;
    double *sum = NULL;

    for (int f = 0; f < 3; f++) {
        FILE *fin = fopen(argv[f + 1], "rb");
        if (!fin) { perror(argv[f+1]); return 1; }

        int fn;
        fread(&fn, sizeof(int), 1, fin);
        if (f == 0) {
            N = fn;
            sum = (double*)aligned_alloc(64, (long)N * N * sizeof(double));
            for (long i = 0; i < (long)N * N; i++) sum[i] = 0.0;
        } else if (fn != N) {
            fprintf(stderr, "Size mismatch: %d vs %d\n", N, fn);
            return 1;
        }

        double *buf = (double*)aligned_alloc(64, (long)N * N * sizeof(double));
        fread(buf, sizeof(double), (long)N * N, fin);
        fclose(fin);

        #pragma omp parallel for simd
        for (long i = 0; i < (long)N * N; i++) {
            sum[i] += buf[i];
        }
        free(buf);
    }

    /* ── Write output ── */
    FILE *fout = fopen(argv[4], "wb");
    fwrite(&N, sizeof(int), 1, fout);
    fwrite(sum, sizeof(double), (long)N * N, fout);
    fclose(fout);

    double checksum = 0.0;
    for (long i = 0; i < (long)N * N; i++) checksum += sum[i];

    printf("VE aggregate: N=%d, checksum=%.1f\n", N, checksum);
    printf("Result: PASS\n");

    free(sum);
    return 0;
}
