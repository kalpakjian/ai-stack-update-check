# -*- coding: utf-8 -*-
"""auto_update.py — 依 check_stack.py 的 JSON 報告自動升級，失敗時回滾。

用法：
    python auto_update.py --report report.json --json-out summary.json
    python auto_update.py --report report.json --dry-run
    python auto_update.py --report report.json --simulate-failure   # 測試回滾

退出碼：0 = 成功或無需更新；1 = 升級失敗但已成功回滾；2 = 回滾失敗（需人工處理）。
"""
from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import tempfile
import time
from datetime import datetime

PY = sys.executable
TORCH_INDEX = "https://download.pytorch.org/whl/{cu}"
TORCH_PKGS = {"torch", "torchvision"}
AUDIO_PKGS = {"torchaudio", "torchcodec"}
NEVER_AUTO = {"ollama", "torch-cuda"}

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
BENCH_SCRIPT = os.path.join(SCRIPT_DIR, "bench_gpu.py")
# 升級前後要比的關鍵指標：(顯示名, bench_gpu.py JSON 的路徑, 越高越好)
BENCH_METRICS = (
    ("matmul fp16 TFLOPS", ("matmul", "fp16", "tflops"), True),
    ("conv fp16+CL ms", ("conv", "mid 56 fp16 CL", "ms"), False),
    ("DistilBERT texts/s", ("hf", "distilbert", "texts_per_s"), True),
    ("ViT ms/img", ("hf", "vit_base", "ms_per_image"), False),
)

# 升級後的健康檢查：匯入所有關鍵套件 + GPU matmul + 真的走 cuDNN 的卷積
# （CUDA 可用但 cuDNN 不可用 → 退出碼 1 → 觸發回滾）
SMOKE_CODE = (
    "import torch, numpy;"
    "print('torch', torch.__version__);"
    "print('cuda_available', torch.cuda.is_available());"
    "print('cudnn_available', torch.backends.cudnn.is_available());"
    "print('cudnn_version', torch.backends.cudnn.version());"
    "dev='cuda' if torch.cuda.is_available() else 'cpu';"
    "x=torch.randn(256,256,device=dev);"
    "print('matmul', tuple((x@x).shape));"
    "import torch.nn as nn;"
    "y=nn.Conv2d(3,4,3,padding=1).to(dev)(torch.randn(4,3,32,32,device=dev));"
    "print('conv', tuple(y.shape), y.device);"
    "import transformers, datasets, accelerate;"
    "import torchvision, torchaudio, torchcodec;"
    "import pandas;"
    "print('imports_ok');"
    "import sys;"
    "sys.exit(0 if (not torch.cuda.is_available()"
    " or torch.backends.cudnn.is_available()) else 1)"
)


def run(cmd, timeout=3600):
    started = time.time()
    proc = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
    return {
        "cmd": " ".join(cmd),
        "rc": proc.returncode,
        "out": (proc.stdout or "")[-4000:],
        "err": (proc.stderr or "")[-4000:],
        "seconds": round(time.time() - started, 1),
    }


def freeze():
    res = run([PY, "-m", "pip", "freeze"], timeout=180)
    if res["rc"] != 0:
        raise RuntimeError("pip freeze failed: " + res["err"][-500:])
    data = {}
    for line in res["out"].splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "==" not in line:
            continue
        name, version = line.split("==", 1)
        data[name.lower().replace("_", "-")] = version
    return data


def write_json(path, data):
    if path:
        with open(path, "w", encoding="utf-8") as handle:
            json.dump(data, handle, ensure_ascii=False, indent=2)

def build_plan(report):
    """把報告中的 updates 轉成實際 pip 指令；排除 Python/Ollama/CUDA-only。"""
    updates = set(report.get("updates") or [])
    skipped = sorted(updates & NEVER_AUTO)

    plain = sorted(p for p in updates if p not in TORCH_PKGS | AUDIO_PKGS | NEVER_AUTO)
    audio = sorted(p for p in updates if p in AUDIO_PKGS)
    want_torch = bool(updates & TORCH_PKGS)
    cu = report.get("newest_cuda")

    cmds, notes = [], []
    if plain:
        cmds.append([PY, "-m", "pip", "install", "--upgrade", *plain])
    if want_torch:
        if cu:
            cmds.append([PY, "-m", "pip", "install", "--upgrade",
                         "torch", "torchvision",
                         "--index-url", TORCH_INDEX.format(cu=cu)])
        else:
            notes.append("torch skipped: no Windows wheel for this Python")
    if audio:
        cmds.append([PY, "-m", "pip", "install", "--upgrade", *audio])
    return cmds, sorted(updates), skipped, notes


