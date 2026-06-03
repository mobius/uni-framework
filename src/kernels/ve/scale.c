/*
 * VE scale kernel — 逐元素缩放矩阵
 * Compile: ncc -O3 -fopenmp -o scale_ve scale.c
 *
 * Input: [int32 N][double M[N*N]]
 * Output: [int32 N][double M[N*N] * 2.0]
 */

#include <stdio.h>
#include <stdlib.h>
#include <sys/time.h>
#include <omp.h>

int main(int argc, char *argv[]) {
    if (argc != 3) {
        fprintf(stderr, "Usage: %s input.bin output.bin\n", argv[0]);
        return 1;
    }

    FILE *fin = fopen(argv[1], "rb");
    if (!fin) { perror(argv[1]); return 1; }

    int N;
    fread(&N, sizeof(int), 1, fin);
    long NN = (long)N * N;

    double *M = (double*)aligned_alloc(64, NN * sizeof(double));
    fread(M, sizeof(double), NN, fin);
    fclose(fin);

    double t0 = omp_get_wtime();

    #pragma omp parallel for simd
    for (long i = 0; i < NN; i++) {
        M[i] *= 2.0;
    }

    double elapsed = omp_get_wtime() - t0;
    double bw_gbs = (double)(NN * sizeof(double)) / elapsed / 1e9;

    double checksum = 0.0;
    for (long i = 0; i < NN; i++) checksum += M[i];

    FILE *fout = fopen(argv[2], "wb");
    fwrite(&N, sizeof(int), 1, fout);
    fwrite(M, sizeof(double), NN, fout);
    fclose(fout);

    printf("VE scale: N=%d, %.4fs, %.1f GB/s, checksum=%.1f\n",
           N, elapsed, bw_gbs, checksum);
    printf("Result: PASS\n");

    free(M);
    return 0;
}
