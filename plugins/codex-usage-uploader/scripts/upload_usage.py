#!/usr/bin/env python3
import argparse
import datetime as dt
import json
import os
import re
import shutil
import socket
import sqlite3
import subprocess
import sys
import tempfile
import urllib.parse
from pathlib import Path


DEFAULT_REPORTS_REPO = "git@github.com:Dipsy524/codex-usage-reports.git"
DEFAULT_BRANCH = "main"
DEDUP_WINDOW_SECONDS = 10 * 60


def fail(message):
    print(f"error: {message}", file=sys.stderr)
    raise SystemExit(1)


def run(args, cwd=None, check=True):
    result = subprocess.run(args, cwd=cwd, text=True, capture_output=True)
    if check and result.returncode != 0:
        detail = (result.stderr or result.stdout).strip()
        fail(f"{' '.join(args)} failed: {detail}")
    return result


def sanitize(value):
    value = re.sub(r"[^A-Za-z0-9._-]+", "-", value.strip())
    return value.strip("-._") or "unknown-machine"


def default_machine_id():
    return sanitize(
        os.environ.get("CODEX_USAGE_MACHINE_ID")
        or os.environ.get("COMPUTERNAME")
        or os.environ.get("HOSTNAME")
        or socket.gethostname()
    )


def candidate_db_paths(explicit=None):
    raw = []
    if explicit:
        raw.append(explicit)
    if os.environ.get("CC_SWITCH_DB"):
        raw.append(os.environ["CC_SWITCH_DB"])
    raw.append(str(Path.home() / ".cc-switch" / "cc-switch.db"))
    for key in ("USERPROFILE", "HOME"):
        if os.environ.get(key):
            raw.append(str(Path(os.environ[key]) / ".cc-switch" / "cc-switch.db"))

    seen = set()
    for item in raw:
        path = Path(item).expanduser()
        key = str(path).lower()
        if key not in seen:
            seen.add(key)
            yield path


def find_cc_switch_db(explicit=None):
    candidates = list(candidate_db_paths(explicit))
    for path in candidates:
        if path.is_file():
            return path
    fail("cc-switch.db not found. Checked: " + ", ".join(str(p) for p in candidates))


def sqlite_ro_uri(path):
    normalized = str(path.resolve()).replace("\\", "/")
    return "file:" + urllib.parse.quote(normalized, safe="/:") + "?mode=ro"


def parse_day(value):
    try:
        return dt.date.fromisoformat(value)
    except ValueError:
        fail(f"invalid --date {value!r}; expected YYYY-MM-DD")


def month_bounds(day):
    start = dt.date(day.year, day.month, 1)
    if day.month == 12:
        end = dt.date(day.year + 1, 1, 1)
    else:
        end = dt.date(day.year, day.month + 1, 1)
    return start, end


def local_epoch(day):
    return int(dt.datetime(day.year, day.month, day.day).timestamp())


