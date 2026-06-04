/*
 * Phi 路径生成内核 — Monte Carlo 期权模拟
 * 几何布朗运动: S_{t+1} = S_t * exp((mu - 0.5*sigma²)*dt + sigma*sqrt(dt)*Z)
 * 检测 barrier (down-and-out), 输出有效路径均价
 *
 * Run: micnativeloadex path_gen.mic -d 0 -t 60 -a "params.bin paths.bin stats.bin"
 *
 * Input (params.bin):  S0, mu, sigma, dt, steps, N_paths, barrier
 * Output (paths.bin):  [count][avg1][avg2]...  (double, count + data)
 * Output (stats.bin):  count, invalid_count
 */

#define _GNU_SOURCE
#include <stdio.h>
#include <stdlib.h>
#include <math.h>
#include <omp.h>

#ifndef M_PI
#define M_PI 3.14159265358979323846
#endif

/* Box-Muller: uniform → standard normal */
static double box_muller(unsigned *seed) {
    double u1 = (double)rand_r(seed) / RAND_MAX;
    double u2 = (double)rand_r(seed) / RAND_MAX;
    if (u1 < 1e-30) u1 = 1e-30;
    return sqrt(-2.0 * log(u1)) * cos(2.0 * M_PI * u2);
}

int main(int argc, char *argv[]) {
    if (argc != 4) {
        fprintf(stderr, "Usage: %s params.bin paths.bin stats.bin\n", argv[0]);
        return 1;
    }

    /* Read parameters */
    FILE *fp = fopen(argv[1], "rb");
    if (!fp) { perror(argv[1]); return 1; }

    double S0, mu, sigma, dt, barrier;
    int steps, N_paths;
    fread(&S0,      sizeof(double), 1, fp);
    fread(&mu,      sizeof(double), 1, fp);
    fread(&sigma,   sizeof(double), 1, fp);
    fread(&dt,      sizeof(double), 1, fp);
    fread(&steps,   sizeof(int), 1, fp);
    fread(&N_paths, sizeof(int), 1, fp);
    fread(&barrier, sizeof(double), 1, fp);
    fclose(fp);

    printf("Phi MC: S0=%.1f sigma=%.2f steps=%d paths=%d B=%.1f\n",
           S0, sigma, steps, N_paths, barrier);

    /* Allocate per-thread results (max N_paths) */
    double *avgs = (double *)malloc(N_paths * sizeof(double));
    int *valid = (int *)calloc(N_paths, sizeof(int));
    if (!avgs || !valid) { fprintf(stderr, "alloc failed\n"); return 1; }

    double drift = (mu - 0.5 * sigma * sigma) * dt;
    double vol   = sigma * sqrt(dt);

    int total_valid = 0;

    #pragma omp parallel
    {
        /* Per-thread result buffer */
        double *local_avgs = (double *)malloc(N_paths * sizeof(double));
        int local_count = 0;
        unsigned seed = 42 + omp_get_thread_num() * 10007;

        #pragma omp for schedule(dynamic, 100)
        for (int p = 0; p < N_paths; p++) {
            double S = S0;
            double sum = 0.0;
            int knocked = 0;

            for (int t = 0; t < steps; t++) {
                double Z = box_muller(&seed);
                S *= exp(drift + vol * Z);
                if (S < barrier) { knocked = 1; break; }
                sum += S;
            }

            if (!knocked && steps > 0) {
                local_avgs[local_count++] = sum / steps;
            }
        }

        /* Merge into global array */
        #pragma omp critical
        {
            for (int i = 0; i < local_count; i++)
                avgs[total_valid + i] = local_avgs[i];
            total_valid += local_count;
        }
        free(local_avgs);
    }

    /* Write path averages */
    FILE *fo = fopen(argv[2], "wb");
    fwrite(&total_valid, sizeof(int), 1, fo);
    fwrite(avgs, sizeof(double), total_valid, fo);
    fclose(fo);

    /* Write stats */
    int invalid = N_paths - total_valid;
    FILE *fs = fopen(argv[3], "wb");
    fwrite(&total_valid, sizeof(int), 1, fs);
    fwrite(&invalid, sizeof(int), 1, fs);
    fclose(fs);

    printf("Phi MC: valid=%d invalid=%d (%.1f%%)\n",
           total_valid, invalid, 100.0 * total_valid / N_paths);

    free(avgs); free(valid);
    return 0;
}
