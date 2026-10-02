"""Audit original-format claims; --apply corrects metadata only under crawler lock.

Requires the existing server to be idle with its scheduler disabled for --apply.
Does not restart/resume the server: deploy the fixed code before resuming collection.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import sqlite3
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from arca_archive.common import RunLock, now_iso
from arca_archive.config import load_settings
from arca_archive.media_integrity import FORMAT_MIME, original_format_mismatch
from scripts.diagnose_backlog import LocalAPI, save


def find_corrections(path):
    result = {"at": now_iso(), "checked": 0, "corrections": [], "issues": []}
    with sqlite3.connect(f"file:{path.as_posix()}?mode=ro", uri=True) as conn:
        conn.row_factory = sqlite3.Row
        rows = conn.execute("SELECT * FROM media WHERE state='downloaded' AND is_original=1").fetchall()
    for row in rows:
        media = dict(row)
        result["checked"] += 1
        path = Path(media["file_path"]) if media["file_path"] else None
        try:
            if path is None:
                raise FileNotFoundError()
            with path.open("rb") as handle:
                mismatch = original_format_mismatch(media, handle.read(64))
                if not mismatch:
                    continue
                handle.seek(0)
                digest = hashlib.file_digest(handle, "sha256").hexdigest()
            if digest != media["sha256"] or path.stat().st_size != media["file_size"]:
                result["issues"].append({"id": media["id"], "error": "FILE_INTEGRITY_MISMATCH"})
                continue
            keys = ("id", "article_id", "file_path", "file_size", "sha256", "is_original",
                    "original_unavailable", "upgrade_attempts", "content_type")
            result["corrections"].append({**{key: media[key] for key in keys},
                                           "expected_format": mismatch[0], "actual_format": mismatch[1]})
        except OSError as exc:
            result["issues"].append({"id": media["id"], "error": type(exc).__name__})
    return result


def apply_corrections(path, rows):
    """Conditional, all-or-nothing updates; any changed file/row aborts the batch."""
    with sqlite3.connect(path) as conn:
        conn.execute("BEGIN IMMEDIATE")
        for row in rows:
            file = Path(row["file_path"])
            with file.open("rb") as handle:
                digest = hashlib.file_digest(handle, "sha256").hexdigest()
            if digest != row["sha256"] or file.stat().st_size != row["file_size"]:
                raise RuntimeError(f"File changed since audit: media {row['id']}")
            cur = conn.execute("""UPDATE media SET is_original=0, original_unavailable=0,
                    upgrade_attempts=0, content_type=?
                WHERE id=? AND state='downloaded' AND is_original=1 AND file_path=? AND sha256=?
                  AND original_unavailable=? AND upgrade_attempts=?""",
                (FORMAT_MIME[row["actual_format"]], row["id"], row["file_path"], row["sha256"],
                 row["original_unavailable"], row["upgrade_attempts"]))
            if cur.rowcount != 1:
                raise RuntimeError(f"Row changed since audit: media {row['id']}")
    return len(rows)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=False)
    settings = load_settings(ROOT / "config/settings.json", create=False)
    if not args.apply:
        audit = find_corrections(settings.db_path)
        save(args.output / "audit.json", audit)
    else:
        state = LocalAPI(settings.server.port)("/api/status")
        if state["running"] or state["scheduler_enabled"] or state["queue"] or state["login_window_open"]:
            raise RuntimeError("Pause the scheduler and wait for collection/login to stop first")
        with RunLock(settings.lock_path):
            with sqlite3.connect(f"file:{settings.db_path.as_posix()}?mode=ro", uri=True) as src:
                with sqlite3.connect(args.output / "before.sqlite3") as dst:
                    src.backup(dst)
                    assert dst.execute("PRAGMA quick_check").fetchone()[0] == "ok"
            audit = find_corrections(settings.db_path)
            save(args.output / "audit.json", audit)
            # Write the complete rollback metadata before changing any DB rows.
            changed = apply_corrections(settings.db_path, audit["corrections"])
            save(args.output / "applied.json", {"at": now_iso(), "changed": changed,
                 "ids": [row["id"] for row in audit["corrections"]], "files_modified": 0})
    print(json.dumps({"checked": audit["checked"], "corrections": len(audit["corrections"]),
                      "issues": len(audit["issues"]), "applied": args.apply}), flush=True)


if __name__ == "__main__":
    main()
