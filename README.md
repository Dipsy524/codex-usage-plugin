# Codex Usage Uploader

Codex plugin for uploading local Codex usage summarized from CC Switch.

Install this marketplace:

```powershell
codex plugin marketplace add Dipsy524/codex-usage-plugin
```

The upload skill reads `~/.cc-switch/cc-switch.db` and pushes JSON summaries to:

```text
git@github.com:Dipsy524/codex-usage-reports.git
```

Each machine needs write access to that private reports repository, usually via a GitHub deploy key with write access.