def query_summary(db_path, start_day, end_day):
    sql = f"""
    SELECT
      COUNT(*) AS requests,
      COALESCE(SUM(l.input_tokens), 0) AS input_tokens_including_cache,
      COALESCE(SUM(CASE
        WHEN l.input_tokens >= l.cache_read_tokens THEN l.input_tokens - l.cache_read_tokens
        ELSE l.input_tokens
      END), 0) AS fresh_input_tokens,
      COALESCE(SUM(l.output_tokens), 0) AS output_tokens,
      COALESCE(SUM(l.cache_read_tokens), 0) AS cache_read_tokens,
      COALESCE(SUM(l.cache_creation_tokens), 0) AS cache_creation_tokens,
      COALESCE(SUM(CAST(l.total_cost_usd AS REAL)), 0) AS cost_usd
    FROM proxy_request_logs l
    WHERE l.app_type = 'codex'
      AND l.created_at >= ?
      AND l.created_at < ?
      AND NOT (
        COALESCE(l.data_source, 'proxy') IN ('session_log', 'codex_session', 'gemini_session', 'opencode_session')
        AND EXISTS (
          SELECT 1
          FROM proxy_request_logs proxy_dedup
          WHERE COALESCE(proxy_dedup.data_source, 'proxy') = 'proxy'
            AND proxy_dedup.app_type = l.app_type
            AND proxy_dedup.status_code >= 200
            AND proxy_dedup.status_code < 300
            AND proxy_dedup.input_tokens = l.input_tokens
            AND proxy_dedup.output_tokens = l.output_tokens
            AND proxy_dedup.cache_read_tokens = l.cache_read_tokens
            AND (
              proxy_dedup.cache_creation_tokens = l.cache_creation_tokens
              OR (l.cache_creation_tokens = 0 AND COALESCE(l.data_source, 'proxy') IN ('codex_session', 'gemini_session', 'opencode_session'))
            )
            AND proxy_dedup.created_at BETWEEN l.created_at - {DEDUP_WINDOW_SECONDS} AND l.created_at + {DEDUP_WINDOW_SECONDS}
            AND (
              LOWER(proxy_dedup.model) = LOWER(l.model)
              OR LOWER(proxy_dedup.model) = 'unknown'
              OR LOWER(l.model) = 'unknown'
            )
        )
      )
    """
    con = sqlite3.connect(sqlite_ro_uri(db_path), uri=True, timeout=5)
    try:
        row = con.execute(sql, (local_epoch(start_day), local_epoch(end_day))).fetchone()
    except sqlite3.OperationalError as exc:
        fail(f"cannot read CC Switch usage table: {exc}")
    finally:
        con.close()

    requests, raw_input, fresh, output, cache_read, cache_create, cost = row
    real_total = fresh + output + cache_read + cache_create
    cacheable = fresh + cache_read + cache_create
    return {
        "requests": int(requests),
        "input_tokens_including_cache": int(raw_input),
        "fresh_input_tokens": int(fresh),
        "output_tokens": int(output),
        "cache_read_tokens": int(cache_read),
        "cache_creation_tokens": int(cache_create),
        "real_total_tokens": int(real_total),
        "cache_hit_rate": round((cache_read / cacheable) if cacheable else 0, 6),
        "cost_usd": round(float(cost), 6),
    }


def report_payload(period_type, start_day, end_day, machine_id, db_path):
    totals = query_summary(db_path, start_day, end_day)
    return {
        "schema_version": 1,
        "source": "cc-switch.db",
        "app_type": "codex",
        "machine_id": machine_id,
        "generated_at": dt.datetime.now().astimezone().isoformat(timespec="seconds"),
        "period": {
            "type": period_type,
            "start": start_day.isoformat(),
            "end": end_day.isoformat(),
        },
        "totals": totals,
    }


def default_workdir():
    if os.environ.get("CODEX_USAGE_WORKDIR"):
        return Path(os.environ["CODEX_USAGE_WORKDIR"]).expanduser()
    if os.environ.get("LOCALAPPDATA"):
        return Path(os.environ["LOCALAPPDATA"]) / "codex-usage-uploader" / "reports"
    return Path.home() / ".cache" / "codex-usage-uploader" / "reports"


def ensure_reports_repo(repo, branch, workdir):
    if not shutil.which("git"):
        fail("git is not installed or not on PATH")

    workdir = Path(workdir).expanduser()
    if (workdir / ".git").is_dir():
        run(["git", "fetch", "origin"], cwd=workdir)
        checkout = run(["git", "checkout", branch], cwd=workdir, check=False)
        if checkout.returncode != 0:
            run(["git", "checkout", "-B", branch], cwd=workdir)
        run(["git", "pull", "--ff-only", "origin", branch], cwd=workdir, check=False)
    else:
        workdir.parent.mkdir(parents=True, exist_ok=True)
        run(["git", "clone", repo, str(workdir)])
        run(["git", "checkout", "-B", branch], cwd=workdir)

    if run(["git", "config", "user.email"], cwd=workdir, check=False).returncode != 0:
        run(["git", "config", "user.email", "codex-usage-uploader@local"], cwd=workdir)
    if run(["git", "config", "user.name"], cwd=workdir, check=False).returncode != 0:
        run(["git", "config", "user.name", "Codex Usage Uploader"], cwd=workdir)
    return workdir


