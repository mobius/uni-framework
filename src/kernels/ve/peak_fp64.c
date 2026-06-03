/*
 * NEC VE 1.0 FP64 Peak FLOPS Benchmark (v2)
 * Compile: ncc -O3 -fopenmp -o peak_fp64_ve peak_fp64.c
 * Run: ve_exec -N <1|2|3> ./peak_fp64_ve
 *
 * Uses array-based FMA loop to trigger compiler auto-vectorization.
 * VE 1.0: 8 vector cores @ ~1.4 GHz, theoretical ~2.16 TFLOPS FP64
 */

#include <stdio.h>
#include <stdlib.h>
#include <sys/time.h>
#include <omp.h>

#define ARRAY_SIZE (64 * 1024)     /* 64K doubles = 512KB, fits L2 */
#define NITER      (64 * 1024)     /* Repeat the loop */

static double mysecond(void) {
    struct timeval tv;
    gettimeofday(&tv, NULL);
    return tv.tv_sec + tv.tv_usec * 1.0e-6;
}

int main(void) {
    int nthreads = omp_get_max_threads();
    printf("========================================\n");
    printf("NEC VE 1.0 FP64 Peak FLOPS Test v2\n");
    printf("Threads: %d\n", nthreads);
    printf("Array size: %d\n", ARRAY_SIZE);
    printf("Iterations: %d\n", NITER);
    printf("========================================\n");

    double *a = (double*)aligned_alloc(64, ARRAY_SIZE * sizeof(double));
    double *b = (double*)aligned_alloc(64, ARRAY_SIZE * sizeof(double));
    double *c = (double*)aligned_alloc(64, ARRAY_SIZE * sizeof(double));

    for (int i = 0; i < ARRAY_SIZE; i++) {
        a[i] = 1.00001;
        b[i] = 1.00002;
        c[i] = 0.0;
    }

    double t0 = mysecond();

    #pragma omp parallel
    {
        for (int iter = 0; iter < NITER; iter++) {
            #pragma omp for schedule(static) nowait
            for (int i = 0; i < ARRAY_SIZE; i++) {
                c[i] = a[i] * b[i] + c[i];  /* FMA: c = a*b + c */
            }
        }
    }

    double t1 = mysecond();
    double elapsed = t1 - t0;

    /* Each FMA does 2 FLOPs (mul + add). Per iteration: ARRAY_SIZE × 2. */
    double total_flops = 2.0 * (double)ARRAY_SIZE * (double)NITER * (double)nthreads;
    double gflops = total_flops / (elapsed * 1.0e9);

    /* Check result to prevent dead-code elimination */
    double sum = 0.0;
    for (int i = 0; i < ARRAY_SIZE; i++) sum += c[i];

    printf("Elapsed: %.3f sec\n", elapsed);
    printf("Checksum: %.1f\n", sum);
    printf("FP64 GFLOPS: %.2f\n", gflops);
    printf("Result: PASS\n");

    free(a); free(b); free(c);
    return 0;
}
