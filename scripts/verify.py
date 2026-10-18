#!/usr/bin/env python3
"""Validate the portable package, including decoded preferences and release bytes."""
from __future__ import annotations

import argparse
import hashlib
import ipaddress
import json
import plistlib
import re
import subprocess
import sys
import tempfile
import zipfile
from pathlib import Path, PurePosixPath

sys.dont_write_bytecode = True

ROOT = Path(__file__).resolve().parents[1]
PREFERENCES = "preferences/com.googlecode.iterm2.plist"
PROFILES = "profiles/profiles.json"
JSON_DATA_KEYS = frozenset({"Workgroups", "CodeReviewSavedPrompts", "StatusPriorityEntries"})
PAYLOAD_DIRS = frozenset({"preferences", "profiles", "fonts", "shell"})
PACKAGE_FILES = frozenset({
    "README.md", "LICENSE", "LICENSE.md", "manifest.json", "install.command", ".gitignore",
    ".github/workflows/ci.yml", "scripts/install.py", "scripts/verify.py", "scripts/smoke.py",
    "scripts/icon_probe.swift",
    PREFERENCES, PROFILES, "fonts/FONT-LICENSE.md", "FONT-LICENSE.md",
    "shell/p10k.zsh", "shell/p10k-lean.zsh", "shell/appearance.zsh",
    "shell/POWERLEVEL10K-LICENSE.txt",
    "fonts/ATTRIBUTION.md",
    "fonts/NERD-FONTS-LICENSE.txt",
})
FONT_FILES = frozenset({
    "fonts/ComicShannsMonoNerdFont-Regular.otf", "fonts/ComicShannsMonoNerdFont-Bold.otf",
    "fonts/ComicShannsMonoNerdFontMono-Regular.otf", "fonts/ComicShannsMonoNerdFontMono-Bold.otf",
})
SENSITIVE_PART = re.compile(
    r"(?:^|[._-])(?:backup|backups|private|history|credentials|secrets?|keychain|"
    r"savedstate|chat|session|sessions|export|exports|staging|stage)(?:$|[._-])", re.I
)
PRIVATE_KEYS = re.compile(
    r"(?:password|passwd|secret|access.?token|refresh.?token|auth.?token|api.?key|"
    r"authorization|installation.?id|machine.?id|computer.?name|host.?name|"
    r"username|session.?history|command.?history|recent.?commands|saved.?state)", re.I
)
FORBIDDEN_KEYS = frozenset({
    "NoSyncInstallationIdentifier", "NoSyncLaunchExperience", "NoSyncAllAppVersions",
    "NoSyncHaveShownPromoForVersion", "NoSyncLastAppVersion", "NoSyncTimeOfFirstLaunch",
    "NoSyncTimeOfLastLaunch", "NoSyncUserHasSeenNewSessionAnnouncement",
    "NoSyncUserHasSeenNewVersion", "NSNavLastRootDirectory", "NSNavRecentPlaces",
    "NSRecentDocuments", "WebKitLocalStorageDatabasePathPreferenceKey",
    "History", "Shell History", "CommandHistory", "PasteHistory", "PasteboardHistory",
    "RecentCommands", "RecentDirectories", "Recent Directories", "RecentHosts", "KnownHosts",
    "SavedState", "Saved State",
})
PORTABLE_NOSYNC_KEYS = frozenset({
    "NoSyncDisableClaudeWatcher", "NoSyncIgnoreSystemWindowRestoration",
    "NoSyncNeverAllowPaste", "NoSyncTipsDisabled", "NoSyncToolbeltProportions",
    "NoSyncWindowRestoresWorkspaceAtLaunch",
})
PATTERNS = (
    ("personal-absolute-path", re.compile(r"(?i)(?:/(?:Users|home)/[^\s/\"'<>]+|[A-Z]:[\\/](?:Users|Documents and Settings)[\\/][^\s\\/\"']+)")),
    ("private-filesystem-path", re.compile(r"/(?:Volumes/|(?:private/)?var/(?:folders|root)/)")),
    ("local-machine-name", re.compile(r"(?i)\b[a-z0-9][a-z0-9-]*\.(?:local|lan|internal)\b")),
    ("private-key", re.compile(r"-----BEGIN (?:[A-Z0-9 ]+ )?PRIVATE KEY-----")),
    ("github-token", re.compile(r"\b(?:gh[pousr]_[A-Za-z0-9]{20,}|github_pat_[A-Za-z0-9_]{20,})\b")),
    ("cloud-access-key", re.compile(r"\b(?:AKIA|ASIA)[A-Z0-9]{16}\b")),
    ("provider-token", re.compile(r"\b(?:sk-(?:proj-|ant-)?[A-Za-z0-9_-]{20,}|xox[baprs]-[A-Za-z0-9-]{16,}|AIza[A-Za-z0-9_-]{30,})\b")),
    ("credential-url", re.compile(r"[a-z][a-z0-9+.-]*://[^\s/@:]+:[^\s/@]+@", re.I)),
    ("bearer-credential", re.compile(r"\bBearer\s+[A-Za-z0-9_.~-]{20,}", re.I)),
    ("jwt-credential", re.compile(r"\beyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\b")),
    ("credential-assignment", re.compile(r"(?i)\b(?:password|passwd|api[_-]?key|access[_-]?token|refresh[_-]?token|client[_-]?secret)\s*[=:]\s*[\"']?[A-Za-z0-9_./+~-]{12,}")),
)
IPV4 = re.compile(r"(?<![\w.])(?:\d{1,3}\.){3}\d{1,3}(?![\w.])")
IPV6 = re.compile(r"(?<![\w:])(?:[0-9a-fA-F]{0,4}:){2,}[0-9a-fA-F:]{0,39}(?![\w:])")
SHA256 = re.compile(r"[0-9a-f]{64}\Z")


