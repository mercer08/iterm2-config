#!/usr/bin/env python3
"""Install the portable bundle, or restore the files saved before an install."""
from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import os
from pathlib import Path
import plistlib
import shutil
import stat
import subprocess
import sys
import tempfile
import re
import uuid
from xml.parsers.expat import ExpatError

sys.dont_write_bytecode = True

ROOT = Path(__file__).resolve().parent.parent
DOMAIN = "com.googlecode.iterm2"
PREFERENCES = Path("Library/Preferences") / (DOMAIN + ".plist")
BACKUPS = Path("Library/Application Support/iterm2-portable/backups")
SHELL_DIR = Path(".config/iterm2-portable")
BLOCK_START = "# >>> iterm2-portable >>>"
BLOCK_END = "# <<< iterm2-portable <<<"
BLOCK = (BLOCK_START + '\n[[ -r "$HOME/.config/iterm2-portable/appearance.zsh" ]] && '
         'source "$HOME/.config/iterm2-portable/appearance.zsh"\n' + BLOCK_END + "\n").encode()
FORMULAE = ("powerlevel10k", "zsh-autosuggestions", "zsh-syntax-highlighting")


class InstallError(Exception):
    """A failure safe to display without printing private preference values."""


def digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def within(path: Path, directory: Path) -> bool:
    return path == directory or directory in path.parents


def run(args: list[str], *, allow_missing: bool = False) -> subprocess.CompletedProcess:
    try:
        result = subprocess.run(args, stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=False)
    except OSError as exc:
        raise InstallError(f"Cannot run {Path(args[0]).name}: {exc.strerror}.") from exc
    if result.returncode and not allow_missing:
        raise InstallError(f"{Path(args[0]).name} failed (exit {result.returncode}); no command output is displayed because it may contain private settings.")
    return result


def load_plist(data: bytes, label: str) -> dict:
    try:
        value = plistlib.loads(data)
    except (ValueError, TypeError, OverflowError, ExpatError, plistlib.InvalidFileException) as exc:
        raise InstallError(f"Cannot decode {label} as a property list.") from exc
    if not isinstance(value, dict):
        raise InstallError(f"{label} must contain a preference dictionary.")
    return value


def validate_bundle() -> dict[str, bytes]:
    verifier = ROOT / "scripts/verify.py"
    if not verifier.is_file():
        raise InstallError("Bundle is incomplete: scripts/verify.py is missing.")
    result = run([sys.executable, "-B", str(verifier), "--root", str(ROOT)], allow_missing=True)
    if result.returncode:
        raise InstallError("Bundle validation failed. Run python3 scripts/verify.py for file/rule diagnostics; nothing was installed.")
    try:
        manifest = json.loads((ROOT / "manifest.json").read_text(encoding="utf-8"))
        files = manifest["files"]
        if manifest["format_version"] != 1 or not isinstance(files, dict):
            raise ValueError
        required = {"preferences/com.googlecode.iterm2.plist", "profiles/profiles.json",
                    "shell/appearance.zsh", "shell/p10k.zsh", "shell/p10k-lean.zsh"}
        if not required.issubset(files):
            raise ValueError
        payload: dict[str, bytes] = {}
        for name, expected in files.items():
            relative = safe_relative(name)
            source = ROOT / relative
            if source.is_symlink() or not source.is_file() or not within(source.resolve(), ROOT):
                raise ValueError
            data = source.read_bytes()
            if not isinstance(expected, str) or digest(data) != expected:
                raise ValueError
            payload[name] = data
        fonts = [name for name in files if name.startswith("fonts/") and name.endswith(".otf")]
        if len(fonts) != 4 or any(len(Path(name).parts) != 2 for name in fonts):
            raise ValueError
        return payload
    except (OSError, ValueError, KeyError, TypeError) as exc:
        raise InstallError("Manifest is missing, invalid, or does not match the bundled files; nothing was installed.") from exc


