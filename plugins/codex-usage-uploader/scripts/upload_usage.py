#!/usr/bin/env python3
import argparse
import datetime as dt
import json
import os
import re
import shutil
import socket
import subprocess
import sys
import tempfile
from pathlib import Path


DEFAULT_REPORTS_REPO = "git@github.com:Dipsy524/codex-usage-reports.git"
DEFAULT_BRANCH = "main"
NEAR_LIMIT_PERCENT = 95


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


def parse_month(value):
    try:
        year, month = value.split("-", 1)
        return dt.date(int(year), int(month), 1)
    except (ValueError, TypeError):
        fail(f"invalid --month {value!r}; expected YYYY-MM")


def parse_day(value):
    try:
        return dt.date.fromisoformat(value)
    except ValueError:
        fail(f"invalid --date {value!r}; expected YYYY-MM-DD")


def month_bounds(day):
    start = dt.date(day.year, day.month, 1)
    end = dt.date(day.year + (day.month == 12), 1 if day.month == 12 else day.month + 1, 1)
    return start, end


def local_epoch(day):
    return int(dt.datetime(day.year, day.month, day.day).timestamp())


def parse_timestamp(value):
    if not value:
        return None
    try:
        return dt.datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone()
    except ValueError:
        return None


def codex_roots(explicit=None):
    if explicit:
        yield Path(explicit).expanduser()
        return

    roots = []
    if os.environ.get("CODEX_HOME"):
        roots.append(Path(os.environ["CODEX_HOME"]))
    roots.append(Path.home() / ".codex")
    for key in ("USERPROFILE", "HOME"):
        if os.environ.get(key):
            roots.append(Path(os.environ[key]) / ".codex")

    seen = set()
    for root in roots:
        root = root.expanduser()
        key = str(root).lower()
        if key not in seen:
            seen.add(key)
            yield root


def iter_session_jsonl(root):
    for base in (root / "sessions", root / "archived_sessions"):
        if base.is_dir():
            yield from base.rglob("*.jsonl")


def empty_week(key, seen_at, month_start, month_end):
    monday = seen_at.date() - dt.timedelta(days=seen_at.weekday())
    return {
        "week": key,
        "start": max(monday, month_start).isoformat(),
        "end": min(monday + dt.timedelta(days=7), month_end).isoformat(),
        "snapshot_count": 0,
        "five_hour_max_percent": 0.0,
        "five_hour_latest_percent": None,
        "seven_day_max_percent": 0.0,
        "seven_day_latest_percent": None,
        "near_limit": False,
        "latest_seen_at": None,
        "_latest_seen_epoch": None,
        "_five_hour_latest_epoch": None,
        "_seven_day_latest_epoch": None,
    }


def update_latest(target, epoch_key, value_key, seen_epoch, used):
    if target[epoch_key] is None or seen_epoch > target[epoch_key]:
        target[epoch_key] = seen_epoch
        target[value_key] = used


def query_monthly_quota(month_start, month_end, codex_home=None):
    start_ts = local_epoch(month_start)
    end_ts = local_epoch(month_end)
    summary = {
        "snapshot_count": 0,
        "five_hour_max_percent": 0.0,
        "five_hour_latest_percent": None,
        "seven_day_max_percent": 0.0,
        "seven_day_latest_percent": None,
        "near_limit_week_count": 0,
        "threshold_percent": NEAR_LIMIT_PERCENT,
        "latest_seen_at": None,
        "weeks": [],
        "_latest_seen_epoch": None,
        "_five_hour_latest_epoch": None,
        "_seven_day_latest_epoch": None,
    }
    weeks = {}

    for root in codex_roots(codex_home):
        for path in iter_session_jsonl(root):
            try:
                lines = path.open("r", encoding="utf-8")
            except OSError:
                continue
            with lines:
                for line in lines:
                    if "rate_limits" not in line:
                        continue
                    try:
                        obj = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    seen_at = parse_timestamp(obj.get("timestamp"))
                    if not seen_at:
                        continue
                    seen_epoch = int(seen_at.timestamp())
                    if seen_epoch < start_ts or seen_epoch >= end_ts:
                        continue
                    rate_limits = (obj.get("payload") or {}).get("rate_limits") or {}
                    if not isinstance(rate_limits, dict):
                        continue

                    year, week, _ = seen_at.isocalendar()
                    key = f"{year}-W{week:02d}"
                    bucket = weeks.setdefault(key, empty_week(key, seen_at, month_start, month_end))
                    bucket["snapshot_count"] += 1
                    summary["snapshot_count"] += 1

                    if bucket["_latest_seen_epoch"] is None or seen_epoch > bucket["_latest_seen_epoch"]:
                        bucket["_latest_seen_epoch"] = seen_epoch
                        bucket["latest_seen_at"] = seen_at.isoformat(timespec="seconds")
                    if summary["_latest_seen_epoch"] is None or seen_epoch > summary["_latest_seen_epoch"]:
                        summary["_latest_seen_epoch"] = seen_epoch
                        summary["latest_seen_at"] = seen_at.isoformat(timespec="seconds")

                    for name, minutes, prefix in (("primary", 300, "five_hour"), ("secondary", 10080, "seven_day")):
                        window = rate_limits.get(name) or {}
                        if not isinstance(window, dict) or window.get("window_minutes") != minutes:
                            continue
                        try:
                            used = float(window["used_percent"])
                        except (KeyError, TypeError, ValueError):
                            continue
                        max_key = f"{prefix}_max_percent"
                        latest_key = f"{prefix}_latest_percent"
                        epoch_key = f"_{prefix}_latest_epoch"
                        bucket[max_key] = max(bucket[max_key], used)
                        summary[max_key] = max(summary[max_key], used)
                        update_latest(bucket, epoch_key, latest_key, seen_epoch, used)
                        update_latest(summary, epoch_key, latest_key, seen_epoch, used)

    for week in sorted(weeks.values(), key=lambda item: item["start"]):
        week["five_hour_max_percent"] = round(week["five_hour_max_percent"], 2)
        week["seven_day_max_percent"] = round(week["seven_day_max_percent"], 2)
        week["near_limit"] = week["seven_day_max_percent"] >= NEAR_LIMIT_PERCENT
        if week["near_limit"]:
            summary["near_limit_week_count"] += 1
        for key in list(week):
            if key.startswith("_"):
                del week[key]
        summary["weeks"].append(week)

    summary["five_hour_max_percent"] = round(summary["five_hour_max_percent"], 2)
    summary["seven_day_max_percent"] = round(summary["seven_day_max_percent"], 2)
    for key in list(summary):
        if key.startswith("_"):
            del summary[key]
    return summary


