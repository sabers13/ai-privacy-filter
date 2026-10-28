#!/usr/bin/env python3
"""Copy the page bundle into the static site checkout that renders it.

This is the same copy the page task performs: JSON data goes under
``ai-privacy-filter/data``, images under ``ai-privacy-filter/assets``. It never
commits into the site repository and never rewrites the site's own
``data/results.json``, which carries the site's leak block.

Usage:
    uv run python scripts/sync_page_bundle.py --site-root /path/to/site
"""

from __future__ import annotations

import argparse
import json
import shutil
import sys
from pathlib import Path
from typing import Final

ROOT: Final = Path(__file__).resolve().parents[1]
DATA_FILES: Final = (
    "kpis.json",
    "demos.json",
    "tryit-samples.json",
    "worked-example.json",
    "demo-enquiries.json",
)


def _parse_args(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--site-root", required=True, help="checkout of the static site")
    parser.add_argument("--bundle", default=None, help="bundle directory (default: results/page)")
    return parser.parse_args(argv)


def _strip_mock(source: Path, target: Path) -> None:
    examples = json.loads(source.read_text(encoding="utf-8"))
    for example in examples:
        if isinstance(example, dict):
            example.pop("mock", None)
    target.write_text(
        json.dumps(examples, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def sync(site_root: Path, bundle: Path) -> list[str]:
    page = site_root / "ai-privacy-filter"
    if not (page / "index.html").is_file():
        raise FileNotFoundError(f"{page} is not the AI Privacy Filter page")
    data = page / "data"
    assets = page / "assets"
    data.mkdir(parents=True, exist_ok=True)
    assets.mkdir(parents=True, exist_ok=True)
    copied: list[str] = []
    for name in DATA_FILES:
        shutil.copyfile(bundle / name, data / name)
        copied.append(f"data/{name}")
    _strip_mock(bundle / "examples.json", data / "examples.json")
    copied.append("data/examples.json (mock flags removed)")
    charts = data / "charts"
    charts.mkdir(exist_ok=True)
    for path in sorted((bundle / "charts").glob("*")):
        shutil.copyfile(path, charts / path.name)
        copied.append(f"data/charts/{path.name}")
    manifest = data / "page-bundle-MANIFEST.md"
    shutil.copyfile(bundle / "MANIFEST.md", manifest)
    copied.append("data/page-bundle-MANIFEST.md")
    shutil.copyfile(bundle / "n8n-workflow.png", assets / "n8n-workflow.png")
    copied.append("assets/n8n-workflow.png")
    screenshots = assets / "screenshots"
    screenshots.mkdir(exist_ok=True)
    for path in sorted((bundle / "screenshots").glob("*.png")):
        shutil.copyfile(path, screenshots / path.name)
        copied.append(f"assets/screenshots/{path.name}")
    return copied


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    bundle = Path(args.bundle).resolve() if args.bundle else ROOT / "results" / "page"
    site_root = Path(args.site_root).resolve()
    try:
        copied = sync(site_root, bundle)
    except FileNotFoundError as exc:
        print(f"sync_page_bundle: {exc}", file=sys.stderr)
        return 1
    print(f"sync_page_bundle: copied {len(copied)} files into {site_root / 'ai-privacy-filter'}")
    print("sync_page_bundle: data/results.json left untouched (the site owns its leak block)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