def safe_relative(value: str) -> Path:
    if not isinstance(value, str) or "\\" in value:
        raise InstallError("Invalid relative path in installation metadata.")
    path = Path(value)
    if not value or path.is_absolute() or any(part in ("..", ".") for part in path.parts):
        raise InstallError("Unsafe relative path in installation metadata.")
    return path


def rebase(value, home: Path):
    if isinstance(value, str):
        if value == "~":
            return str(home)
        if value.startswith("~/"):
            return str(home / value[2:])
        return value
    if isinstance(value, list):
        return [rebase(item, home) for item in value]
    if isinstance(value, dict):
        return {key: rebase(item, home) for key, item in value.items()}
    if isinstance(value, bytes):
        try:
            decoded = json.loads(value)
        except (ValueError, UnicodeError):
            return value
        return json.dumps(rebase(decoded, home), ensure_ascii=False, separators=(",", ":")).encode()
    return value


def check_target(home: Path, relative: Path) -> Path:
    path = home / relative
    if not within(path, home):
        raise InstallError("Destination escapes the selected home.")
    for part in [home, *reversed(path.relative_to(home).parents), relative]:
        # Check every existing component without following symlinks.
        candidate = part if part == home else home / part
        if candidate.is_symlink():
            raise InstallError(f"Refusing a symlink at destination {relative}; preserve or relocate it manually first.")
        if candidate.exists() and candidate != path and not candidate.is_dir():
            raise InstallError(f"A destination parent is not a directory: {relative}.")
    if path.exists() and not path.is_file():
        raise InstallError(f"Destination is not a regular file: {relative}.")
    if path.exists():
        info = path.stat()
        if info.st_uid != os.getuid():
            raise InstallError(f"Destination is owned by another account: {relative}; it was left untouched.")
        if info.st_nlink != 1:
            raise InstallError(f"Destination has multiple hard links: {relative}; preserve or relocate it manually first.")
    return path


def zshrc_content(original: bytes) -> bytes:
    if b"\x00" in original:
        raise InstallError("Existing .zshrc contains NUL bytes; it was left untouched.")
    start = BLOCK_START.encode()
    end = BLOCK_END.encode()
    lines = original.splitlines(keepends=True)
    starts = [i for i, line in enumerate(lines) if line.rstrip(b"\r\n") == start]
    ends = [i for i, line in enumerate(lines) if line.rstrip(b"\r\n") == end]
    if not starts and not ends:
        return original + (b"\n" if original and not original.endswith(b"\n") else b"") + BLOCK
    if len(starts) != 1 or len(ends) != 1 or starts[0] >= ends[0]:
        raise InstallError("Existing .zshrc has an incomplete or repeated iterm2-portable block; repair the markers before installing.")
    return b"".join(lines[:starts[0]]) + BLOCK + b"".join(lines[ends[0] + 1:])


def macos_required() -> None:
    if sys.platform != "darwin":
        raise InstallError("Production install/restore requires macOS. Use --stage DIR to prepare a home without dependencies or network access.")


def refuse_running_iterm() -> None:
    # Process-name searches can miss a running macOS app. Query its bundle identity.
    result = run(["/usr/bin/osascript", "-e", 'application id "com.googlecode.iterm2" is running'], allow_missing=True)
    state = result.stdout.strip()
    if result.returncode or state not in (b"true", b"false"):
        raise InstallError("Could not reliably determine whether iTerm2 is running; nothing was installed.")
    if state == b"true":
        raise InstallError("iTerm2 is running. Quit it yourself after saving your sessions, then retry; the installer never closes sessions.")


def export_domain() -> tuple[dict, bytes | None]:
    result = run(["/usr/bin/defaults", "export", DOMAIN, "-"], allow_missing=True)
    if result.returncode == 0:
        return load_plist(result.stdout, "target preferences"), result.stdout
    if b"does not exist" in result.stderr.lower() or b"does not exist" in result.stdout.lower():
        return {}, None
    raise InstallError("Cannot export the target preferences domain; nothing was installed.")


def check_custom_folder(preferences: dict) -> None:
    if preferences.get("LoadPrefsFromCustomFolder"):
        raise InstallError("iTerm2 loads a custom preferences folder. In iTerm2 Settings > General > Preferences, disable 'Load preferences from a custom folder or URL', quit iTerm2, and retry. The installer will not silently write an unused preferences domain.")

