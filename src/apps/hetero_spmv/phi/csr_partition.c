/*
 * Phi CSR 列分块内核 v2
 * 简化版: 单线程计数 + 单线程分布, 避免并行竞争
 */

#define _GNU_SOURCE
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <omp.h>

int main(int argc, char *argv[]) {
    const char *in_path = argc > 1 ? argv[1] : "/tmp/spmv_in.csr";
    const char *out_pre = argc > 2 ? argv[2] : "/tmp/spmv";

    FILE *fin = fopen(in_path, "rb");
    if (!fin) { perror(in_path); return 1; }

    int N, nnz;
    fread(&N, sizeof(int), 1, fin);
    fread(&nnz, sizeof(int), 1, fin);

    int *rp = (int *)malloc((N+1)*sizeof(int));
    int *cols = (int *)malloc(nnz*sizeof(int));
    double *vals = (double *)malloc(nnz*sizeof(double));
    double *x = (double *)malloc(N*sizeof(double));

    fread(rp, sizeof(int), N+1, fin);
    fread(cols, sizeof(int), nnz, fin);
    fread(vals, sizeof(double), nnz, fin);
    fread(x, sizeof(double), N, fin);
    fclose(fin);

    printf("Phi read: N=%d nnz=%d\n", N, nnz);

    int chunk = (N+2)/3;
    int c0[3], c1[3];
    for (int v=0;v<3;v++) { c0[v]=v*chunk; c1[v]=(v==2)?N:(v+1)*chunk; }

    /* Pass 1: count per VE per row */
    int *cnt[3];
    for (int v=0;v<3;v++) cnt[v]=(int*)calloc(N,sizeof(int));
    for (int i=0;i<N;i++)
        for (int j=rp[i];j<rp[i+1];j++) {
            int ve=cols[j]/chunk; if(ve>2)ve=2;
            cnt[ve][i]++;
        }

    int tot[3]={0,0,0};
    for (int v=0;v<3;v++) for(int i=0;i<N;i++) tot[v]+=cnt[v][i];
    printf("Phi count: %d+%d+%d=%d %s\n",tot[0],tot[1],tot[2],tot[0]+tot[1]+tot[2],
           (tot[0]+tot[1]+tot[2]==nnz)?"OK":"FAIL");

    /* Pass 2: build cumulative row_ptr for each VE (for output) */
    int *brp[3];
    for (int v=0;v<3;v++) {
        brp[v]=(int*)malloc((N+1)*sizeof(int));
        int t=0; brp[v][0]=0;
        for (int i=0;i<N;i++) { t+=cnt[v][i]; brp[v][i+1]=t; }
    }

    /* Pass 3: fill output arrays */
    int *bcols[3]; double *bvals[3];
    int *pos[3];
    for (int v=0;v<3;v++) {
        bcols[v]=(int*)malloc(tot[v]*sizeof(int));
        bvals[v]=(double*)malloc(tot[v]*sizeof(double));
        pos[v]=(int*)calloc(N,sizeof(int));
    }

    for (int i=0;i<N;i++) {
        int p[3];
        for (int v=0;v<3;v++) p[v]=(i==0)?0:brp[v][i];
        for (int j=rp[i];j<rp[i+1];j++) {
            int ve=cols[j]/chunk; if(ve>2)ve=2;
            int k=p[ve]++;
            bcols[ve][k]=cols[j]; bvals[ve][k]=vals[j];
        }
    }

    /* Write blocks */
    for (int v=0;v<3;v++) {
        char nm[128]; snprintf(nm,sizeof(nm),"%s_block_%d.bin",out_pre,v);
        FILE *f=fopen(nm,"wb");
        fwrite(&N,4,1,f); fwrite(&tot[v],4,1,f);
        fwrite(&c0[v],4,1,f); fwrite(&c1[v],4,1,f);
        fwrite(brp[v],4,N+1,f);
        fwrite(bcols[v],4,tot[v],f);
        fwrite(bvals[v],8,tot[v],f);
        fwrite(x,8,N,f);
        fclose(f);
        printf("Phi block %d: nnz=%d cols=[%d,%d)\n",v,tot[v],c0[v],c1[v]);
    }

    /* Full CSR copy */
    {
        char nm[128]; snprintf(nm,sizeof(nm),"%s_full.csr",out_pre);
        FILE *f=fopen(nm,"wb");
        fwrite(&N,4,1,f); fwrite(&nnz,4,1,f);
        fwrite(rp,4,N+1,f); fwrite(cols,4,nnz,f);
        fwrite(vals,8,nnz,f); fwrite(x,8,N,f);
        fclose(f);
    }

    free(rp);free(cols);free(vals);free(x);
    for(int v=0;v<3;v++){free(cnt[v]);free(brp[v]);free(bcols[v]);free(bvals[v]);free(pos[v]);}
    return 0;
}
