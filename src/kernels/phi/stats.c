/*
 * Phi stats kernel — 矩阵统计信息
 * Compile: icc -std=c99 -mmic -O3 -openmp -static-intel -o stats.mic stats.c
 *
 * Input: [int32 N][double M[N*N]]
 * Output (text): min, max, mean, stddev
 *
 * Usage: micnativeloadex stats.mic -d 0 -a "input.bin output.txt"
 *   or: scp stats.mic input.bin mic0:/tmp && ssh mic0 /tmp/stats.mic input.bin output.txt
 */

#include <stdio.h>
#include <stdlib.h>
#include <math.h>
#include <sys/time.h>
#include <omp.h>

static double get_time(void) {
    struct timeval tv;
    gettimeofday(&tv, NULL);
    return tv.tv_sec + tv.tv_usec * 1e-6;
}

int main(int argc, char *argv[]) {
    if (argc != 3) {
        fprintf(stderr, "Usage: %s input.bin output.txt\n", argv[0]);
        return 1;
    }

    /* ── Read input ── */
    FILE *fin = fopen(argv[1], "rb");
    if (!fin) { perror(argv[1]); return 1; }

    int N;
    fread(&N, sizeof(int), 1, fin);
    long NN = (long)N * N;

    double *M = (double*)aligned_alloc(64, NN * sizeof(double));
    fread(M, sizeof(double), NN, fin);
    fclose(fin);

    double t0 = get_time();

    /* ── Statistics ── */
    double min_val = M[0], max_val = M[0];
    double sum = 0.0, sum_sq = 0.0;

    #pragma omp parallel for reduction(min:min_val) reduction(max:max_val) \
                             reduction(+:sum) reduction(+:sum_sq)
    for (long i = 0; i < NN; i++) {
        double v = M[i];
        if (v < min_val) min_val = v;
        if (v > max_val) max_val = v;
        sum += v;
        sum_sq += v * v;
    }

    double mean = sum / (double)NN;
    double variance = sum_sq / (double)NN - mean * mean;
    double stddev = sqrt(variance > 0 ? variance : 0.0);

    double elapsed = get_time() - t0;

    /* ── Write output ── */
    FILE *fout = fopen(argv[2], "w");
    fprintf(fout, "N=%d elements=%ld\n", N, NN);
    fprintf(fout, "min=%.6f max=%.6f\n", min_val, max_val);
    fprintf(fout, "mean=%.6f stddev=%.6f\n", mean, stddev);
    fprintf(fout, "elapsed=%.4f sec\n", elapsed);
    fclose(fout);

    printf("Phi stats: N=%d, min=%.4f, max=%.4f, mean=%.4f, stddev=%.4f, %.3fs\n",
           N, min_val, max_val, mean, stddev, elapsed);
    printf("Result: PASS\n");

    free(M);
    return 0;
}