def package_path(name: str) -> bool:
    return name in PACKAGE_FILES or name in FONT_FILES


def safe_name(name: str) -> str:
    return name if package_path(name) else "<unexpected-file>"


def validate(root: Path) -> list[str]:
    """Return only redacted findings. Never include scanned values in diagnostics."""
    root = Path(root)
    errors: list[str] = []

    def issue(name: str, rule: str, location: str = "$") -> None:
        message = f"{safe_name(name)}: {rule} at {location}"
        if message not in errors:
            errors.append(message)

    def text(value: str, name: str, location: str) -> None:
        if name == "shell/p10k-lean.zsh" and location == "$bytes":
            # This exact public upstream comment documents asdf's generic global path.
            # Preserve the baseline; continue scanning every other byte and every secret rule.
            upstream_example = '  # - global  `asdf current` says "set by /' + 'home/' + 'username/file"'
            value = value.replace(upstream_example, "  # Public upstream asdf global-path example.")
            upstream_local_example = '  # - local   `asdf current` says "set by /some/not/' + 'home/' + 'directory/file"'
            value = value.replace(upstream_local_example, "  # Public upstream asdf local-path example.")
        for rule, pattern in PATTERNS:
            if pattern.search(value):
                issue(name, rule, location)
        for pattern in (IPV4, IPV6):
            for match in pattern.finditer(value):
                try:
                    address = ipaddress.ip_address(match.group())
                except ValueError:
                    continue
                if address.is_private or address.is_loopback or address.is_link_local:
                    issue(name, "private-network-address", location)
        # JSON stored inside strings must receive the same structural checks.
        stripped = value.strip()
        if stripped.startswith(("{", "[")):
            try:
                nested = json.loads(stripped)
            except (ValueError, RecursionError):
                pass
            else:
                walk(nested, name, location + ".json")

    def walk(value: object, name: str, location: str = "$", data_key: str = "") -> None:
        if isinstance(value, dict):
            for index, (key, item) in enumerate(value.items()):
                child = f"{location}[field#{index}]"
                if not isinstance(key, str):
                    issue(name, "non-string-object-key", child)
                    continue
                text(key, name, child)
                if key in JSON_DATA_KEYS and not isinstance(item, bytes):
                    issue(name, "invalid-json-plist-data-type", child)
                if key in FORBIDDEN_KEYS:
                    issue(name, "source-state-preference", child)
                if PRIVATE_KEYS.search(key) and item not in (None, "", [], {}, False):
                    issue(name, "private-preference-field", child)
                if key.startswith("NoSync") and key not in PORTABLE_NOSYNC_KEYS:
                    issue(name, "source-state-preference", child)
                if key in {"Dynamic Profile Filename", "Dynamic Profile Parent Name", "Is Dynamic Profile", "Rewritable"}:
                    issue(name, "dynamic-profile-source", child)
                if key in {"Working Directory", "Log Directory", "Background Image Location"}:
                    if isinstance(item, str) and item and not (item == "~" or item.startswith("~/")):
                        issue(name, "nonportable-preference-path", child)
                walk(item, name, child, key)
        elif isinstance(value, list):
            for index, item in enumerate(value):
                walk(item, name, f"{location}[{index}]", data_key)
        elif isinstance(value, bytes):
            if data_key not in JSON_DATA_KEYS:
                issue(name, "opaque-plist-data", location)
                return
            try:
                decoded = value.decode("utf-8")
                nested = json.loads(decoded)
            except (UnicodeError, ValueError, RecursionError):
                issue(name, "invalid-json-plist-data", location)
                return
            text(decoded, name, location + ".data")
            walk(nested, name, location + ".data")
        elif isinstance(value, str):
            text(value, name, location)

    if not root.is_dir():
        return ["<package>: missing-package-root at $"]
    files: dict[str, Path] = {}
    for path in sorted(root.rglob("*")):
        relative = path.relative_to(root)
        if ".git" in relative.parts:
            continue
        name = relative.as_posix()
        if path.is_symlink():
            issue(name, "symlink-not-publishable")
            continue
        if not path.is_file():
            continue
        files[name] = path
        if any(SENSITIVE_PART.search(part) for part in relative.parts) or path.name in {
            ".env", ".DS_Store", ".zsh_history", ".bash_history", "id_rsa", "id_ed25519",
        } or "__pycache__" in relative.parts or path.suffix in {".pyc", ".db", ".sqlite", ".sqlite3", ".key", ".pem"}:
            issue(name, "sensitive-file")
        if not package_path(name):
            issue(name, "unexpected-package-file")
        try:
            raw = path.read_bytes()
        except OSError:
            issue(name, "unreadable-file")
            continue
        if relative.parts[0] == "fonts" and path.suffix == ".otf":
            if not raw.startswith(b"OTTO"):
                issue(name, "invalid-opentype-font")
            continue  # Exact manifest hashes bind every byte of font binaries.
        if path.suffix == ".plist":
            try:
                document = plistlib.loads(raw)
            except (ValueError, TypeError, plistlib.InvalidFileException, OverflowError):
                issue(name, "invalid-plist")
            else:
                walk(document, name)
            # Binary plist strings and data are covered by structural decoding.
            if raw.startswith(b"bplist"):
                continue
        try:
            decoded = raw.decode("utf-8")
        except UnicodeError:
            issue(name, "unexpected-binary-file")
            continue
        text(decoded, name, "$bytes")
        if path.suffix == ".json":
            try:
                document = json.loads(decoded)
            except (ValueError, RecursionError):
                issue(name, "invalid-json")
            else:
                walk(document, name)

    def load(name: str, loader):
        try:
            return loader(files[name].read_bytes())
        except (KeyError, OSError, ValueError, TypeError, plistlib.InvalidFileException, OverflowError):
            issue(name, "missing-or-invalid-required-file")
            return None

    manifest = load("manifest.json", json.loads)
    if not isinstance(manifest, dict) or manifest.get("format_version") != 1:
        issue("manifest.json", "invalid-manifest-format")
        manifest = {}
    if manifest.get("source_iterm2_version") != "3.7.6" or manifest.get("font_version") != "3.5.1":
        issue("manifest.json", "unexpected-package-version")
    hashes = manifest.get("files")
    if not isinstance(hashes, dict):
        issue("manifest.json", "invalid-manifest-files")
        hashes = {}
    payloads = {name for name in files if PurePosixPath(name).parts[0] in PAYLOAD_DIRS}
    if set(hashes) != payloads:
        issue("manifest.json", "manifest-file-set-mismatch")
    for name, digest in hashes.items():
        if not isinstance(name, str) or name not in files or not isinstance(digest, str) or not SHA256.fullmatch(digest):
            issue("manifest.json", "invalid-manifest-entry")
            continue
        if hashlib.sha256(files[name].read_bytes()).hexdigest() != digest:
            issue(name, "manifest-hash-mismatch")
    required_files = (
        PREFERENCES, PROFILES, "shell/appearance.zsh", "shell/p10k.zsh", "shell/p10k-lean.zsh",
        "fonts/FONT-LICENSE.md", "shell/POWERLEVEL10K-LICENSE.txt", "README.md", "install.command",
        "scripts/install.py", "scripts/verify.py", "scripts/smoke.py", ".github/workflows/ci.yml", ".gitignore",
    )
    for required in required_files:
        if required not in files:
            issue(required, "missing-required-file")
    fonts = [name for name in payloads if name.startswith("fonts/") and name.endswith(".otf")]
    if len(fonts) != 4:
        issue("manifest.json", "expected-four-font-files")
    preferences = load(PREFERENCES, plistlib.loads)
    profiles_doc = load(PROFILES, json.loads)
    if not isinstance(preferences, dict) or not isinstance(profiles_doc, dict):
        issue(PREFERENCES, "invalid-preferences-or-profiles")
        return errors
    profiles = preferences.get("New Bookmarks")
    if not isinstance(profiles, list) or not profiles:
        issue(PREFERENCES, "missing-profiles")
        return errors
    if profiles_doc.get("Profiles") != profiles:
        issue(PROFILES, "profile-plist-mismatch")
    guids = [profile.get("Guid") if isinstance(profile, dict) else None for profile in profiles]
    if any(not isinstance(guid, str) or not re.fullmatch(r"[0-9A-Fa-f]{8}(?:-[0-9A-Fa-f]{4}){3}-[0-9A-Fa-f]{12}", guid) for guid in guids) or len(set(str(guid) for guid in guids)) != len(guids):
        issue(PREFERENCES, "invalid-or-duplicate-profile-guid")
    if preferences.get("Default Bookmark Guid") not in guids:
        issue(PREFERENCES, "invalid-default-profile-guid")
    return errors


