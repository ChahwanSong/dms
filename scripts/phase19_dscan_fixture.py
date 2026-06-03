#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
from pathlib import Path
import stat
import sys
import time


def main() -> int:
    parser = argparse.ArgumentParser(prog="dscan")
    parser.add_argument("--directory", "-d", required=True)
    parser.add_argument("--output", "-o", required=True)
    parser.add_argument("--print", action="store_true", dest="print_summary")
    parser.add_argument("--top-k", "-k", type=int, default=10)
    parser.add_argument("legacy_directory", nargs="?")
    args = parser.parse_args()

    root = Path(args.directory)
    output = Path(args.output)
    report = scan(root, top_k=args.top_k)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report, indent=2, sort_keys=True), encoding="utf-8")
    if args.print_summary:
        summary = report["summary"]
        print(f"directory: {report['directory']}")
        print(f"total_entries: {summary['total_entries']}")
        print(f"total_files: {summary['total_files']}")
        print(f"total_directories: {summary['total_directories']}")
    return 0


def scan(root: Path, *, top_k: int) -> dict:
    total_files = 0
    total_directories = 0
    total_symlinks = 0
    total_other = 0
    total_bytes = 0
    broken_paths: list[dict] = []

    def visit(path: Path) -> None:
        nonlocal total_files, total_directories, total_symlinks, total_other, total_bytes
        try:
            entry_stat = path.lstat()
        except OSError as exc:
            broken_paths.append({"path": str(path), "reason": ["missing"], "message": str(exc)})
            return
        mode = entry_stat.st_mode
        if stat.S_ISDIR(mode):
            total_directories += 1
            try:
                entries = list(path.iterdir())
            except OSError as exc:
                broken_paths.append(
                    {"path": str(path), "reason": ["unreadable"], "message": str(exc)}
                )
                return
            for child in entries:
                visit(child)
        elif stat.S_ISREG(mode):
            total_files += 1
            total_bytes += entry_stat.st_size
        elif stat.S_ISLNK(mode):
            total_symlinks += 1
        else:
            total_other += 1

    visit(root)
    total_entries = total_files + total_directories + total_symlinks + total_other
    return {
        "directory": str(root),
        "generated_at_epoch": int(time.time()),
        "top_k": top_k,
        "thresholds": {},
        "summary": {
            "total_entries": total_entries,
            "total_files": total_files,
            "total_directories": total_directories,
            "total_symlinks": total_symlinks,
            "total_other": total_other,
            "total_bytes": total_bytes,
        },
        "file_size_histogram": [],
        "time_histograms": {"atime": [], "mtime": [], "ctime": []},
        "oldest": {"atime": [], "mtime": [], "ctime": []},
        "broken_paths": broken_paths,
    }


if __name__ == "__main__":
    raise SystemExit(main())
