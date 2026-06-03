#!/usr/bin/env python3
"""
dataprep_app.py — 异构数据预处理流水线

Pipeline:
  1. Host: 生成 M×N 随机数据 (含 ~5% 异常值)
  2. Phi: 异常值检测 + 列均值填充
  3. VE1: z-score 标准化
  4. VE2: PCA 降维 (NLC LAPACK dsyev)
  5. Host: 对比 numpy 参考, 输出报告

Usage:
  ./env/.venv/bin/python3 src/apps/hetero_dataprep/dataprep_app.py
"""

import sys, os, time, struct, subprocess, uuid
from pathlib import Path

PROJECT = Path(__file__).resolve().parent.parent.parent.parent
APP = PROJECT / "src/apps/hetero_dataprep"
MIC_LIBS = PROJECT.parent / "intel_phi/icc_mic_libs"

M, N = 1024, 64   # samples × features


def sh(cmd, to=120, env=None):
    r = subprocess.run(cmd, shell=True, capture_output=True, text=True, timeout=to, env=env)
    return r.returncode, r.stdout.strip(), r.stderr.strip(), time.time()


def compile_phi():
    s,d=APP/"phi/data_clean.c", APP/"phi/data_clean.mic"
    if d.exists(): return True
    print("[compile] Phi clean...")
    rc,_,err,_=sh(f"podman start centos7-phi-dev 2>/dev/null && "
        f"podman cp {s} centos7-phi-dev:/tmp/dc.c && "
        f"podman exec centos7-phi-dev bash -c 'source /opt/intel/bin/compilervars.sh intel64 && "
        f"icc -std=c99 -mmic -O3 -openmp -o /tmp/dc.mic /tmp/dc.c' && "
        f"podman cp centos7-phi-dev:/tmp/dc.mic {d}")
    return rc==0

def compile_ve(name,src,extra=""):
    k=APP/"ve"; out=k/name
    if out.exists(): return True
    print(f"[compile] VE {name}...")
    rc,_,err,_=sh(f"ncc -O3 -fopenmp {extra} -o {out} {k/src}")
    return rc==0


def run_phi_clean(inp:Path,wd:Path)->Path:
    """scp → Phi → scp"""
    uid=uuid.uuid4().hex[:8]
    remote_in = f"/tmp/dc_{uid}_in.bin"
    remote_out= f"/tmp/dc_{uid}_out.bin"

    print("[phi] scp → mic0...")
    sh(f"scp {inp} mic0:{remote_in}",to=30)

    env=os.environ.copy()
    if MIC_LIBS.is_dir(): env["SINK_LD_LIBRARY_PATH"]=str(MIC_LIBS)
    print("[phi] clean...")
    rc,out,err,_=sh(f"micnativeloadex {APP/'phi/data_clean.mic'} -d 0 -t 60 -a \"{remote_in} {remote_out}\"",env=env,to=120)
    for l in (out+err).splitlines():
        if l.strip(): print(f"    {l.strip()}")
    if rc!=0: return None

    out_path=wd/"cleaned.bin"
    sh(f"scp mic0:{remote_out} {out_path}",to=30)
    print(f"  → {out_path.stat().st_size//1024}KB")
    return out_path


def run_ve_std(inp:Path,wd:Path)->Path:
    exe=APP/"ve/standardize_ve"; out_path=wd/"std.bin"
    print(f"[ve1] standardize...")
    rc,stdout,stderr,_=sh(f"/opt/nec/ve/bin/ve_exec -N 1 {exe} {inp} {out_path}")
    for l in (stdout+stderr).splitlines():
        if l.strip(): print(f"    {l.strip()}")
    return out_path


def run_ve_pca(inp:Path,wd:Path)->Path:
    exe=APP/"ve/pca_ve"; out_prefix=wd/"pca"
    print(f"[ve2] PCA...")
    rc,stdout,stderr,_=sh(f"/opt/nec/ve/bin/ve_exec -N 2 {exe} {inp} {out_prefix}")
    for l in (stdout+stderr).splitlines():
        if l.strip(): print(f"    {l.strip()}")
    return out_prefix


