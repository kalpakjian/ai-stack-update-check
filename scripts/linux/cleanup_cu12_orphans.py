#!/usr/bin/env python3
"""
cleanup_cu12_orphans.py — 清掉 venv 裡孤兒的 nvidia_*_cu12-* dist-info。

為什麼需要：cu12 與 cu13 的 NVIDIA wheel 會安裝到**重疊**的路徑
（例如 nvidia/nccl/lib/libnccl.so.2）。cu12 的 dist-info 雖然已經被 cu13 取代，
但它還留在 site-packages 裡，會造成兩個問題：
  * importlib.metadata 回報過期版本（cuDNN 顯示成 9.19.0.56）
  * 檔案覆寫競態贏了 -> libtorch_cuda.so: undefined symbol

只有「孤兒包獨佔、沒有任何 live 包宣稱」的路徑會被刪除；共用路徑保留，
並用 force-reinstall 讓 live 的 cu13 套件重新佔用它們。

用法：
    python cleanup_cu12_orphans.py --venv /path/to/.venv            # dry-run
    python cleanup_cu12_orphans.py --venv /path/to/.venv --apply
"""
import argparse
import csv
import glob
import os
import shutil
import subprocess
import sys

PY_BIN = ""
SP = ""
APPLY = False


def record(dist_info):
    path = os.path.join(SP, dist_info, "RECORD")
    if not os.path.exists(path):
        return []
    with open(path) as fh:
        return [row[0] for row in csv.reader(fh) if row]


def main():
    global APPLY, PY_BIN, SP
    ap = argparse.ArgumentParser(description="Remove orphaned nvidia cu12 dist-info dirs.")
    ap.add_argument("--venv", required=True, help="path to the venv to clean")
    ap.add_argument("--apply", action="store_true", help="actually delete (default: dry-run)")
    args = ap.parse_args()

    venv = os.path.abspath(args.venv)
    APPLY = args.apply
    PY_BIN = os.path.join(venv, "bin", "python")
    site = glob.glob(os.path.join(venv, "lib", "python*", "site-packages"))
    if not site:
        print(f"[FAIL] 找不到 site-packages：{venv}")
        return 2
    SP = site[0]
    if not os.path.exists(PY_BIN):
        print(f"[FAIL] 找不到 python：{PY_BIN}")
        return 2

    orphans = sorted(os.path.basename(d) for d in glob.glob(os.path.join(SP, "nvidia_*_cu12-*.dist-info")))
    live = sorted(
        os.path.basename(d)
        for d in glob.glob(os.path.join(SP, "nvidia_*.dist-info"))
        if os.path.basename(d) not in orphans
    )
    print(f"orphans (cu12) : {len(orphans)}")
    print(f"live  (cu13/…) : {len(live)}")

    live_files = set()
    for d in live:
        live_files.update(record(d))

    orphan_files = set()
    for d in orphans:
        orphan_files.update(record(d))

    shared = sorted(orphan_files & live_files)
    exclusive = sorted(orphan_files - live_files)
    print(f"\nshared paths (cu13 owns them too, kept) : {len(shared)}")
    print(f"exclusive orphan paths (safe to delete)  : {len(exclusive)}")

    # live packages that must own their shared paths -> force reinstall
    needs_reinstall = []
    for d in live:
        files = set(record(d))
        if files & orphan_files:
            needs_reinstall.append(d)
    print(f"\nlive packages overlapping orphan paths (force-reinstall):")
    for d in needs_reinstall:
        print(f"  - {d}")

    if not APPLY:
        print("\n[dry-run] 加上 --apply 才會真的刪除")
        return 0

    for d in needs_reinstall:
        # dist-info dir name -> "nvidia_cudnn_cu13-9.24.0.43.dist-info"
        stem = d[: -len(".dist-info")]
        name, _, version = stem.rpartition("-")
        pkg = name.replace("_", "-")
        print(f"  reinstall {pkg}=={version} (cu132 index)")
        subprocess.run(
            [
                "uv", "pip", "install", "--python", PY_BIN,
                "--reinstall", "--no-deps", f"{pkg}=={version}",
                "--index-url", "https://download.pytorch.org/whl/cu132",
            ],
            check=False,
        )

    removed_files = 0
    for rel in exclusive:
        p = os.path.join(SP, rel)
        if os.path.isfile(p):
            os.remove(p)
            removed_files += 1
        elif os.path.isdir(p) and not os.listdir(p):
            os.rmdir(p)

    for d in orphans:
        p = os.path.join(SP, d)
        if os.path.isdir(p):
            shutil.rmtree(p)

    print(f"\nremoved {removed_files} exclusive orphan files")
    print(f"removed {len(orphans)} orphan dist-info dirs")
    return 0


if __name__ == "__main__":
    sys.exit(main())