def apply_plan(cmds):
    results = []
    for cmd in cmds:
        res = run(cmd)
        results.append(res)
        if res["rc"] != 0:
            break
    return results


def smoke():
    return run([PY, "-c", SMOKE_CODE], timeout=300)


def pip_check():
    return run([PY, "-m", "pip", "check"], timeout=120)


def bench(quick=False):
    """跑 bench_gpu.py 拿 JSON；失敗回 {'error': ...}。"""
    if not os.path.isfile(BENCH_SCRIPT):
        return {"error": "bench_gpu.py not found next to auto_update.py"}
    out_path = os.path.join(tempfile.gettempdir(), "ai-stack-bench-auto.json")
    cmd = [PY, BENCH_SCRIPT, "--json-out", out_path]
    if quick:
        cmd.append("--quick")
    res = run(cmd, timeout=1800)
    if res["rc"] != 0:
        return {"error": "benchmark exit %d: %s"
                % (res["rc"], (res["err"] or "").strip()[-300:])}
    try:
        with open(out_path, "r", encoding="utf-8") as handle:
            return json.load(handle)
    except Exception as exc:  # noqa: BLE001
        return {"error": "cannot read benchmark output: %s" % exc}


def bench_metrics(data):
    """只留關鍵指標。"""
    out = {}
    for label, path, _ in BENCH_METRICS:
        current = data
        for key in path:
            current = current.get(key) if isinstance(current, dict) else None
        if isinstance(current, (int, float)):
            out[label] = round(float(current), 3)
    return out


def bench_delta(before, after):
    """升級前後差異（含「越高越好／越低越好」判定）。"""
    rows = {}
    for label, _, higher_better in BENCH_METRICS:
        old, new = before.get(label), after.get(label)
        if not old or not new:
            continue
        pct = (new - old) / old * 100.0
        rows[label] = {"before": old, "after": new, "pct": round(pct, 1),
                       "better": (pct >= 0) if higher_better else (pct <= 0)}
    return rows


def rollback(changed, before):
    """把本次變動的套件還原到快照版本；新裝的套件則卸載。"""
    plain_specs, torch_specs, new_pkgs = [], [], []
    for name in sorted(changed):
        old = before.get(name)
        if not old:
            new_pkgs.append(name)
            continue
        base = old.split("+")[0]
        if name in TORCH_PKGS:
            torch_specs.append("%s==%s" % (name, base))
        else:
            plain_specs.append("%s==%s" % (name, base))

    old_cu = None
    match = re.search(r"\+cu(\d+)", before.get("torch", "") or "")
    if match:
        old_cu = "cu" + match.group(1)

    results = []
    if plain_specs:
        results.append(run([PY, "-m", "pip", "install", "--force-reinstall",
                            "--no-deps", *plain_specs]))
    if torch_specs:
        cmd = [PY, "-m", "pip", "install", "--force-reinstall", "--no-deps", *torch_specs]
        if old_cu:
            cmd += ["--index-url", TORCH_INDEX.format(cu=old_cu)]
        results.append(run(cmd))
    if new_pkgs:
        results.append(run([PY, "-m", "pip", "uninstall", "-y", *new_pkgs], timeout=180))
    return results


