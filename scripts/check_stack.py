# -*- coding: utf-8 -*-
"""check_stack.py — 檢查本機 AI 堆疊是否有更新。

涵蓋：Python（預設解譯器）、PyTorch/CUDA wheel、torchvision、torchaudio、
torchcodec、transformers、datasets、accelerate、numpy、pandas、Ollama、GPU 驅動。

用法：
    python check_stack.py                 # 表格輸出
    python check_stack.py --json          # JSON 輸出（給程式用）
    python check_stack.py --runtime       # 額外匯入 torch 驗證 CUDA
    python check_stack.py --no-network    # 只列已安裝版本，不查線上

退出碼：0 = 沒有可用更新（或被 torch 阻擋的 Python 升級）；1 = 有可用更新；2 = 參數/環境錯誤。
只使用標準函式庫，不需要 pip install 任何東西。
"""
from __future__ import annotations

import argparse
import concurrent.futures as futures
import importlib.metadata as md
import json
import re
import shutil
import subprocess
import sys
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


def build_report(local, remote, runtime=None):
    rows, notes, updates = [], [], []
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

    cmds = []
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


def runtime_check():
    code = (
        "import torch;"
        "print(torch.__version__);"
        "print(torch.cuda.is_available());"
        "print(torch.cuda.get_device_name(0) if torch.cuda.is_available() else '')"
    )
    flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    try:
        out = subprocess.run(
            [sys.executable, "-c", code],
            capture_output=True, text=True, timeout=180, creationflags=flags,
        )
    except Exception as exc:  # noqa: BLE001
        return "torch runtime check failed: %s" % exc
    if out.returncode != 0:
        tail = (out.stderr or "").strip().splitlines()
        return "torch import failed: " + (tail[-1] if tail else "unknown error")
    lines = (out.stdout or "").strip().splitlines()
    version = lines[0] if lines else "?"
    cuda = lines[1] if len(lines) > 1 else "?"
    name = lines[2] if len(lines) > 2 else ""
    return "%s | CUDA available: %s%s" % (version, cuda, (" | " + name) if name else "")


def main(argv=None):
    parser = argparse.ArgumentParser(
        description="Check the local AI stack (Python/PyTorch/transformers/Ollama) for updates."
    )
    parser.add_argument("--json", action="store_true", help="JSON output")
    parser.add_argument("--runtime", action="store_true",
                        help="import torch and verify CUDA availability")
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

    runtime = runtime_check() if args.runtime else None
    report = build_report(local, remote, runtime)
    render(report, as_json=args.json)
    if args.json_out:
        with open(args.json_out, "w", encoding="utf-8") as handle:
            json.dump(report, handle, ensure_ascii=False, indent=2)
    return 1 if report["updates"] else 0


if __name__ == "__main__":
    sys.exit(main())