def check_hannotate() -> None:
    result = run(["/usr/sbin/system_profiler", "SPFontsDataType", "-json"])
    try:
        fonts = json.loads(result.stdout)
    except (ValueError, UnicodeError) as exc:
        raise InstallError("Cannot inspect installed fonts; no preferences were changed.") from exc

    def contains_font(value) -> bool:
        if isinstance(value, dict):
            return any(contains_font(item) for item in value.values())
        if isinstance(value, list):
            return any(contains_font(item) for item in value)
        return value == "HannotateSC-W5"

    if not contains_font(fonts):
        raise InstallError("HannotateSC-W5 is required by these profiles. Open Font Book, download/enable Hannotate SC, then retry. This proprietary macOS font is not bundled or installed automatically.")


def check_iterm_version(home: Path) -> None:
    applications = (Path("/Applications/iTerm.app"), home / "Applications/iTerm.app")
    versions = []
    for application in applications:
        if not application.is_dir():
            continue
        try:
            info = load_plist((application / "Contents/Info.plist").read_bytes(), "iTerm2 application metadata")
            version = info.get("CFBundleShortVersionString", "")
            match = re.match(r"^(\d+)\.(\d+)(?:\.(\d+))?", version)
            if not match:
                raise ValueError
            versions.append(tuple(int(part or 0) for part in match.groups()))
        except (OSError, ValueError, TypeError) as exc:
            raise InstallError("Cannot determine the installed iTerm2 version; no preferences were changed.") from exc
    if not versions or any(version < (3, 7, 6) for version in versions):
        raise InstallError("These settings require iTerm2 3.7.6 or newer. Update each installed copy of iTerm2, then retry.")



def dependencies(install: bool, home: Path) -> None:
    brew = shutil.which("brew")
    if not brew:
        for candidate in (Path("/opt/homebrew/bin/brew"), Path("/usr/local/bin/brew")):
            if candidate.is_file() and os.access(candidate, os.X_OK):
                brew = str(candidate)
                break
    if not brew:
        raise InstallError("Homebrew is required for the shell appearance dependencies. Install Homebrew from https://brew.sh, then rerun --apply --install-deps.")
    if install:
        print("Installing dependencies with Homebrew (network access may be required).", flush=True)
        run([brew, "install", "--cask", "iterm2"])
        run([brew, "install", *FORMULAE])
    missing = []
    if not any(path.is_dir() for path in (Path("/Applications/iTerm.app"), home / "Applications/iTerm.app")):
        missing.append("iTerm2")
    for formula in FORMULAE:
        result = run([brew, "list", "--versions", formula], allow_missing=True)
        if result.returncode or not result.stdout.strip():
            missing.append(formula)
    if missing:
        raise InstallError("Missing dependencies: " + ", ".join(missing) + ". Rerun with --apply --install-deps to install them using Homebrew.")


def private_directory(path: Path) -> None:
    if path.is_symlink() or (path.exists() and not path.is_dir()):
        raise InstallError("Backup destination is not a regular private directory.")
    path.mkdir(mode=0o700, parents=True, exist_ok=True)
    os.chmod(path, 0o700)


def copy_file(source: Path, destination: Path) -> None:
    if sys.platform == "darwin":
        # Native ditto preserves macOS ACLs and resource forks as well as xattrs.
        run(["/usr/bin/ditto", "--rsrc", "--extattr", "--acl", str(source), str(destination)])
    else:
        shutil.copy2(source, destination)


def atomic_write(path: Path, data: bytes, mode: int = 0o600, *, metadata: Path | None = None) -> None:
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    fd, temporary = tempfile.mkstemp(prefix=".iterm2-portable-", dir=path.parent)
    temporary_path = Path(temporary)
    os.close(fd)
    try:
        if metadata is not None:
            copy_file(metadata, temporary_path)
            original_stat = metadata.stat()
            os.chown(temporary_path, original_stat.st_uid, original_stat.st_gid)
        with temporary_path.open("wb") as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        if metadata is None:
            os.chmod(temporary_path, mode)
        os.replace(temporary_path, path)
    finally:
        temporary_path.unlink(missing_ok=True)


