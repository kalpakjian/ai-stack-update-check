#!/usr/bin/env python3
"""審計單一 venv 的 AI stack 完整性。

檢查項目：
  1. 核心套件：torch / torchvision / torchaudio / triton 是否齊全且可 import
  2. cu12 / cu13 混裝偵測（nvidia-*-cu12 與 *-cu13 安裝到同名檔案）
  3. RECORD 完整性 / 孤兒 dist-info / 檔案歸屬衝突
  4. cuDNN：metadata 版本 vs torch 實際回報 vs 磁碟 lib vs 實際載入路徑
  5. NCCL 符號完整性與載入路徑
  6. 功能測試：matmul / cuDNN conv（對 float32 參考）/ SDPA / torch.compile / tv / ta
  7. uv pip check 依賴一致性
"""
import argparse
import json
import os
import re
import subprocess
import sys
from pathlib import Path

OK, BAD, WARN = "PASS", "FAIL", "WARN"
results = []


def check(name, status, detail=""):
    results.append((name, status, detail))
    icon = {OK: "  \033[32m[PASS]\033[0m", BAD: "  \033[31m[FAIL]\033[0m", WARN: "  \033[33m[WARN]\033[0m"}[status]
    print(f"{icon} {name}" + (f"  -  {detail}" if detail else ""))
    return status


