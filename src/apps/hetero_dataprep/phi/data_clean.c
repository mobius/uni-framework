/*
 * Phi 数据清洗内核
 * 异常值检测 (threshold) + 替换为列均值
 * Run: micnativeloadex data_clean.mic -d 0 -t 60 -a "in.bin out.bin"
 */

#include <stdio.h>
#include <stdlib.h>
#include <math.h>
#include <omp.h>

int main(int argc, char *argv[]) {
    if (argc != 3) { fprintf(stderr,"Usage: %s in.bin out.bin\n",argv[0]); return 1; }

    FILE *f = fopen(argv[1],"rb");
    int M,N; fread(&M,4,1,f); fread(&N,4,1,f);
    double *data = (double*)malloc(M*N*8);
    fread(data,8,M*N,f); fclose(f);

    printf("Phi clean: M=%d N=%d\n",M,N);

    /* Compute column means (parallel) */
    double *mean = (double*)calloc(N,8);
    #pragma omp parallel for
    for (int j=0;j<N;j++) {
        double s=0;
        for (int i=0;i<M;i++) s+=data[i*N+j];
        mean[j]=s/M;
    }

    /* Replace outliers (|x-mean| > 3*threshold) with column mean */
    double thr = 3.0;  /* 3 sigma equivalent for unit-variance data */
    #pragma omp parallel for
    for (int j=0;j<N;j++) {
        for (int i=0;i<M;i++) {
            double v = data[i*N+j];
            if (fabs(v) > thr) data[i*N+j] = mean[j];
        }
    }

    /* Write cleaned data */
    FILE *out = fopen(argv[2],"wb");
    fwrite(&M,4,1,out); fwrite(&N,4,1,out);
    fwrite(data,8,M*N,out); fclose(out);

    printf("Phi clean: done M=%d N=%d\n",M,N);
    free(data); free(mean);
    return 0;
}
