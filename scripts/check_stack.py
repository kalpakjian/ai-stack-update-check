# -*- coding: utf-8 -*-
"""check_stack.py — 檢查本機 AI 堆疊是否有更新。

涵蓋：Python（預設解譯器）、PyTorch/CUDA wheel、cuDNN、torchvision、torchaudio、
torchcodec、transformers、datasets、accelerate、numpy、pandas、Ollama、GPU 驅動，
以及「cuDNN 有沒有完整複製進 CUDA 目錄」。

用法：
    python check_stack.py                 # 表格輸出
    python check_stack.py --json          # JSON 輸出（給程式用）
    python check_stack.py --runtime       # 額外匯入 torch 驗證 CUDA 與 cuDNN
    python check_stack.py --bench         # 另跑 GPU 效能基準（約 1 分鐘）

cuDNN 說明：cuDNN 隨 torch wheel 一起安裝（Windows 在 `torch\\lib\\cudnn*.dll`，
Linux 在 `nvidia-cudnn-*`），**不是**可以獨立 pip 升級的套件，所以本工具只檢查
「裝好了沒、版本多少」，不把它列成更新項目。兩層檢查：

  1. 輕量層（預設）：掃 torch 的 cuDNN 函式庫檔案＋讀 PE FileVersion，不匯入 torch。
  2. 實測層（`--runtime`）：用官方 API `torch.backends.cudnn.is_available()` /
     `version()`，並回報實際載入的 cudnn DLL 與一次真的走 cuDNN 的 GPU 卷積。

另外會比對 `CUDNN\\v*`（zip 或 installer 的來源）與 `CUDA\\v*` 的 `bin`／`include`／
`lib\\x64`：cuDNN 裝好後還要**手動複製**到 CUDA 目錄，漏抄不會報錯，只會靜默少檔
（`cuda_cudnn` 欄位；不齊全就進 `warnings`）。

退出碼：0 = 沒有可用更新（或被 torch 阻擋的 Python 升級）；1 = 有可用更新；2 = 參數/環境錯誤。
cuDNN 異常不改變退出碼，但會出現在報告的 `cudnn.status` 與 `warnings`。
只使用標準函式庫，不需要 pip install 任何東西。
"""
from __future__ import annotations

import argparse
import concurrent.futures as futures
import glob
import importlib.metadata as md
import importlib.util as ilu
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import urllib.error
import urllib.request
from datetime import datetime

UA = {"User-Agent": "ai-stack-update-check/1.0 (local skill)"}
PYPI_URL = "https://pypi.org/pypi/{pkg}/json"
PYTORCH_ROOT = "https://download.pytorch.org/whl/"
PYTORCH_WHEEL = "https://download.pytorch.org/whl/{cu}/torch-{ver}%2B{cu}-{tag}-{tag}-win_amd64.whl"
OLLAMA_BASE = "http://localhost:11434"

PACKAGES = [
    "torch", "torchvision", "torchaudio", "torchcodec",
    "transformers", "datasets", "accelerate", "numpy", "pandas",
]
CUDA_FALLBACK = ["cu134", "cu132", "cu130", "cu129", "cu128", "cu126"]
CUDNN_CORE_DLL = "cudnn64_9.dll"
CUDNN_GLOBS = ("cudnn*.dll", "libcudnn*.so*")
PROGRAM_FILES = os.environ.get("ProgramFiles") or r"C:\Program Files"
CUDNN_ROOT = os.path.join(PROGRAM_FILES, "NVIDIA", "CUDNN")
CUDA_TOOLKIT_ROOT = os.path.join(PROGRAM_FILES, "NVIDIA GPU Computing Toolkit", "CUDA")
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
BENCH_SCRIPT = os.path.join(SCRIPT_DIR, "bench_gpu.py")


# ----------------------------------------------------------------- helpers
def http_json(url, timeout=10):
    req = urllib.request.Request(url, headers=UA)
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8"))


def http_ok(url, timeout=8):
    req = urllib.request.Request(url, headers=UA, method="HEAD")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return 200 <= resp.status < 300
    except Exception:
        return False


def vparse(text):
    """'2.14.1+cu132' -> (2, 14, 1)；'v0.40.6' -> (0, 40, 6)。"""
    if not text:
        return ()
    base = str(text).split("+")[0].lstrip("vV")
    return tuple(int(x) for x in re.findall(r"\d+", base)[:4])


