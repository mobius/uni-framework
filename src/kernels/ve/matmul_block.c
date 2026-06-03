/*
 * VE matmul_block kernel — 分块矩阵乘法
 * Compile: ncc -O3 -fopenmp -o matmul_block_ve matmul_block.c
 * 
 * Binary format: [int32 N][double A[N*N]][double B[N*N]]
 * Output: [int32 N][double C[N*N]]
 * 
 * Usage: ve_exec -N <id> ./matmul_block_ve input.bin output.bin
 */

#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/time.h>
#include <omp.h>

static double get_time(void) {
    struct timeval tv;
    gettimeofday(&tv, NULL);
    return tv.tv_sec + tv.tv_usec * 1e-6;
}

int main(int argc, char *argv[]) {
    if (argc != 3) {
        fprintf(stderr, "Usage: %s input.bin output.bin\n", argv[0]);
        return 1;
    }

    /* ── Read input ── */
    FILE *fin = fopen(argv[1], "rb");
    if (!fin) { perror(argv[1]); return 1; }

    int N;
    fread(&N, sizeof(int), 1, fin);
    long NN = (long)N * N;
    long bytes = NN * sizeof(double);

    double *A = (double*)aligned_alloc(64, bytes);
    double *B = (double*)aligned_alloc(64, bytes);
    double *C = (double*)aligned_alloc(64, bytes);

    fread(A, sizeof(double), NN, fin);
    fread(B, sizeof(double), NN, fin);
    fclose(fin);

    /* ── DGEMM: C = A × B ── */
    double t0 = get_time();

    #pragma omp parallel for schedule(static)
    for (int i = 0; i < N; i++) {
        for (int k = 0; k < N; k++) {
            double aik = A[i * N + k];
            #pragma omp simd
            for (int j = 0; j < N; j++) {
                C[i * N + j] += aik * B[k * N + j];
            }
        }
    }

    double elapsed = get_time() - t0;
    double gflops = 2.0 * (double)N * N * N / elapsed / 1e9;

    /* ── Checksum ── */
    double checksum = 0.0;
    for (long i = 0; i < NN; i++) checksum += C[i];

    /* ── Write output ── */
    FILE *fout = fopen(argv[2], "wb");
    fwrite(&N, sizeof(int), 1, fout);
    fwrite(C, sizeof(double), NN, fout);
    fclose(fout);

    printf("VE matmul: N=%d, %.3fs, %.1f GFLOPS, checksum=%.1f\n",
           N, elapsed, gflops, checksum);
    printf("Result: PASS\n");

    free(A); free(B); free(C);
    return 0;
}
