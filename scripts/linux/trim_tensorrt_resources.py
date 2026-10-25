#!/usr/bin/env python3
"""trim_tensorrt_resources.py — 裁掉 TensorRT 用不到的 GPU 架構資源檔。

背景
----
NVIDIA 的 ``tensorrt_cu13_libs`` wheel 把 **所有** GPU 架構的 builder resource
都塞進 ``tensorrt_libs/``，而且連 Windows 專用的 blob 也一併附上：

    libnvinfer_builder_resource_sm75.so.11.3.0    (113 MiB)
    libnvinfer_builder_resource_sm80.so.11.3.0    (182 MiB)
    libnvinfer_builder_resource_sm86.so.11.3.0    (172 MiB)
    libnvinfer_builder_resource_sm89.so.11.3.0    (181 MiB)  <- 本機 RTX 4090
    libnvinfer_builder_resource_sm90.so.11.3.0    (441 MiB)
    libnvinfer_builder_resource_sm100.so.11.3.0   (278 MiB)
    libnvinfer_builder_resource_sm120.so.11.3.0   (257 MiB)
    libnvinfer_builder_resource_ptx.so.11.3.0     (231 MiB)  <- PTX JIT fallback
    libnvinfer_builder_resource_win_*.so.11.3.0   (1.81 GiB, Windows 專用)

在 Linux 上跑 RTX 4090（sm_89）時，真正會被載入的只有 ``sm89`` 與 ``ptx``；
其餘 14 個檔案永遠不會被碰，卻佔掉約 3.2 GiB。

本工具 **只搬走**（同一個檔案系統內 rename，瞬時完成）確定用不到的檔案：

  * ``win_*``  —— Windows 專用，Linux 永遠用不到
  * 非本機 compute capability 的架構 blob —— 例如 sm90/sm100/sm120

保留：本機架構（sm89）、``ptx``（前向相容的 PTX JIT fallback）、
以及 ``libnvinfer.so.11`` / ``libnvinfer_plugin.so.11`` /
``libnvonnxparser.so.11`` 等核心函式庫。

用法
----
    # 預設 dry-run：只列出會搬走什麼、可以回收多少
    python trim_tensorrt_resources.py --venv /path/to/.venv-nightly

    # 真的搬走（搬到 <venv>/../.tensorrt-trim-<timestamp>/，可還原）
    python trim_tensorrt_resources.py --venv /path/to/.venv-nightly --apply

    # 驗證 TRT 沒事之後，永久刪掉暫存區，磁碟空間才真的回收
    python trim_tensorrt_resources.py --venv /path/to/.venv-nightly --purge

    # 若發現問題，把檔案搬回去
    python trim_tensorrt_resources.py --venv /path/to/.venv-nightly --restore

安全說明
--------
* 檔案是 **搬走** 不是刪除，暫存區放在 **venv 外面** 的
  ``<venv>/../.tensorrt-trim-<timestamp>/``，所以 ``du -sh <venv>`` 立刻縮小；
  同一個檔案系統內 rename，瞬時完成。
* ``--purge`` 才會真正刪掉（釋放磁碟空間），在此之前 ``--restore`` 都能完整還原。
* ``RECORD`` 不動 —— 這些檔案仍被 ``tensorrt_cu13_libs`` 的 RECORD 記載，
  所以 ``uv pip check`` 與 ``pip check`` 都不會報錯。
* 搬走之後請跑一次 TRT 煙霧測試（見 README）確認 builder 與 engine 反序列化正常。
"""
from __future__ import annotations

import argparse
import os
import re
import shutil
import subprocess
import sys
import time
from pathlib import Path

RESOURCE_RE = re.compile(r"^libnvinfer_builder_resource_(?P<arch>[a-z0-9_]+)\.so")
TRASH_PREFIX = ".tensorrt-trim-"
LEGACY_TRASH_PREFIX = ".trash-trim-"


def find_libs_dir(venv: Path) -> Path | None:
    """在 venv 裡找到承載 tensorrt_libs/ 的 site-packages。"""
    for sp in venv.glob("lib/python*/site-packages"):
        if (sp / "tensorrt_libs").is_dir():
            return sp / "tensorrt_libs"
    return None