def write_json(path, payload):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def upload(args):
    db_path = find_cc_switch_db(args.db)
    machine_id = sanitize(args.machine_id or default_machine_id())
    day = parse_day(args.date) if args.date else dt.date.today()
    month_start, month_end = month_bounds(day)
    daily = report_payload("daily", day, day + dt.timedelta(days=1), machine_id, db_path)
    monthly = report_payload("monthly", month_start, month_end, machine_id, db_path)

    repo = args.repo or os.environ.get("CODEX_USAGE_REPORTS_REPO") or DEFAULT_REPORTS_REPO
    branch = args.branch or os.environ.get("CODEX_USAGE_REPORTS_BRANCH") or DEFAULT_BRANCH
    workdir = Path(args.workdir).expanduser() if args.workdir else default_workdir()

    if args.dry_run:
        print(json.dumps({"daily": daily, "monthly": monthly}, ensure_ascii=False, indent=2))
        return

    repo_dir = ensure_reports_repo(repo, branch, workdir)
    write_json(repo_dir / "usage" / "daily" / f"{day.year:04d}" / f"{day.month:02d}" / f"{day.day:02d}" / f"{machine_id}.json", daily)
    write_json(repo_dir / "usage" / "monthly" / f"{month_start.year:04d}" / f"{month_start.month:02d}" / f"{machine_id}.json", monthly)

    run(["git", "add", "usage"], cwd=repo_dir)
    status = run(["git", "status", "--porcelain"], cwd=repo_dir).stdout.strip()
    if not status:
        print("No usage changes to upload.")
        return

    run(["git", "commit", "-m", f"Update Codex usage for {machine_id} {day.isoformat()}"], cwd=repo_dir)
    run(["git", "push", "-u", "origin", branch], cwd=repo_dir)
    print(f"Uploaded Codex usage for {machine_id} to {repo}.")


def self_test():
    with tempfile.TemporaryDirectory() as td:
        db = Path(td) / "cc-switch.db"
        con = sqlite3.connect(db)
        con.execute(
            """
            CREATE TABLE proxy_request_logs (
              request_id TEXT PRIMARY KEY,
              app_type TEXT,
              model TEXT,
              input_tokens INTEGER,
              output_tokens INTEGER,
              cache_read_tokens INTEGER,
              cache_creation_tokens INTEGER,
              total_cost_usd TEXT,
              status_code INTEGER,
              created_at INTEGER,
              data_source TEXT
            )
            """
        )
        base = local_epoch(dt.date.today())
        rows = [
            ("session-1", "codex", "gpt-5", 1000, 50, 600, 0, "0.100000", 200, base + 1, "codex_session"),
            ("proxy-1", "codex", "gpt-5", 200, 10, 0, 0, "0.200000", 200, base + 2, "proxy"),
            ("session-dup", "codex", "gpt-5", 200, 10, 0, 0, "0.200000", 200, base + 3, "codex_session"),
        ]
        con.executemany("INSERT INTO proxy_request_logs VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)", rows)
        con.commit()
        con.close()

        summary = query_summary(db, dt.date.today(), dt.date.today() + dt.timedelta(days=1))
        assert summary["requests"] == 2, summary
        assert summary["fresh_input_tokens"] == 600, summary
        assert summary["output_tokens"] == 60, summary
        assert summary["cache_read_tokens"] == 600, summary
        assert summary["real_total_tokens"] == 1260, summary
        assert summary["cost_usd"] == 0.3, summary
    print("self-test passed")


def main():
    parser = argparse.ArgumentParser(description="Upload local Codex usage from CC Switch to GitHub reports.")
    parser.add_argument("--date", help="local day to upload, YYYY-MM-DD; default today")
    parser.add_argument("--machine-id", help="stable machine label; default hostname")
    parser.add_argument("--db", help="explicit path to cc-switch.db")
    parser.add_argument("--repo", help="reports repo remote")
    parser.add_argument("--branch", help="reports repo branch")
    parser.add_argument("--workdir", help="local clone/cache directory for the reports repo")
    parser.add_argument("--dry-run", action="store_true", help="print JSON without cloning or pushing")
    parser.add_argument("--self-test", action="store_true", help="run a tiny SQLite aggregation test")
    args = parser.parse_args()

    if args.self_test:
        self_test()
    else:
        upload(args)


if __name__ == "__main__":
    main()
