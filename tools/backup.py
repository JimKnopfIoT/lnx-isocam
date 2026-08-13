#!/usr/bin/env python3
"""Mirror the SonoCam device storage into a local backup folder.

Usage:
  python3 tools/backup.py --host 192.168.1.100 --manifest        # list + sizes only
  python3 tools/backup.py --host 192.168.1.100 --pull            # download everything
  python3 tools/backup.py --pull --skip Android                  # skip a subtree
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from isocam.webclient import CameraHTTP  # noqa: E402

DEST = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "orig-backup")


def human(n: int) -> str:
    for unit in ("B", "KB", "MB", "GB"):
        if n < 1024 or unit == "GB":
            return f"{n:.1f}{unit}" if unit != "B" else f"{n}B"
        n /= 1024
    return f"{n}"


def manifest(cam: CameraHTTP, skip: list[str]) -> list[dict]:
    nodes = []
    for node in cam.walk():
        top = node.relpath.split("/", 1)[0]
        if top in skip:
            continue
        nodes.append({"path": node.relpath, "is_dir": node.is_dir, "size": node.size})
        kind = "d" if node.is_dir else f"{human(node.size):>9}"
        print(f"  {kind}  {node.relpath}")
    return nodes


def summarize(nodes: list[dict]) -> None:
    by_top: dict[str, list[int]] = {}
    for n in nodes:
        if n["is_dir"]:
            continue
        top = n["path"].split("/", 1)[0] if "/" in n["path"] else "(root)"
        by_top.setdefault(top, []).append(n["size"])
    print("\n=== summary ===")
    grand = 0
    for top, sizes in sorted(by_top.items()):
        tot = sum(sizes)
        grand += tot
        print(f"  {top:14} {len(sizes):>4} files  {human(tot):>10}")
    print(f"  {'TOTAL':14} {sum(1 for n in nodes if not n['is_dir']):>4} files  {human(grand):>10}")


def pull(cam: CameraHTTP, nodes: list[dict], cached: dict[str, str]) -> None:
    files = [n for n in nodes if not n["is_dir"]]
    files.sort(key=lambda n: n["size"])  # smallest/most-important first
    done = 0
    for n in files:
        local = os.path.join(DEST, n["path"])
        if n["path"] in cached and os.path.exists(cached[n["path"]]):
            os.makedirs(os.path.dirname(local) or ".", exist_ok=True)
            if not os.path.exists(local):
                os.replace(cached[n["path"]], local) if False else _copy(cached[n["path"]], local)
            done += 1
            print(f"  [{done}/{len(files)}] (cached) {n['path']}")
            continue
        t0 = time.time()
        try:
            got = cam.download(n["path"], local)
            dt = time.time() - t0
            done += 1
            print(f"  [{done}/{len(files)}] {n['path']}  {human(got)} in {dt:.1f}s")
        except OSError as exc:
            print(f"  [!!] {n['path']}: {exc}")
    print(f"\nBacked up {done}/{len(files)} files into {DEST}")


def _copy(src: str, dst: str) -> None:
    import shutil
    shutil.copy2(src, dst)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--host", default="192.168.1.100")
    ap.add_argument("--port", type=int, default=9999)
    ap.add_argument("--manifest", action="store_true", help="list + sizes only")
    ap.add_argument("--pull", action="store_true", help="download files")
    ap.add_argument("--skip", action="append", default=[], help="top-level dir to skip")
    ap.add_argument("--cache", action="append", default=[],
                    help="path=localfile to reuse an already-downloaded file")
    args = ap.parse_args()

    cam = CameraHTTP(args.host, args.port)
    print(f"health: {cam.health()!r}")
    print(f"walking storage on {args.host}:{args.port} (skip={args.skip}) …")
    nodes = manifest(cam, args.skip)
    summarize(nodes)
    os.makedirs(DEST, exist_ok=True)
    with open(os.path.join(DEST, "manifest.json"), "w") as fh:
        json.dump(nodes, fh, indent=2)
    print(f"manifest.json written to {DEST}")

    cached = dict(c.split("=", 1) for c in args.cache)
    if args.pull:
        pull(cam, nodes, cached)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
