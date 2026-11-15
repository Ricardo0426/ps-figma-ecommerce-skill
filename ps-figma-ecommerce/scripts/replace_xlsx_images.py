"""Replace existing embedded XLSX images without rewriting workbook XML.

Defaults to a read-only plan. Mapping JSON is a list of {"row": 7,
"image": "relative/to/mapping.jpg", "label": "optional description"}.
Every image anchored in the specified worksheet column must be mapped unless
--allow-partial is explicit. Supports ordinary one/two-cell picture anchors and
WPS DISPIMG/cellimages. Does not insert, resize or move drawings.
"""

from __future__ import annotations

import argparse
import copy
import datetime as dt
import hashlib
import io
import json
import os
from pathlib import Path
import posixpath
import re
import shutil
import struct
import sys
import tempfile
import zipfile
import xml.etree.ElementTree as ET


NS = {
    "m": "http://schemas.openxmlformats.org/spreadsheetml/2006/main",
    "r": "http://schemas.openxmlformats.org/officeDocument/2006/relationships",
    "xdr": "http://schemas.openxmlformats.org/drawingml/2006/spreadsheetDrawing",
    "a": "http://schemas.openxmlformats.org/drawingml/2006/main",
}
RID = "{" + NS["r"] + "}id"
EMBED = "{" + NS["r"] + "}embed"
DISPIMG = re.compile(r'\bDISPIMG\s*\(\s*"([^\"]+)"', re.I)


def digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def rels_part(owner: str) -> str:
    return posixpath.join(posixpath.dirname(owner), "_rels", posixpath.basename(owner) + ".rels")


def resolve_target(owner: str, target: str) -> str:
    name = target.lstrip("/") if target.startswith("/") else posixpath.normpath(posixpath.join(posixpath.dirname(owner), target))
    if name.startswith("../") or name == "..":
        raise ValueError("Relationship escapes the package: " + target)
    return name


def relationships(z: zipfile.ZipFile, owner: str) -> dict:
    part = rels_part(owner)
    if part not in z.namelist():
        return {}
    return {r.get("Id"): dict(r.attrib) for r in ET.fromstring(z.read(part))}


def sheet_part(z: zipfile.ZipFile, name: str) -> str:
    rels = relationships(z, "xl/workbook.xml")
    matches = [s for s in ET.fromstring(z.read("xl/workbook.xml")).findall("m:sheets/m:sheet", NS) if s.get("name") == name]
    if len(matches) != 1:
        raise ValueError(f"Expected one worksheet named {name!r}; found {len(matches)}")
    rel = rels.get(matches[0].get(RID), {})
    if rel.get("TargetMode") == "External" or not rel.get("Target"):
        raise ValueError("Worksheet relationship is external or missing")
    return resolve_target("xl/workbook.xml", rel["Target"])


def read_cells(z: zipfile.ZipFile, sheet: str) -> dict:
    strings = []
    if "xl/sharedStrings.xml" in z.namelist():
        strings = ["".join(t.text or "" for t in item.iter("{" + NS["m"] + "}t")) for item in ET.fromstring(z.read("xl/sharedStrings.xml"))]
    cells = {}
    for c in ET.fromstring(z.read(sheet)).findall("m:sheetData/m:row/m:c", NS):
        formula, value = c.find("m:f", NS), c.find("m:v", NS)
        if formula is not None:
            text = formula.text or ""
        elif c.get("t") == "s" and value is not None:
            text = strings[int(value.text)]
        elif c.get("t") == "inlineStr":
            text = "".join(t.text or "" for t in c.findall(".//m:t", NS))
        else:
            text = value.text if value is not None and value.text is not None else ""
        cells[c.get("r")] = text
    return cells


def column_index(value: str) -> int:
    if not re.fullmatch(r"[A-Za-z]{1,3}", value):
        raise ValueError("--image-column must be an Excel column such as J")
    result = 0
    for char in value.upper():
        result = result * 26 + ord(char) - 64
    if result > 16384:
        raise ValueError("Column exceeds Excel's XFD limit")
    return result - 1