def run_probe(py, code):
    """在 venv 內跑一段 python，回傳 (stdout, stderr, returncode)"""
    p = subprocess.run([str(py), "-c", code], capture_output=True, text=True)
    if "__JSON__" not in p.stdout:
        return None, p
    return json.loads(p.stdout.split("__JSON__", 1)[1].strip().splitlines()[0]), p


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("venv")
    ap.add_argument("--label", default="")
    args = ap.parse_args()
    venv = Path(args.venv).resolve()
    py = venv / "bin" / "python"
    label = args.label or venv.name
    print("=" * 78)
    print(f"venv audit : {label}   ({venv})")
    print("=" * 78)

    if not py.exists():
        check("venv exists", BAD, f"not found: {py}")
        return 1

    PROBE = r'''
import json, os, re, sys, importlib.metadata as md
out = {}
out["python"] = sys.version.split()[0]
try:
    import torch
    out["torch"] = torch.__version__
    out["torch_cuda"] = torch.version.cuda
    out["cudnn_runtime"] = torch.backends.cudnn.version()
    out["cuda_avail"] = torch.cuda.is_available()
    out["device"] = torch.cuda.get_device_name(0) if torch.cuda.is_available() else None
    out["arch_list"] = torch.cuda.get_arch_list()
except Exception as e:
    out["torch_error"] = f"{type(e).__name__}: {e}"
for name in ("torchvision", "torchaudio", "triton"):
    try:
        mod = __import__(name)
        out[name] = getattr(mod, "__version__", "?")
    except Exception as e:
        out[name + "_error"] = f"{type(e).__name__}: {str(e)[:120]}"
# runtime-loaded shared objects (proves venv-internal, not system)
loaded = {}
try:
    for line in open("/proc/self/maps", errors="ignore"):
        m = re.search(r"(\S+\.so[^\s]*)", line)
        if not m:
            continue
        path = m.group(1)
        b = os.path.basename(path)
        if any(k in b for k in ("libcudnn", "libnccl", "libcublas", "libcudart",
                                "libcusparse", "libcusolver", "libnvrtc")):
            loaded.setdefault(b, path)
    out["loaded"] = loaded
except Exception as e:
    out["loaded_error"] = str(e)
dists = {}
for d in md.distributions():
    try:
        n = (d.metadata["Name"] or "").lower()
        v = d.version
    except Exception:
        continue
    if n.startswith("nvidia-") or n in ("torch", "torchvision", "torchaudio", "triton"):
        dists.setdefault(n, []).append(v)
out["dists"] = dists
out["n_distributions"] = len(list(md.distributions()))
out["purelib"] = __import__("sysconfig").get_paths()["purelib"]
print("__JSON__" + json.dumps(out))
'''
    info, proc = run_probe(py, PROBE)
    if info is None:
        check("venv python introspection", BAD, (proc.stderr or proc.stdout)[-600:])
        return 1
    site = Path(info["purelib"])

    print("\n[1] core packages")
    for key in ("torch", "torchvision", "torchaudio", "triton"):
        val, err = info.get(key), info.get(key + "_error")
        if err:
            check(key, BAD, err)
        elif val:
            check(key, OK, val)
        else:
            check(key, WARN, "not installed (only needed for torch.compile)")
    print(f"    torch CUDA {info.get('torch_cuda')} | cuDNN runtime {info.get('cudnn_runtime')}"
          f" | GPU {info.get('device')} | sm_{''.join(map(str, info.get('arch_list') or []))}")
    check("CUDA available", OK if info.get("cuda_avail") else BAD, str(info.get("cuda_avail")))

    print("\n[2] nvidia package cu12 / cu13 consistency")
    dists = info["dists"]
    cu12 = sorted(k for k in dists if "cu12" in k)
    cu13 = sorted(k for k in dists if "cu13" in k)
    if cu12 and cu13:
        check("cu12/cu13 mixing", BAD, f"cu12={cu12} cu13={cu13}")
    elif cu12:
        check("cu12/cu13 consistency", BAD, f"still cu12: {cu12}")
    elif cu13:
        check("cu12/cu13 consistency", OK, f"{len(cu13)} cu13 packages, no cu12 leftovers")
    else:
        check("cu12/cu13 consistency", WARN, "no nvidia-* packages found")

    print("\n[3] install integrity (RECORD / orphan dist-info / file ownership)")
    REC = r'''
import json, importlib.metadata as md, sysconfig
from pathlib import Path
sp = Path(sysconfig.get_paths()["purelib"])
installed, records = {}, {}
for d in md.distributions():
    try:
        name = (d.metadata["Name"] or "").lower().replace("_", "-")
    except Exception:
        continue
    installed[name] = d.version
    try:
        rec = d.read_text("RECORD") or ""
    except Exception:
        rec = ""
    for line in rec.splitlines():
        path = line.rsplit(",", 2)[0]
        if path and not path.endswith(".pyc"):
            records.setdefault(path, []).append(name)
missing, orphan = [], []
for name, ver in installed.items():
    if not name.startswith("nvidia-"):
        continue
    try:
        rec = md.distribution(name).read_text("RECORD") or ""
    except Exception:
        continue
    for line in rec.splitlines():
        path = line.rsplit(",", 2)[0]
        if path and not path.endswith(".pyc") and not (sp / path).exists():
            missing.append(f"{name}:{path}")
for p in sp.glob("nvidia_*"):
    if p.name.endswith(".dist-info"):
        # 直接讀 dist-info 內的 METADATA 取 Name，避免用資料夾名去猜（含版本號）
        name = None
        try:
            for line in (p / "METADATA").read_text(errors="ignore").splitlines():
                if line.startswith("Name:"):
                    name = line.split(":", 1)[1].strip().lower().replace("_", "-")
                    break
        except Exception:
            pass
        if name is None or name not in installed:
            orphan.append(f"{p.name} (Name={name})")
conflicts = []
for path, owners in records.items():
    if len(owners) > 1:
        kinds = {"cu12" if "cu12" in o else ("cu13" if "cu13" in o else "other") for o in owners}
        if "cu12" in kinds and "cu13" in kinds:
            conflicts.append([path, owners])
print("__JSON__" + json.dumps({
    "missing": missing[:40], "n_missing": len(missing),
    "orphan": orphan, "conflicts": conflicts[:40], "n_conflicts": len(conflicts)}))
'''
    r2, p2 = run_probe(py, REC)
    if r2 is None:
        check("RECORD audit", WARN, (p2.stderr or p2.stdout)[-300:])
    else:
        check("RECORD files present", OK if r2["n_missing"] == 0 else BAD,
              "all present" if r2["n_missing"] == 0 else f"{r2['n_missing']} missing: {r2['missing'][:5]}")
        check("no orphan dist-info", OK if not r2["orphan"] else BAD,
              "none" if not r2["orphan"] else str(r2["orphan"]))
        check("no cu12/cu13 file conflicts", OK if r2["n_conflicts"] == 0 else BAD,
              "none" if r2["n_conflicts"] == 0 else f"{r2['n_conflicts']}: {r2['conflicts'][:3]}")

    print("\n[4] cuDNN correctness")
    cudnn_meta = None
    for k, v in dists.items():
        if k in ("nvidia-cudnn-cu13", "nvidia-cudnn-cu12"):
            cudnn_meta = v[0] if isinstance(v, list) else v
            break
    runtime = info.get("cudnn_runtime")
    cudnn_lib = site / "nvidia" / "cudnn" / "lib"
    cudnn_pkg = next(iter(cudnn_lib.glob("libcudnn.so*")), None)
    disk_ver = None
    if cudnn_pkg:
        # cuDNN 9 不以明文儲存版本字串；直接對磁碟上的 .so 呼叫 cudnnGetVersion()
        # （不經 torch，得到獨立於 torch 的第三方確認）
        probe = r'''
import ctypes, json, sys
try:
    lib = ctypes.CDLL(sys.argv[1])
    lib.cudnnGetVersion.restype = ctypes.c_int
    print("__JSON__" + json.dumps({"v": lib.cudnnGetVersion()}))
except Exception as e:
    print("__JSON__" + json.dumps({"err": str(e)}))
'''
        r3, p4 = run_probe(py, probe.replace("sys.argv[1]", repr(str(cudnn_pkg))))
        if r3 and "v" in r3:
            disk_ver = f"{r3['v'] // 10000}.{(r3['v'] % 10000) // 100}.{r3['v'] % 100}"

    def cudnn_str(r):
        """cuDNN runtime 編碼: major*10000 + minor*100 + patch"""
        return f"{r // 10000}.{(r % 10000) // 100}.{r % 100}" if r else None

    print(f"    metadata nvidia-cudnn : {cudnn_meta}")
    print(f"    disk libcudnn.so.9     : {disk_ver}  (direct cudnnGetVersion, no torch)")
    print(f"    torch reports cudnn   : {runtime} -> {cudnn_str(runtime)}" if runtime else "    torch: N/A")
    if cudnn_meta and runtime:
        want = ".".join(cudnn_meta.split(".")[:3])
        got = cudnn_str(runtime)
        check("metadata == torch runtime version", OK if want == got else BAD,
              f"metadata {want} / runtime {got}")
    else:
        check("cuDNN version compare", BAD, "missing metadata or torch cannot report")
    if disk_ver and runtime:
        check("disk lib == runtime version", OK if disk_ver == cudnn_str(runtime) else WARN,
              f"disk {disk_ver} / runtime {cudnn_str(runtime)}")
    loaded = info.get("loaded", {})
    cu_path = next((v for k, v in loaded.items() if k.startswith("libcudnn")), None)
    check("cuDNN loaded from venv (not system)", OK if cu_path and str(site) in cu_path else BAD,
          cu_path or "libcudnn not in /proc/self/maps")

    print("\n[5] NCCL")
    nc_path = next((v for k, v in loaded.items() if k.startswith("libnccl")), None)
    if nc_path:
        syms = subprocess.run(["nm", "-D", "--defined-only", nc_path], capture_output=True, text=True).stdout
        need = ["ncclCommResume", "ncclCommInitRank", "ncclAllReduce", "ncclGetVersion"]
        miss = [s for s in need if s not in syms]
        check("NCCL symbols complete", OK if not miss else BAD, "OK" if not miss else f"missing {miss}")
        check("NCCL loaded from venv", OK if str(site) in nc_path else BAD, nc_path)
        nv = subprocess.run([str(py), "-c", "import torch;print(torch.cuda.nccl.version())"],
                            capture_output=True, text=True).stdout.strip()
        check("NCCL version", OK if nv else WARN, f"torch.cuda.nccl.version() = {nv}")
    else:
        check("NCCL loaded", WARN, "libnccl not mapped in this process")

    print("\n[6] functional tests")
    FUNC = r'''
import json, torch
out = {}
x = torch.randn(4096, 4096, device="cuda", dtype=torch.float16)
y = x @ x
torch.cuda.synchronize()
out["matmul_finite"] = bool(torch.isfinite(y).all())
conv = torch.nn.Conv2d(64, 64, 3, padding=1).cuda().half().to(memory_format=torch.channels_last)
i = torch.randn(32, 64, 56, 56, device="cuda", dtype=torch.float16).to(memory_format=torch.channels_last)
o = conv(i)
torch.cuda.synchronize()
ref = torch.nn.functional.conv2d(i.float(), conv.weight.float(), conv.bias.float(), padding=1)
out["conv_finite"] = bool(torch.isfinite(o).all())
out["conv_err"] = round(float((o.float() - ref).abs().max()), 5)
out["conv_shape"] = list(o.shape)
out["cudnn_enabled"] = bool(torch.backends.cudnn.enabled)
q = torch.randn(4096, 4096, device="cuda", dtype=torch.float16)
a = torch.nn.functional.scaled_dot_product_attention(q, q, q)
torch.cuda.synchronize()
out["sdpa_finite"] = bool(torch.isfinite(a).all())
try:
    cm = torch.compile(torch.nn.Linear(256, 256).cuda())
    ci = torch.randn(8, 256, device="cuda")
    out["compile_finite"] = bool(torch.isfinite(cm(ci)).all())
except Exception as e:
    out["compile_error"] = f"{type(e).__name__}: {str(e)[:150]}"
try:
    from torchvision.ops import nms
    b = torch.tensor([[0.,0.,10.,10.],[1.,1.,11.,11.],[100.,100.,110.,110.]], device="cuda")
    out["tv_nms"] = nms(b, torch.tensor([0.9,0.8,0.7], device="cuda"), 0.5).tolist()
except Exception as e:
    out["tv_error"] = f"{type(e).__name__}: {str(e)[:120]}"
try:
    import torchaudio
    w = torch.sin(torch.arange(1600, dtype=torch.float32)/16000*3.14159*440).unsqueeze(0)
    out["ta_resample"] = list(torchaudio.functional.resample(w, 16000, 8000).shape)
    out["ta_mel"] = list(torchaudio.transforms.MelSpectrogram(16000, n_mels=64)(w).shape)
except Exception as e:
    out["ta_error"] = f"{type(e).__name__}: {str(e)[:150]}"
print("__JSON__" + json.dumps(out))
'''
    f, p3 = run_probe(py, FUNC)
    if f is None:
        check("functional tests", BAD, (p3.stderr or p3.stdout)[-700:])
    else:
        check("fp16 matmul finite", OK if f.get("matmul_finite") else BAD, "")
        err = f.get("conv_err", 99)
        check("cuDNN conv numerically correct", OK if f.get("conv_finite") and err < 0.05 else BAD,
              f"shape={f.get('conv_shape')} max|fp16-fp32ref|={err} cudnn_enabled={f.get('cudnn_enabled')}")
        check("SDPA finite", OK if f.get("sdpa_finite") else BAD, "")
        if "compile_error" in f:
            check("torch.compile (triton)", BAD, f["compile_error"])
        else:
            check("torch.compile (triton)", OK if f.get("compile_finite") else BAD, "compiled and ran")
        if "tv_error" in f:
            check("torchvision.ops.nms", BAD, f["tv_error"])
        else:
            check("torchvision.ops.nms", OK, f"kept {f.get('tv_nms')} boxes")
        if "ta_error" in f:
            check("torchaudio functional", BAD, f["ta_error"])
        else:
            check("torchaudio functional", OK, f"resample->{f.get('ta_resample')} mel->{f.get('ta_mel')}")

    print("\n[7] dependency consistency")
    uv = subprocess.run(["uv", "pip", "check", "--python", str(py)], capture_output=True, text=True)
    out = (uv.stdout + uv.stderr).strip().replace("\n", " | ")
    check("uv pip check", OK if uv.returncode == 0 else BAD, out or "no issues")

    print("\n" + "-" * 78)
    n_bad = sum(1 for _, s, _ in results if s == BAD)
    n_warn = sum(1 for _, s, _ in results if s == WARN)
    verdict = OK if n_bad == 0 else BAD
    print(f"VERDICT [{label}]: {n_bad} failed, {n_warn} warnings -> {verdict}")
    print("-" * 78)
    return 0 if n_bad == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