def is_install_target(relative: Path) -> bool:
    return (relative in (PREFERENCES, Path(".zshrc")) or
            (relative.parent == SHELL_DIR and relative.name in ("appearance.zsh", "p10k.zsh", "p10k-lean.zsh")) or
            (relative.parent == Path("Library/Fonts") and relative.suffix == ".otf"))


def make_backup(home: Path, changed: dict[Path, bytes], production: bool,
                domain_data: bytes | None) -> tuple[Path, dict]:
    parent = home / BACKUPS
    # A symlink anywhere in the backup path would send private data elsewhere.
    for ancestor in [home, *(home / part for part in reversed(BACKUPS.parents)), parent]:
        if ancestor.is_symlink() or (ancestor.exists() and not ancestor.is_dir()):
            raise InstallError("Backup path contains a symlink or a non-directory.")
    private_directory(parent.parent)
    private_directory(parent)
    stamp = dt.datetime.now(dt.timezone.utc).strftime("%Y%m%dT%H%M%S.%fZ")
    backup = parent / (stamp + "-" + uuid.uuid4().hex[:8])
    private_directory(backup)
    private_directory(backup / "files")
    record = {"format_version": 1, "home": str(home), "production": production,
              "files": [], "created_directories": [], "domain_existed": domain_data is not None}
    created_directories: set[str] = set()
    for number, (relative, data) in enumerate(changed.items()):
        target = check_target(home, relative)
        for ancestor in target.parents:
            if ancestor == home:
                break
            if not ancestor.exists():
                created_directories.add(str(ancestor.relative_to(home)))
        entry = {"path": str(relative), "existed": target.exists(), "installed_sha256": digest(data)}
        if target.exists():
            saved = backup / "files" / str(number)
            original_stat = target.stat()
            copy_file(target, saved)
            entry.update({"snapshot": "files/" + str(number), "sha256": digest(saved.read_bytes()),
                          "mode": stat.S_IMODE(original_stat.st_mode),
                          "uid": original_stat.st_uid, "gid": original_stat.st_gid,
                          "atime_ns": original_stat.st_atime_ns, "mtime_ns": original_stat.st_mtime_ns})
            os.chmod(saved, 0o600)
        record["files"].append(entry)
    record["created_directories"] = sorted(created_directories, key=lambda item: len(Path(item).parts), reverse=True)
    if production and domain_data is not None:
        atomic_write(backup / "domain.plist", domain_data)
        record["domain_sha256"] = digest(domain_data)
    atomic_write(backup / "backup.json", json.dumps(record, indent=2).encode() + b"\n")
    return backup, record