def tracked_package_files(root: Path) -> list[str]:
    result = subprocess.run(
        ["git", "-C", str(root), "ls-files", "-z"], check=True,
        stdout=subprocess.PIPE, stderr=subprocess.PIPE,
    )
    names = result.stdout.decode("utf-8").split("\0")
    names = [name for name in names if name]
    if not names or any(not package_path(name) for name in names):
        raise ValueError("tracked package file set is invalid")
    required = {"README.md", "manifest.json", "install.command", "scripts/install.py", "scripts/verify.py", "scripts/smoke.py", ".github/workflows/ci.yml"}
    if not required.issubset(names):
        raise ValueError("required package files are not tracked")
    return sorted(names)


def validate_archive(archive: Path) -> list[str]:
    try:
        with zipfile.ZipFile(archive) as bundle, tempfile.TemporaryDirectory(prefix="iterm2-archive-") as temporary:
            names = bundle.namelist()
            if len(names) != len(set(names)) or any(not package_path(name) for name in names):
                return ["<archive>: unexpected-or-duplicate-member at $"]
            for entry in bundle.infolist():
                if entry.file_size > 64 * 1024 * 1024 or (entry.external_attr >> 16) & 0o170000 == 0o120000:
                    return ["<archive>: unsafe-member at $"]
            bundle.extractall(temporary)
            return validate(Path(temporary))
    except (OSError, ValueError, zipfile.BadZipFile, RuntimeError):
        return ["<archive>: invalid-archive at $"]


