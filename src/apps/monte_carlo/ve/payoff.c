/*
 * VE payoff 计算内核
 * payoff = max(avgS - K, 0) * exp(-rT)
 *
 * Compile: ncc -O3 -fopenmp -o payoff_ve payoff.c
 *
 * Run: ve_exec -N 1 ./payoff_ve paths.bin K r T output.bin
 *
 * Input:  [int count][double avgs[count]]
 * Output: [int count][double payoffs[count]]
 */

#include <stdio.h>
#include <stdlib.h>
#include <math.h>
#include <omp.h>
#include <sys/time.h>

static double ws(void){struct timeval tv;gettimeofday(&tv,0);return tv.tv_sec+tv.tv_usec*1e-6;}

int main(int argc, char *argv[]) {
    if (argc != 6) {
        fprintf(stderr, "Usage: %s paths.bin K r T output.bin\n", argv[0]);
        return 1;
    }

    double K   = atof(argv[2]);
    double r   = atof(argv[3]);
    double T   = atof(argv[4]);
    double df  = exp(-r * T);  /* discount factor */

    FILE *fin = fopen(argv[1], "rb");
    if (!fin) { perror(argv[1]); return 1; }

    int count;
    fread(&count, sizeof(int), 1, fin);
    double *avgs = (double *)malloc(count * sizeof(double));
    fread(avgs, sizeof(double), count, fin);
    fclose(fin);

    double *payoffs = (double *)malloc(count * sizeof(double));

    printf("VE payoff: count=%d K=%.1f r=%.2f T=%.1f\n", count, K, r, T);
    double t0 = ws();

    #pragma omp parallel for
    for (int i = 0; i < count; i++) {
        double p = avgs[i] - K;
        payoffs[i] = (p > 0 ? p : 0.0) * df;
    }

    double elapsed = ws() - t0;

    /* Statistics */
    double sum = 0, sum2 = 0;
    #pragma omp parallel for reduction(+:sum,sum2)
    for (int i = 0; i < count; i++) {
        sum  += payoffs[i];
        sum2 += payoffs[i] * payoffs[i];
    }
    double mean = sum / count;
    double stdv = sqrt(sum2 / count - mean * mean);

    printf("VE payoff: price=%.4f std=%.4f CI=[%.4f,%.4f] (%.3fs)\n",
           mean, stdv,
           mean - 1.96 * stdv / sqrt(count),
           mean + 1.96 * stdv / sqrt(count),
           elapsed);

    FILE *fout = fopen(argv[5], "wb");
    fwrite(&count, sizeof(int), 1, fout);
    fwrite(payoffs, sizeof(double), count, fout);
    fclose(fout);

    free(avgs); free(payoffs);
    return 0;
}
