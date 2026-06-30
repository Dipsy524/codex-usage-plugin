---
name: upload-codex-usage
description: Upload this machine's Codex usage summary from the local CC Switch SQLite database to the fixed GitHub reports repository. Use when the user asks to upload, sync, push, report, or record local Codex/CC Switch usage.
---

# Upload Codex Usage

Run the bundled script from this skill's plugin root:

```powershell
python scripts/upload_usage.py
```

Behavior:

- Requires `~/.cc-switch/cc-switch.db`; fail if CC Switch is not installed or has not created the database.
- Reads only `proxy_request_logs` for `app_type = 'codex'`.
- Uploads this machine's daily and month-to-date JSON summaries to `git@github.com:Dipsy524/codex-usage-reports.git`.
- Uses the local `git` credential or SSH deploy key. If `git push` fails, report that the machine lacks write access to the private reports repo.

Useful options:

```powershell
python scripts/upload_usage.py --date 2026-06-30
python scripts/upload_usage.py --machine-id office-pc-01
python scripts/upload_usage.py --dry-run
python scripts/upload_usage.py --self-test
```

Environment overrides:

- `CC_SWITCH_DB`: explicit path to `cc-switch.db`.
- `CODEX_USAGE_MACHINE_ID`: stable machine label for report filenames.
- `CODEX_USAGE_REPORTS_REPO`: reports repo remote, default `git@github.com:Dipsy524/codex-usage-reports.git`.
- `CODEX_USAGE_REPORTS_BRANCH`: reports repo branch, default `main`.
- `CODEX_USAGE_WORKDIR`: local clone/cache path for the reports repo.
