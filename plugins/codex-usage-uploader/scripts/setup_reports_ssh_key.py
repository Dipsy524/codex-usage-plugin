#!/usr/bin/env python3
import argparse
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path


DEFAULT_KEY_NAME = "codex_usage_reports_ed25519"
DEFAULT_COMMENT = "codex-usage-reports"
REPORTS_REPO = "Dipsy524/codex-usage-reports"
SSH_ALIAS = "github-codex-usage"


def fail(message):
    print(f"error: {message}", file=sys.stderr)
    raise SystemExit(1)


def find_ssh_keygen():
    found = shutil.which("ssh-keygen")
    if found:
        return found

    roots = []
    git = shutil.which("git")
    if git:
        path = Path(git).resolve()
        roots.extend(path.parents)

    if os.name == "nt":
        roots.extend(Path(p) for p in (os.environ.get("ProgramFiles"), os.environ.get("ProgramFiles(x86)")) if p)

    seen = set()
    for root in roots:
        key = str(root).lower()
        if key in seen:
            continue
        seen.add(key)
        for rel in ("usr/bin/ssh-keygen.exe", "mingw64/bin/ssh-keygen.exe", "OpenSSH/ssh-keygen.exe"):
            candidate = root / rel
            if candidate.is_file():
                return str(candidate)
    return None


def key_paths(home, key_name):
    key_path = home / ".ssh" / key_name
    return key_path, Path(str(key_path) + ".pub")


def generate_key(home, key_name, comment, force=False):
    ssh_keygen = find_ssh_keygen()
    if not ssh_keygen:
        fail("ssh-keygen not found. Install Git for Windows or OpenSSH, then rerun this script.")

    key_path, pub_path = key_paths(home, key_name)
    if key_path.exists() and pub_path.exists() and not force:
        return False, key_path, pub_path, pub_path.read_text(encoding="utf-8").strip()
    if (key_path.exists() or pub_path.exists()) and not force:
        fail(f"incomplete key pair exists at {key_path}; rerun with --force to replace it")

    key_path.parent.mkdir(parents=True, exist_ok=True)
    if force:
        key_path.unlink(missing_ok=True)
        pub_path.unlink(missing_ok=True)

    result = subprocess.run(
        [ssh_keygen, "-t", "ed25519", "-C", comment, "-f", str(key_path), "-N", ""],
        text=True,
        capture_output=True,
    )
    if result.returncode != 0:
        fail((result.stderr or result.stdout).strip())
    return True, key_path, pub_path, pub_path.read_text(encoding="utf-8").strip()


def print_result(created, key_path, pub_path, public_key):
    action = "created" if created else "exists"
    print(f"SSH key {action}.")
    print()
    print(f"Private key path: {key_path}")
    print(f"Public key path:  {pub_path}")
    print()
    print("Public key:")
    print(public_key)
    print()
    print("Next steps:")
    print(f"1. Open GitHub repo: {REPORTS_REPO}")
    print("2. Go to Settings -> Deploy keys -> Add deploy key")
    print("3. Paste the public key above")
    print("4. Enable: Allow write access")
    print()
    print("If this machine should use this dedicated key, add this to ~/.ssh/config:")
    print(f"Host {SSH_ALIAS}")
    print("  HostName github.com")
    print("  User git")
    print(f"  IdentityFile {key_path}")
    print("  IdentitiesOnly yes")
    print()
    print("Then set this environment variable and restart Codex Desktop:")
    print(f'setx CODEX_USAGE_REPORTS_REPO "git@{SSH_ALIAS}:{REPORTS_REPO}.git"')


def self_test():
    with tempfile.TemporaryDirectory() as td:
        created, key_path, pub_path, public_key = generate_key(Path(td), "test_key", "test", force=False)
        assert created, public_key
        assert key_path.is_file(), key_path
        assert pub_path.is_file(), pub_path
        assert public_key.startswith("ssh-ed25519 "), public_key
    print("self-test passed")


def main():
    parser = argparse.ArgumentParser(description="Generate an SSH deploy key for the private Codex usage reports repo.")
    parser.add_argument("--key-name", default=DEFAULT_KEY_NAME, help=f"key filename under ~/.ssh; default {DEFAULT_KEY_NAME}")
    parser.add_argument("--comment", default=DEFAULT_COMMENT, help=f"SSH key comment; default {DEFAULT_COMMENT}")
    parser.add_argument("--home", default=str(Path.home()), help="home directory; default current user home")
    parser.add_argument("--force", action="store_true", help="replace the existing key pair with the same key name")
    parser.add_argument("--self-test", action="store_true", help="generate a temporary key and verify output")
    args = parser.parse_args()

    if args.self_test:
        self_test()
        return

    created, key_path, pub_path, public_key = generate_key(Path(args.home).expanduser(), args.key_name, args.comment, args.force)
    print_result(created, key_path, pub_path, public_key)


if __name__ == "__main__":
    main()