def report_payload(month_start, month_end, machine_id, codex_home=None):
    quota = query_monthly_quota(month_start, month_end, codex_home)
    if quota["snapshot_count"] == 0:
        fail(f"no Codex rate limit snapshots found for {month_start:%Y-%m}")
    return {
        "schema_version": 2,
        "source": "codex-jsonl",
        "app_type": "codex",
        "machine_id": machine_id,
        "generated_at": dt.datetime.now().astimezone().isoformat(timespec="seconds"),
        "period": {
            "type": "monthly",
            "start": month_start.isoformat(),
            "end": month_end.isoformat(),
        },
        "quota": quota,
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


def selected_month(args):
    if args.month and args.date:
        fail("use either --month or --date, not both")
    if args.month:
        return month_bounds(parse_month(args.month))
    if args.date:
        return month_bounds(parse_day(args.date))
    return month_bounds(dt.date.today())


def upload(args):
    machine_id = sanitize(args.machine_id or default_machine_id())
    month_start, month_end = selected_month(args)
    monthly = report_payload(month_start, month_end, machine_id, args.codex_home)

    repo = args.repo or os.environ.get("CODEX_USAGE_REPORTS_REPO") or DEFAULT_REPORTS_REPO
    branch = args.branch or os.environ.get("CODEX_USAGE_REPORTS_BRANCH") or DEFAULT_BRANCH
    workdir = Path(args.workdir).expanduser() if args.workdir else default_workdir()

    if args.dry_run:
        print(json.dumps(monthly, ensure_ascii=False, indent=2))
        return

    repo_dir = ensure_reports_repo(repo, branch, workdir)
    write_json(repo_dir / "usage" / "monthly" / f"{month_start.year:04d}" / f"{month_start.month:02d}" / f"{machine_id}.json", monthly)

    run(["git", "add", "usage"], cwd=repo_dir)
    status = run(["git", "status", "--porcelain"], cwd=repo_dir).stdout.strip()
    if not status:
        print("No quota changes to upload.")
        return

    run(["git", "commit", "-m", f"Update Codex quota for {machine_id} {month_start:%Y-%m}"], cwd=repo_dir)
    run(["git", "push", "-u", "origin", branch], cwd=repo_dir)
    print(f"Uploaded Codex quota for {machine_id} {month_start:%Y-%m} to {repo}.")


def self_test():
    with tempfile.TemporaryDirectory() as td:
        root = Path(td) / ".codex"
        sessions = root / "sessions" / "2026" / "06" / "02"
        sessions.mkdir(parents=True)
        log = sessions / "rollout.jsonl"
        rows = [
            ("2026-06-02T01:00:00Z", 20, 94),
            ("2026-06-03T01:00:00Z", 55, 96),
            ("2026-06-10T01:00:00Z", 10, 30),
            ("2026-07-01T01:00:00Z", 99, 99),
        ]
        log.write_text(
            "\n".join(
                json.dumps(
                    {
                        "timestamp": timestamp,
                        "type": "event_msg",
                        "payload": {
                            "type": "token_count",
                            "rate_limits": {
                                "primary": {"used_percent": five_hour, "window_minutes": 300},
                                "secondary": {"used_percent": seven_day, "window_minutes": 10080},
                            },
                        },
                    }
                )
                for timestamp, five_hour, seven_day in rows
            )
            + "\n",
            encoding="utf-8",
        )

        quota = query_monthly_quota(dt.date(2026, 6, 1), dt.date(2026, 7, 1), root)
        assert quota["snapshot_count"] == 3, quota
        assert quota["five_hour_max_percent"] == 55, quota
        assert quota["seven_day_max_percent"] == 96, quota
        assert quota["near_limit_week_count"] == 1, quota
        assert len(quota["weeks"]) == 2, quota
    print("self-test passed")


def main():
    parser = argparse.ArgumentParser(description="Upload local Codex monthly quota usage from Codex JSONL logs.")
    parser.add_argument("--month", help="month to upload, YYYY-MM; default current month")
    parser.add_argument("--date", help="choose the month containing this local day, YYYY-MM-DD")
    parser.add_argument("--machine-id", help="stable machine/account label; default hostname")
    parser.add_argument("--codex-home", help="explicit Codex home directory; default CODEX_HOME or ~/.codex")
    parser.add_argument("--repo", help="reports repo remote")
    parser.add_argument("--branch", help="reports repo branch")
    parser.add_argument("--workdir", help="local clone/cache directory for the reports repo")
    parser.add_argument("--dry-run", action="store_true", help="print JSON without cloning or pushing")
    parser.add_argument("--self-test", action="store_true", help="run a tiny JSONL aggregation test")
    args = parser.parse_args()

    if args.self_test:
        self_test()
    else:
        upload(args)


if __name__ == "__main__":
    main()
