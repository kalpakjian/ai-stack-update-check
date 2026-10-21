# -*- coding: utf-8 -*-
"""bench_gpu.py — PyTorch + CUDA + cuDNN 效能基準（微基準 + 文章基線工作負載）。

測什麼：
  1. cuBLAS matmul：fp32（TF32 off/on）、fp16、bf16 → TFLOPS
  2. cuDNN conv2d：fp32（TF32 off/on）、fp16；contiguous vs channels_last
  3. torch.backends.cudnn.benchmark（autotune）開關的前後差異
  4. SDPA（flash attention）延遲
  5. 文章基線工作負載：DistilBERT SST-2 批次推論（texts/s）、ViT-base 224（ms/img）

用法：
    python bench_gpu.py                 # 全部
    python bench_gpu.py --skip-hf       # 只跑微基準（不載模型）
    python bench_gpu.py --json          # 附 JSON（給日誌／比較用）
    python bench_gpu.py --iters 50

輸出全英文（Windows 主控台重導向時會用 GBK 解碼，中文會亂碼）。
"""

from __future__ import annotations

import argparse
import json
import os
import time

import torch
import torch.nn as nn


def sync():
    torch.cuda.synchronize()


def timeit(fn, warmup=10, iters=50):
    """回傳 (每次毫秒, 峰值配置 MB)。"""
    for _ in range(warmup):
        fn()
    sync()
    torch.cuda.reset_peak_memory_stats()
    start, end = torch.cuda.Event(True), torch.cuda.Event(True)
    start.record()
    for _ in range(iters):
        fn()
    end.record()
    sync()
    peak = torch.cuda.max_memory_allocated() / 1048576
    return start.elapsed_time(end) / iters, peak


def matmul_tflops(size, dtype, tf32, iters):
    a = torch.randn(size, size, device="cuda", dtype=dtype)
    b = torch.randn(size, size, device="cuda", dtype=dtype)
    torch.backends.cuda.matmul.allow_tf32 = tf32
    ms, _ = timeit(lambda: a @ b, iters=iters)
    flops = 2.0 * size ** 3
    return ms, flops / (ms / 1000) / 1e12