def installed_version(pkg):
    try:
        return md.version(pkg)
    except md.PackageNotFoundError:
        return None


def run_cmd(cmd, timeout=6):
    flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    try:
        out = subprocess.run(
            cmd, capture_output=True, text=True, timeout=timeout,
            creationflags=flags,
        )
        return out.stdout.strip() if out.returncode == 0 else None
    except Exception:
        return None


# ---------------------------------------------------------------- cudnn
def cudnn_version_text(raw):
    """cuDNN 的整數版本 → '9.24.0'。

    cuDNN 9.x 是 major*10000 + minor*100 + patch（92400 = 9.24.0），
    cuDNN 8.x 是 major*1000 + minor*100 + patch（8907 = 8.9.7）。
    """
    try:
        value = int(raw)
    except (TypeError, ValueError):
        return None
    if value <= 0:
        return None
    if value >= 10000:
        return "%d.%d.%d" % (value // 10000, value % 10000 // 100, value % 100)
    return "%d.%d.%d" % (value // 1000, value % 1000 // 100, value % 100)


def cudnn_version_from_name(name):
    """從檔名撈版本（Linux 的 libcudnn.so.9.24.0）；撈不到回 None。"""
    match = re.search(r"(\d+\.\d+\.\d+)", name or "")
    return match.group(1) if match else None


def torch_cudnn_files():
    """掃出 torch 安裝的 cuDNN 函式庫（只讀檔，不匯入 torch，所以很快）。

    只回報「有幾個檔、多大」與能從檔名判斷的版本（Linux）。
    Windows 的 `cudnn64_9.dll` 檔名不含版本，而且直接載入它去問 `cudnnGetVersion()`
    可能被 PATH 上另一套系統版 cuDNN 的相依 DLL 混進來（實測會回錯版本），
    所以確切版本一律交給 `--runtime` 的官方 API。
    """
    try:
        spec = ilu.find_spec("torch")
    except Exception:  # noqa: BLE001
        spec = None
    if not spec or not spec.origin:
        return {"dir": None, "count": 0, "mb": 0.0, "version": None, "files": []}

    torch_dir = os.path.dirname(spec.origin)
    site_packages = os.path.dirname(torch_dir)
    candidates = (
        os.path.join(torch_dir, "lib"),                         # Windows: torch\lib\cudnn*.dll
        os.path.join(site_packages, "nvidia", "cudnn", "lib"),  # Linux: pip 的 nvidia-cudnn-*
    )

    for lib_dir in candidates:
        if not os.path.isdir(lib_dir):
            continue
        files = []
        for pattern in CUDNN_GLOBS:
            for path in sorted(glob.glob(os.path.join(lib_dir, pattern))):
                try:
                    size = os.path.getsize(path)
                except OSError:
                    size = 0
                files.append({"name": os.path.basename(path),
                              "mb": round(size / 1048576, 1)})
        if not files:
            continue
        version = None
        for entry in files:
            base = entry["name"].lower()
            if base.startswith("cudnn64") or base.startswith("libcudnn."):
                # 檔名帶版本（Linux 的 libcudnn.so.9.24.0）才算；Windows 的
                # cudnn64_9.dll 沒有版本資訊，確切版本交給 --runtime 的官方 API。
                version = cudnn_version_from_name(entry["name"])
                if version:
                    break
        return {
            "dir": lib_dir,
            "count": len(files),
            "mb": round(sum(f["mb"] for f in files), 1),
            "version": version,
            "files": files,
        }
    return {"dir": None, "count": 0, "mb": 0.0, "version": None, "files": []}


def _file_sizes(directory):
    """{檔名: 大小}；目錄不存在就回空字典。"""
    sizes = {}
    if directory and os.path.isdir(directory):
        for path in glob.glob(os.path.join(directory, "*")):
            if os.path.isfile(path):
                try:
                    sizes[os.path.basename(path)] = os.path.getsize(path)
                except OSError:
                    pass
    return sizes


def cuda_cudnn_copies():
    """比對 `CUDNN\\v*` 來源與 `CUDA\\v*` 安裝目錄的 bin／include／lib\\x64 是否齊全。

    cuDNN 的 zip 與 installer 都只是把檔案放到一個地方，還要**手動複製**到 CUDA 目錄；
    漏抄 include（常見）或漏寫 installer 的 CUDA 版本那一層，都不會報錯、只會靜默少檔。
    這裡只比對「檔名 + 大小」，不讀內容——lib\\x64 內有 GB 級 static lib，雜湊會太慢。
    """
    if os.name != "nt" or not os.path.isdir(CUDNN_ROOT):
        return []

    results = []
    for cudnn_dir in sorted(glob.glob(os.path.join(CUDNN_ROOT, "v*"))):
        if not os.path.isdir(cudnn_dir):
            continue
        cudnn_ver = os.path.basename(cudnn_dir)
        for include_dir in sorted(glob.glob(os.path.join(cudnn_dir, "include", "*"))):
            if not os.path.isdir(include_dir):
                continue
            cuda_ver = os.path.basename(include_dir)
            cuda_dir = os.path.join(CUDA_TOOLKIT_ROOT, "v" + cuda_ver)
            entry = {"cudnn": cudnn_ver, "cuda": cuda_ver, "toolkit": cuda_dir,
                     "sections": {}, "missing": []}
            if not os.path.isdir(cuda_dir):
                entry["status"] = "toolkit not installed"
                results.append(entry)
                continue

            for name, src_rel, dst_rels in (
                ("bin", os.path.join("bin", cuda_ver, "x64"),
                 ("bin", os.path.join("bin", "x64"))),
                ("include", os.path.join("include", cuda_ver), ("include",)),
                ("lib", os.path.join("lib", cuda_ver, "x64"),
                 (os.path.join("lib", "x64"),)),
            ):
                source = _file_sizes(os.path.join(cudnn_dir, src_rel))
                if not source:
                    continue
                best_dst, best_hit, best_sizes = None, -1, {}
                for rel in dst_rels:
                    sizes = _file_sizes(os.path.join(cuda_dir, rel))
                    hit = sum(1 for f, size in source.items() if sizes.get(f) == size)
                    if hit > best_hit:
                        best_dst, best_hit, best_sizes = rel, hit, sizes
                missing = sorted(f for f, size in source.items()
                                 if best_sizes.get(f) != size)
                entry["sections"][name] = {
                    "source": os.path.join(cudnn_dir, src_rel),
                    "dest": os.path.join(cuda_dir, best_dst) if best_dst else None,
                    "expected": len(source), "found": best_hit, "missing": missing,
                }
                entry["missing"].extend("%s\\%s" % (best_dst, f) for f in missing)

            entry["status"] = "complete" if not entry["missing"] else "incomplete"
            results.append(entry)
    return results


def run_bench(quick=False):
    """跑 scripts/bench_gpu.py（子行程）並回傳它的 JSON；失敗回 {'error': ...}。

    需要 torch，而且會實際操 GPU（預設約 1 分鐘；--quick 約 5-10 秒）。
    """
    if not os.path.isfile(BENCH_SCRIPT):
        return {"error": "bench_gpu.py not found next to check_stack.py"}
    out_path = os.path.join(tempfile.gettempdir(), "ai-stack-bench.json")
    cmd = [sys.executable, BENCH_SCRIPT, "--json-out", out_path]
    if quick:
        cmd.append("--quick")
    flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=900,
                              creationflags=flags)
    except Exception as exc:  # noqa: BLE001
        return {"error": "benchmark failed: %s" % exc}
    if proc.returncode != 0:
        tail = (proc.stderr or "").strip().splitlines()
        return {"error": "benchmark exit %d: %s"
                % (proc.returncode, tail[-1] if tail else "unknown error")}
    try:
        with open(out_path, "r", encoding="utf-8") as handle:
            return json.load(handle)
    except Exception as exc:  # noqa: BLE001
        return {"error": "cannot read benchmark output: %s" % exc}


def bench_summary(bench):
    """把基準 JSON 壓成一行（給 Notes 用）。"""
    parts = []
    matmul = (bench.get("matmul") or {}).get("fp16") or {}
    if matmul.get("tflops"):
        parts.append("matmul fp16 %.0f TFLOPS" % matmul["tflops"])
    conv = bench.get("conv") or {}
    for key, entry in conv.items():
        if key.startswith("mid 56 fp16 CL") and entry.get("ms"):
            parts.append("conv fp16+CL %.2f ms" % entry["ms"])
            break
    hf = bench.get("hf") or {}
    if (hf.get("distilbert") or {}).get("texts_per_s"):
        parts.append("DistilBERT %.0f texts/s" % hf["distilbert"]["texts_per_s"])
    if (hf.get("vit_base") or {}).get("ms_per_image"):
        parts.append("ViT %.2f ms/img" % hf["vit_base"]["ms_per_image"])
    return ", ".join(parts) or "no metrics"


# ------------------------------------------------------------ local checks
def gpu_info():
    if not shutil.which("nvidia-smi"):
        return {}
    rows = run_cmd(["nvidia-smi", "--query-gpu=name,driver_version",
                    "--format=csv,noheader"])
    full = run_cmd(["nvidia-smi"])
    cuda = None
    if full:
        m = re.search(r"CUDA (?:UMD )?Version:\s*([\d.]+)", full)
        cuda = m.group(1) if m else None
    first = (rows or "").splitlines()[0].strip() if rows else None
    name, _, driver = (first or "").partition(", ")
    return {"name": name or None, "driver": driver or None, "cuda": cuda}


def ollama_local():
    try:
        ver = http_json(OLLAMA_BASE + "/api/version", timeout=3)
        tags = http_json(OLLAMA_BASE + "/api/tags", timeout=3)
        return {"version": ver.get("version"), "models": len(tags.get("models", []))}
    except Exception:
        return {"version": None, "models": None}


def local_snapshot():
    return {
        "checked_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "python": {
            "installed": "%d.%d.%d" % sys.version_info[:3],
            "executable": sys.executable,
        },
        "packages": {p: installed_version(p) for p in PACKAGES},
        "gpu": gpu_info(),
        "cudnn": torch_cudnn_files(),
        "cuda_cudnn": cuda_cudnn_copies(),
        "ollama": ollama_local(),
    }


