/*
 * VE MPI AllReduce 扩展性测试
 *
 * 测量 1/2/3 卡 MPI_Allreduce(SUM) 扩展效率。
 * 数组大小 512MB float64 (~67M elements)
 *
 * Compile:
 *   source /opt/nec/ve/mpi/3.10.0/bin64/necmpivars-runtime.sh
 *   mpincc -O3 -o mpi_allreduce_ve mpi_allreduce.c
 *
 * Run:
 *   mpirun -ve 0-2 -np 3 ./mpi_allreduce_ve
 */

#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/time.h>
#include <mpi.h>

#define MB 512
#define NELEM ((size_t)(MB) * 1024UL * 1024UL / sizeof(double))

static double wall_sec(void) {
    struct timeval tv;
    gettimeofday(&tv, NULL);
    return tv.tv_sec + tv.tv_usec * 1e-6;
}

int main(int argc, char *argv[]) {
    MPI_Init(&argc, &argv);

    int rank, nprocs;
    MPI_Comm_rank(MPI_COMM_WORLD, &rank);
    MPI_Comm_size(MPI_COMM_WORLD, &nprocs);

    double *buf = (double *)aligned_alloc(64, NELEM * sizeof(double));
    if (!buf) {
        if (rank == 0) fprintf(stderr, "alloc %zu MB failed\n", (size_t)MB);
        MPI_Abort(MPI_COMM_WORLD, 1);
    }

    /* Fill: each rank contributes unique values */
    for (size_t i = 0; i < NELEM; i++) {
        buf[i] = (double)(rank + 1);  /* rank 0→1.0, rank 1→2.0, rank 2→3.0 */
    }

    MPI_Barrier(MPI_COMM_WORLD);
    double t0 = wall_sec();

    /* AllReduce: SUM → each rank gets nprocs * (rank+1) */
    MPI_Allreduce(MPI_IN_PLACE, buf, (int)NELEM, MPI_DOUBLE,
                  MPI_SUM, MPI_COMM_WORLD);

    double elapsed = wall_sec() - t0;

    /* Verify: result should be sum(1..nprocs) = nprocs*(nprocs+1)/2 */
    double expected = (double)(nprocs * (nprocs + 1)) / 2.0;
    int ok = (buf[0] == expected) ? 1 : 0;

    double gb = (double)NELEM * sizeof(double) / 1e9;
    double bw = gb / elapsed;  /* GB/s effective bandwidth */

    if (rank == 0) {
        printf("MPI_AllReduce: ranks=%d MB=%d elapsed=%.4fs BW=%.2f_GB/s verify=%s\n",
               nprocs, MB, elapsed, bw, ok ? "OK" : "FAIL");
    }

    free(buf);
    MPI_Finalize();
    return ok ? 0 : 1;
}
