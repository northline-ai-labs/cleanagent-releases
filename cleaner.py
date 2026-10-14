"""
CleanAgent — Cleanup Engine v2.0
Cross-platform cleanup with bulletproof Windows support.
"""

import os
import shutil
import platform
import subprocess
import sys
from pathlib import Path
from datetime import datetime, timedelta
from dataclasses import dataclass, asdict
from typing import List, Dict
from scanner import fmt_size

# Windows subprocess flag to hide console windows
_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)


@dataclass
class CleanupResult:
    operation: str
    label: str
    files_cleaned: int
    space_freed: int
    success: bool
    details: str

    def to_dict(self):
        d = asdict(self)
        d["space_freed_mb"] = round(self.space_freed / (1024 * 1024), 1)
        d["space_freed_display"] = fmt_size(self.space_freed)
        return d


def _safe_delete_file(p: Path) -> int:
    """Delete a file, return bytes freed. Returns 0 on failure."""
    try:
        size = p.stat().st_size
        p.unlink()
        return size
    except (PermissionError, OSError):
        return 0


def _safe_delete_dir(p: Path) -> int:
    """Delete a directory tree, return bytes freed. Returns 0 on failure."""
    try:
        size = sum(
            f.stat().st_size for f in p.rglob("*")
            if f.is_file()
        )
        shutil.rmtree(p, ignore_errors=True)
        return size
    except (PermissionError, OSError):
        return 0