def blip_index(root: ET.Element) -> dict:
    return {id(blip): i for i, blip in enumerate(root.findall(".//a:blip", NS))}


def image_ref(owner: str, blip: ET.Element, indexes: dict, rels: dict) -> dict:
    rid = blip.get(EMBED)
    rel = rels.get(rid, {})
    if rel.get("TargetMode") == "External" or not rel.get("Target"):
        raise ValueError(f"External or missing embedded-image relationship: {owner} {rid}")
    return {"media": resolve_target(owner, rel["Target"]), "part": owner, "rid": rid,
            "usage": f"{owner}::blip{indexes[id(blip)]}"}


def drawing_map(z: zipfile.ZipFile, sheet: str, column: int) -> dict:
    result = {}
    for rel in relationships(z, sheet).values():
        if not rel.get("Type", "").endswith("/drawing"):
            continue
        if rel.get("TargetMode") == "External":
            raise ValueError("External drawings are unsupported")
        owner = resolve_target(sheet, rel["Target"])
        root = ET.fromstring(z.read(owner))
        indexes, rels = blip_index(root), relationships(z, owner)
        for anchor in root:
            start = anchor.find("xdr:from", NS)
            if start is None:
                continue
            col, row = start.find("xdr:col", NS), start.find("xdr:row", NS)
            if col is None or row is None or int(col.text) != column:
                continue
            for picture in anchor.findall("xdr:pic", NS):
                for blip in picture.findall(".//a:blip", NS):
                    result.setdefault(int(row.text) + 1, []).append({**image_ref(owner, blip, indexes, rels), "kind": "drawing"})
    return result


def cellimage_map(z: zipfile.ZipFile) -> dict:
    result = {}
    for owner in z.namelist():
        if not owner.lower().endswith("cellimages.xml"):
            continue
        root = ET.fromstring(z.read(owner))
        indexes, rels = blip_index(root), relationships(z, owner)
        for item in root:
            props = item.find(".//xdr:cNvPr", NS)
            if props is None or not props.get("name"):
                continue
            for blip in item.findall(".//a:blip", NS):
                result.setdefault(props.get("name"), []).append({**image_ref(owner, blip, indexes, rels), "kind": "cellimage", "imageId": props.get("name")})
    return result


def all_uses(z: zipfile.ZipFile) -> tuple[dict, dict]:
    """Account for known blips and conservatively block every unknown consumer.

    Looking only at DrawingML is insufficient: VML header/footer pictures and
    custom parts can share the same media file. Inspect every OPC relationship
    part, even when its owner is not an .xml file or is not understood here.
    A relation to recognized media that is not exclusively consumed by known
    a:blip/@r:embed nodes adds a blocking usage token.
    """
    media, formulas = {}, {}
    roots, known_relids = {}, {}
    for part in z.namelist():
        if not part.endswith(".xml"):
            continue
        root = ET.fromstring(z.read(part))
        roots[part] = root
        rels = relationships(z, part)
        for i, blip in enumerate(root.findall(".//a:blip", NS)):
            rel = rels.get(blip.get(EMBED), {})
            if rel.get("Target") and rel.get("TargetMode") != "External":
                name = resolve_target(part, rel["Target"])
                media.setdefault(name, set()).add(f"{part}::blip{i}")
                known_relids.setdefault(part, set()).add(blip.get(EMBED))
        if part.startswith("xl/worksheets/"):
            for cell, text in read_cells(z, part).items():
                match = DISPIMG.search(text)
                if match:
                    formulas.setdefault(match[1], set()).add(f"{part}!{cell}")

    # Snapshot recognized media first; relationship types may be custom or
    # missing, so target equality matters in addition to the /image type.
    recognized_media = set(media)
    for relpart in z.namelist():
        if not relpart.endswith(".rels"):
            continue
        if relpart == "_rels/.rels":
            owner = ""
        elif "/_rels/" in relpart:
            prefix, filename = relpart.rsplit("/_rels/", 1)
            if "/" in filename:
                raise ValueError("Malformed relationship part: " + relpart)
            owner = posixpath.join(prefix, filename[:-5])
        else:
            raise ValueError("Unrecognized relationship-part location: " + relpart)
        for rel in ET.fromstring(z.read(relpart)):
            if rel.get("TargetMode") == "External" or not rel.get("Target"):
                continue
            target = resolve_target(owner, rel.get("Target"))
            if target not in recognized_media and not rel.get("Type", "").endswith("/image"):
                continue
            rid = rel.get("Id")
            root = roots.get(owner)
            known = rid in known_relids.get(owner, set())
            unknown = not known
            if root is not None and known:
                for node in root.iter():
                    for attr, value in node.attrib.items():
                        if value == rid and not (node.tag == "{" + NS["a"] + "}blip" and attr == EMBED):
                            unknown = True
            if unknown:
                media.setdefault(target, set()).add(f"{owner or '[package]'}::unknown-image-consumer::{rid}")
    return media, formulas


