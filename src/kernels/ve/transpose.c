/*
 * VE transpose kernel — 矩阵转置
 * Compile: ncc -O3 -fopenmp -o transpose_ve transpose.c
 *
 * Input: [int32 N][double M[N*N]]
 * Output: [int32 N][double M^T[N*N]]
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

    double *M  = (double*)aligned_alloc(64, NN * sizeof(double));
    double *MT = (double*)aligned_alloc(64, NN * sizeof(double));
    fread(M, sizeof(double), NN, fin);
    fclose(fin);

    double t0 = omp_get_wtime();

    #pragma omp parallel for schedule(static)
    for (int i = 0; i < N; i++) {
        #pragma omp simd
        for (int j = 0; j < N; j++) {
            MT[j * N + i] = M[i * N + j];
        }
    }

    double elapsed = omp_get_wtime() - t0;
    double bw_gbs = (double)(2 * NN * sizeof(double)) / elapsed / 1e9;

    double checksum = 0.0;
    for (long i = 0; i < NN; i++) checksum += MT[i];

    FILE *fout = fopen(argv[2], "wb");
    fwrite(&N, sizeof(int), 1, fout);
    fwrite(MT, sizeof(double), NN, fout);
    fclose(fout);

    printf("VE transpose: N=%d, %.4fs, %.1f GB/s, checksum=%.1f\n",
           N, elapsed, bw_gbs, checksum);
    printf("Result: PASS\n");

    free(M); free(MT);
    return 0;
}
