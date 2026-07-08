"""Maintenance CLI for the MCSNRcat catalog — safe edits without touching Excel.

The versioned CSV ``data/lmc_snrs_extended_v<N>.csv`` is the single source of
truth. This tool makes the routine edits (confirm a candidate, set a field,
cut a new version) safely, keeping the id/status/name conventions consistent
and validating the result. You can still edit the CSV by hand in any editor —
``build.py`` validates on every build — but these commands avoid the fiddly
bits (the ``MCSNR`` prefix, moving the old designation to ``alias``, etc.).

Common workflows
----------------
Confirm a candidate as an SNR (bumps to a new version automatically)::

    python catalog.py confirm c45 --new-id "MCSNR J0614-7251" \
        --confirm-ref Sa25 --note "Confirmed by Sasaki+25; ..."

Set an arbitrary field on one object (in place, current latest version)::

    python catalog.py set c46 sn_type CC

Just check the latest CSV is well-formed::

    python catalog.py validate

Cut the next version file (copy latest -> v<N+1>) to start a batch of edits::

    python catalog.py new-version

Reference short-codes are resolved to ADS bibcodes via REF_REGISTRY below;
add new papers there as they appear.
"""

from __future__ import annotations

import argparse
import logging
import shutil
from pathlib import Path

import pandas as pd

from build import latest_catalog, validate_catalog

logger = logging.getLogger(__name__)

DATA_DIR = Path(__file__).parent / "data"

#: Short reference code -> (ADS bibcode / citation, confidence). Grow as new
#: papers are cited. ``verify-bibcode`` = still needs an ADS lookup.
REF_REGISTRY: dict[str, tuple[str, str]] = {
    "M16": ("2016A&A...585A.162M", "ok"),
    "B17": ("2017ApJS..230....2B", "ok"),
    "L17": ("2017ApJ...837...36L", "ok"),
    "Y21": ("2021MNRAS.500.2336Y", "ok"),
    "K22": ("2022MNRAS.515.4099K", "ok"),
    "B23": ("2022MNRAS.518.2574B", "ok"),
    "Za24": ("2024A&A...692A.237Z", "ok"),
    "F22": ("2022MNRAS.512..265F", "ok"),  # Filipovic+22, MNRAS 512, 265 (J0624-6948)
    "Sa25": ("2025A&A...693L..15S", "ok"),  # Sasaki+25, A&A 693, L15
    "TW": ("Shukla 2024, MSc thesis", "ok"),
}


def _next_version_path(latest: Path) -> Path:
    n = int(latest.stem.rsplit("_v", 1)[-1])
    return latest.with_name(f"lmc_snrs_extended_v{n + 1}.csv")


def _load_latest() -> tuple[pd.DataFrame, Path]:
    path = latest_catalog(DATA_DIR)
    return pd.read_csv(path, dtype=object), path


def _write(df: pd.DataFrame, path: Path) -> None:
    version = path.stem.rsplit("_v", 1)[-1]
    df["dataset_version"] = version
    validate_catalog(df)
    df.to_csv(path, index=False)
    logger.info("wrote %s (%d objects)", path, len(df))


def cmd_validate(_args: argparse.Namespace) -> None:
    df, path = _load_latest()
    validate_catalog(df)
    counts = df["klass"].value_counts().to_dict()
    logger.info("OK: %s — %s", path.name, counts)


def cmd_new_version(_args: argparse.Namespace) -> None:
    df, path = _load_latest()
    new = _next_version_path(path)
    if new.exists():
        raise SystemExit(f"{new} already exists")
    shutil.copy(path, new)
    logger.info("created %s (copy of %s) — edit it, then run build.py", new.name, path.name)


def cmd_set(args: argparse.Namespace) -> None:
    df, path = _load_latest()
    m = df["snr_key"] == args.snr_key
    if m.sum() != 1:
        raise SystemExit(f"snr_key {args.snr_key!r} matched {m.sum()} rows")
    if args.field not in df.columns:
        raise SystemExit(f"unknown column {args.field!r}")
    df.loc[m, args.field] = args.value
    _write(df, path)  # in place; run `new-version` first for a clean version bump


def cmd_confirm(args: argparse.Namespace) -> None:
    """Promote a candidate to confirmed, into a NEW version file."""
    df, path = _load_latest()
    m = df["snr_key"] == args.snr_key
    if m.sum() != 1:
        raise SystemExit(f"snr_key {args.snr_key!r} matched {m.sum()} rows")
    row = df[m].iloc[0]
    if row["klass"] == "SNR":
        raise SystemExit(f"{args.snr_key} is already confirmed ({row['id']})")

    new_id = args.new_id
    if not new_id.startswith("MCSNR "):
        raise SystemExit("--new-id must be the full designation, e.g. 'MCSNR J0614-7251'")
    new_name = new_id[len("MCSNR "):].strip()
    old_name = str(row["name"])

    df.loc[m, "klass"] = "SNR"
    df.loc[m, "id"] = new_id
    df.loc[m, "name"] = new_name
    # preserve the old (usually longer/discovery) designation as a searchable alias
    if new_name != old_name and (pd.isna(row["alias"]) or not str(row["alias"]).strip()):
        df.loc[m, "alias"] = old_name
    if args.confirm_ref:
        bib, _conf = REF_REGISTRY.get(args.confirm_ref, (args.confirm_ref, "unmapped"))
        df.loc[m, "ref_confirm_code"] = args.confirm_ref
        df.loc[m, "ref_confirm"] = bib
    if args.note:
        df.loc[m, "thesis_note"] = args.note

    out = path if args.in_place else _next_version_path(path)
    if not args.in_place and out.exists():
        raise SystemExit(f"{out} already exists; use --in-place or remove it")
    _write(df, out)
    logger.info("confirmed %s -> %s in %s", args.snr_key, new_id, out.name)


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest="cmd", required=True)

    sub.add_parser("validate", help="validate the latest catalog CSV").set_defaults(func=cmd_validate)
    sub.add_parser("new-version", help="copy latest CSV to the next version").set_defaults(func=cmd_new_version)

    ps = sub.add_parser("set", help="set one field on one object (in place)")
    ps.add_argument("snr_key"); ps.add_argument("field"); ps.add_argument("value")
    ps.set_defaults(func=cmd_set)

    pc = sub.add_parser("confirm", help="promote a candidate to confirmed (new version)")
    pc.add_argument("snr_key", help="e.g. c45")
    pc.add_argument("--new-id", required=True, help="full designation, e.g. 'MCSNR J0614-7251'")
    pc.add_argument("--confirm-ref", help="short code for confirming paper (see REF_REGISTRY), e.g. Sa25")
    pc.add_argument("--note", help="banner text shown on the object page")
    pc.add_argument("--in-place", action="store_true", help="edit latest file instead of bumping version")
    pc.set_defaults(func=cmd_confirm)

    args = p.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