def image_info(data: bytes) -> tuple[str, tuple[int, int]]:
    """Read supported raster type and dimensions, and fully decode if Pillow exists."""
    kind = None
    dims = None
    if data.startswith(b"\x89PNG\r\n\x1a\n") and len(data) >= 24 and data[12:16] == b"IHDR":
        kind, dims = "png", struct.unpack(">II", data[16:24])
    elif data[:6] in (b"GIF87a", b"GIF89a") and len(data) >= 10:
        kind, dims = "gif", struct.unpack("<HH", data[6:10])
    elif data.startswith(b"\xff\xd8"):
        pos = 2
        sof = {0xC0, 0xC1, 0xC2, 0xC3, 0xC5, 0xC6, 0xC7, 0xC9, 0xCA, 0xCB, 0xCD, 0xCE, 0xCF}
        while pos + 4 < len(data):
            if data[pos] != 0xFF:
                raise ValueError("Malformed JPEG marker")
            while pos < len(data) and data[pos] == 0xFF:
                pos += 1
            marker = data[pos]
            pos += 1
            if marker in (0xD8, 0x01) or 0xD0 <= marker <= 0xD7:
                continue
            if marker in (0xD9, 0xDA):
                break
            length = struct.unpack(">H", data[pos:pos + 2])[0]
            if length < 2 or pos + length > len(data):
                raise ValueError("Malformed JPEG segment")
            if marker in sof:
                h, w = struct.unpack(">HH", data[pos + 3:pos + 7])
                kind, dims = "jpeg", (w, h)
                break
            pos += length
    if kind is None or dims is None or min(dims) <= 0:
        raise ValueError("Expected a valid PNG, JPEG or GIF image")
    try:
        from PIL import Image
    except ImportError:
        pass
    else:
        with Image.open(io.BytesIO(data)) as image:
            image.verify()
        # verify() checks file structure; reopen to actually decode pixels.
        with Image.open(io.BytesIO(data)) as image:
            image.load()
    return kind, dims