class SystemCleaner:
    # ── Safe cache subdirectories to clean ───────────────
    MAC_SAFE_CACHES = [
        "Google/Chrome/Default/Cache",
        "Google/Chrome/Default/Code Cache",
        "com.apple.Safari/WebKitCache",
        "org.mozilla.firefox",
        "com.brave.Browser",
        "com.microsoft.edgemac",
        "com.spotify.client",
        "com.microsoft.VSCode",
        "com.apple.iTunes",
        "pip",
        "yarn",
        "com.docker.docker",
        "org.swift.swiftpm",
        "Homebrew",
    ]

    WIN_SAFE_CACHES = [
        # Relative to LOCALAPPDATA
        "Google/Chrome/User Data/Default/Cache",
        "Google/Chrome/User Data/Default/Code Cache",
        "Microsoft/Edge/User Data/Default/Cache",
        "Microsoft/Edge/User Data/Default/Code Cache",
        "BraveSoftware/Brave-Browser/User Data/Default/Cache",
        "Spotify/Data",
        "pip/cache",
        "yarn/Cache",
    ]

    WIN_APPDATA_CACHES = [
        # Relative to APPDATA
        "Mozilla/Firefox/Profiles",
    ]

    def __init__(self):
        self.home = Path.home()
        self.is_mac = platform.system() == "Darwin"
        self.is_win = platform.system() == "Windows"
        self.results: List[CleanupResult] = []

    def run_all(self) -> Dict:
        self.results = []
        self.clean_browser_caches()
        self.clean_system_logs()
        self.clean_temp_files()
        self.clean_trash()
        self.clean_package_caches()
        self.flush_dns()

        total_files = sum(r.files_cleaned for r in self.results)
        total_space = sum(r.space_freed for r in self.results)
        return {
            "total_files_cleaned": total_files,
            "total_space_freed_mb": round(total_space / (1024 * 1024), 1),
            "total_space_freed_display": fmt_size(total_space),
            "total_space_freed_bytes": total_space,
            "operations": [r.to_dict() for r in self.results],
            "timestamp": datetime.now().isoformat(),
        }

    # ── Browser & App Caches ─────────────────────────────
    def clean_browser_caches(self) -> CleanupResult:
        files_cleaned = 0
        space_freed = 0
        targets = []

        if self.is_mac:
            base = self.home / "Library" / "Caches"
            targets = [(base / sub) for sub in self.MAC_SAFE_CACHES]
        elif self.is_win:
            local = os.environ.get("LOCALAPPDATA", "")
            appdata = os.environ.get("APPDATA", "")
            if local:
                targets += [(Path(local) / sub) for sub in self.WIN_SAFE_CACHES]
            if appdata:
                targets += [(Path(appdata) / sub) for sub in self.WIN_APPDATA_CACHES]
        else:
            base = self.home / ".cache"
            targets = [
                base / "google-chrome",
                base / "mozilla",
                base / "BraveSoftware",
            ]

        for target in targets:
            if not target.exists():
                continue
            try:
                if target.is_file():
                    freed = _safe_delete_file(target)
                    if freed:
                        space_freed += freed
                        files_cleaned += 1
                    continue
                for f in target.rglob("*"):
                    if f.is_file():
                        freed = _safe_delete_file(f)
                        if freed:
                            space_freed += freed
                            files_cleaned += 1
            except (PermissionError, OSError):
                continue

        r = CleanupResult(
            "browser_caches", "Browser & App Caches",
            files_cleaned, space_freed, True,
            f"Cleaned {files_cleaned} files ({fmt_size(space_freed)})",
        )
        self.results.append(r)
        return r

    # ── System Logs ──────────────────────────────────────
    def clean_system_logs(self) -> CleanupResult:
        files_cleaned = 0
        space_freed = 0

        if self.is_mac:
            dirs = [self.home / "Library" / "Logs"]
        elif self.is_win:
            dirs = []
            temp = os.environ.get("TEMP")
            if temp:
                dirs.append(Path(temp))
            # Windows event logs are not safe to delete directly,
            # so we only clean temp logs
            local = os.environ.get("LOCALAPPDATA")
            if local:
                dirs.append(Path(local) / "CrashDumps")
        else:
            dirs = [Path("/var/log")]

        for d in dirs:
            if not d.exists():
                continue
            try:
                for pattern in ["*.log", "*.log.gz", "*.log.old", "*.dmp", "*.mdmp"]:
                    for f in d.rglob(pattern):
                        try:
                            st = f.stat()
                            age = datetime.now() - datetime.fromtimestamp(st.st_mtime)
                            if age.days > 7 or pattern.endswith((".gz", ".old", ".dmp", ".mdmp")):
                                freed = _safe_delete_file(f)
                                if freed:
                                    space_freed += freed
                                    files_cleaned += 1
                        except (PermissionError, OSError):
                            continue
            except (PermissionError, OSError):
                continue

        r = CleanupResult(
            "system_logs", "Old System Logs",
            files_cleaned, space_freed, True,
            f"Removed {files_cleaned} logs ({fmt_size(space_freed)})",
        )
        self.results.append(r)
        return r

    # ── Temp Files ───────────────────────────────────────
    def clean_temp_files(self) -> CleanupResult:
        files_cleaned = 0
        space_freed = 0

        if self.is_mac:
            dirs = [
                self.home / "Library" / "Caches" / "com.apple.dt.Xcode",
                Path("/private/var/folders"),  # partial temp cleanup
            ]
        elif self.is_win:
            dirs = []
            temp = os.environ.get("TEMP")
            if temp:
                dirs.append(Path(temp))
            local = os.environ.get("LOCALAPPDATA")
            if local:
                dirs.append(Path(local) / "Temp")
            # Windows temp cleanup
            dirs.append(Path("C:/Windows/Temp"))
        else:
            dirs = [Path("/tmp")]

        for d in dirs:
            if not d.exists():
                continue
            try:
                for f in d.rglob("*"):
                    if f.is_file():
                        try:
                            st = f.stat()
                            age = datetime.now() - datetime.fromtimestamp(st.st_mtime)
                            if age.days > 3:
                                freed = _safe_delete_file(f)
                                if freed:
                                    space_freed += freed
                                    files_cleaned += 1
                        except (PermissionError, OSError):
                            continue
            except (PermissionError, OSError):
                continue

        r = CleanupResult(
            "temp_files", "Temporary Files",
            files_cleaned, space_freed, True,
            f"Removed {files_cleaned} temp files ({fmt_size(space_freed)})",
        )
        self.results.append(r)
        return r

    # ── Trash / Recycle Bin ──────────────────────────────
    def clean_trash(self) -> CleanupResult:
        files_cleaned = 0
        space_freed = 0

        if self.is_mac:
            trash = self.home / ".Trash"
            if trash.exists():
                try:
                    for f in trash.iterdir():
                        try:
                            if f.is_file():
                                freed = _safe_delete_file(f)
                                if freed:
                                    space_freed += freed
                                    files_cleaned += 1
                            elif f.is_dir():
                                freed = _safe_delete_dir(f)
                                if freed:
                                    space_freed += freed
                                    files_cleaned += 1
                        except (PermissionError, OSError):
                            continue
                except (PermissionError, OSError):
                    pass

        elif self.is_win:
            try:
                result = subprocess.run(
                    [
                        "powershell", "-NoProfile", "-Command",
                        "Clear-RecycleBin -Force -ErrorAction SilentlyContinue",
                    ],
                    capture_output=True, timeout=30,
                    creationflags=_NO_WINDOW,
                )
                if result.returncode == 0:
                    files_cleaned = 1
            except Exception:
                pass

        r = CleanupResult(
            "trash", "Trash / Recycle Bin",
            files_cleaned, space_freed, True,
            f"Emptied {files_cleaned} items ({fmt_size(space_freed)})",
        )
        self.results.append(r)
        return r

    # ── Package Manager Caches ───────────────────────────
    def clean_package_caches(self) -> CleanupResult:
        files_cleaned = 0
        space_freed = 0

        def _folder_size(p: Path) -> int:
            total = 0
            for f in p.rglob("*"):
                try:
                    if f.is_file() and not f.is_symlink():
                        total += f.stat().st_size
                except OSError:
                    pass
            return total

        def _wipe(p: Path) -> None:
            nonlocal files_cleaned, space_freed
            if p.exists():
                size = _folder_size(p)
                shutil.rmtree(p, ignore_errors=True)
                if not p.exists():
                    files_cleaned += 1
                    space_freed += size

        # pip cache: delete the folder directly. Never run pip/pip3:
        # on Macs without developer tools, /usr/bin/pip3 triggers an install prompt.
        if self.is_win:
            _wipe(Path(os.environ.get("LOCALAPPDATA", "")) / "pip" / "Cache")
        else:
            _wipe(Path.home() / "Library" / "Caches" / "pip")

        # npm cache: delete the folder directly
        if self.is_win:
            _wipe(Path(os.environ.get("LOCALAPPDATA", "")) / "npm-cache" / "_cacache")
        else:
            _wipe(Path.home() / ".npm" / "_cacache")

        # Homebrew: only if actually installed (GUI apps may not inherit PATH)
        if self.is_mac:
            brew = shutil.which("brew") or next(
                (b for b in ("/opt/homebrew/bin/brew", "/usr/local/bin/brew")
                 if Path(b).exists()), None)
            if brew:
                cache = Path.home() / "Library" / "Caches" / "Homebrew"
                before = _folder_size(cache) if cache.exists() else 0
                try:
                    result = subprocess.run(
                        [brew, "cleanup", "--prune=all", "-s"],
                        capture_output=True, timeout=120,
                    )
                    if result.returncode == 0:
                        files_cleaned += 1
                        after = _folder_size(cache) if cache.exists() else 0
                        space_freed += max(0, before - after)
                except Exception:
                    pass

        r = CleanupResult(
            "package_caches", "Package Caches (pip/brew/npm)",
            files_cleaned, space_freed, True,
            f"Cleared {files_cleaned} package caches ({fmt_size(space_freed)})",
        )
        self.results.append(r)
        return r

    # ── DNS Flush ────────────────────────────────────────
    def flush_dns(self) -> CleanupResult:
        success = False
        details = ""
        try:
            if self.is_mac:
                subprocess.run(
                    ["dscacheutil", "-flushcache"],
                    capture_output=True, timeout=10,
                )
                success = True
                details = "DNS cache flushed"
            elif self.is_win:
                subprocess.run(
                    ["ipconfig", "/flushdns"],
                    capture_output=True, timeout=10,
                    creationflags=_NO_WINDOW,
                )
                success = True
                details = "DNS cache flushed"
        except Exception as e:
            details = f"DNS flush skipped: {e}"

        r = CleanupResult("dns_flush", "DNS Cache", 0, 0, success, details)
        self.results.append(r)
        return r

    # ── Windows Disk Cleanup (Windows-only bonus) ────────
    def run_windows_disk_cleanup(self) -> CleanupResult:
        """Run built-in Windows Disk Cleanup utility."""
        if not self.is_win:
            r = CleanupResult("win_disk_cleanup", "Windows Disk Cleanup", 0, 0, False, "Not on Windows")
            self.results.append(r)
            return r

        try:
            # Run cleanmgr silently with sageset
            subprocess.run(
                ["cleanmgr", "/d", "C:", "/verylowdisk"],
                capture_output=True, timeout=120,
                creationflags=_NO_WINDOW,
            )
            r = CleanupResult(
                "win_disk_cleanup", "Windows Disk Cleanup",
                1, 0, True, "Windows Disk Cleanup completed",
            )
        except Exception as e:
            r = CleanupResult(
                "win_disk_cleanup", "Windows Disk Cleanup",
                0, 0, False, f"Disk Cleanup failed: {e}",
            )

        self.results.append(r)
        return r

    # ── Selective Cleanup Methods ────────────────────────

    @staticmethod
    def delete_files(file_paths: List[str]) -> Dict:
        """Delete specific files by path. Returns summary."""
        deleted = 0
        freed = 0
        errors = []

        for fp in file_paths:
            p = Path(fp)
            if not p.exists():
                errors.append(f"Not found: {p.name}")
                continue
            try:
                if p.is_file():
                    size = p.stat().st_size
                    p.unlink()
                    deleted += 1
                    freed += size
                elif p.is_dir():
                    ds = sum(x.stat().st_size for x in p.rglob("*") if x.is_file())
                    shutil.rmtree(p)
                    deleted += 1
                    freed += ds
            except (PermissionError, OSError) as e:
                errors.append(f"Can't delete {p.name}: {e}")

        return {
            "deleted": deleted,
            "space_freed_bytes": freed,
            "space_freed_display": fmt_size(freed),
            "errors": errors,
        }


    @staticmethod
    def uninstall_app(app_path: str) -> Dict:
        """Move an app to Trash (Mac) or delete app folder (Windows)."""
        p = Path(app_path)
        if not p.exists():
            return {"success": False, "error": "Not found: " + app_path}

        try:
            size = sum(
                f.stat().st_size for f in p.rglob("*") if f.is_file()
            ) if p.is_dir() else p.stat().st_size

            is_mac = platform.system() == "Darwin"
            is_win = platform.system() == "Windows"

            if is_mac:
                osa_cmd = 'tell application "Finder" to delete POSIX file "' + app_path + '"'
                result = subprocess.run(
                    ["osascript", "-e", osa_cmd],
                    capture_output=True, text=True, timeout=15,
                )
                if result.returncode != 0:
                    return {"success": False, "error": result.stderr.strip() or "Move to Trash failed"}
            elif is_win:
                shutil.rmtree(p, ignore_errors=True)
            else:
                shutil.rmtree(p, ignore_errors=True)

            return {
                "success": True,
                "space_freed_bytes": size,
                "space_freed_display": fmt_size(size),
                "app_name": p.stem if p.name.endswith(".app") else p.name,
            }
        except Exception as e:
            return {"success": False, "error": str(e)}

    @staticmethod
    def delete_duplicates(duplicate_groups: List[Dict]) -> Dict:
        """For each group, keep the first file and delete the rest."""
        deleted = 0
        freed = 0
        errors = []

        for group in duplicate_groups:
            files = group.get("files", [])
            if len(files) < 2:
                continue
            for f in files[1:]:
                p = Path(f["path"])
                try:
                    if p.exists():
                        freed += p.stat().st_size
                        p.unlink()
                        deleted += 1
                except (PermissionError, OSError) as e:
                    errors.append(f"Can't delete {p.name}: {e}")

        return {
            "deleted": deleted,
            "space_freed_bytes": freed,
            "space_freed_display": fmt_size(freed),
            "errors": errors,
        }
    @staticmethod
    def archive_files(file_paths: List[str], dest_dir: str, delete_originals: bool = False) -> Dict:
        """
        Archive a list of files into a single timestamped ZIP at dest_dir.
        Optionally delete the originals after a successful archive.
        """
        import zipfile

        dest = Path(dest_dir).expanduser()
        try:
            dest.mkdir(parents=True, exist_ok=True)
        except (PermissionError, OSError) as e:
            return {"success": False, "error": f"Cannot write to {dest}: {e}"}

        ts = datetime.now().strftime("%Y%m%d-%H%M%S")
        zip_path = dest / f"cleanagent-archive-{ts}.zip"

        archived = 0
        skipped = []
        total_bytes = 0
        seen_names = {}

        try:
            with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED, allowZip64=True) as zf:
                for fp in file_paths:
                    p = Path(fp)
                    if not p.exists() or not p.is_file():
                        skipped.append(p.name or fp)
                        continue
                    try:
                        # Avoid name collisions by suffixing duplicates
                        arcname = p.name
                        if arcname in seen_names:
                            seen_names[arcname] += 1
                            stem, dot, ext = arcname.rpartition(".")
                            arcname = (
                                f"{stem}_{seen_names[p.name]}.{ext}"
                                if dot else f"{arcname}_{seen_names[p.name]}"
                            )
                        else:
                            seen_names[arcname] = 0
                        zf.write(p, arcname=arcname)
                        total_bytes += p.stat().st_size
                        archived += 1
                    except (PermissionError, OSError) as e:
                        skipped.append(f"{p.name}: {e}")
        except Exception as e:
            return {"success": False, "error": f"Archive failed: {e}"}

        deleted = 0
        delete_errors = []
        if delete_originals and archived > 0:
            for fp in file_paths:
                p = Path(fp)
                if not p.exists() or not p.is_file():
                    continue
                try:
                    p.unlink()
                    deleted += 1
                except (PermissionError, OSError) as e:
                    delete_errors.append(f"{p.name}: {e}")

        return {
            "success": True,
            "zip_path": str(zip_path),
            "archived": archived,
            "skipped": skipped,
            "deleted_originals": deleted,
            "delete_errors": delete_errors,
            "total_bytes": total_bytes,
            "total_display": fmt_size(total_bytes),
        }

    @staticmethod
    def pick_folder(initial_dir: str = None) -> Dict:
        """Open a native folder picker via AppleScript (Mac) or PowerShell (Windows)."""
        is_mac = platform.system() == "Darwin"
        is_win = platform.system() == "Windows"
        try:
            if is_mac:
                script = (
                    'try\n'
                    '  set chosenFolder to choose folder with prompt "Choose where to save the CleanAgent archive"\n'
                    '  POSIX path of chosenFolder\n'
                    'on error number -128\n'
                    '  return "CANCELLED"\n'
                    'end try'
                )
                result = subprocess.run(
                    ["osascript", "-e", script],
                    capture_output=True, text=True, timeout=120,
                )
                out = (result.stdout or "").strip()
                if not out or out == "CANCELLED":
                    return {"success": False, "cancelled": True}
                return {"success": True, "path": out.rstrip("/")}

            elif is_win:
                ps = (
                    "Add-Type -AssemblyName System.Windows.Forms; "
                    "$f = New-Object System.Windows.Forms.FolderBrowserDialog; "
                    "$f.Description = 'Choose where to save the CleanAgent archive'; "
                    "$f.ShowNewFolderButton = $true; "
                    "if ($f.ShowDialog() -eq 'OK') { Write-Output $f.SelectedPath } "
                    "else { Write-Output 'CANCELLED' }"
                )
                result = subprocess.run(
                    ["powershell", "-NoProfile", "-STA", "-Command", ps],
                    capture_output=True, text=True, timeout=120,
                    creationflags=_NO_WINDOW,
                )
                out = (result.stdout or "").strip()
                if not out or out == "CANCELLED":
                    return {"success": False, "cancelled": True}
                return {"success": True, "path": out}

            else:
                # Linux fallback: default to ~/Documents
                default = str(Path.home() / "Documents")
                return {"success": True, "path": default}
        except Exception as e:
            return {"success": False, "error": str(e)}
