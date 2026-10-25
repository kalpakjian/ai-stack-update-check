#!/usr/bin/env python3
"""
AI stack benchmark - reproducible A/B/C harness.

Shape provenance (recovered from bench_before_driver_update.json by fitting):
  matmul        : N x N, N = 2048          (fit: 0.3012 vs baseline 0.303 ms)
  conv mid 56   : 32 x 256 x 56 x 56 -> 256, k3 s1   (fit: 1.588 vs baseline 1.603 ms)
  attention     : (4, 8, 1024, 64) fp16    (fit: 0.057  vs baseline 0.058 ms)
  conv stem 224 : shape NOT recovered -> own shape, NOT comparable to baseline

Usage: bench_stack.py <output.json>
"""
import json
import sys
import time

import torch
import torch.nn.functional as F

BATCH = 32
MATMUL_N = 2048
MID_C, MID_R = 256, 56
ATTN = (4, 8, 1024, 64)


def bench(fn, warmup=10, iters=30):
    for _ in range(warmup):
        fn()
    torch.cuda.synchronize()
    t0 = time.perf_counter()
    for _ in range(iters):
        fn()
    torch.cuda.synchronize()
    return (time.perf_counter() - t0) / iters * 1000.0


def peak_mb():
    return torch.cuda.max_memory_allocated() / 2 ** 20


def matmul_block():
    out = {}
    for tf32 in (False, True):
        torch.backends.cuda.matmul.allow_tf32 = tf32
        x = torch.randn(MATMUL_N, MATMUL_N, device="cuda", dtype=torch.float32)
        ms = bench(lambda: x @ x)
        out["fp32 (tf32 off)" if not tf32 else "fp32 (tf32 on)"] = {
            "ms": round(ms, 3),
            "tflops": round(2 * MATMUL_N ** 3 / (ms / 1000) / 1e12, 1),
        }
        del x
        torch.cuda.empty_cache()
    torch.backends.cuda.matmul.allow_tf32 = False
    for dt, key in ((torch.float16, "fp16"), (torch.bfloat16, "bf16")):
        x = torch.randn(MATMUL_N, MATMUL_N, device="cuda", dtype=dt)
        ms = bench(lambda: x @ x)
        out[key] = {
            "ms": round(ms, 3),
            "tflops": round(2 * MATMUL_N ** 3 / (ms / 1000) / 1e12, 1),
        }
        del x
        torch.cuda.empty_cache()
    return out


def _conv_case(cin, cout, res, k, stride, dtype, channels_last):
    torch.cuda.empty_cache()
    torch.cuda.reset_peak_memory_stats()
    x = torch.randn(BATCH, cin, res, res, device="cuda", dtype=dtype)
    w = torch.randn(cout, cin, k, k, device="cuda", dtype=dtype)
    if channels_last:
        x = x.to(memory_format=torch.channels_last)
        w = w.to(memory_format=torch.channels_last)
    ms = bench(lambda: F.conv2d(x, w, stride=stride, padding=k // 2))
    return {
        "ms": round(ms, 3),
        "images_per_s": round(BATCH / (ms / 1000), 1),
        "peak_mb": round(peak_mb(), 1),
    }


def conv_block():
    out = {}
    # --- mid 56 (baseline-comparable) ---
    torch.backends.cudnn.allow_tf32 = False
    out["mid 56 fp32 tf32off"] = _conv_case(MID_C, MID_C, MID_R, 3, 1, torch.float32, False)
    torch.backends.cudnn.allow_tf32 = True
    out["mid 56 fp32 tf32on"] = _conv_case(MID_C, MID_C, MID_R, 3, 1, torch.float32, False)
    out["mid 56 fp32 tf32on CL"] = _conv_case(MID_C, MID_C, MID_R, 3, 1, torch.float32, True)
    torch.backends.cudnn.allow_tf32 = False
    out["mid 56 fp16"] = _conv_case(MID_C, MID_C, MID_R, 3, 1, torch.float16, False)
    out["mid 56 fp16 CL"] = _conv_case(MID_C, MID_C, MID_R, 3, 1, torch.float16, True)
    # --- stem 224 (own shape, NOT comparable to baseline) ---
    out["stem 224 3->64 k3 s1 fp32"] = _conv_case(3, 64, 224, 3, 1, torch.float32, False)
    out["stem 224 3->64 k3 s1 fp16"] = _conv_case(3, 64, 224, 3, 1, torch.float16, False)
    return out


def attention_block():
    q = torch.randn(*ATTN, device="cuda", dtype=torch.float16)
    ms = bench(lambda: F.scaled_dot_product_attention(q, q, q))
    flops = 4 * ATTN[0] * ATTN[1] * ATTN[2] ** 2 * ATTN[3]
    return {"sdpa_fp16": {"ms": round(ms, 3), "tflops": round(flops / (ms / 1000) / 1e12, 1)}}


def cudnn_benchmark_block():
    torch.backends.cudnn.benchmark = False
    x = torch.randn(BATCH, MID_C, MID_R, MID_R, device="cuda", dtype=torch.float16)
    w = torch.randn(MID_C, MID_C, 3, 3, device="cuda", dtype=torch.float16)
    off = bench(lambda: F.conv2d(x, w, padding=1), warmup=5, iters=20)
    torch.backends.cudnn.benchmark = True
    t0 = time.perf_counter()
    F.conv2d(x, w, padding=1)
    torch.cuda.synchronize()
    auto = time.perf_counter() - t0
    on = bench(lambda: F.conv2d(x, w, padding=1), warmup=5, iters=20)
    return {
        "benchmark_off_ms": round(off, 3),
        "benchmark_on_ms": round(on, 3),
        "speedup": round(off / on, 3),
        "autotune_seconds": round(auto, 3),
    }


def main():
    out = {
        "env": {
            "torch": torch.__version__,
            "cuda": torch.version.cuda,
            "cudnn": torch.backends.cudnn.version(),
            "device": torch.cuda.get_device_name(0),
            "tf32_matmul_default": torch.backends.cuda.matmul.allow_tf32,
            "tf32_cudnn_default": torch.backends.cudnn.allow_tf32,
            "cudnn_benchmark_default": torch.backends.cudnn.benchmark,
        },
        "matmul": matmul_block(),
        "conv": conv_block(),
        "cudnn_benchmark": cudnn_benchmark_block(),
        "attention": attention_block(),
    }
    path = sys.argv[1] if len(sys.argv) > 1 else "bench.json"
    with open(path, "w") as f:
        json.dump(out, f, indent=2)
    print(f"written {path}  torch={out['env']['torch']} cuda={out['env']['cuda']}")


if __name__ == "__main__":
    main()
