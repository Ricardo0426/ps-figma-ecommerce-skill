"""Validate exported images against a PS-Figma delivery index; optionally render a contact sheet."""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont, ImageOps


def run(args):
    manifest = args.manifest.resolve()
    items = json.loads(manifest.read_text(encoding="utf-8-sig"))
    if not isinstance(items, list) or not items:
        raise ValueError("Manifest must be a non-empty JSON array")
    root = args.root.resolve() if args.root else manifest.parent
    seen_ids, seen_sequence = set(), set()
    files, previews, errors = [], [], []
    for item in items:
        if not isinstance(item, dict):
            raise ValueError("Every manifest item must be an object")
        output_id = item.get("output_id")
        if not isinstance(output_id, str) or not output_id.strip() or output_id in seen_ids:
            raise ValueError("Every output_id must be a unique non-empty string")
        seen_ids.add(output_id)
        sequence = item.get("sequence")
        if sequence is not None:
            if not isinstance(sequence, int) or isinstance(sequence, bool) or sequence < 1 or sequence in seen_sequence:
                raise ValueError("Provided sequence values must be unique positive integers")
            seen_sequence.add(sequence)
        size = [item.get("width"), item.get("height")]
        if not all(isinstance(n, int) and not isinstance(n, bool) and n > 0 for n in size):
            raise ValueError(f"{output_id}: width/height must be positive integers")
        paths = item.get("files")
        if not isinstance(paths, dict) or not paths:
            raise ValueError(f"{output_id}: files must contain at least one named image path")
        preview = None
        for kind, value in paths.items():
            if not isinstance(value, str) or not value:
                raise ValueError(f"{output_id}: image path must be a non-empty string")
            path = Path(value)
            if not path.is_absolute():
                path = root / path
            path = path.resolve()
            record = {"output_id": output_id, "kind": kind, "path": str(path), "expected_size": size}
            try:
                with Image.open(path) as im:
                    im.verify()
                with Image.open(path) as im:
                    im.load()
                    record.update({"size": list(im.size), "format": im.format, "mode": im.mode})
                    if list(im.size) != size:
                        raise ValueError(f"Dimensions {list(im.size)} differ from {size}")
                    expected_format = {".jpg": "JPEG", ".jpeg": "JPEG", ".png": "PNG"}.get(path.suffix.lower())
                    if expected_format is None:
                        raise ValueError("Delivery images must use .png, .jpg or .jpeg")
                    if expected_format and im.format != expected_format:
                        raise ValueError(f"Extension {path.suffix} differs from actual format {im.format}")
                    rgba = im.convert("RGBA")
                    record["alpha_bbox"] = list(rgba.getchannel("A").getbbox() or ())
                    if preview is None:
                        base = Image.new("RGB", im.size, "#101014")
                        base.paste(rgba, mask=rgba.getchannel("A"))
                        preview = base
                record["sha256"] = hashlib.sha256(path.read_bytes()).hexdigest()
                record["status"] = "pass"
            except Exception as exc:
                record.update({"status": "fail", "error": str(exc)})
                errors.append({"output_id": output_id, "path": str(path), "error": str(exc)})
            files.append(record)
        if preview is not None:
            previews.append((sequence or len(previews) + 1, output_id, preview))
    report = {
        "status": "pass" if not errors else "fail",
        "outputs": len(items),
        "files_checked": len(files),
        "errors": errors,
        "files": files,
        "limits": "Checks file readability, dimensions and format, not product accuracy, copy or visual alignment.",
    }
    hashes = {}
    for record in files:
        if record.get("sha256"):
            hashes.setdefault(record["sha256"], []).append(record["path"])
    report["identical_file_groups"] = [paths for paths in hashes.values() if len(paths) > 1]
    if args.contact_sheet and not errors:
        target = args.contact_sheet.resolve()
        if target in [Path(x["path"]) for x in files] or target == manifest:
            raise ValueError("Contact sheet must not overwrite an input")
        if target.exists():
            raise ValueError("Contact sheet already exists; use a new output path")
        previews.sort(key=lambda x: x[0])
        columns = min(3, len(previews))
        cell_w, cell_h, pad, title_h = 420, 420, 16, 32
        canvas = Image.new("RGB", (columns * cell_w, ((len(previews) + columns - 1) // columns) * (cell_h + title_h)), "#24242A")
        drawer = ImageDraw.Draw(canvas)
        font = ImageFont.truetype(str(args.font), 18) if args.font else ImageFont.load_default(size=18)
        for index, (sequence, label, im) in enumerate(previews):
            x, y = index % columns * cell_w, index // columns * (cell_h + title_h)
            thumb = ImageOps.contain(im, (cell_w - pad * 2, cell_h - pad * 2))
            canvas.paste(thumb, (x + (cell_w - thumb.width) // 2, y + title_h + (cell_h - thumb.height) // 2))
            # output_id is used instead of assuming the host has the source language's fonts.
            drawer.text((x + pad, y + 6), f"{sequence:02d} {label}", fill="white", font=font)
        target.parent.mkdir(parents=True, exist_ok=True)
        canvas.save(target)
        report["contact_sheet"] = str(target)
    if args.report:
        target = args.report.resolve()
        if target == manifest or target in [Path(x["path"]) for x in files]:
            raise ValueError("Report must not overwrite an input")
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return report


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--manifest", type=Path, required=True)
    p.add_argument("--root", type=Path)
    p.add_argument("--report", type=Path)
    p.add_argument("--contact-sheet", type=Path)
    p.add_argument("--font", type=Path)
    args = p.parse_args()
    try:
        report = run(args)
        print(json.dumps({k: v for k, v in report.items() if k != "files"}, ensure_ascii=False, indent=2))
        return 0 if report["status"] == "pass" else 1
    except Exception as exc:
        print(json.dumps({"status": "fail", "error": str(exc)}, ensure_ascii=False), file=sys.stderr)
        return 1


if __name__ == "__main__":
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stderr.reconfigure(encoding="utf-8")
    raise SystemExit(main())
