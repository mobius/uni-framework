/*
 * VE 持续负载内核 — 运行 duration 秒 (默认 10s)
 * Compile: ncc -O3 -fopenmp -o busy_ve busy.c
 * Run: ve_exec -N 1 ./busy_ve [duration_s]
 */

#include <stdio.h>
#include <stdlib.h>
#include <omp.h>
#include <sys/time.h>

static double ws(void){struct timeval tv;gettimeofday(&tv,0);return tv.tv_sec+tv.tv_usec*1e-6;}

int main(int argc,char**argv){
    double dur = argc>1 ? atof(argv[1]) : 10.0;
    if(dur<1) dur=1; if(dur>120) dur=120;

    double t0=ws(), flops=0;
    #pragma omp parallel reduction(+:flops)
    {
        double a=1.0001,b=0.9999,c=0;
        while(ws()-t0 < dur){
            for(int k=0;k<100000;k++) c+=a*b;  /* ~100K FMA per iter */
            flops+=200000; /* 100K FMA × 2 ops */
        }
    }
    double el=ws()-t0;
    printf("VE busy: %.2fs, %.1f GFLOPS\n",el,flops/el/1e9);
    return 0;
}