# ----------------------------------------------------------- remote checks
def pypi_info(pkg):
    try:
        return http_json(PYPI_URL.format(pkg=pkg), timeout=15)
    except Exception:
        return None


def python_release_info():
    try:
        data = http_json("https://endoflife.date/api/python.json", timeout=15)
        newest = max(data, key=lambda c: vparse(c.get("cycle")))
        return {
            "latest": newest.get("latest"),
            "cycle": newest.get("cycle"),
            "eol": newest.get("eol"),
            "released": newest.get("releaseDate"),
        }
    except Exception:
        return {}


def torch_max_python(torch_pypi):
    """從 PyPI 上 torch 最新版的 wheel 檔名推算最高支援的 CPython 版本。"""
    if not torch_pypi:
        return None
    versions = set()
    for item in torch_pypi.get("urls", []):
        name = item.get("filename", "")
        m = re.search(r"-cp3(\d+)-cp3\1-(?:win_amd64|manylinux|macosx)", name)
        if m:
            versions.add(int(m.group(1)))
    return "3.%d" % max(versions) if versions else None


def cuda_indexes():
    try:
        req = urllib.request.Request(PYTORCH_ROOT, headers=UA)
        with urllib.request.urlopen(req, timeout=15) as resp:
            html = resp.read().decode("utf-8", "ignore")
        found = set(re.findall(r"\b(cu\d{2,3})/", html))
        if found:
            return sorted(found, key=lambda s: int(s[2:]), reverse=True)
    except Exception:
        pass
    return list(CUDA_FALLBACK)