def make_plan(z: zipfile.ZipFile, sheet: str, column_name: str, mapping: Path,
              allow_partial: bool, allow_dimension_change: bool) -> list:
    column = column_index(column_name)
    column_name = column_name.upper()
    requested = json.loads(mapping.read_text(encoding="utf-8-sig"))
    if not isinstance(requested, list) or not requested:
        raise ValueError("Mapping must be a nonempty JSON list")
    drawings = drawing_map(z, sheet, column)
    cellimages = cellimage_map(z)
    cells = read_cells(z, sheet)
    media_uses, formula_uses = all_uses(z)
    located = dict(drawings)
    for cell, text in cells.items():
        match = re.fullmatch(re.escape(column_name) + r"([1-9]\d*)", cell)
        formula = DISPIMG.search(text)
        if match and formula:
            row = int(match[1])
            if row in located:
                raise ValueError(f"{cell} has both a drawing and DISPIMG image")
            located[row] = cellimages.get(formula[1], [])
    planned, seen = [], set()
    for item in requested:
        if not isinstance(item, dict) or isinstance(item.get("row"), bool) or not isinstance(item.get("row"), int) or not 1 <= item["row"] <= 1048576:
            raise ValueError("Each mapping needs an integer Excel row from 1 to 1048576")
        row = item["row"]
        if row in seen:
            raise ValueError(f"Duplicate mapping row {row}")
        seen.add(row)
        candidates = located.get(row, [])
        if len(candidates) != 1:
            raise ValueError(f"{column_name}{row}: expected exactly one embedded picture, found {len(candidates)}")
        target = candidates[0]
        if not isinstance(item.get("image"), str) or not item["image"]:
            raise ValueError(f"Row {row}: image path is missing")
        path = Path(item["image"])
        path = (mapping.parent / path).resolve() if not path.is_absolute() else path.resolve()
        image = path.read_bytes()
        oldkind, olddims = image_info(z.read(target["media"]))
        kind, dims = image_info(image)
        if kind != oldkind:
            raise ValueError(f"{column_name}{row}: {kind} replacement cannot replace {oldkind} media without changing content types")
        if not allow_dimension_change and dims != olddims:
            raise ValueError(f"{column_name}{row}: image dimensions {dims} differ from {olddims}; use --allow-dimension-change intentionally")
        planned.append({"row": row, "cell": f"{column_name}{row}", "image": str(path),
                        "label": item.get("label", ""), "format": kind, "dimensions": list(dims),
                        "originalDimensions": list(olddims), "sha256": digest(image), **target})
    if not allow_partial and seen != set(located):
        raise ValueError(f"Mapping is incomplete; missing image rows: {sorted(set(located) - seen)}. Use --allow-partial for an intentional local update.")
    for media in {p["media"] for p in planned}:
        selected = [p for p in planned if p["media"] == media]
        if len({p["sha256"] for p in selected}) != 1:
            raise ValueError(f"Shared media has conflicting replacement images: {media}")
        if {p["usage"] for p in selected} != media_uses.get(media, set()):
            raise ValueError(f"Media is also used outside the selected targets: {media}")
        for image_id in {p["imageId"] for p in selected if "imageId" in p}:
            selected_cells = {f"{sheet}!{p['cell']}" for p in selected if p.get("imageId") == image_id}
            if selected_cells != formula_uses.get(image_id, set()):
                raise ValueError(f"Cell image {image_id} is referenced outside the selected targets")
    return sorted(planned, key=lambda p: p["row"])


def verify(original: zipfile.ZipFile, output: Path, replacements: dict) -> dict:
    with zipfile.ZipFile(output) as saved:
        if saved.testzip() is not None or saved.namelist() != original.namelist() or saved.comment != original.comment:
            raise ValueError("Saved XLSX integrity, entry order or archive comment changed")
        for name in original.namelist():
            if saved.read(name) != replacements.get(name, original.read(name)):
                raise ValueError("Saved package verification failed: " + name)
    return {"replacedMediaParts": len(replacements), "unchangedOtherParts": len(original.namelist()) - len(replacements),
            "allXmlAndRelationshipsByteIdentical": True}


def atomic_copy(source: Path, dest: Path) -> None:
    handle, name = tempfile.mkstemp(prefix=dest.stem + ".", suffix=".updating.xlsx", dir=dest.parent)
    os.close(handle)
    pending = Path(name)
    try:
        shutil.copyfile(source, pending)
        if digest(pending.read_bytes()) != digest(source.read_bytes()):
            raise ValueError("Copy verification failed")
        os.replace(pending, dest)
    finally:
        if pending.exists():
            pending.unlink()


