#!/usr/bin/env python3
"""Print the models named in config/models.yaml as ``provider<TAB>tag`` lines.

Kept as a standalone script rather than inlined in pull_models.sh because
nesting a heredoc inside a process substitution is a portability minefield.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Any

import yaml


def extract(manifest: dict[str, Any], include_optional: bool) -> list[tuple[str, str]]:
    """Return unique (provider, physical) pairs, preserving manifest order."""
    seen: set[tuple[str, str]] = set()
    out: list[tuple[str, str]] = []
    for entry in manifest.get("models") or []:
        if not include_optional and entry.get("optional"):
            continue
        provider = str(entry.get("provider", ""))
        physical = str(entry.get("physical", ""))
        if not provider or not physical:
            continue
        # The mock provider has no weights to download.
        if provider == "mock":
            continue
        pair = (provider, physical)
        if pair not in seen:
            seen.add(pair)
            out.append(pair)
    return out


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("manifest", type=Path)
    parser.add_argument("--all", action="store_true", help="include optional models")
    parser.add_argument("--provider", help="only list models served by this provider", default=None)
    args = parser.parse_args()

    if not args.manifest.is_file():
        print(f"manifest not found: {args.manifest}", file=sys.stderr)
        return 1

    try:
        manifest = yaml.safe_load(args.manifest.read_text(encoding="utf-8")) or {}
    except yaml.YAMLError as exc:
        print(f"{args.manifest} is not valid YAML: {exc}", file=sys.stderr)
        return 1

    pairs = extract(manifest, args.all)
    if args.provider:
        pairs = [p for p in pairs if p[0] == args.provider]
    if not pairs:
        print(f"no downloadable models found in {args.manifest}", file=sys.stderr)
        return 1

    for provider, physical in pairs:
        print(f"{provider}\t{physical}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