def newest_usable_cuda(torch_latest, py_tag, indexes):
    """找出『有實際 Windows wheel』的最新 CUDA 索引，而非只看目錄存在。"""
    if not torch_latest or not py_tag:
        return None

    def probe(cu):
        return cu if http_ok(
            PYTORCH_WHEEL.format(cu=cu, ver=torch_latest, tag=py_tag)
        ) else None

    with futures.ThreadPoolExecutor(max_workers=6) as pool:
        for cu, ok in zip(indexes, pool.map(probe, indexes)):
            if ok:
                return ok
    return None


def ollama_latest():
    try:
        data = http_json(
            "https://api.github.com/repos/ollama/ollama/releases/latest", timeout=15
        )
        return {
            "version": (data.get("tag_name") or "").lstrip("v"),
            "published": data.get("published_at"),
        }
    except Exception:
        return {}


# -------------------------------------------------------------- analysis
def compare_status(installed, latest):
    if not installed:
        return "not installed"
    if not latest:
        return "check failed"
    return "up to date" if vparse(installed) >= vparse(latest) else "update available"


def build_report(local, remote, runtime=None, runtime_detail=None, bench=None):
    rows, notes, updates, warnings, cmds = [], [], [], [], []
    pypi = remote.get("pypi") or {}
    py_info = remote.get("python") or {}
    offline = bool(remote.get("offline"))

    py_inst = local["python"]["installed"]
    py_latest = py_info.get("latest")
    torch_max = remote.get("torch_max_python")
    py_status = "not checked" if offline else compare_status(py_inst, py_latest)
    blocked = False
    if py_status == "update available" and torch_max and vparse(py_latest) > vparse(torch_max):
        py_status = "newer exists (torch max %s)" % torch_max
        blocked = True
        notes.append(
            "Python %s is out, but the newest torch wheel only supports up to "
            "%s (%s); upgrading now would break PyTorch."
            % (py_latest, torch_max, "cp" + torch_max.replace(".", ""))
        )
    rows.append({"component": "Python", "installed": py_inst,
                 "latest": py_latest or "?", "status": py_status})

    for pkg in PACKAGES:
        inst = local["packages"].get(pkg)
        info = pypi.get(pkg)
        latest = ((info or {}).get("info") or {}).get("version")
        status = "not checked" if offline else compare_status(inst, latest)
        rows.append({"component": pkg, "installed": inst or "-",
                     "latest": latest or "?", "status": status})
        if status == "update available":
            updates.append(pkg)

    inst_torch = local["packages"].get("torch") or ""
    m = re.search(r"\+cu(\d+)", inst_torch)
    inst_cu = "cu" + m.group(1) if m else None
    newest_cu = remote.get("newest_cuda")
    cuda_upgrade = None
    if inst_cu and newest_cu and int(newest_cu[2:]) > int(inst_cu[2:]):
        cuda_upgrade = newest_cu
        updates.append("torch-cuda")
        notes.append("A newer CUDA build of the same torch version exists: %s (currently %s)."
                     % (newest_cu, inst_cu))
    elif inst_cu:
        if offline:
            notes.append("CUDA build: %s (online check skipped)" % inst_cu)
        else:
            notes.append("CUDA build: %s (newest available for Windows: %s)"
                         % (inst_cu, newest_cu or "none"))

    # ---- cuDNN：隨 torch wheel 一起來，不是可獨立 pip 升級的套件 ----
    cudnn_files = local.get("cudnn") or {}
    detail = runtime_detail or {}
    cudnn_ver = cudnn_version_text(detail.get("cudnn_version"))
    file_ver = cudnn_files.get("version")
    libs = cudnn_files.get("count") or 0
    cudnn_installed = "-"
    if detail and cudnn_ver:
        cudnn_installed = cudnn_ver
    elif file_ver:
        cudnn_installed = "%s (%d libs)" % (file_ver, libs)
    elif libs:
        cudnn_installed = "%d libs, %.0f MB" % (libs, cudnn_files.get("mb") or 0)

    if local["packages"].get("torch") is None:
        cudnn_status = "not installed"
    elif runtime is not None and not detail:
        cudnn_status = "check failed"
    elif detail:
        cudnn_status = ("ok" if (detail.get("cudnn_available") and cudnn_ver)
                        else "not available")
    elif libs:
        cudnn_status = "present (not verified)"
    else:
        cudnn_status = "not found"

    if cudnn_status == "ok":
        loaded = detail.get("cudnn_dll") or ""
        notes.append("cuDNN %s available (ships inside the torch wheel%s)."
                     % (cudnn_ver, "; loaded from " + loaded if loaded else ""))
    elif cudnn_status == "present (not verified)":
        notes.append("cuDNN libraries found in %s (%d libs, %.0f MB)%s; run with --runtime "
                     "for the version torch actually uses "
                     "(torch.backends.cudnn.version())."
                     % (cudnn_files.get("dir"), libs, cudnn_files.get("mb") or 0,
                        " - " + file_ver if file_ver else ""))
    elif cudnn_status == "not available":
        if detail.get("cuda_available"):
            warnings.append(
                "cuDNN is not usable while CUDA is available: "
                "torch.backends.cudnn.is_available()=False, version=%s. "
                "cuDNN cannot be upgraded on its own - reinstall the matching torch wheel."
                % (detail.get("cudnn_version") or "None"))
            cmds.append(
                "# cuDNN unusable: reinstall the matching torch wheel, e.g. "
                "python -m pip install --force-reinstall torch torchvision --index-url "
                "https://download.pytorch.org/whl/%s" % (inst_cu or "cu132"))
        else:
            notes.append("cuDNN not available because CUDA is not available in this "
                         "interpreter (CPU-only torch build?).")
    elif cudnn_status == "not found":
        warnings.append("No cuDNN library found next to torch; "
                        "run with --runtime to verify.")
    elif cudnn_status == "check failed":
        notes.append("cuDNN could not be verified: " + str(runtime))

    rows.append({"component": "cuDNN", "installed": cudnn_installed,
                 "latest": "-", "status": cudnn_status})

    # ---- CUDA 目錄的 cuDNN 齊全度（zip／installer 都要手動複製）----
    for copy in local.get("cuda_cudnn") or []:
        label = "cuDNN %s -> CUDA v%s" % (copy.get("cudnn"), copy.get("cuda"))
        sections = copy.get("sections") or {}
        if copy.get("status") == "complete":
            sec_detail = ", ".join(
                "%s %d/%d" % (name, sections[name]["found"], sections[name]["expected"])
                for name in ("bin", "include", "lib") if name in sections)
            notes.append("%s: %s (complete)." % (label, sec_detail))
        elif copy.get("status") == "toolkit not installed":
            notes.append("%s: CUDA toolkit not installed at %s (nothing to copy)."
                         % (label, copy.get("toolkit")))
        else:
            missing = copy.get("missing") or []
            sec_detail = ", ".join(
                "%s %d/%d" % (name, sections[name]["found"], sections[name]["expected"])
                for name in ("bin", "include", "lib") if name in sections)
            shown = ", ".join(missing[:10]) + ("..." if len(missing) > 10 else "")
            warnings.append("%s: %d file(s) missing (%s) - the cuDNN files are not fully "
                            "copied into the CUDA toolkit: %s"
                            % (label, len(missing), sec_detail, shown))
            first = next((sections[n] for n in ("include", "bin", "lib")
                          if n in sections and sections[n].get("missing")), None)
            if first:
                cmds.append("# %s: copy the missing files (elevated shell), e.g. "
                            "Copy-Item '%s\\*' '%s\\' -Force"
                            % (label, first["source"], first["dest"]))

    ollama_inst = (local.get("ollama") or {}).get("version")
    ollama_remote = (remote.get("ollama") or {}).get("version")
    o_status = "not checked" if offline else compare_status(ollama_inst, ollama_remote)
    rows.append({"component": "Ollama",
                 "installed": ollama_inst or "not running",
                 "latest": ollama_remote or "?",
                 "status": o_status})
    if o_status == "update available":
        updates.append("ollama")

    gpu = local.get("gpu") or {}
    gpu_line = None
    if gpu.get("name"):
        gpu_line = "%s | driver %s | CUDA %s" % (
            gpu.get("name"), gpu.get("driver") or "?", gpu.get("cuda") or "?")

    pip_pkgs = [p for p in updates if p in PACKAGES]
    if pip_pkgs:
        cmds.append("python -m pip install --upgrade " + " ".join(pip_pkgs))
    if cuda_upgrade:
        cmds.append(
            "python -m pip install --upgrade torch torchvision --index-url "
            "https://download.pytorch.org/whl/%s" % cuda_upgrade)
    if "ollama" in updates:
        cmds.append("winget upgrade Ollama.Ollama    # or https://ollama.com/download")
    if blocked:
        target = "cp%d%d" % vparse(py_latest)[:2] if vparse(py_latest) else "cp3xx"
        cmds.append("# Python %s: do not upgrade yet; wait for a torch %s wheel"
                    % (py_latest, target))

    if bench:
        if bench.get("error"):
            warnings.append("GPU benchmark failed: " + str(bench["error"]))
        else:
            notes.append("GPU benchmark: " + bench_summary(bench))

    return {
        "checked_at": local["checked_at"],
        "interpreter": local["python"]["executable"],
        "rows": rows,
        "notes": notes,
        "gpu": gpu_line,
        "runtime": runtime,
        "updates": updates,
        "python_blocked": blocked,
        "python_latest": py_latest,
        "torch_max_python": torch_max,
        "installed_cuda": inst_cu,
        "newest_cuda": newest_cu,
        "cudnn": {
            "status": cudnn_status,
            "installed": cudnn_installed,
            "version": cudnn_ver or file_ver,
            "available": detail.get("cudnn_available") if detail else None,
            "enabled": detail.get("cudnn_enabled") if detail else None,
            "dll": detail.get("cudnn_dll") or None,
            "dir": cudnn_files.get("dir"),
            "libs": libs,
            "mb": cudnn_files.get("mb") or 0.0,
            "conv": detail.get("conv") or None,
        },
        "runtime_detail": detail or None,
        "cuda_cudnn": local.get("cuda_cudnn") or [],
        "bench": bench,
        "warnings": warnings,
        "commands": cmds,
        "ollama_models": (local.get("ollama") or {}).get("models"),
    }