def build_archive(root: Path, archive: Path) -> list[str]:
    errors = validate(root)
    if errors:
        return errors
    try:
        names = tracked_package_files(root)
        # Keep outputs outside the tree so every repository byte remains scanned.
        if archive.resolve().is_relative_to(root.resolve()):
            return ["<archive>: output-must-be-outside-package at $"]
        with zipfile.ZipFile(archive, "w", compression=zipfile.ZIP_DEFLATED) as bundle:
            for name in names:
                bundle.write(root / name, name)
    except (OSError, ValueError, subprocess.CalledProcessError, UnicodeError):
        return ["<archive>: archive-build-failed at $"]
    errors = validate_archive(archive)
    if errors:
        archive.unlink(missing_ok=True)
    return errors


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=ROOT)
    group = parser.add_mutually_exclusive_group()
    group.add_argument("--archive", type=Path, help="validate a release ZIP and all extracted bytes")
    group.add_argument("--build-archive", type=Path, help="package explicit Git-tracked files and validate the ZIP")
    args = parser.parse_args()
    errors = validate_archive(args.archive) if args.archive else (
        build_archive(args.root, args.build_archive) if args.build_archive else validate(args.root)
    )
    if errors:
        print("Package validation failed:", file=sys.stderr)
        for error in errors:
            print(f"  {error}", file=sys.stderr)
        return 1
    print("Package privacy and integrity validation passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