def main(argv=None):
    parser = argparse.ArgumentParser(
        description="Apply updates from a check_stack JSON report, with rollback on failure."
    )
    parser.add_argument("--report", required=True, help="check_stack.py --json-out file")
    parser.add_argument("--json-out", help="write the auto-update summary JSON here")
    parser.add_argument("--dry-run", action="store_true", help="print commands only")
    parser.add_argument("--simulate-failure", action="store_true",
                        help="force the post-upgrade check to fail (tests rollback)")
    parser.add_argument("--bench", action="store_true",
                        help="run the GPU benchmark before and after the upgrade")
    parser.add_argument("--bench-quick", action="store_true",
                        help="with --bench: fast subset (no HF baselines)")
    args = parser.parse_args(argv)

    with open(args.report, "r", encoding="utf-8") as handle:
        report = json.load(handle)

    cmds, updates, skipped, notes = build_plan(report)
    summary = {
        "timestamp": datetime.now().astimezone().isoformat(timespec="seconds"),
        "updates_detected": updates,
        "skipped": skipped,
        "notes": notes,
        "planned_commands": [" ".join(c) for c in cmds],
        "dry_run": bool(args.dry_run),
        "changed": {},
        "upgraded": {},
        "smoke": None,
        "pip_check": None,
        "rollback": "not-needed",
        "result": None,
    }

    if not cmds:
        summary["result"] = "no-op"
        print("Auto-update: nothing to do.")
        write_json(args.json_out, summary)
        return 0

    if args.dry_run:
        for cmd in summary["planned_commands"]:
            print("DRY-RUN:", cmd)
        summary["result"] = "dry-run"
        write_json(args.json_out, summary)
        return 0

    if args.bench:
        print("Benchmark (before)...")
        summary["bench_before"] = bench(args.bench_quick)

    before = freeze()
    summary["before"] = before

    install_results = apply_plan(cmds)
    summary["install"] = install_results
    after = freeze()
    summary["after"] = after

    changed = {k: [before.get(k), after.get(k)]
               for k in set(before) | set(after)
               if before.get(k) != after.get(k)}
    summary["changed"] = changed
    summary["upgraded"] = {
        k: {"from": before.get(k), "to": after.get(k)}
        for k in changed if k in set(updates)
    }

    install_ok = all(item["rc"] == 0 for item in install_results)
    if install_ok:
        result = smoke()
        summary["smoke"] = "pass" if result["rc"] == 0 else "fail"
        summary["smoke_detail"] = {
            "rc": result["rc"], "out": result["out"], "err": result["err"]
        }
    else:
        summary["smoke"] = "skipped (install failed)"

    ok = install_ok and summary["smoke"] == "pass"
    if args.simulate_failure and ok:
        summary["smoke"] = "forced-fail"
        ok = False

    if ok:
        check = pip_check()
        summary["pip_check"] = "pass" if check["rc"] == 0 else "fail"
        if check["rc"] != 0:
            summary["pip_check_detail"] = {"out": check["out"], "err": check["err"]}
            ok = False

    if ok:
        if args.bench:
            print("Benchmark (after)...")
            after_bench = bench(args.bench_quick)
            summary["bench_after"] = after_bench
            deltas = bench_delta(bench_metrics(summary.get("bench_before") or {}),
                                 bench_metrics(after_bench))
            summary["bench_delta"] = deltas
            if deltas:
                print("Bench: " + ", ".join(
                    "%s %s->%s (%+.1f%%)" % (k, v["before"], v["after"], v["pct"])
                    for k, v in deltas.items()))
            elif after_bench.get("error"):
                print("Bench: failed - %s" % after_bench["error"])
        summary["result"] = "success" if changed else "no-op"
        detail = ", ".join("%s %s->%s" % (k, v["from"], v["to"])
                           for k, v in summary["upgraded"].items())
        print("Auto-update OK: %s" % (detail or "no version change"))
        print("Smoke: %s | pip check: %s" % (summary["smoke"], summary["pip_check"]))
        write_json(args.json_out, summary)
        return 0

    # ---- failure: roll back ----
    print("Auto-update FAILED (smoke=%s). Rolling back %d package(s)..."
          % (summary["smoke"], len(changed)))
    rb_results = rollback(changed, before)
    summary["rollback_log"] = rb_results

    restored = freeze()
    mismatches = {k: [before.get(k), restored.get(k)]
                  for k in changed if before.get(k) != restored.get(k)}
    summary["rollback_mismatches"] = mismatches
    rb_ok = all(item["rc"] == 0 for item in rb_results) and not mismatches
    summary["rollback"] = "success" if rb_ok else "failed"

    if rb_ok:
        verify = smoke()
        summary["post_rollback_smoke"] = "pass" if verify["rc"] == 0 else "fail"
        summary["result"] = "rolled-back"
        print("Rollback completed; post-rollback smoke: %s"
              % summary["post_rollback_smoke"])
        write_json(args.json_out, summary)
        return 1

    summary["result"] = "rollback-failed"
    print("Rollback FAILED. Manual action required. Mismatches: %s" % mismatches)
    write_json(args.json_out, summary)
    return 2


if __name__ == "__main__":
    sys.exit(main())

