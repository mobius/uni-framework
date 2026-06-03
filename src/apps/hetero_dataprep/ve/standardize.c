/*
 * VE 标准化内核
 * z-score: (x - mean) / std per column
 * Compile: ncc -O3 -fopenmp -o standardize_ve standardize.c
 */

#include <stdio.h>
#include <stdlib.h>
#include <math.h>
#include <omp.h>
#include <sys/time.h>

static double ws(void){struct timeval tv;gettimeofday(&tv,0);return tv.tv_sec+tv.tv_usec*1e-6;}

int main(int argc,char**argv){
    if(argc!=3){fprintf(stderr,"Usage: %s in.bin out.bin\n",argv[0]);return 1;}

    FILE*f=fopen(argv[1],"rb");
    int M,N;fread(&M,4,1,f);fread(&N,4,1,f);
    double*d=(double*)malloc(M*N*8);
    double*o=(double*)malloc(M*N*8);
    fread(d,8,M*N,f);fclose(f);

    printf("VE standardize: M=%d N=%d\n",M,N);
    double t0=ws();

    #pragma omp parallel for
    for(int j=0;j<N;j++){
        double s=0,s2=0;
        for(int i=0;i<M;i++){double v=d[i*N+j];s+=v;s2+=v*v;}
        double m=s/M, std=sqrt(s2/M-m*m);
        if(std<1e-12)std=1.0;
        for(int i=0;i<M;i++)o[i*N+j]=(d[i*N+j]-m)/std;
    }

    double el=ws()-t0;

    FILE*out=fopen(argv[2],"wb");
    fwrite(&M,4,1,out);fwrite(&N,4,1,out);fwrite(o,8,M*N,out);fclose(out);

    printf("VE standardize: done (%.4fs)\n",el);
    free(d);free(o);return 0;
}
