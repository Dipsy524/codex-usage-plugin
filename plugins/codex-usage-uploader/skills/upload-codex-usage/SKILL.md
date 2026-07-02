---
name: upload-codex-usage
description: Upload this machine's monthly Codex quota usage from local Codex JSONL session logs to the fixed GitHub reports repository. Use when the user asks to upload, sync, push, report, record, or统计本机某月 Codex 额度、周额度、5小时额度、7天额度使用情况.
---

# Upload Codex Usage

Run the bundled script from this skill's plugin root:

```powershell
python scripts/upload_usage.py
```

Behavior:

- Reads Codex JSONL session logs under `CODEX_HOME`, `~/.codex/sessions`, and `~/.codex/archived_sessions`.
- Reads only `rate_limits` snapshots; do not upload prompts, responses, token totals, or cost.
- Uploads this machine's monthly quota summary grouped by natural weeks to `git@github.com:Dipsy524/codex-usage-reports.git`.
- Requires a machine/account label from `CODEX_USAGE_MACHINE_ID` or `--machine-id`; if missing, stop and tell the user: `请提供您的机器名称`.
- When the user provides the name, persist it before uploading. On Windows run `setx CODEX_USAGE_MACHINE_ID "<name>"` and also set `$env:CODEX_USAGE_MACHINE_ID="<name>"` for the current PowerShell session, then rerun the upload. Use that same name for future uploads.
- Uses the local `git` credential or SSH deploy key. If `git push` fails, report that the machine lacks write access to the private reports repo.

Useful options:

```powershell
python scripts/upload_usage.py --month 2026-06
python scripts/upload_usage.py --date 2026-06-30
python scripts/upload_usage.py --machine-id office-pc-01
python scripts/upload_usage.py --dry-run
python scripts/upload_usage.py --self-test
```

Environment overrides:

- `CODEX_USAGE_MACHINE_ID`: stable machine/account label for report filenames.
- `CODEX_USAGE_REPORTS_REPO`: reports repo remote, default `git@github.com:Dipsy524/codex-usage-reports.git`.
- `CODEX_USAGE_REPORTS_BRANCH`: reports repo branch, default `main`.
- `CODEX_USAGE_WORKDIR`: local clone/cache path for the reports repo.
