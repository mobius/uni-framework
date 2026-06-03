/*
 * VE PCA 降维内核 (无 LAPACK 依赖)
 * 幂迭代求 top-K 特征值和特征向量
 * Compile: ncc -O3 -fopenmp -o pca_ve pca.c -lblas_openmp
 */

#include <stdio.h>
#include <stdlib.h>
#include <math.h>
#include <string.h>
#include <omp.h>
#include <sys/time.h>

static double ws(void){struct timeval tv;gettimeofday(&tv,0);return tv.tv_sec+tv.tv_usec*1e-6;}

#define K 16  /* top-K PCs */

int main(int argc,char**argv){
    if(argc!=3){fprintf(stderr,"Usage: %s in.bin out_prefix\n",argv[0]);return 1;}

    FILE*f=fopen(argv[1],"rb");
    int M,N;fread(&M,4,1,f);fread(&N,4,1,f);
    double*X=(double*)malloc(M*N*8);
    fread(X,8,M*N,f);fclose(f);

    printf("VE PCA: M=%d N=%d K=%d\n",M,N,K);
    double t0=ws();

    /* Covariance: C = X^T X / (M-1) [N×N] */
    double*C=(double*)calloc(N*N,8);
    #pragma omp parallel for collapse(2)
    for(int j=0;j<N;j++)
        for(int kk=j;kk<N;kk++){
            double s=0;
            for(int i=0;i<M;i++)s+=X[i*N+j]*X[i*N+kk];
            C[j*N+kk]=s/(M-1); C[kk*N+j]=C[j*N+kk];
        }

    /* Power iteration for top-K eigenvectors */
    double*eig=(double*)malloc(K*8);
    double*vec=(double*)malloc(N*K*8);
    double*wk=(double*)malloc(N*8);
    double*wk2=(double*)malloc(N*8);

    for(int k=0;k<K;k++){
        /* Random init */
        for(int i=0;i<N;i++) wk[i]=(double)(i+1)/(N+1);

        /* Power iteration: 80 iters */
        for(int iter=0;iter<80;iter++){
            /* wk2 = C @ wk */
            #pragma omp parallel for
            for(int j=0;j<N;j++){
                double s=0;
                for(int kk=0;kk<N;kk++) s+=C[j*N+kk]*wk[kk];
                wk2[j]=s;
            }
            /* Orthogonalize against previous eigenvectors */
            for(int p=0;p<k;p++){
                double dot=0;
                for(int j=0;j<N;j++) dot+=wk2[j]*vec[j*K+p];
                for(int j=0;j<N;j++) wk2[j]-=dot*vec[j*K+p];
            }
            /* Normalize */
            double norm=0;
            for(int j=0;j<N;j++) norm+=wk2[j]*wk2[j];
            norm=sqrt(norm);
            if(norm<1e-30) break;
            for(int j=0;j<N;j++) wk[j]=wk2[j]/norm;
        }

        /* Rayleigh quotient: eigenvalue */
        double rq=0;
        #pragma omp parallel for reduction(+:rq)
        for(int j=0;j<N;j++){
            double s=0;
            for(int kk=0;kk<N;kk++) s+=C[j*N+kk]*wk[kk];
            rq+=wk[j]*s;
        }
        eig[k]=rq;

        /* Store eigenvector */
        for(int j=0;j<N;j++) vec[j*K+k]=wk[j];
    }

    double el=ws()-t0;
    printf("VE PCA: eig=[%.3f %.3f %.3f] (%.3fs)\n",eig[0],eig[1],eig[2],el);

    /* Project: proj = X @ vec [M×K] */
    double*proj=(double*)calloc(M*K,8);
    #pragma omp parallel for collapse(2)
    for(int i=0;i<M;i++)
        for(int k=0;k<K;k++){
            double s=0;
            for(int j=0;j<N;j++) s+=X[i*N+j]*vec[j*K+k];
            proj[i*K+k]=s;
        }

    /* Write eigenvalues */
    char nm[128]; int kk=K;
    snprintf(nm,sizeof(nm),"%s_eig.bin",argv[2]);
    FILE*ef=fopen(nm,"wb"); fwrite(&kk,4,1,ef); fwrite(eig,8,kk,ef); fclose(ef);

    /* Write projections */
    snprintf(nm,sizeof(nm),"%s_proj.bin",argv[2]);
    FILE*pf=fopen(nm,"wb"); fwrite(&M,4,1,pf); fwrite(&kk,4,1,pf); fwrite(proj,8,M*kk,pf); fclose(pf);

    free(X);free(C);free(eig);free(vec);free(wk);free(wk2);free(proj);
    return 0;
}