# ---------------------------------------------------------------- output
def render(report, as_json=False):
    if as_json:
        print(json.dumps(report, ensure_ascii=False, indent=2))
        return

    widths = (14, 20, 18, 34)
    line = "-" * sum(widths)
    print("=" * sum(widths))
    print("AI Stack Update Check   " + report["checked_at"])
    print("Interpreter: " + report["interpreter"])
    print("=" * sum(widths))
    print("".join(h.ljust(w) for h, w in zip(
        ("Component", "Installed", "Latest", "Status"), widths)))
    print(line)
    for row in report["rows"]:
        values = (row["component"], row["installed"], row["latest"], row["status"])
        print("".join(str(v)[:w].ljust(w) for v, w in zip(values, widths)))
    print(line)
    if report.get("gpu"):
        print("GPU: " + report["gpu"])
    if report.get("runtime"):
        print("Runtime: " + report["runtime"])
    if report.get("ollama_models") is not None:
        print("Ollama models: %s" % report["ollama_models"])
    if report.get("warnings"):
        print("\nWarnings:")
        for warning in report["warnings"]:
            print("  ! " + warning)
    if report.get("notes"):
        print("\nNotes:")
        for note in report["notes"]:
            print("  - " + note)
    if report.get("commands"):
        print("\nSuggested commands:")
        for cmd in report["commands"]:
            print("  " + cmd)

    if report["python_blocked"]:
        print("\nResult: no safe Python upgrade yet (blocked by torch wheel support).")
    elif report["updates"]:
        print("\nResult: %d update(s) available: %s"
              % (len(report["updates"]), ", ".join(report["updates"])))
    else:
        print("\nResult: everything up to date.")
    if report.get("warnings"):
        print("Note: %d warning(s) above need attention."
              % len(report["warnings"]))


