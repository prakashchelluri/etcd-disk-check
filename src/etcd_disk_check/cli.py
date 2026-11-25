"""Command line interface."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from . import __version__
from .bench import BenchError, bench, run_fio
from .metrics import MetricsError, analyze, load
from .report import EXIT_ERROR, render_json, render_text

EPILOG = "exit codes: 0 PASS, 1 WARN, 2 FAIL, 3 could not measure (missing fio, bad input, usage)"


class _Parser(argparse.ArgumentParser):
    # argparse exits 2 on usage errors, which would read as FAIL to cron/CI.
    def error(self, message: str):
        self.print_usage(sys.stderr)
        self.exit(EXIT_ERROR, f"{self.prog}: error: {message}\n")


def build_parser() -> argparse.ArgumentParser:
    p = _Parser(
        prog="etcd-disk-check",
        description="Tell whether a disk is fast enough for etcd, and explain why not.",
        epilog=EPILOG,
    )
    p.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    sub = p.add_subparsers(dest="mode", required=True)

    b = sub.add_parser("bench", help="run fio with etcd's WAL-like workload", epilog=EPILOG)
    src = b.add_mutually_exclusive_group(required=True)
    src.add_argument("--dir", help="directory on the disk to test, e.g. /var/lib/etcd")
    src.add_argument("--fio-json", metavar="FILE", help="analyse saved fio JSON output instead")

    m = sub.add_parser(
        "metrics", help="compute p99 disk latency from etcd's Prometheus metrics", epilog=EPILOG
    )
    m.add_argument("source", help="metrics file, http(s) URL, or - for stdin")
    m.add_argument("--timeout", type=float, default=10.0, help="HTTP timeout in seconds")

    for sp in (b, m):
        sp.add_argument("--json", action="store_true", help="print a JSON report")
    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        if args.mode == "bench":
            if args.fio_json:
                data = json.loads(Path(args.fio_json).read_text(encoding="utf-8"))
                result = bench(data, args.fio_json)
            else:
                result = bench(run_fio(args.dir), args.dir)
        else:
            result = analyze(load(args.source, args.timeout), args.source)
    except (BenchError, MetricsError, OSError, ValueError) as e:
        if args.json:
            print(json.dumps({"mode": args.mode, "status": "ERROR", "error": str(e)}, indent=2))
        else:
            print(f"etcd-disk-check: error: {e}", file=sys.stderr)
        return EXIT_ERROR

    print(render_json(result) if args.json else render_text(result))
    return result.exit_code


if __name__ == "__main__":
    sys.exit(main())