def conv_bench(shape, out_ch, kernel, stride, dtype, tf32, channels_last, iters):
    x = torch.randn(*shape, device="cuda", dtype=dtype)
    conv = nn.Conv2d(shape[1], out_ch, kernel, stride=stride, padding=kernel // 2)
    conv = conv.cuda().to(dtype)
    if channels_last:
        x = x.to(memory_format=torch.channels_last)
        conv = conv.to(memory_format=torch.channels_last)
    torch.backends.cudnn.allow_tf32 = tf32
    ms, peak = timeit(lambda: conv(x), iters=iters)
    images = shape[0]
    return ms, images / (ms / 1000), peak


def sdpa_bench(batch, heads, seq, dim, iters):
    q = torch.randn(batch, heads, seq, dim, device="cuda", dtype=torch.float16)
    k = torch.randn(batch, heads, seq, dim, device="cuda", dtype=torch.float16)
    v = torch.randn(batch, heads, seq, dim, device="cuda", dtype=torch.float16)
    ms, _ = timeit(
        lambda: torch.nn.functional.scaled_dot_product_attention(q, k, v), iters=iters)
    flops = 4.0 * batch * heads * seq * seq * dim  # QK^T + AV
    return ms, flops / (ms / 1000) / 1e12


def hf_baselines(batch_texts, batch_imgs):
    """文章基線：DistilBERT SST-2（texts/s）與 ViT-base 224（ms/img）。"""
    from transformers import (AutoModelForImageClassification,
                              AutoModelForSequenceClassification, AutoTokenizer)

    out = {}
    tok = AutoTokenizer.from_pretrained("distilbert-base-uncased-finetuned-sst-2-english")
    model = AutoModelForSequenceClassification.from_pretrained(
        "distilbert-base-uncased-finetuned-sst-2-english").cuda().eval()
    text = ("The local AI stack should update itself and roll back when a smoke test fails. "
            "PyTorch wheels decide which Python version is actually safe to install.")
    total, n = 10000, 0
    t0 = time.perf_counter()
    with torch.inference_mode():
        for _ in range(total // batch_texts):
            batch = tok([text] * batch_texts, return_tensors="pt", padding="max_length",
                        truncation=True, max_length=128).to("cuda")
            model(**batch)
            n += batch_texts
    sync()
    seconds = time.perf_counter() - t0
    out["distilbert"] = {"texts": n, "seconds": round(seconds, 2),
                         "texts_per_s": round(n / seconds, 1),
                         "peak_mb": round(torch.cuda.max_memory_allocated() / 1048576, 1)}
    del model
    torch.cuda.empty_cache()

    model = AutoModelForImageClassification.from_pretrained(
        "google/vit-base-patch16-224").cuda().eval()
    x = torch.randn(batch_imgs, 3, 224, 224, device="cuda")
    ms, _ = timeit(lambda: model(x), warmup=5, iters=20)
    out["vit_base"] = {"batch": batch_imgs, "ms_per_batch": round(ms, 2),
                       "ms_per_image": round(ms / batch_imgs, 2),
                       "images_per_s": round(batch_imgs / (ms / 1000), 1)}
    return out


def main(argv=None):
    parser = argparse.ArgumentParser(description="PyTorch + CUDA + cuDNN benchmark")
    parser.add_argument("--iters", type=int, default=50)
    parser.add_argument("--matmul-size", type=int, default=4096)
    parser.add_argument("--skip-hf", action="store_true", help="skip the HF model baselines")
    parser.add_argument("--only-hf", action="store_true", help="run only the HF baselines")
    parser.add_argument("--db-batch", type=int, default=64, help="DistilBERT batch size")
    parser.add_argument("--vit-batch", type=int, default=1, help="ViT batch size")
    parser.add_argument("--json", action="store_true", help="also print JSON")
    parser.add_argument("--json-out", metavar="PATH", help="write the JSON report to PATH")
    parser.add_argument("--quick", action="store_true",
                        help="fast subset (iters 20, matmul 2048, no HF) for before/after runs")
    args = parser.parse_args(argv)

    if args.quick:
        args.iters = min(args.iters, 20)
        args.matmul_size = min(args.matmul_size, 2048)
        args.skip_hf = True

    if args.only_hf:
        os.environ.setdefault("HF_HUB_OFFLINE", "1")
        os.environ.setdefault("TRANSFORMERS_OFFLINE", "1")
        hf = hf_baselines(args.db_batch, args.vit_batch)
        if args.json_out:
            with open(args.json_out, "w", encoding="utf-8") as handle:
                json.dump({"hf": hf}, handle, ensure_ascii=False, indent=2)
        print(json.dumps(hf, ensure_ascii=False, indent=2))
        return 0

    report = {"env": {}, "matmul": {}, "conv": {}, "cudnn_benchmark": {},
              "attention": {}, "hf": {}}
    torch.backends.cudnn.benchmark = False

    e = report["env"]
    e["torch"] = torch.__version__
    e["cuda"] = torch.version.cuda
    e["cudnn"] = torch.backends.cudnn.version()
    e["device"] = torch.cuda.get_device_name(0)
    e["capability"] = list(torch.cuda.get_device_capability(0))
    e["tf32_matmul_default"] = torch.backends.cuda.matmul.allow_tf32
    e["tf32_cudnn_default"] = torch.backends.cudnn.allow_tf32
    e["cudnn_benchmark_default"] = torch.backends.cudnn.benchmark

    print("=" * 78)
    print("PyTorch + CUDA + cuDNN benchmark")
    print("torch %s | cuda %s | cudnn %s | %s sm_%d%d"
          % (e["torch"], e["cuda"], e["cudnn"], e["device"],
             e["capability"][0], e["capability"][1]))
    print("defaults: tf32_matmul=%s tf32_cudnn=%s cudnn_benchmark=%s"
          % (e["tf32_matmul_default"], e["tf32_cudnn_default"],
             e["cudnn_benchmark_default"]))
    print("=" * 78)

    size = args.matmul_size
    print("\n[cuBLAS] matmul %dx%d  (warmup 10, iters %d)" % (size, size, args.iters))
    for label, dtype, tf32 in (("fp32 (tf32 off)", torch.float32, False),
                               ("fp32 (tf32 on) ", torch.float32, True),
                               ("fp16            ", torch.float16, False),
                               ("bf16            ", torch.bfloat16, False)):
        ms, tflops = matmul_tflops(size, dtype, tf32, args.iters)
        report["matmul"][label.strip()] = {"ms": round(ms, 3), "tflops": round(tflops, 1)}
        print("  %s  %7.3f ms   %7.1f TFLOPS" % (label, ms, tflops))

    print("\n[cuDNN] conv2d  (warmup 10, iters %d)" % args.iters)
    stem = (32, 3, 224, 224)
    mid = (32, 256, 56, 56)
    cases = (
        ("stem 224 fp32 tf32off", stem, 64, 7, 2, torch.float32, False, False),
        ("stem 224 fp32 tf32on ", stem, 64, 7, 2, torch.float32, True, False),
        ("stem 224 fp16        ", stem, 64, 7, 2, torch.float16, False, False),
        ("mid 56 fp32 tf32off  ", mid, 256, 3, 1, torch.float32, False, False),
        ("mid 56 fp32 tf32on   ", mid, 256, 3, 1, torch.float32, True, False),
        ("mid 56 fp32 tf32on CL", mid, 256, 3, 1, torch.float32, True, True),
        ("mid 56 fp16          ", mid, 256, 3, 1, torch.float16, False, False),
        ("mid 56 fp16 CL       ", mid, 256, 3, 1, torch.float16, False, True),
    )
    for label, shape, out_ch, k, s, dtype, tf32, cl in cases:
        ms, ips, peak = conv_bench(shape, out_ch, k, s, dtype, tf32, cl, args.iters)
        report["conv"][label.strip()] = {"ms": round(ms, 3),
                                         "images_per_s": round(ips, 1),
                                         "peak_mb": round(peak, 1)}
        print("  %s  %7.3f ms   %8.1f img/s   peak %6.1f MB" % (label, ms, ips, peak))

    print("\n[cuDNN] torch.backends.cudnn.benchmark (autotune) effect")
    bench_case = (mid, 256, 3, 1, torch.float32, True, False)
    torch.backends.cudnn.benchmark = False
    ms_off, _, _ = conv_bench(*bench_case, iters=args.iters)
    torch.backends.cudnn.benchmark = True
    t0 = time.perf_counter()
    conv_bench(*bench_case, iters=3)          # first calls include autotune
    tune = time.perf_counter() - t0
    ms_on, _, _ = conv_bench(*bench_case, iters=args.iters)
    torch.backends.cudnn.benchmark = False
    report["cudnn_benchmark"] = {"benchmark_off_ms": round(ms_off, 3),
                                 "benchmark_on_ms": round(ms_on, 3),
                                 "speedup": round(ms_off / ms_on, 3),
                                 "autotune_seconds": round(tune, 3)}
    print("  benchmark=False  %7.3f ms" % ms_off)
    print("  benchmark=True   %7.3f ms   (speedup %.2fx, first 3 calls/autotune %.2f s)"
          % (ms_on, ms_off / ms_on, tune))

    print("\n[attention] SDPA fp16 batch 8 x heads 16 x seq 512 x dim 64")
    ms, tflops = sdpa_bench(8, 16, 512, 64, args.iters)
    report["attention"]["sdpa_fp16"] = {"ms": round(ms, 3), "tflops": round(tflops, 1)}
    print("  %7.3f ms   %7.1f TFLOPS" % (ms, tflops))

    if not args.skip_hf:
        print("\n[HF baselines] cached models, offline")
        os.environ.setdefault("HF_HUB_OFFLINE", "1")
        os.environ.setdefault("TRANSFORMERS_OFFLINE", "1")
        report["hf"] = hf_baselines(args.db_batch, args.vit_batch)
        d, v = report["hf"]["distilbert"], report["hf"]["vit_base"]
        print("  DistilBERT SST-2  %d texts in %.2f s -> %.1f texts/s (peak %.1f MB)"
              % (d["texts"], d["seconds"], d["texts_per_s"], d["peak_mb"]))
        print("  ViT-base 224      %.2f ms/batch(%d) -> %.2f ms/img, %.1f img/s"
              % (v["ms_per_batch"], v["batch"], v["ms_per_image"], v["images_per_s"]))

    print("\n" + "=" * 78)
    if args.json_out:
        with open(args.json_out, "w", encoding="utf-8") as handle:
            json.dump(report, handle, ensure_ascii=False, indent=2)
    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