def run(args: argparse.Namespace) -> dict:
    if args.in_place and not args.apply:
        raise ValueError("--in-place requires --apply")
    source, output, mapping = args.source.resolve(), args.output.resolve(), args.mapping.resolve()
    if source == output:
        raise ValueError("--output must be separate from --source even with --in-place")
    if output == mapping:
        raise ValueError("--output must not overwrite the mapping JSON input")
    original_bytes = source.read_bytes()
    source_sha = digest(original_bytes)
    with zipfile.ZipFile(io.BytesIO(original_bytes)) as original:
        if len(set(original.namelist())) != len(original.namelist()):
            raise ValueError("Duplicate ZIP entries are unsupported")
        sheet = sheet_part(original, args.sheet)
        planned = make_plan(original, sheet, args.image_column, mapping, args.allow_partial, args.allow_dimension_change)
        if output in {Path(p["image"]) for p in planned}:
            raise ValueError("--output must not overwrite any replacement image input")
        result = {"ok": True, "mode": "apply" if args.apply else "dry-run", "source": str(source),
                  "sourceSha256": source_sha, "output": str(output), "mapping": planned,
                  "sourceUpdated": False, "outputWritten": False}
        if not args.apply:
            return result
        replacements = {p["media"]: Path(p["image"]).read_bytes() for p in planned}
        for p in planned:
            if digest(replacements[p["media"]]) != p["sha256"]:
                raise ValueError("A replacement changed during preparation: " + p["image"])
        output.parent.mkdir(parents=True, exist_ok=True)
        handle, name = tempfile.mkstemp(prefix=output.stem + ".", suffix=".pending.xlsx", dir=output.parent)
        os.close(handle)
        pending = Path(name)
        try:
            with zipfile.ZipFile(pending, "w") as saved:
                saved.comment = original.comment
                for info in original.infolist():
                    saved.writestr(copy.copy(info), replacements.get(info.filename, original.read(info.filename)))
            result["verification"] = verify(original, pending, replacements)
            os.replace(pending, output)
        finally:
            if pending.exists():
                pending.unlink()
    result.update({"outputWritten": True, "outputSha256": digest(output.read_bytes())})
    if not args.in_place:
        return result
    if digest(source.read_bytes()) != source_sha:
        result.update({"ok": False, "error": "Source workbook changed during preparation; verified output retained", "sourceUpdateStatus": "source_changed"})
        return result
    backup_dir = args.backup_dir.resolve() if args.backup_dir else output.parent / "backups"
    backup_dir.mkdir(parents=True, exist_ok=True)
    stamp = dt.datetime.now(dt.timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    backup = backup_dir / f"{source.stem}.before-{stamp}-{source_sha[:8]}{source.suffix}"
    with backup.open("xb") as f:
        f.write(original_bytes)
    if digest(backup.read_bytes()) != source_sha:
        raise ValueError("Source backup verification failed")
    result["backup"] = str(backup)
    try:
        if digest(source.read_bytes()) != source_sha:
            raise RuntimeError("Source workbook changed before final update")
        atomic_copy(output, source)
    except (OSError, RuntimeError) as exc:
        result.update({"ok": False, "error": str(exc), "sourceUpdateStatus": "not_updated", "sourceUpdated": False})
        return result
    if digest(source.read_bytes()) != result["outputSha256"]:
        raise ValueError("Final source/output hash mismatch")
    result.update({"sourceUpdated": True, "sourceUpdateStatus": "updated"})
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--sheet", required=True)
    parser.add_argument("--image-column", required=True)
    parser.add_argument("--mapping", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--apply", action="store_true", help="Write a verified new workbook; default is a read-only plan")
    mode.add_argument("--dry-run", action="store_true", help="Explicit read-only mode (the default)")
    parser.add_argument("--in-place", action="store_true", help="Also back up and update the source after output verification")
    parser.add_argument("--backup-dir", type=Path)
    parser.add_argument("--allow-partial", action="store_true", help="Allow an intentional subset of pictures in the column")
    parser.add_argument("--allow-dimension-change", action="store_true", help="Accept changed raster dimensions; drawing geometry stays unchanged")
    args = parser.parse_args()
    try:
        result = run(args)
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0 if result["ok"] else 2
    except Exception as exc:
        print(json.dumps({"ok": False, "error": str(exc), "sourceUpdated": False}, ensure_ascii=False), file=sys.stderr)
        return 1


if __name__ == "__main__":
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stderr.reconfigure(encoding="utf-8")
    raise SystemExit(main())
