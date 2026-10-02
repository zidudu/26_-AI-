"""Bounded V1.2.4 backlog audit. Inventory is read-only; --probe stores sampled files.

Uses the application's existing access, rate limits, original preference and storage.
Never writes signed URLs/cookies into diagnostic artifacts. Run from the project venv.
"""
from __future__ import annotations

import argparse
from collections import Counter, defaultdict
from contextlib import contextmanager
import hashlib
import json
from pathlib import Path
import sqlite3
import sys
import threading
import time
import urllib.parse
import urllib.request

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from arca_archive.common import RunLock, now_iso, parse_iso, utcnow
from arca_archive.config import load_settings
from arca_archive.db import Database
from arca_archive.pipeline.collect import collect_article
from arca_archive.pipeline.media import _download_one, _is_expired
from arca_archive.pipeline.runner import CrawlContext, StopRequested, initial_fetcher_kind


def save(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")


def bucket(row):
    if not row["enabled"]:
        return "disabled_channel"
    if row["article_state"] != "collected":
        return "parent_" + row["article_state"]
    if not row["collect_media"]:
        return "media_disabled"
    return "active_collected"


def read_population(path):
    with sqlite3.connect(f"file:{path.as_posix()}?mode=ro", uri=True) as conn:
        conn.row_factory = sqlite3.Row
        return [dict(r) for r in conn.execute("""
            SELECT m.*, a.state article_state, a.channel_id, c.slug, c.enabled,
                   c.collect_media, c.site
            FROM media m JOIN articles a ON a.id=m.article_id
            JOIN channels c ON c.id=a.channel_id
            WHERE m.state='pending' ORDER BY m.created_at,m.id
        """)]


def public_row(row):
    keys = ("id", "article_id", "channel_id", "slug", "kind", "origin", "created_at",
            "article_state", "attempts", "state_code", "next_retry_at")
    age = (utcnow() - parse_iso(row["created_at"])).total_seconds() / 86400
    return {**{k: row.get(k) for k in keys}, "bucket": bucket(row),
            "age_days": round(age, 3), "all_stored_urls_expired": _is_expired(row)}


def choose_sample(rows):
    """Purposeful coverage, not a random sample or a population success estimate.

    Image strata: oldest/middle/newest distinct posts. Other kinds: oldest/newest.
    Also one oldest pending item per active non-collected parent-state/channel.
    """
    groups = defaultdict(list)
    for row in rows:
        if row.get("site") != "arca" or not row["enabled"] or not row["collect_media"]:
            continue
        groups[(row["channel_id"], row["article_state"], row["kind"] if row["article_state"] == "collected" else "*")].append(row)
    chosen = []
    for (cid, state, kind), group in sorted(groups.items()):
        group = sorted(group, key=lambda r: (r["created_at"], r["id"]))
        by_post = list({r["article_id"]: r for r in reversed(group)}.values())
        by_post.sort(key=lambda r: (r["created_at"], r["id"]))
        indexes = [0]
        if state == "collected":
            if kind == "image":
                indexes.append(len(by_post) // 2)
            indexes.append(len(by_post) - 1)
        for index in dict.fromkeys(indexes):
            chosen.append(by_post[index])
    return chosen


def inventory(settings, folder, name):
    rows = read_population(settings.db_path)
    public = [public_row(r) for r in rows]
    grouped = Counter((r["slug"], r["bucket"], r["kind"]) for r in public)
    summary = {"at": now_iso(), "pending": len(rows),
               "buckets": dict(Counter(r["bucket"] for r in public)),
               "stored_urls_all_expired": sum(r["all_stored_urls_expired"] for r in public),
               "age_bands": dict(Counter("under_1d" if r["age_days"] < 1 else "1_to_3d" if r["age_days"] < 3 else "3d_or_more" for r in public)),
               "groups": [{"channel": c, "bucket": b, "kind": k, "count": n} for (c, b, k), n in sorted(grouped.items())]}
    save(folder / f"{name}_summary.json", summary)
    save(folder / f"{name}_cohort.json", public)
    return rows, summary


def magic_format(header):
    if header.startswith((b"GIF87a", b"GIF89a")):
        return "gif"
    if header.startswith(b"\x89PNG\r\n\x1a\n"):
        return "png"
    if header.startswith(b"\xff\xd8\xff"):
        return "jpg"
    if header[:4] == b"RIFF" and header[8:12] == b"WEBP":
        return "webp"
    if header[4:8] == b"ftyp":
        return "mp4_family"
    return "unknown"


def audit_sample_files(settings, folder):
    """DB's is_original is a claim, not proof. Validate stored formats independently."""
    result = json.loads((folder / "probe_result.json").read_text(encoding="utf-8"))
    audit = []
    with sqlite3.connect(f"file:{settings.db_path.as_posix()}?mode=ro", uri=True) as conn:
        conn.row_factory = sqlite3.Row
        for sample in result["samples"]:
            if sample.get("outcome") != "downloaded":
                continue
            row = conn.execute("SELECT * FROM media WHERE id=?", (sample["id"],)).fetchone()
            path = Path(row["file_path"])
            with path.open("rb") as handle:
                fmt = magic_format(handle.read(32))
                handle.seek(0)
                digest = hashlib.file_digest(handle, "sha256").hexdigest()
            source_ext = Path(urllib.parse.urlsplit(row["original_url"] or row["source_key"]).path).suffix.lower()
            contradicted = source_ext == ".gif" and fmt == "mp4_family" and row["is_original"] == 1
            audit.append({"id": sample["id"], "source_extension": source_ext,
                          "file_format": fmt, "content_type": row["content_type"],
                          "db_original_flag": row["is_original"],
                          "original_flag_contradicted": contradicted,
                          "hash_verified": digest == row["sha256"],
                          "size_verified": path.stat().st_size == row["file_size"]})
    save(folder / "file_audit.json", {"at": now_iso(), "files": audit,
        "limitation": "Matching format/hash verifies local integrity, not equality with the uploader's original file."})
    return audit


class LocalAPI:
    def __init__(self, port):
        self.base = f"http://127.0.0.1:{port}"

    def __call__(self, path, data=None):
        request = urllib.request.Request(self.base + path,
            data=None if data is None else urllib.parse.urlencode(data).encode())
        with urllib.request.urlopen(request, timeout=15) as response:
            body = response.read()
            return json.loads(body) if "json" in response.headers.get("Content-Type", "") else {"status": response.status}


@contextmanager
def paused_service(api, folder):
    before = api("/api/status")
    save(folder / "runtime_before.json", before)
    if before["queue"] or before["login_window_open"] or before.get("stopping"):
        raise RuntimeError("Queue/login/stop activity exists; do not interfere with it")
    interrupted = None
    disabled = False
    restore = {}
    try:
        # Set before the request: a timeout could happen after the server accepts it.
        disabled = True
        api("/scheduler", {"enabled": "off"})
        current = api("/api/status")
        if current["queue"]:
            raise RuntimeError("Queue changed while pausing; no stop requested")
        if current["running"]:
            interrupted = current["current"]["channel_id"]
            api("/api/stop", {})
        until = time.monotonic() + 120
        while api("/api/status")["running"]:
            if time.monotonic() > until:
                raise RuntimeError("Crawler did not stop within 120 seconds")
            time.sleep(1)
        yield
    finally:
        # Each restoration is attempted independently, even if the other fails.
        if interrupted is not None:
            try:
                restore["resume"] = api(f"/api/run/{interrupted}", {})
            except Exception as exc:
                restore["resume_error"] = type(exc).__name__
        if disabled:
            try:
                restore["scheduler"] = api("/scheduler", {"enabled": "on" if before["scheduler_enabled"] else "off"})
            except Exception as exc:
                restore["scheduler_error"] = type(exc).__name__
        save(folder / "runtime_restore.json", restore)
        if any(k.endswith("error") for k in restore):
            raise RuntimeError("Service restoration needs attention; see runtime_restore.json")


class Recorder:
    def __init__(self, path):
        self.path = path
        self.active = {}
        self.rows = []

    def emit(self, data):
        row = {"at": now_iso(), **self.active, **data}
        self.rows.append(row)
        with self.path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(row, ensure_ascii=False) + "\n")


class TimedFetcher:
    def __init__(self, inner, recorder):
        self.inner, self.recorder = inner, recorder
        self.last_page = None

    def __getattr__(self, key):
        return getattr(self.inner, key)

    def call(self, kind, url, *args, **kwargs):
        begin = time.monotonic()
        info = {"operation": kind, "host": urllib.parse.urlsplit(url).hostname,
                "original_request": "type=orig" in url}
        try:
            result = getattr(self.inner, kind)(url, *args, **kwargs)
            info.update(status=result.status, bytes=len(getattr(result, "data", b"")),
                        content_type=getattr(result, "content_type", None))
            if kind == "fetch_page":
                self.last_page = result
            return result
        except Exception as exc:
            info["error"] = getattr(exc, "code", type(exc).__name__)
            raise
        finally:
            info["seconds"] = round(time.monotonic() - begin, 6)
            self.recorder.emit(info)

    def fetch_page(self, url):
        return self.call("fetch_page", url)

    def download(self, url, **kwargs):
        return self.call("download", url, **kwargs)


class TimedLimiter:
    def __init__(self, inner, label, recorder):
        self.inner, self.label, self.recorder = inner, label, recorder

    def wait(self):
        begin = time.monotonic()
        try:
            return self.inner.wait()
        finally:
            self.recorder.emit({"operation": self.label, "seconds": round(time.monotonic() - begin, 6)})


def probe(settings, folder, minutes):
    api = LocalAPI(settings.server.port)
    results = {"started_at": now_iso(), "original_preference": settings.media.request_original,
               "sampling": "purposeful distinct-post channel/kind/age coverage; not random",
               "runs": [], "samples": []}
    recorder = Recorder(folder / "requests.jsonl")
    with paused_service(api, folder):
        with RunLock(settings.lock_path):
            with sqlite3.connect(f"file:{settings.db_path.as_posix()}?mode=ro", uri=True) as src:
                with sqlite3.connect(folder / "before.sqlite3") as dst:
                    src.backup(dst)
                    assert dst.execute("PRAGMA quick_check").fetchone()[0] == "ok"
            rows, summary = inventory(settings, folder, "before")
            selected = choose_sample(rows)
            save(folder / "sample_plan.json", [public_row(r) for r in selected])
            print(json.dumps({"pending": len(rows), "samples": len(selected), "buckets": summary["buckets"]}), flush=True)
            db = Database(settings.db_path)
            deadline_stop = threading.Event()
            timer = threading.Timer(minutes * 60, deadline_stop.set)
            timer.daemon = True
            timer.start()
            abort = False
            try:
                for cid in dict.fromkeys(r["channel_id"] for r in selected):
                    if abort or deadline_stop.is_set():
                        break
                    channel = db.get_channel(cid)
                    run = db.create_run(channel, "diagnostic_v1.2.4")
                    ctx = CrawlContext(settings, db, run, channel, deadline_stop)
                    factory = ctx._default_factory
                    ctx._fetcher_factory = lambda kind: TimedFetcher(factory(kind), recorder)
                    for attr in ("page_limiter", "media_limiter", "original_limiter"):
                        setattr(ctx, attr, TimedLimiter(getattr(ctx, attr), attr, recorder))
                    status, code = "success", "OK"
                    try:
                        ctx.use_fetcher(initial_fetcher_kind(channel))
                        ctx.set_stage("recheck")
                        for target in (r for r in selected if r["channel_id"] == cid):
                            ctx.check_stop()
                            recorder.active = {"run_id": run["id"], "media_id": target["id"], "article_id": target["article_id"]}
                            item = {**public_row(target), "run_id": run["id"]}
                            results["samples"].append(item)
                            begin = time.monotonic()
                            try:
                                outcome = collect_article(ctx, db.get_article(target["article_id"]), recheck=True)
                                item["page_total_seconds"] = round(time.monotonic() - begin, 6)
                                item["article_outcome"] = outcome
                                if outcome not in ("collected", "updated", "unchanged"):
                                    item["outcome"] = "source_" + outcome
                                    continue
                                article = db.get_article(target["article_id"])
                                parsed = ctx.site.parse_article(ctx.fetcher.last_page,
                                    {**article, "remote_id": article.get("remote_id") or str(article["id"])}, channel,
                                    ctx.site.media_hosts(settings.media.allowed_hosts), bool(channel.get("collect_comments", 1)))
                                item["parsed"] = {"body_chars": len(parsed.body_html), "media_refs": len(parsed.media),
                                                  "comments": len(parsed.comments), "warnings": parsed.warnings}
                                fresh_keys = {m["source_key"] for m in parsed.media}
                                media = db.get_media(target["id"])
                                if media["source_key"] not in fresh_keys:
                                    item["outcome"] = "reference_absent_on_current_page"
                                    continue
                                if _is_expired(media):
                                    item["outcome"] = "urls_still_expired_after_refresh"
                                    continue
                                ctx.stats.media_targets += 1
                                begin_media = time.monotonic()
                                ok = _download_one(ctx, article, media)
                                item["media_total_seconds"] = round(time.monotonic() - begin_media, 6)
                                updated = db.get_media(media["id"])
                                item.update(outcome="downloaded" if ok else "download_failed",
                                            state=updated["state"], code=updated["state_code"],
                                            bytes=updated["file_size"], original=updated["is_original"])
                                if ok:
                                    path = Path(updated["file_path"])
                                    with path.open("rb") as handle:
                                        item["hash_verified"] = hashlib.file_digest(handle, "sha256").hexdigest() == updated["sha256"]
                                    item["size_verified"] = path.stat().st_size == updated["file_size"]
                            except Exception as exc:
                                item.update(outcome="interrupted", code=getattr(exc, "code", type(exc).__name__))
                                raise
                            finally:
                                item["total_seconds"] = round(time.monotonic() - begin, 6)
                                ctx.checkpoint()
                                save(folder / "probe_result.json", results)
                                print(json.dumps(item, ensure_ascii=False), flush=True)
                    except Exception as exc:
                        code = getattr(exc, "code", type(exc).__name__)
                        status = "cancelled" if isinstance(exc, StopRequested) else "failed"
                        abort = True  # No access-error workaround or alternate-channel probing.
                    finally:
                        ctx.close_fetcher()
                        if status == "success" and (ctx.stats.has_item_failures or any(s.get("outcome") != "downloaded" for s in results["samples"] if s["run_id"] == run["id"])):
                            status, code = "partial", "PARTIAL_DIAGNOSTIC"
                        db.finish_run(run["id"], status, code, None, ctx.stats.as_dict())
                        results["runs"].append({"id": run["id"], "channel": channel["slug"], "status": status, "code": code})
            finally:
                timer.cancel()
                db.close()
                results["finished_at"] = now_iso()
                results["unattempted_ids"] = [r["id"] for r in selected if r["id"] not in {s["id"] for s in results["samples"]}]
                save(folder / "probe_result.json", results)
                inventory(settings, folder, "after")
    print(json.dumps({"finished": True, "folder": str(folder), "runs": results["runs"]}), flush=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--probe", action="store_true", help="Pause collection, back up DB, download bounded sample, resume")
    parser.add_argument("--minutes", type=int, default=20)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if not 1 <= args.minutes <= 30:
        parser.error("--minutes must be 1..30")
    args.output.mkdir(parents=True, exist_ok=False)
    settings = load_settings(ROOT / "config/settings.json", create=False)
    if args.probe:
        try:
            probe(settings, args.output, args.minutes)
        finally:
            # Retain file evidence and explicit health failure even if the web
            # server exits before the restoration requests can reach it.
            if (args.output / "probe_result.json").exists():
                audit_sample_files(settings, args.output)
            try:
                state = LocalAPI(settings.server.port)("/api/status")
            except Exception as exc:
                state = {"at": now_iso(), "reachable": False, "error": type(exc).__name__}
            save(args.output / "runtime_after.json", state)
    else:
        rows, summary = inventory(settings, args.output, "inventory")
        save(args.output / "sample_plan.json", [public_row(r) for r in choose_sample(rows)])
        print(json.dumps(summary, ensure_ascii=False))


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    main()
