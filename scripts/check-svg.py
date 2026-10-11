"""Checks that SVG files are safe and README-ready (GitHub shows them in <img>, so no scripts and no external files).

usage: python -I scripts/check-svg.py [--max-kb 80] FILE_OR_DIR...
exit code 1 when any file has an error. Warnings never fail the run.
"""
import argparse
import re
import sys
import xml.etree.ElementTree as ET
from pathlib import Path

NS = "{http://www.w3.org/2000/svg}"
ANIM_TAGS = {"animate", "animateTransform", "animateMotion", "set"}


def check(path: Path, max_kb: int):
    errors, warns = [], []
    raw = path.read_bytes()
    if len(raw) > max_kb * 1024:
        errors.append(f"{len(raw) // 1024} KB is over the {max_kb} KB limit")
    text = raw.decode("utf-8", errors="replace")
    try:
        root = ET.fromstring(raw)
    except ET.ParseError as exc:
        return [f"not valid XML: {exc}"], warns
    if root.tag != f"{NS}svg":
        errors.append("root element is not <svg xmlns=http://www.w3.org/2000/svg>")
    if not root.get("viewBox"):
        errors.append("missing viewBox")
    if root.get("width") and root.get("height") is None:
        warns.append("width without height")
    tags = {el.tag.replace(NS, "") for el in root.iter()}
    for bad in ("script", "foreignObject", "iframe", "image"):
        if bad in tags:
            errors.append(f"<{bad}> is not allowed" + (" (embed shapes, not raster images)" if bad == "image" else ""))
    for el in root.iter():
        for attr, val in el.attrib.items():
            name = attr.split("}")[-1]
            if name.lower().startswith("on"):
                errors.append(f"event handler attribute {name}")
            if name == "href" and not val.startswith("#"):
                errors.append(f"external reference href={val[:60]!r}")
    if re.search(r"@import|url\(\s*['\"]?https?:", text, re.I):
        errors.append("external @import or url(http...)")
    if re.search(r"https?://", re.sub(r'xmlns(:\w+)?="[^"]*"', "", text)):
        warns.append("contains an http(s) URL in the text; fine if it is only visible text")
    if "title" not in tags:
        warns.append("no <title> (screen readers and tooltips)")
    animated = bool(tags & ANIM_TAGS) or "@keyframes" in text
    if animated and "prefers-reduced-motion" not in text:
        warns.append("animated but has no prefers-reduced-motion rule")
    if "font-family" in text and not re.search(r"font-family[^;>]*(sans-serif|monospace|serif)", text):
        warns.append("font-family without a generic fallback")
    return errors, warns


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("paths", nargs="+")
    ap.add_argument("--max-kb", type=int, default=80)
    args = ap.parse_args()
    files = []
    for p in map(Path, args.paths):
        files += sorted(p.rglob("*.svg")) if p.is_dir() else [p]
    bad = 0
    for f in files:
        errors, warns = check(f, args.max_kb)
        status = "FAIL" if errors else ("warn" if warns else "ok  ")
        print(f"{status} {f}")
        for e in errors:
            print(f"       error: {e}")
        for w in warns:
            print(f"       warn : {w}")
        bad += bool(errors)
    print(f"\n{len(files)} file(s), {bad} with errors")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
