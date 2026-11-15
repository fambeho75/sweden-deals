#!/usr/bin/env python3
"""Copy Deal Hunter's local feed into this site directory.

Deal Hunter writes plain JSON and images to a folder on the box
(default: /workspace/sweden-deals-feed). This script validates that
feed, copies referenced images, removes leftovers, and writes deals.json.

Usage:
  python3 scripts/sync_from_feed.py
  python3 scripts/sync_from_feed.py /path/to/feed
"""
from __future__ import annotations

import argparse
import json
import shutil
import sys
from pathlib import Path

DEFAULT_FEED = Path("/workspace/sweden-deals-feed")


def die(message: str, code: int = 1) -> None:
    print(f"error: {message}", file=sys.stderr)
    raise SystemExit(code)


def resolve_under(root: Path, rel: str) -> Path | None:
    if not rel or not isinstance(rel, str):
        return None
    if rel.startswith("/") or rel.startswith("\\"):
        return None
    parts = Path(rel).parts
    if ".." in parts or not parts:
        return None
    if parts[0] != "images":
        return None
    root = root.resolve()
    candidate = (root / rel).resolve()
    try:
        candidate.relative_to(root)
    except ValueError:
        return None
    return candidate


def load_feed(src_json: Path) -> dict:
    if not src_json.is_file():
        die(f"feed file not found: {src_json}")
    try:
        raw = src_json.read_text(encoding="utf-8")
        data = json.loads(raw)
    except json.JSONDecodeError as exc:
        die(f"could not parse {src_json}: {exc}")
    except OSError as exc:
        die(f"could not read {src_json}: {exc}")
    if not isinstance(data, dict):
        die(f"{src_json} is not a JSON object")
    deals = data.get("deals")
    if not isinstance(deals, list) or len(deals) == 0:
        die("invalid or empty deals list; leaving existing deals.json untouched")
    return data


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "feed_dir",
        nargs="?",
        default=str(DEFAULT_FEED),
        help="folder with deals.json and images/ (default: %(default)s)",
    )
    args = parser.parse_args()

    feed_dir = Path(args.feed_dir).expanduser().resolve()
    dest_root = Path(__file__).resolve().parent.parent
    dest_json = dest_root / "deals.json"
    dest_images = dest_root / "images"

    data = load_feed(feed_dir / "deals.json")
    deals = data["deals"]
    if data.get("count") is None:
        data["count"] = len(deals)

    dest_images.mkdir(parents=True, exist_ok=True)

    referenced: set[Path] = set()
    copied = kept = missing = 0

    for deal in deals:
        if not isinstance(deal, dict):
            continue
        rel = deal.get("image") or deal.get("image_url")
        src = resolve_under(feed_dir, rel) if isinstance(rel, str) else None
        if src is None:
            if rel:
                deal["image"] = None
                missing += 1
            continue
        if not src.is_file():
            deal["image"] = None
            missing += 1
            continue
        dest = dest_root / Path(rel)
        try:
            dest.resolve().relative_to(dest_root.resolve())
        except ValueError:
            deal["image"] = None
            missing += 1
            continue
        dest.parent.mkdir(parents=True, exist_ok=True)
        referenced.add(dest.resolve())
        same = (
            dest.is_file()
            and dest.stat().st_size == src.stat().st_size
            and int(dest.stat().st_mtime) == int(src.stat().st_mtime)
        )
        if same:
            kept += 1
        else:
            shutil.copy2(src, dest)
            copied += 1

    removed = 0
    if dest_images.exists():
        for path in sorted(dest_images.rglob("*"), reverse=True):
            if path.is_file() and path.resolve() not in referenced:
                path.unlink()
                removed += 1
            elif path.is_dir() and not any(path.iterdir()):
                path.rmdir()

    tmp = dest_json.with_name("deals.json.tmp")
    tmp.write_text(
        json.dumps(data, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    tmp.replace(dest_json)

    generated = data.get("generated_at") or "?"
    count = data.get("count", len(deals))
    print(
        f"{generated}  {count} deals  "
        f"images: {copied} copied, {kept} kept, {removed} removed"
        + (f", {missing} missing" if missing else "")
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
