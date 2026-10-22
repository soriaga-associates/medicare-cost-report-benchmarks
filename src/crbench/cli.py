"""Command line: download, build, info."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from crbench import __version__, build, download


def cmd_download(args) -> int:
    try:
        download.download_all(Path(args.data_dir), force=args.force)
    except RuntimeError as e:
        print(f"Download failed: {e}")
        return 1
    print("Done.")
    return 0


def cmd_build(args) -> int:
    results = build.build(args.provider, Path(args.data_dir), Path(args.output_dir))
    return 0 if results else 1


def cmd_info(args) -> int:
    data_dir, out_dir = Path(args.data_dir), Path(args.output_dir)
    print(f"medicare-cost-report-benchmarks {__version__}")
    print(f"\nCached source files ({data_dir}):")
    manifest = download.load_manifest(data_dir)
    for key in [download.HOSPICE_ZIP, download.HOSPICE_DOC, *[download.hha_key(y) for y in download.HHA_YEARS]]:
        p = data_dir / key
        e = manifest.get(key, {})
        state = f"{p.stat().st_size:,} bytes, retrieved {e.get('retrieved', 'unknown')}" if p.exists() else "missing"
        print(f"  {key}: {state}")
    print(f"\nOutputs ({out_dir}):")
    for key, spec in build.PROVIDERS.items():
        p = out_dir / spec["dir"] / "benchmarks.json"
        if not p.exists():
            print(f"  {key}: not built yet")
            continue
        meta = json.loads(p.read_text(encoding="utf-8"))["meta"]
        print(f"  {key}: years {meta['years'][0]}-{meta['years'][-1]}, latest complete year {meta['latest_complete_year']}, built {meta['generated_at']}")
    return 0


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(
        prog="crbench", description="Benchmarks from CMS Medicare cost reports (hospice and home health)."
    )
    parser.add_argument("--data-dir", default="data/raw", help="cache for downloaded CMS files (default: data/raw)")
    parser.add_argument("--output-dir", default="output", help="where outputs are written (default: output)")
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("download", help="download the CMS source files into the cache")
    p.add_argument("--force", action="store_true", help="download again even if cached")
    p.set_defaults(func=cmd_download)

    p = sub.add_parser("build", help="build the benchmark files")
    p.add_argument("--provider", choices=["hospice", "home-health", "all"], default="all")
    p.set_defaults(func=cmd_build)

    p = sub.add_parser("info", help="show cached files and built outputs")
    p.set_defaults(func=cmd_info)

    args = parser.parse_args(argv)
    return args.func(args)