# 實測子行程：CUDA + cuDNN（官方 API）+ 一次真的走 cuDNN 的 GPU 卷積 + 實際載入的 cudnn DLL
RUNTIME_CODE = "\n".join([
    "import json, torch",
    "info = {}",
    "info['torch'] = torch.__version__",
    "info['cuda_available'] = bool(torch.cuda.is_available())",
    "info['cuda_version'] = torch.version.cuda or ''",
    "info['device'] = torch.cuda.get_device_name(0) if info['cuda_available'] else ''",
    "info['cudnn_available'] = bool(torch.backends.cudnn.is_available())",
    "info['cudnn_version'] = torch.backends.cudnn.version() or 0",
    "info['cudnn_enabled'] = bool(torch.backends.cudnn.enabled)",
    "info['cudnn_dll'] = ''",
    "info['conv'] = ''",
    "if info['cuda_available']:",
    "    import torch.nn as nn",
    "    x = torch.randn(8, 3, 32, 32, device='cuda')",
    "    y = nn.Conv2d(3, 4, 3, padding=1).cuda()(x)",
    "    info['conv'] = '%s %s' % (tuple(y.shape), y.device)",
    "try:",
    "    import ctypes",
    "    k32 = ctypes.WinDLL('kernel32', use_last_error=True)",
    "    k32.GetModuleHandleW.restype = ctypes.c_void_p",
    "    k32.GetModuleFileNameW.argtypes = [ctypes.c_void_p, ctypes.c_wchar_p, ctypes.c_uint]",
    "    k32.GetModuleFileNameW.restype = ctypes.c_uint",
    "    handle = k32.GetModuleHandleW('%s')" % CUDNN_CORE_DLL,
    "    if handle:",
    "        buf = ctypes.create_unicode_buffer(32768)",
    "        if k32.GetModuleFileNameW(handle, buf, 32768):",
    "            info['cudnn_dll'] = buf.value",
    "except Exception:",
    "    pass",
    "print(json.dumps(info))",
])