def verify(wd:Path):
    import numpy as np
    # Read original + cleaned
    raw=np.frombuffer((wd/"input.bin").read_bytes()[8:],'f8').reshape(M,N)
    cln=np.frombuffer((wd/"cleaned.bin").read_bytes()[8:],'f8').reshape(M,N)

    outliers=np.sum(np.abs(raw)>3.0)
    replaced=np.sum(np.abs(raw-cln)>1e-12)
    print(f"\n[verify] outliers={outliers} replaced={replaced}")

    # Reference pipeline
    ref_mean=cln.mean(axis=0); ref_std=cln.std(axis=0,ddof=0)
    ref_std[ref_std<1e-12]=1.0
    ref_z=(cln-ref_mean)/ref_std

    std=np.frombuffer((wd/"std.bin").read_bytes()[8:],'f8').reshape(M,N)
    std_diff=np.abs(ref_z-std).max()
    print(f"  standardize max_diff: {std_diff:.2e}")

    # PCA: read eigenvalues
    eig=np.frombuffer((wd/"pca_eig.bin").read_bytes()[4:],'f8')  # first 4 bytes = K
    K=struct.unpack('i',(wd/"pca_eig.bin").read_bytes()[:4])[0]
    proj=np.frombuffer((wd/"pca_proj.bin").read_bytes()[8:],'f8').reshape(M,K)

    # Reference PCA (numpy)
    C=ref_z.T@ref_z/(M-1)
    w,v=np.linalg.eigh(C);
    w=w[::-1]; v=v[:,::-1]  # descending
    ref_proj=ref_z@v[:,:K]

    # PCA: compare eigenvalues and projection covariance
    eig_diff=np.abs(eig-w[:K]).max()
    # Projection: sign-agnostic (eigenvectors may be negated)
    proj_var=np.abs(ref_proj-proj).max()
    proj_sq=np.abs(np.corrcoef(ref_proj.T,proj.T)[:K,K:]).max()  # max abs correlation
    print(f"  PCA eig max_diff: {eig_diff:.2e}")
    print(f"  PCA eigenvalues: VE[{eig[0]:.2f},{eig[1]:.2f},...,{eig[K-1]:.2f}]")
    print(f"  PCA eigenvalues: np[{w[0]:.2f},{w[1]:.2f},...,{w[K-1]:.2f}]")
    print(f"  PCA proj max abs diff: {proj_var:.2e}")
    print(f"  PCA proj max corr:      {proj_sq:.3f}")

    ok = std_diff < 1e-8 and eig_diff < 1e-1 and proj_sq > 0.95
    return ok, outliers, replaced


def main():
    import numpy as np
    print("="*60)
    print("  异构数据预处理: Phi清洗 → VE1标准化 → VE2 PCA")
    print(f"  M={M} N={N}")
    print("="*60)

    wd=APP/"run_data"; wd.mkdir(parents=True,exist_ok=True)

    if not compile_phi(): return print("❌ Phi compile fail")
    if not compile_ve("standardize_ve","standardize.c"): return print("❌ VE std fail")
    if not compile_ve("pca_ve","pca.c","-lblas_openmp"): return print("❌ VE pca fail")

    # 1. Generate data (M×N, 5% outliers)
    print("\n[host] generate M×N random data...")
    rng=np.random.default_rng(42)
    data=rng.normal(0,1,(M,N))
    outlier_mask=rng.random((M,N))<0.05
    data[outlier_mask]=rng.normal(0,5,np.sum(outlier_mask))
    inp=wd/"input.bin"
    inp.write_bytes(struct.pack("ii",M,N)+data.astype('f8').tobytes())
    print(f"  {M}×{N}, ~{outlier_mask.sum()} outliers → {inp}")

    # 2. Phi clean
    print()
    cln=run_phi_clean(inp,wd)
    if not cln: return print("❌ Phi fail")

    # 3. VE1 standardize
    print()
    std=run_ve_std(cln,wd)
    if not std: return print("❌ VE std fail")

    # 4. VE2 PCA
    print()
    pca=run_ve_pca(std,wd)
    if not pca: return print("❌ VE pca fail")

    # 5. Verify
    ok,outliers,replaced=verify(wd)

    print("\n"+"="*60)
    print("  结果")
    print("="*60)
    print(f"  数据: {M}×{N}, 异常值 {outliers} → 替换 {replaced}")
    print(f"  正确性: {'✅ 通过' if ok else '❌ 失败'}")


if __name__=="__main__":
    main()
