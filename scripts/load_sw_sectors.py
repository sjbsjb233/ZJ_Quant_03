#!/usr/bin/env python
"""Import a 申万 (or any) sector classification tree into ``reference.sectors``.

Reads the ``sw2021_sectors_with_members.csv`` deliverable (see the sectors
import plan) and upserts it through :func:`quant_data.ingest.ingest_sectors`.
Every row carries the sector dictionary fields *and* the de-normalised member
roster (``members`` JSON array + ``member_count``).

CSV columns (UTF-8, BOM tolerated)::

    code,name,level,parent_code,classification_system,members,member_count,member_source

``member_source`` is provenance metadata only -- it is not a schema business
field and is ignored on import. ``member_count`` is recomputed from ``members``
so the loaded data always satisfies invariant INV-SEC-004.

Usage::

    python load_sw_sectors.py --csv sw2021_sectors_with_members.csv \\
        --root /path/to/data_root --source manual

    # use a version-tagged classification system instead of the CSV's value
    python load_sw_sectors.py --csv sw2021_sectors_with_members.csv \\
        --root /path/to/data_root --system sw_2021

    # legacy 5-column dictionary CSV with no member roster
    python load_sw_sectors.py --csv sw2021_sectors.csv --root /path --no-members
"""
from __future__ import annotations

import argparse
import csv
import json
import sys
from pathlib import Path


def _parse_int(value, *, field: str, code: str):
    value = (value or "").strip()
    if value == "":
        return None
    try:
        return int(value)
    except ValueError:
        raise SystemExit(f"[load_sw_sectors] row {code!r}: {field} is not an int: {value!r}")


def _parse_members(raw, *, code: str):
    """Return (members_json_str_or_None, member_count_or_None)."""
    raw = (raw or "").strip()
    if raw == "":
        return None, None
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise SystemExit(f"[load_sw_sectors] row {code!r}: members is not valid JSON: {exc}")
    if not isinstance(parsed, list):
        raise SystemExit(f"[load_sw_sectors] row {code!r}: members must be a JSON array")
    # re-serialise compactly so the stored form is canonical, and derive the
    # count from the array itself (CSV's member_count column is advisory).
    return json.dumps(parsed, ensure_ascii=False), len(parsed)


def read_csv(path: Path, *, system: str | None, with_members: bool) -> list[dict]:
    rows: list[dict] = []
    # utf-8-sig transparently strips a leading BOM (the file is "UTF-8 with BOM").
    with path.open(encoding="utf-8-sig", newline="") as fh:
        reader = csv.DictReader(fh)
        required = {"code", "name", "level", "parent_code", "classification_system"}
        missing = required - set(reader.fieldnames or [])
        if missing:
            raise SystemExit(
                f"[load_sw_sectors] {path} missing required columns: {sorted(missing)}"
            )
        for raw in reader:
            code = (raw.get("code") or "").strip()
            if not code:
                continue  # skip blank lines
            parent = (raw.get("parent_code") or "").strip() or None
            rec = {
                "code": code,
                "name": (raw.get("name") or "").strip(),
                "level": _parse_int(raw.get("level"), field="level", code=code),
                "parent_code": parent,
                "classification_system": system or (raw.get("classification_system") or "").strip(),
            }
            if with_members:
                members, count = _parse_members(raw.get("members"), code=code)
                rec["members"] = members
                rec["member_count"] = count
            rows.append(rec)
    return rows


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Import sector tree + members into reference.sectors")
    ap.add_argument("--csv", required=True, type=Path, help="path to the sectors CSV")
    ap.add_argument("--root", default=None, help="data root (defaults to QUANT_DATA_ROOT / ./data)")
    ap.add_argument("--source", default="manual", help="metadata _source tag (default: manual)")
    ap.add_argument("--system", default=None,
                    help="override classification_system for every row (e.g. sw_2021)")
    ap.add_argument("--no-members", action="store_true",
                    help="read only the 5 dictionary columns; leave members/member_count NULL")
    ap.add_argument("--dry-run", action="store_true", help="parse + summarise but do not write")
    args = ap.parse_args(argv)

    if not args.csv.exists():
        raise SystemExit(f"[load_sw_sectors] CSV not found: {args.csv}")

    rows = read_csv(args.csv, system=args.system, with_members=not args.no_members)
    with_roster = [r for r in rows if r.get("members") is not None]
    max_count = max((r["member_count"] for r in with_roster), default=0)
    print(f"[load_sw_sectors] {len(rows)} rows; max member_count={max_count}, "
          f"rows with members={len(with_roster)}")

    if args.dry_run:
        print("[load_sw_sectors] --dry-run: nothing written")
        return 0

    # imported lazily so --help / --dry-run work without the package installed
    from quant_data import ingest

    n = ingest.ingest_sectors(rows, source=args.source, root=args.root)
    print(f"[load_sw_sectors] upserted {n} rows into reference.sectors")
    return 0


if __name__ == "__main__":
    sys.exit(main())