def runtime_probe():
    """匯入 torch 實測 CUDA 與 cuDNN；回傳 (一行摘要, 明細 dict) 或 (錯誤字串, None)。"""
    flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    try:
        out = subprocess.run(
            [sys.executable, "-c", RUNTIME_CODE],
            capture_output=True, text=True, timeout=300, creationflags=flags,
        )
    except Exception as exc:  # noqa: BLE001
        return "torch runtime check failed: %s" % exc, None
    if out.returncode != 0:
        tail = (out.stderr or "").strip().splitlines()
        return "torch import failed: " + (tail[-1] if tail else "unknown error"), None

    detail = None
    for line in reversed((out.stdout or "").strip().splitlines()):
        line = line.strip()
        if line.startswith("{") and line.endswith("}"):
            try:
                detail = json.loads(line)
                break
            except ValueError:
                continue
    if detail is None:
        return "torch runtime check returned no parsable output", None

    summary = "%s | CUDA available: %s%s" % (
        detail.get("torch") or "?",
        detail.get("cuda_available"),
        (" | " + detail["device"]) if detail.get("device") else "",
    )
    cudnn_text = cudnn_version_text(detail.get("cudnn_version"))
    if detail.get("cudnn_available") and cudnn_text:
        summary += " | cuDNN %s" % cudnn_text
        if detail.get("cudnn_dll"):
            summary += " (%s)" % os.path.basename(detail["cudnn_dll"])
    else:
        summary += " | cuDNN not available"
    return summary, detail