def local_arch() -> str | None:
    """本機 GPU 的 compute capability，例如 'sm89'。"""
    try:
        out = subprocess.run(
            ["nvidia-smi", "--query-gpu=compute_cap", "--format=csv,noheader"],
            capture_output=True, text=True, timeout=20,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if out.returncode != 0:
        return None
    caps = [m.group(1) + m.group(2) for m in re.finditer(r"(\d+)\.(\d+)", out.stdout)]
    return f"sm{caps[0]}" if caps else None


def classify(libs: Path, keep_arch: str | None):
    """把資源檔分成 (keep, removable) 兩組。"""
    keep, removable = [], []
    for path in sorted(libs.iterdir()):
        if not path.is_file():
            continue
        m = RESOURCE_RE.match(path.name)
        if not m:
            continue
        arch = m.group("arch")
        if arch.startswith("win_"):
            removable.append((path, "Windows 專用"))
        elif arch in ("ptx", keep_arch):
            keep.append((path, "必要"))
        else:
            removable.append((path, f"非本機架構（本機 {keep_arch}）"))
    return keep, removable


def human(size: int) -> str:
    return f"{size / 1024 ** 2:.1f} MiB"


def find_trash(venv: Path, libs: Path) -> Path | None:
    """找最近一次裁切產生的暫存區（新版在 venv 外面，舊版在 tensorrt_libs 裡）。"""
    candidates = []
    for base, prefix in ((venv.parent, TRASH_PREFIX), (libs, LEGACY_TRASH_PREFIX)):
        if base.is_dir():
            candidates.extend(p for p in base.iterdir()
                              if p.is_dir() and p.name.startswith(prefix))
    if not candidates:
        return None
    return sorted(candidates, key=lambda p: p.name)[-1]


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Trim unused TensorRT GPU-architecture resource blobs.")
    ap.add_argument("--venv", required=True, help="path to the venv to trim")
    ap.add_argument("--apply", action="store_true", help="actually move the files (default: dry-run)")
    ap.add_argument("--restore", action="store_true", help="restore the most recent trash directory")
    ap.add_argument("--purge", action="store_true", help="permanently delete the trash directory (frees the disk space)")
    ap.add_argument("--keep-arch", default="", help="override the local compute capability (e.g. sm89)")
    args = ap.parse_args(argv)

    venv = Path(args.venv).resolve()
    libs = find_libs_dir(venv)
    if libs is None:
        print(f"[FAIL] 在 {venv} 找不到 tensorrt_libs/（這個 venv 沒有裝 TensorRT？）")
        return 2

    if args.restore:
        trash = find_trash(venv, libs)
        if trash is None:
            print("[FAIL] 找不到裁切暫存區，沒有可還原的內容")
            return 2
        moved = 0
        freed = 0
        for src in sorted(trash.iterdir()):
            dest = libs / src.name
            if dest.exists():
                print(f"  [skip] 目標已存在：{dest.name}")
                continue
            freed += src.stat().st_size
            shutil.move(str(src), str(dest))
            moved += 1
        shutil.rmtree(trash, ignore_errors=True)
        print(f"[OK] 已還原 {moved} 個檔案（{human(freed)}）")
        return 0

    if args.purge:
        trash = find_trash(venv, libs)
        if trash is None:
            print("[FAIL] 找不到裁切暫存區，沒有東西可以刪")
            return 2
        size = sum(p.stat().st_size for p in trash.rglob("*") if p.is_file())
        shutil.rmtree(trash, ignore_errors=True)
        print(f"[OK] 已永久刪除 {trash}（釋放 {human(size)}）")
        return 0

    keep_arch = args.keep_arch or local_arch()
    if not keep_arch:
        print("[FAIL] 讀不到本機 GPU 的 compute capability（nvidia-smi 不可用？），"
              "請用 --keep-arch smXX 指定")
        return 2

    keep, removable = classify(libs, keep_arch)
    total = sum(p.stat().st_size for p, _ in removable)

    print(f"venv            : {venv}")
    print(f"tensorrt_libs   : {libs}")
    print(f"本機 GPU 架構   : {keep_arch}")
    print()
    print(f"保留 ({len(keep)} 個, {human(sum(p.stat().st_size for p, _ in keep))}):")
    for path, why in keep:
        print(f"  + {path.name}  ({human(path.stat().st_size)})  [{why}]")
    print()
    print(f"可搬走 ({len(removable)} 個, 共 {human(total)}):")
    for path, why in removable:
        print(f"  - {path.name}  ({human(path.stat().st_size)})  [{why}]")

    if not removable:
        print("\n沒有可裁切的資源檔。")
        return 0

    if not args.apply:
        print("\n[dry-run] 加上 --apply 才會真的搬走（可再用 --restore 還原）")
        return 0

    trash = venv.parent / f"{TRASH_PREFIX}{time.strftime('%Y%m%d-%H%M%S')}"
    trash.mkdir(parents=True, exist_ok=False)
    for path, _ in removable:
        shutil.move(str(path), str(trash / path.name))
    print(f"\n[OK] 已搬走 {len(removable)} 個檔案到 {trash}")
    print(f"     venv 立刻縮小 {human(total)}；磁碟空間要等 --purge 才真正釋放")
    print(f"     驗證 TRT 沒問題後：")
    print(f"       python {os.path.basename(__file__)} --venv {venv} --purge")
    print(f"     若有問題則可還原：")
    print(f"       python {os.path.basename(__file__)} --venv {venv} --restore")
    return 0


if __name__ == "__main__":
    sys.exit(main())

