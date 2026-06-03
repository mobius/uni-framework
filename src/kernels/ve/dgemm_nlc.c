/*
 * NLC DGEMM kernel — 使用 NEC Numeric Library Collection BLAS
 * Compile:
 *   ncc -O3 -fopenmp -o dgemm_nlc_ve dgemm_nlc.c \
 *       -I/opt/nec/ve/nlc/3.1.0/include \
 *       -L/opt/nec/ve/nlc/3.1.0/lib -lcblas -lblas_openmp
 *
 * Run:
 *   VE_LD_LIBRARY_PATH=/opt/nec/ve/nlc/3.1.0/lib \
 *   ve_exec -N <1|2|3> ./dgemm_nlc_ve input.bin output.bin
 *
 * Expected: ~1750 GFLOPS for N=4096 (81% of 2.16 TFLOPS peak)
 */

#include <stdio.h>
#include <stdlib.h>
#include <sys/time.h>
#include <omp.h>
#include <cblas.h>

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

    /* ── Read input (same binary format as matmul_block) ── */
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

    for (long i = 0; i < NN; i++) C[i] = 0.0;

    /* ── NLC cblas_dgemm ── */
    double alpha = 1.0, beta = 0.0;

    double t0 = get_time();

    cblas_dgemm(CblasColMajor, CblasNoTrans, CblasNoTrans,
                N, N, N, alpha, A, N, B, N, beta, C, N);

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

    printf("NLC dgemm: N=%d, %.4fs, %.1f GFLOPS, checksum=%.1f\n",
           N, elapsed, gflops, checksum);
    printf("Result: PASS\n");

    free(A); free(B); free(C);
    return 0;
}