def main(argv=None):
    parser = argparse.ArgumentParser(
        description="Check the local AI stack (Python/PyTorch/transformers/Ollama) for updates."
    )
    parser.add_argument("--json", action="store_true", help="JSON output")
    parser.add_argument("--runtime", action="store_true",
                        help="import torch and verify CUDA + cuDNN availability")
    parser.add_argument("--bench", action="store_true",
                        help="also run scripts/bench_gpu.py (GPU benchmark, slow)")
    parser.add_argument("--bench-quick", action="store_true",
                        help="with --bench: fast subset (no HF baselines)")
    parser.add_argument("--no-network", action="store_true",
                        help="skip online version checks")
    parser.add_argument("--json-out", metavar="PATH",
                        help="also write the JSON report to PATH")
    args = parser.parse_args(argv)

    local = local_snapshot()
    remote = {"offline": args.no_network}

    if not args.no_network:
        with futures.ThreadPoolExecutor(max_workers=8) as pool:
            results = dict(zip(PACKAGES, pool.map(pypi_info, PACKAGES)))
        remote["pypi"] = results
        remote["python"] = python_release_info()
        remote["ollama"] = ollama_latest()
        torch_latest = (((results.get("torch") or {}).get("info") or {})
                        .get("version"))
        indexes = cuda_indexes()
        remote["cuda_indexes"] = indexes
        remote["torch_max_python"] = torch_max_python(results.get("torch"))
        py_tag = "cp%d%d" % sys.version_info[:2]
        remote["newest_cuda"] = newest_usable_cuda(torch_latest, py_tag, indexes)

    runtime, runtime_detail = runtime_probe() if args.runtime else (None, None)
    bench = run_bench(args.bench_quick) if args.bench else None
    report = build_report(local, remote, runtime, runtime_detail, bench)
    render(report, as_json=args.json)
    if args.json_out:
        with open(args.json_out, "w", encoding="utf-8") as handle:
            json.dump(report, handle, ensure_ascii=False, indent=2)
    return 1 if report["updates"] else 0


if __name__ == "__main__":
    sys.exit(main())