def read_backup(backup: Path) -> tuple[Path, dict]:
    if not backup.is_dir() or backup.is_symlink():
        raise InstallError("Backup must be an existing regular directory.")
    try:
        metadata = backup / "backup.json"
        if metadata.is_symlink():
            raise ValueError
        record = json.loads(metadata.read_text(encoding="utf-8"))
        if (record["format_version"] != 1 or not isinstance(record["production"], bool) or
                not isinstance(record["domain_existed"], bool) or not isinstance(record["files"], list)):
            raise ValueError
        home = Path(record["home"])
        if not home.is_absolute() or home.is_symlink() or home.resolve() != home:
            raise ValueError
        if backup.parent != home / BACKUPS or within(backup, ROOT):
            raise ValueError
        if record["production"] and home != Path.home().resolve():
            raise InstallError("Production backup belongs to a different home; restore from the original account.")
        seen = set()
        for entry in record["files"]:
            relative = safe_relative(entry["path"])
            if not is_install_target(relative) or str(relative) in seen or not isinstance(entry["existed"], bool):
                raise ValueError
            seen.add(str(relative))
            check_target(home, relative)
            if entry["existed"]:
                saved_relative = safe_relative(entry["snapshot"])
                saved = backup / saved_relative
                if saved_relative.parent != Path("files") or saved.is_symlink() or not within(saved.resolve(), backup):
                    raise ValueError
                if digest(saved.read_bytes()) != entry["sha256"]:
                    raise ValueError
                if not isinstance(entry["mode"], int) or not 0 <= entry["mode"] <= 0o7777:
                    raise ValueError
                if not all(isinstance(entry[key], int) for key in ("uid", "gid", "atime_ns", "mtime_ns")):
                    raise ValueError
        for directory in record["created_directories"]:
            relative = safe_relative(directory)
            if not any(relative in Path(entry["path"]).parents for entry in record["files"]):
                raise ValueError
            # The dummy leaf lets the same destination safety check cover directories.
            check_target(home, relative / ".iterm2-portable-restore-check")
        if record["production"] and record["domain_existed"]:
            domain_file = backup / "domain.plist"
            if domain_file.is_symlink() or digest(domain_file.read_bytes()) != record["domain_sha256"]:
                raise ValueError
            load_plist(domain_file.read_bytes(), "backup preferences")
        return home, record
    except (OSError, ValueError, KeyError, TypeError) as exc:
        raise InstallError("Backup metadata or saved files are incomplete, unsafe, or damaged; nothing was restored.") from exc


def restore_files(backup: Path, home: Path, record: dict) -> None:
    if record["production"]:
        if record["domain_existed"]:
            run(["/usr/bin/defaults", "import", DOMAIN, str(backup / "domain.plist")])
        else:
            result = run(["/usr/bin/defaults", "delete", DOMAIN], allow_missing=True)
            if result.returncode and b"not found" not in result.stderr.lower() and b"does not exist" not in result.stderr.lower():
                raise InstallError("Cannot remove the preference domain created by this install; backup retained.")
    for entry in record["files"]:
        path = check_target(home, safe_relative(entry["path"]))
        if entry["existed"]:
            saved = backup / entry["snapshot"]
            atomic_write(path, saved.read_bytes(), metadata=saved)
            os.chown(path, entry["uid"], entry["gid"])
            os.chmod(path, entry["mode"])
            os.utime(path, ns=(entry["atime_ns"], entry["mtime_ns"]))
        else:
            path.unlink(missing_ok=True)
    for relative in record["created_directories"]:
        directory = home / relative
        try:
            directory.rmdir()
        except FileNotFoundError:
            pass
        except OSError:
            # Keep directories that now contain unrelated files.
            if not directory.is_dir():
                raise


