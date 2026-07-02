---
name: setup-reports-ssh-key
description: Generate or reuse this machine's SSH public key for granting write access to the private Codex usage reports repository. Use when the user asks to configure a new machine, generate an SSH key/public key/deploy key, allow a machine to upload Codex usage reports, or set up GitHub reports repo upload permissions.
---

# Setup Reports SSH Key

Run the bundled script from this skill's plugin root:

```powershell
python scripts/setup_reports_ssh_key.py
```

Behavior:

- Finds `ssh-keygen` from PATH or Git for Windows.
- Creates or reuses `~/.ssh/codex_usage_reports_ed25519`.
- Prints only the public key and setup instructions; never print the private key.
- Tells the user to add the public key to `Dipsy524/codex-usage-reports` as a GitHub Deploy key with `Allow write access`.
- If using the dedicated key, tell the user to add the printed `~/.ssh/config` host alias and set `CODEX_USAGE_REPORTS_REPO` to the printed alias remote, then restart Codex Desktop.

Useful options:

```powershell
python scripts/setup_reports_ssh_key.py --force
python scripts/setup_reports_ssh_key.py --key-name codex_usage_reports_ed25519
python scripts/setup_reports_ssh_key.py --self-test
```
