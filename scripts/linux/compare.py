#!/usr/bin/env python3
"""compare.py — 把多份 bench_stack.py 產出的 JSON 排成對照表。

用法：
    python compare.py --dir /path/to/benchmarks

每個 JSON 的頂層鍵必須是 env / matmul / conv / attention / cudnn_benchmark
（bench_stack.py 的輸出格式）。
"""
import argparse
import json
import os

FILES = [
    ("base  cu130+drv610", "bench_before_driver_update.json"),
    ("A     cu130+drv615", "bench_A_cu130_newdriver.json"),
    ("B     cu132+drv615", "bench_B_cu132.json"),
    ("C     cu134+drv615", "bench_C_cu134.json"),
]


def main():
    ap = argparse.ArgumentParser(description="Compare bench_stack.py JSON outputs.")
    ap.add_argument("--dir", required=True, help="directory holding the benchmark JSON files")
    args = ap.parse_args()
    D = os.path.abspath(args.dir)

    rows = []
    for label, fn in FILES:
        p = os.path.join(D, fn)
        if not os.path.exists(p):
            print("MISSING", fn)
            continue
        d = json.load(open(p, encoding="utf-8"))
        e = d["env"]
        mm = d["matmul"]
        cv = d["conv"]
        at = d["attention"]["sdpa_fp16"]
        cb = d["cudnn_benchmark"]
        rows.append(dict(
            label=label, torch=e["torch"], cuda=e["cuda"], cudnn=e["cudnn"],
            m32=mm["fp32 (tf32 off)"]["tflops"], m32on=mm["fp32 (tf32 on)"]["tflops"],
            m16=mm["fp16"]["tflops"], mbf=mm["bf16"]["tflops"],
            c56=cv.get("mid 56 fp32 tf32off", {}).get("ms"),
            c56on=cv.get("mid 56 fp32 tf32on", {}).get("ms"),
            c56fp16=cv.get("mid 56 fp16", {}).get("ms"),
            c56cl=cv.get("mid 56 fp16 CL", {}).get("ms"),
            vram=cv.get("mid 56 fp16 CL", {}).get("peak_mb"),
            sdpa=at["ms"], sdpaf=at["tflops"], cb=cb["speedup"]))

    hdr = (f"{'':22}{'torch':26}{'cuda':6}{'cudnn':8}{'fp32':>7}{'tf32on':>8}"
           f"{'fp16':>8}{'bf16':>8}{'c56f32':>8}{'c56fp16':>9}{'c56CL':>7}{'SDPA':>7}{'TFLOPS':>8}")
    print(hdr)
    print("-" * len(hdr))
    for r in rows:
        print(f"{r['label']:22}{r['torch']:26}{r['cuda']:6}{str(r['cudnn']):8}"
              f"{r['m32']:>7}{r['m32on']:>8}{r['m16']:>8}{r['mbf']:>8}"
              f"{r['c56']:>8}{r['c56fp16']:>9}{r['c56cl']:>7}{r['sdpa']:>7}{r['sdpaf']:>8}")
    print()
    print("相對基準(base)的變化 %  [A=驅動效果 / B=cu132 / C=cu134]")
    if not rows:
        return 1
    b = rows[0]
    for r in rows[1:]:
        def pct(k):
            return f"{(r[k] / b[k] - 1) * 100:+6.1f}%" if b.get(k) else "   n/a"
        print(f"  {r['label']:22} fp16matmul {pct('m16')} | tf32on {pct('m32on')} | "
              f"c56fp16 {pct('c56fp16')} | c56CL {pct('c56cl')} | SDPA {pct('sdpa')}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
