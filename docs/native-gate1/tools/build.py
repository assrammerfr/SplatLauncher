#!/usr/bin/env python3
"""Parallel MSVC build for the SplatNative first runnable unit.

Captures the vcvars64 environment once, compiles every runtime + generated
translation unit with a bounded thread pool, then links splatnative.exe.
Usage:  python tools/build.py [--jobs N]
"""
import argparse
import glob
import os
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
VCVARS = r"C:\Program Files\Microsoft Visual Studio\2022\BuildTools\VC\Auxiliary\Build\vcvars64.bat"
OBJ = os.path.join(ROOT, "build", "obj")
EXE = os.path.join(ROOT, "build", "splatnative.exe")


def vc_env():
    out = subprocess.run(
        ["cmd", "/c", f'"{VCVARS}" >nul && set'],
        capture_output=True, text=True, shell=False)
    env = {}
    for line in out.stdout.splitlines():
        if "=" in line:
            k, _, v = line.partition("=")
            env[k] = v
    if "INCLUDE" not in env:
        print(out.stdout[:2000], out.stderr[:2000])
        raise SystemExit("vcvars64 failed")
    return env


def compile_one(src, env):
    obj = os.path.join(OBJ, os.path.splitext(os.path.basename(src))[0] + ".obj")
    cmd = ["cl", "/nologo", "/c", "/O2", "/bigobj", "/MD",
           "/I" + os.path.join(ROOT, "src", "runtime"),
           src, "/Fo" + obj]
    r = subprocess.run(cmd, capture_output=True, text=True, env=env, cwd=ROOT)
    ok = r.returncode == 0 and os.path.exists(obj)
    return src, ok, r.stdout + r.stderr


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--jobs", type=int, default=4)
    args = ap.parse_args()
    os.makedirs(OBJ, exist_ok=True)
    env = vc_env()

    sources = [
        os.path.join(ROOT, "src", "runtime", "main.c"),
        os.path.join(ROOT, "src", "runtime", "a64_helpers.c"),
        os.path.join(ROOT, "src", "runtime", "nso_loader.c"),
        os.path.join(ROOT, "src", "runtime", "vendor", "lz4.c"),
    ] + sorted(glob.glob(os.path.join(ROOT, "build", "gen", "*.c")))
    print(f"[build] {len(sources)} translation units, jobs={args.jobs}")

    failures = []
    done = 0
    with ThreadPoolExecutor(max_workers=args.jobs) as pool:
        futs = {pool.submit(compile_one, s, env): s for s in sources}
        for fut in as_completed(futs):
            src, ok, out = fut.result()
            done += 1
            if not ok:
                failures.append((src, out))
                print(f"[build] FAIL {os.path.basename(src)}")
            elif done % 10 == 0:
                print(f"[build] {done}/{len(sources)} compiled")
            else:
                print(f"[build] ok {os.path.basename(src)}")

    if failures:
        for src, out in failures[:6]:
            print(f"---- {os.path.basename(src)} ----")
            print("\n".join(out.splitlines()[:40]))
        print(f"[build] {len(failures)} TUs failed")
        sys.exit(1)

    objs = sorted(glob.glob(os.path.join(OBJ, "*.obj")))
    print(f"[build] linking {len(objs)} objects ...")
    cmd = ["cl", "/nologo", *[os.path.join(OBJ, os.path.basename(o)) for o in objs],
           "/Fe" + EXE, "/link", "/SUBSYSTEM:CONSOLE"]
    # cl link mode takes sources; pass obj files directly
    r = subprocess.run(cmd, capture_output=True, text=True, env=env, cwd=ROOT)
    if r.returncode != 0 or not os.path.exists(EXE):
        print(r.stdout[-4000:])
        print(r.stderr[-4000:])
        sys.exit(2)
    print(f"[build] OK -> {EXE} ({os.path.getsize(EXE)} B)")


if __name__ == "__main__":
    main()