def install(home: Path, payload: dict[str, bytes], production: bool, install_deps: bool) -> None:
    if production:
        macos_required()
        refuse_running_iterm()
        current, domain_data = export_domain()
        check_custom_folder(current)
        check_hannotate()
        dependencies(install_deps, home)
        check_iterm_version(home)
        refuse_running_iterm()
    else:
        target = check_target(home, PREFERENCES)
        current = load_plist(target.read_bytes(), "staged preferences") if target.exists() else {}
        domain_data = None
    source = load_plist(payload["preferences/com.googlecode.iterm2.plist"], "bundle preferences")
    # Update public settings while keeping target-only private state and custom-folder flags.
    merged = dict(current)
    custom_flags = {key: current[key] for key in ("LoadPrefsFromCustomFolder", "PrefsCustomFolder") if key in current}
    merged.update(rebase(source, home))
    merged.update(custom_flags)
    desired = {PREFERENCES: plistlib.dumps(merged, fmt=plistlib.FMT_XML, sort_keys=True)}
    for name, data in payload.items():
        relative = Path(name)
        if relative.parent == Path("fonts") and relative.suffix == ".otf":
            desired[Path("Library/Fonts") / relative.name] = data
        elif relative.parent == Path("shell") and relative.name in ("appearance.zsh", "p10k.zsh", "p10k-lean.zsh"):
            desired[SHELL_DIR / relative.name] = data
    zshrc = check_target(home, Path(".zshrc"))
    desired[Path(".zshrc")] = zshrc_content(zshrc.read_bytes() if zshrc.exists() else b"")
    changed = {}
    for relative, data in desired.items():
        target = check_target(home, relative)
        if not target.exists() or target.read_bytes() != data:
            changed[relative] = data
    # defaults may hold a domain newer than the on-disk plist; always preserve its snapshot.
    if production:
        changed[PREFERENCES] = desired[PREFERENCES]
    if not changed:
        print("Installation already matches the bundle; no files changed.")
        return
    backup, record = make_backup(home, changed, production, domain_data)
    try:
        for relative, data in changed.items():
            target = check_target(home, relative)
            previous_mode = stat.S_IMODE(target.stat().st_mode) if target.exists() else (0o644 if relative.parent == Path("Library/Fonts") else 0o600)
            atomic_write(target, data, previous_mode, metadata=target if target.exists() else None)
        if production:
            refuse_running_iterm()
            run(["/usr/bin/defaults", "import", DOMAIN, str(home / PREFERENCES)])
    except (OSError, InstallError, ValueError) as exc:
        try:
            # Never write preferences behind an application that started during installation.
            if production:
                refuse_running_iterm()
            restore_files(backup, home, record)
        except (OSError, InstallError, ValueError) as restore_error:
            raise InstallError(f"Installation failed and automatic rollback could not finish. Backup: {backup}. Close iTerm2 and run --restore with that directory. Rollback reason: {restore_error}") from exc
        raise InstallError(f"Installation failed; original files were restored. Backup retained at {backup}.") from exc
    print(("Installed" if production else "Staged") + f" portable preferences, four fonts, shell appearance, and the marked .zshrc block under {home}.")
    print(f"Private backup: {backup}")
    print(f'Restore: python3 scripts/install.py --restore "{backup}"')
    if production:
        print("Open iTerm2 yourself to load the preferences. Homebrew dependencies are not removed by restore.")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, epilog="--apply is explicit consent to update this account. No sessions are closed. --stage never uses the network. Restore overwrites files affected by the selected install, including subsequent edits; backups stay local.")
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--stage", metavar="DIR", type=Path, help="create a complete isolated installation in DIR (no macOS/dependency/network requirement)")
    mode.add_argument("--apply", action="store_true", help="explicitly consent to install into the current macOS account")
    mode.add_argument("--restore", metavar="BACKUP_DIR", type=Path, help="restore every affected file and original nonexistence from a private backup")
    parser.add_argument("--install-deps", action="store_true", help="with --apply only, install iTerm2 and shell dependencies using existing Homebrew")
    args = parser.parse_args(argv)
    if args.install_deps and not args.apply:
        parser.error("--install-deps requires --apply; --stage and --restore never install dependencies")
    try:
        if args.restore:
            backup = args.restore.expanduser().absolute()
            if backup.is_symlink():
                raise InstallError("Refusing a symlink backup directory.")
            backup = backup.resolve()
            home, record = read_backup(backup)
            if record["production"]:
                macos_required()
                refuse_running_iterm()
            restore_files(backup, home, record)
            print(f"Restored original files and preference state under {home}; private backup retained at {backup}.")
            return 0
        payload = validate_bundle()
        if args.stage:
            requested = args.stage.expanduser().absolute()
            if requested.is_symlink():
                raise InstallError("Staged home must not be a symlink.")
            home = requested.resolve()
            if within(home, ROOT) or within(ROOT, home) or home == Path.home().resolve():
                raise InstallError("Choose a staged home outside the bundle and outside your real home root.")
            if home.exists() and not home.is_dir():
                raise InstallError("Staged home must be a directory.")
            home.mkdir(parents=True, exist_ok=True, mode=0o700)
        else:
            home = Path.home().resolve()
        install(home, payload, bool(args.apply), args.install_deps)
        return 0
    except InstallError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1
    except (OSError, ValueError, TypeError, OverflowError) as exc:
        # Avoid dumping plist values, external command output, or Python tracebacks.
        detail = exc.strerror if isinstance(exc, OSError) else type(exc).__name__
        print(f"Error: installation operation failed ({detail}); keep any printed private backup for --restore.", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
