#!/usr/bin/env python3
"""Exercise staged installation, consumer defaults import, and rejection boundaries."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import plistlib
import shutil
import subprocess
import sys
import tempfile
import uuid
from pathlib import Path

sys.dont_write_bytecode = True
import verify


class SmokeFailure(Exception):
    pass


def command(arguments: list[str], label: str, *, succeeds: bool = True) -> None:
    result = subprocess.run(
        arguments, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1"},
    )
    if (result.returncode == 0) != succeeds:
        # Subprocess output may contain fixture values. Keep all failures redacted.
        raise SmokeFailure(f"{label}: unexpected exit status {result.returncode}")


def snapshot(root: Path) -> dict[str, tuple[str, int]]:
    return {
        file.relative_to(root).as_posix(): (hashlib.sha256(file.read_bytes()).hexdigest(), file.stat().st_mode & 0o777)
        for file in root.rglob("*") if file.is_file()
    }


def rebase(value: object, home: Path) -> object:
    if isinstance(value, dict):
        return {key: rebase(item, home) for key, item in value.items()}
    if isinstance(value, list):
        return [rebase(item, home) for item in value]
    if isinstance(value, bytes):
        return rebase(json.loads(value), home)
    if isinstance(value, str) and (value == "~" or value.startswith("~/")):
        return str(home) + value[1:]
    return value


def refresh_manifest(root: Path, name: str) -> None:
    manifest_path = root / "manifest.json"
    manifest = json.loads(manifest_path.read_text())
    manifest["files"][name] = hashlib.sha256((root / name).read_bytes()).hexdigest()
    manifest_path.write_text(json.dumps(manifest, indent=2) + "\n")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument("--require-defaults", action="store_true", help="fail unless the macOS defaults roundtrip runs")
    parser.add_argument("--require-running-iterm", action="store_true", help="with iTerm2 already open, exercise only its read-only installation guard before other checks")
    args = parser.parse_args()
    root = args.root.resolve()
    try:
        findings = verify.validate(root)
        if findings:
            raise SmokeFailure("baseline package validation failed")
        if args.require_defaults and sys.platform != "darwin":
            raise SmokeFailure("macOS defaults roundtrip is required")
        if args.require_running_iterm:
            if sys.platform != "darwin":
                raise SmokeFailure("running iTerm2 guard requires macOS")
            import install as installer
            try:
                installer.refuse_running_iterm()
            except installer.InstallError:
                print("Running iTerm2 installation guard refused the live application.")
            else:
                raise SmokeFailure("running iTerm2 was not rejected by the installation guard")
        with tempfile.TemporaryDirectory(prefix="iterm2-smoke-") as temporary:
            scratch = Path(temporary).resolve()
            home = scratch / "target-home"
            home.mkdir()
            original_zshrc = b"export EDITOR=vi\n"
            (home / ".zshrc").write_bytes(original_zshrc)
            (home / ".zshrc").chmod(0o640)
            install = [sys.executable, str(root / "scripts/install.py")]
            command(install + ["--stage", str(home)], "first staged install")
            first = snapshot(home)
            if not first:
                raise SmokeFailure("stage created no installation files")
            command(install + ["--stage", str(home)], "repeat staged install")
            if first != snapshot(home):
                raise SmokeFailure("repeat staged install changed package bytes or file permissions")
            staged_plist = home / "Library/Preferences/com.googlecode.iterm2.plist"
            source_preferences = plistlib.loads((root / verify.PREFERENCES).read_bytes())
            staged_preferences = plistlib.loads(staged_plist.read_bytes())
            if rebase(staged_preferences, home) != rebase(source_preferences, home):
                raise SmokeFailure("staged preferences differ from rebased package preferences")
            for font in (root / "fonts").glob("*.otf"):
                if (home / "Library/Fonts" / font.name).read_bytes() != font.read_bytes():
                    raise SmokeFailure("staged font bytes differ from package")
            shell_destination = home / ".config/iterm2-portable"
            for name in ("appearance.zsh", "p10k.zsh", "p10k-lean.zsh"):
                if (shell_destination / name).read_bytes() != (root / "shell" / name).read_bytes():
                    raise SmokeFailure("staged shell bytes differ from package")
            if "appearance.zsh" not in (home / ".zshrc").read_text():
                raise SmokeFailure("staged shell loader is missing")
            if sys.platform == "darwin":
                domain = "org.iterm2portable.smoke." + uuid.uuid4().hex
                exported = scratch / "defaults-roundtrip.plist"
                try:
                    command(["defaults", "import", domain, str(staged_plist)], "temporary-domain defaults import")
                    command(["defaults", "export", domain, str(exported)], "temporary-domain defaults export")
                    if plistlib.loads(exported.read_bytes()) != staged_preferences:
                        raise SmokeFailure("defaults import/export changed preference values")
                finally:
                    command(["defaults", "delete", domain], "temporary-domain defaults cleanup")
                print("macOS temporary-domain defaults roundtrip passed.")
            else:
                print("macOS defaults roundtrip deferred to the macOS CI job.")
            backup_root = home / "Library/Application Support/iterm2-portable/backups"
            records = list(backup_root.glob("*/backup.json"))
            if len(records) != 1:
                raise SmokeFailure("repeat installation created an unexpected backup count")
            command(install + ["--restore", str(records[0].parent)], "staged backup restore")
            restored_zshrc = home / ".zshrc"
            if restored_zshrc.read_bytes() != original_zshrc or restored_zshrc.stat().st_mode & 0o777 != 0o640:
                raise SmokeFailure("staged restore did not preserve existing shell bytes and permissions")
            for name in first:
                path = home / name
                if name != ".zshrc" and not path.is_relative_to(backup_root) and path.exists():
                    raise SmokeFailure("staged restore retained an originally absent installation file")

            fixture = scratch / "package"
            shutil.copytree(root, fixture, ignore=shutil.ignore_patterns(".git"))
            prefs = fixture / verify.PREFERENCES
            original = prefs.read_bytes()
            cases = (
                ("decoded-personal-path", "personal-absolute-path", json.dumps({"path": "/" + "Users/" + "private-example"}).encode()),
                ("decoded-credential", "github-token", json.dumps({"value": "gh" + "p_" + "A" * 36}).encode()),
                ("invalid-json-data", "invalid-json-plist-data", bytes([255, 0, 1])),
                ("decoded-private-address", "private-network-address", json.dumps({"address": ".".join(map(str, (192, 168, 4, 5)))}).encode()),
                ("json-string-path", "personal-absolute-path", json.dumps(json.dumps({"path": "/" + "home/" + "private-example"})).encode()),
            )
            for label, rule, payload in cases:
                document = plistlib.loads(original)
                document["Workgroups"] = payload
                prefs.write_bytes(plistlib.dumps(document, fmt=plistlib.FMT_BINARY))
                refresh_manifest(fixture, verify.PREFERENCES)
                findings = verify.validate(fixture)
                if not any(f": {rule} at " in finding for finding in findings):
                    raise SmokeFailure(f"{label}: privacy fixture was not rejected by its rule")
                joined = "\n".join(findings)
                if "private-example" in joined or "A" * 36 in joined:
                    raise SmokeFailure(f"{label}: scanner diagnostic exposed fixture contents")
                command([sys.executable, str(fixture / "scripts/verify.py")], label, succeeds=False)
            prefs.write_bytes(original)
            refresh_manifest(fixture, verify.PREFERENCES)
            if verify.validate(fixture):
                raise SmokeFailure("privacy fixture restoration failed")
            ignored_private_file = fixture / ".env"
            ignored_private_file.write_text("gh" + "p_" + "B" * 36 + "\n")
            findings = verify.validate(fixture)
            if not any(": sensitive-file at " in finding for finding in findings) or not any(": github-token at " in finding for finding in findings):
                raise SmokeFailure("ignored private file escaped all-bytes scanning")
            if "B" * 36 in "\n".join(findings):
                raise SmokeFailure("ignored private file scanner diagnostic exposed fixture contents")
            command([sys.executable, str(fixture / "scripts/verify.py")], "ignored private file rejection", succeeds=False)
            ignored_private_file.unlink()
            target = fixture / "shell/p10k.zsh"
            target.write_bytes(target.read_bytes() + b"\n# integrity fixture\n")
            command([sys.executable, str(fixture / "scripts/verify.py")], "manifest tamper validation", succeeds=False)
            rejected_home = scratch / "rejected-home"
            command([sys.executable, str(fixture / "scripts/install.py"), "--stage", str(rejected_home)], "installer manifest tamper rejection", succeeds=False)
            if rejected_home.exists() and any(rejected_home.rglob("*")):
                raise SmokeFailure("rejected install wrote target files")
        print("Staging, repeat-install idempotency, lossless restore, privacy fixtures, and tamper rejection passed.")
        return 0
    except (SmokeFailure, OSError, ValueError, TypeError, plistlib.InvalidFileException) as error:
        # Expected smoke failures use fixed labels, never scanned values or contents.
        label = str(error) if isinstance(error, SmokeFailure) else type(error).__name__
        print(f"Smoke failed: {label}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
