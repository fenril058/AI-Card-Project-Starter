from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Sequence

from project_env import ProjectEnvError, load_project_env

from .errors import LicenseManagerError
from .service import LicenseService


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m licenses",
        description=(
            "Fetch, normalize, verify, and report "
            "AI model license metadata."
        ),
    )
    parser.add_argument(
        "--root",
        default=".",
        help="Repository root; defaults to the current directory",
    )

    subparsers = parser.add_subparsers(
        dest="command",
        required=True,
    )

    sync_parser = subparsers.add_parser(
        "sync",
        help="Fetch provider data and generate records",
    )
    sync_parser.add_argument(
        "--asset",
        action="append",
        default=[],
        help="Asset id; repeatable",
    )
    sync_parser.add_argument(
        "--timeout",
        type=float,
        default=30.0,
    )
    sync_parser.add_argument(
        "--skip-hash",
        action="store_true",
    )
    sync_parser.add_argument(
        "--allow-hash-mismatch",
        action="store_true",
    )
    sync_parser.add_argument(
        "--no-report",
        action="store_true",
    )
    sync_parser.add_argument(
        "--fail-fast",
        action="store_true",
    )

    verify_parser = subparsers.add_parser(
        "verify",
        help="Verify generated metadata and local files",
    )
    verify_parser.add_argument(
        "--asset",
        action="append",
        default=[],
        help="Asset id; repeatable",
    )
    verify_parser.add_argument(
        "--require-approved",
        action="store_true",
    )
    verify_parser.add_argument(
        "--require-local-files",
        action="store_true",
    )
    verify_parser.add_argument(
        "--require-evidence",
        action="store_true",
    )
    verify_parser.add_argument(
        "--skip-hash",
        action="store_true",
    )

    subparsers.add_parser(
        "report",
        help="Regenerate licenses/README.md",
    )

    return parser


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    root = Path(args.root).resolve()

    try:
        load_project_env(root / ".env")
        service = LicenseService(root)
        if args.command == "sync":
            service.sync(
                asset_ids=args.asset,
                timeout=args.timeout,
                skip_hash=args.skip_hash,
                allow_hash_mismatch=args.allow_hash_mismatch,
                no_report=args.no_report,
                fail_fast=args.fail_fast,
            )
        elif args.command == "verify":
            service.verify(
                asset_ids=args.asset,
                require_approved=args.require_approved,
                require_local_files=args.require_local_files,
                require_evidence=args.require_evidence,
                skip_hash=args.skip_hash,
            )
        elif args.command == "report":
            service.report()
        else:
            parser.error(f"Unknown command: {args.command}")

    except (LicenseManagerError, ProjectEnvError) as exc:
        print(f"FATAL: {exc}", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        print("Interrupted", file=sys.stderr)
        return 130

    return 0
