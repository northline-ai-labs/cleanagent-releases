"""
CleanAgent — Cross-platform System Scanner v2.0
Bulletproof Windows + Mac + Linux support.
"""

import os
import sys
import time
import hashlib
import platform
import subprocess
from pathlib import Path
from datetime import datetime, timedelta
from collections import defaultdict
from dataclasses import dataclass, asdict
from typing import Dict, List, Optional

import psutil

SYSTEM_FILES = {
    ".localized", ".DS_Store", ".Spotlight-V100", ".fseventsd",
    ".Trashes", ".TemporaryItems", "Thumbs.db", "desktop.ini",
    ".com.apple.timemachine.donotpresent",
}


def fmt_size(b: int) -> str:
    if b >= 1024 ** 3:
        return f"{b / 1024**3:.1f} GB"
    elif b >= 1024 ** 2:
        return f"{b / 1024**2:.1f} MB"
    elif b >= 1024:
        return f"{b / 1024:.1f} KB"
    return f"{b} B"


def is_sys_file(p: Path) -> bool:
    return p.name in SYSTEM_FILES or p.name.startswith("._")


def safe_stat(p: Path) -> Optional[os.stat_result]:
    """Safely stat a file, returning None on any error."""
    try:
        return p.stat()
    except (PermissionError, OSError, ValueError):
        return None


@dataclass
class SystemMetrics:
    cpu_usage: float
    cpu_count: int
    memory_total_gb: float
    memory_used_gb: float
    memory_usage: float
    disk_total_gb: float
    disk_used_gb: float
    disk_free_gb: float
    disk_usage: float
    performance_score: float
    os_name: str
    os_version: str
    hostname: str
    uptime_hours: float
    uptime_days: float
    restart_recommended: bool
    timestamp: str

    def to_dict(self):
        return asdict(self)


class SystemScanner:
    def __init__(self):
        self.home = Path.home()
        self.is_mac = platform.system() == "Darwin"
        self.is_win = platform.system() == "Windows"
        self.is_linux = platform.system() == "Linux"

    # ── System Metrics ───────────────────────────────────
    def get_metrics(self) -> SystemMetrics:
        psutil.cpu_percent(interval=0)
        cpu = psutil.cpu_percent(interval=1.0)
        mem = psutil.virtual_memory()

        # Disk: use "/" on Mac/Linux, "C:\\" on Windows
        disk_path = "C:\\" if self.is_win else "/"
        try:
            disk = psutil.disk_usage(disk_path)
        except Exception:
            disk = psutil.disk_usage(str(self.home))

        try:
            boot = datetime.fromtimestamp(psutil.boot_time())
            up_s = (datetime.now() - boot).total_seconds()
        except Exception:
            up_s = 0

        # Performance score: weighted combination
        score = 100 - ((cpu * 0.35) + (mem.percent * 0.35) + (disk.percent * 0.30))
        score = max(0, min(100, score))
        if cpu < 30:
            score += 5
        if mem.percent < 60:
            score += 5
        if disk.percent < 75:
            score += 5
        score = min(100, round(score, 1))

        # Friendly OS name
        if self.is_win:
            os_display = f"Windows {platform.version()}"
        elif self.is_mac:
            os_display = "macOS"
        else:
            os_display = platform.system()

        return SystemMetrics(
            cpu_usage=round(cpu, 1),
            cpu_count=psutil.cpu_count() or 1,
            memory_total_gb=round(mem.total / 1024**3, 1),
            memory_used_gb=round(mem.used / 1024**3, 1),
            memory_usage=round(mem.percent, 1),
            disk_total_gb=round(disk.total / 1024**3, 1),
            disk_used_gb=round(disk.used / 1024**3, 1),
            disk_free_gb=round(disk.free / 1024**3, 1),
            disk_usage=round(disk.percent, 1),
            performance_score=score,
            os_name=platform.system(),
            os_version=os_display,
            hostname=platform.node(),
            uptime_hours=round(up_s / 3600, 1),
            uptime_days=round(up_s / 86400, 1),
            restart_recommended=up_s / 86400 > 7,
            timestamp=datetime.now().isoformat(),
        )

    # ── Cache Analysis ───────────────────────────────────
    def analyze_caches(self) -> Dict:
        if self.is_mac:
            dirs = [self.home / "Library" / "Caches"]
        elif self.is_win:
            dirs = self._win_cache_dirs()
        else:
            dirs = [self.home / ".cache"]

        total = 0
        count = 0
        details = []

        for d in dirs:
            if not d.exists():
                continue
            try:
                for item in d.iterdir():
                    if is_sys_file(item):
                        continue
                    try:
                        if item.is_file():
                            st = safe_stat(item)
                            if st:
                                total += st.st_size
                                count += 1
                        elif item.is_dir():
                            ds = self._dir_size(item)
                            total += ds
                            count += 1
                            if ds > 50 * 1024 * 1024:
                                details.append({
                                    "name": item.name,
                                    "path": str(item),
                                    "size_bytes": ds,
                                    "size_display": fmt_size(ds),
                                    "size_mb": round(ds / 1024**2, 1),
                                })
                    except (PermissionError, OSError):
                        continue
            except (PermissionError, OSError):
                continue

        return {
            "total_size_mb": round(total / 1024**2, 1),
            "total_display": fmt_size(total),
            "file_count": count,
            "large_caches": sorted(details, key=lambda x: x["size_bytes"], reverse=True)[:10],
        }

    def _win_cache_dirs(self) -> List[Path]:
        """Get Windows cache directories safely."""
        dirs = []
        temp = os.environ.get("TEMP")
        if temp:
            dirs.append(Path(temp))
        local = os.environ.get("LOCALAPPDATA")
        if local:
            dirs.append(Path(local) / "Temp")
            # Browser caches
            dirs.append(Path(local) / "Google" / "Chrome" / "User Data" / "Default" / "Cache")
            dirs.append(Path(local) / "Microsoft" / "Edge" / "User Data" / "Default" / "Cache")
            dirs.append(Path(local) / "BraveSoftware" / "Brave-Browser" / "User Data" / "Default" / "Cache")
        appdata = os.environ.get("APPDATA")
        if appdata:
            dirs.append(Path(appdata) / "Mozilla" / "Firefox" / "Profiles")
        return [d for d in dirs if d.exists()]

    # ── Large Files ──────────────────────────────────────
    def find_large_files(self, min_mb: int = 100) -> List[Dict]:
        dirs = [
            self.home / "Downloads",
            self.home / "Documents",
            self.home / "Desktop",
        ]
        mn = min_mb * 1024 * 1024
        out = []

        for d in dirs:
            if not d.exists():
                continue
            try:
                for f in d.rglob("*"):
                    if is_sys_file(f):
                        continue
                    try:
                        st = safe_stat(f)
                        if st and f.is_file() and st.st_size >= mn:
                            out.append({
                                "name": f.name,
                                "path": str(f),
                                "size_bytes": st.st_size,
                                "size_mb": round(st.st_size / 1024**2, 1),
                                "size_display": fmt_size(st.st_size),
                                "modified": datetime.fromtimestamp(st.st_mtime).strftime("%Y-%m-%d"),
                                "location": d.name,
                            })
                    except (PermissionError, OSError):
                        continue
            except (PermissionError, OSError):
                continue

        return sorted(out, key=lambda x: x["size_bytes"], reverse=True)[:20]

    # ── Duplicate Finder ─────────────────────────────────
    def find_duplicates(self) -> List[Dict]:
        dirs = [
            self.home / "Downloads",
            self.home / "Documents",
            self.home / "Desktop",
        ]
        sm = defaultdict(list)

        for d in dirs:
            if not d.exists():
                continue
            try:
                for f in d.rglob("*"):
                    if is_sys_file(f):
                        continue
                    try:
                        st = safe_stat(f)
                        if st and f.is_file() and st.st_size > 4096:
                            sm[st.st_size].append(f)
                    except (PermissionError, OSError):
                        continue
            except (PermissionError, OSError):
                continue

        dups = []
        for size, files in sm.items():
            if len(files) < 2:
                continue
            hm = defaultdict(list)
            for f in files:
                try:
                    h = hashlib.md5()
                    with open(f, "rb") as fh:
                        for chunk in iter(lambda: fh.read(8192), b""):
                            h.update(chunk)
                    hm[h.hexdigest()].append(f)
                except (PermissionError, OSError):
                    continue

            for fhash, matched in hm.items():
                if len(matched) >= 2:
                    wasted = (len(matched) - 1) * size
                    dups.append({
                        "hash": fhash[:12],
                        "size_bytes": size,
                        "size_mb": round(size / 1024**2, 2),
                        "size_display": fmt_size(size),
                        "count": len(matched),
                        "files": [
                            {"name": f.name, "path": str(f), "location": f.parent.name}
                            for f in matched
                        ],
                        "wasted_mb": round(wasted / 1024**2, 2),
                        "wasted_display": fmt_size(wasted),
                    })

        return sorted(dups, key=lambda x: x["wasted_mb"], reverse=True)[:15]

    # ── Downloads Analysis ───────────────────────────────
    def analyze_downloads(self) -> Dict:
        dl = self.home / "Downloads"
        if not dl.exists():
            return {
                "total_size_mb": 0, "total_display": "0 B",
                "file_count": 0, "old_files": [], "by_type": [],
            }

        total = 0
        count = 0
        old = []
        bt = defaultdict(lambda: {"count": 0, "size": 0})
        cutoff = datetime.now() - timedelta(days=30)

        try:
            for f in dl.iterdir():
                if is_sys_file(f):
                    continue
                try:
                    if f.is_file():
                        st = safe_stat(f)
                        if not st:
                            continue
                        total += st.st_size
                        count += 1
                        ext = f.suffix.lower() or "no ext"
                        bt[ext]["count"] += 1
                        bt[ext]["size"] += st.st_size
                        mt = datetime.fromtimestamp(st.st_mtime)
                        if mt < cutoff:
                            old.append({
                                "name": f.name,
                                "path": str(f),
                                "size_bytes": st.st_size,
                                "size_display": fmt_size(st.st_size),
                                "size_mb": round(st.st_size / 1024**2, 2),
                                "age_days": (datetime.now() - mt).days,
                                "modified": mt.strftime("%Y-%m-%d"),
                            })
                except (PermissionError, OSError):
                    continue
        except (PermissionError, OSError):
            pass

        ts = [
            {"type": k, "count": v["count"], "size_display": fmt_size(v["size"])}
            for k, v in sorted(bt.items(), key=lambda x: x[1]["size"], reverse=True)[:10]
        ]

        return {
            "total_size_mb": round(total / 1024**2, 1),
            "total_display": fmt_size(total),
            "file_count": count,
            "old_files": sorted(old, key=lambda x: x["age_days"], reverse=True)[:20],
            "by_type": ts,
        }

    # ── Startup Items ────────────────────────────────────
    def get_startup_items(self) -> List[Dict]:
        items = []

        if self.is_mac:
            for d in [
                self.home / "Library" / "LaunchAgents",
                Path("/Library/LaunchAgents"),
                Path("/Library/LaunchDaemons"),
            ]:
                if not d.exists():
                    continue
                try:
                    for f in d.iterdir():
                        if f.suffix == ".plist" and not is_sys_file(f):
                            items.append({"name": f.stem, "path": str(f), "location": d.name})
                except (PermissionError, OSError):
                    continue

        elif self.is_win:
            # Windows startup folders
            startup_dirs = []
            appdata = os.environ.get("APPDATA")
            if appdata:
                startup_dirs.append(
                    Path(appdata) / "Microsoft" / "Windows" / "Start Menu" / "Programs" / "Startup"
                )
            startup_dirs.append(
                Path("C:/ProgramData/Microsoft/Windows/Start Menu/Programs/Startup")
            )

            for d in startup_dirs:
                if not d.exists():
                    continue
                try:
                    for f in d.iterdir():
                        items.append({"name": f.stem, "path": str(f), "location": d.name})
                except (PermissionError, OSError):
                    continue

            # Also check registry-based startup items via wmic
            try:
                result = subprocess.run(
                    ["wmic", "startup", "get", "caption", "/format:list"],
                    capture_output=True, text=True, timeout=10,
                    creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
                )
                if result.returncode == 0:
                    for line in result.stdout.splitlines():
                        line = line.strip()
                        if line.startswith("Caption=") and line[8:]:
                            name = line[8:]
                            if name and name not in [i["name"] for i in items]:
                                items.append({"name": name, "path": "Registry", "location": "Registry"})
            except Exception:
                pass

        return items

    # ── Top Processes ────────────────────────────────────
    def get_top_processes(self, limit: int = 10) -> List[Dict]:
        # Prime CPU percentages
        for p in psutil.process_iter(["cpu_percent"]):
            try:
                p.cpu_percent()
            except (psutil.NoSuchProcess, psutil.AccessDenied, psutil.ZombieProcess):
                pass
        time.sleep(1.0)

        groups = defaultdict(lambda: {"cpu": 0.0, "mem": 0.0, "count": 0})
        for p in psutil.process_iter(["pid", "name", "cpu_percent", "memory_percent"]):
            try:
                i = p.info
                n = i.get("name") or "Unknown"
                groups[n]["cpu"] += (i.get("cpu_percent") or 0)
                groups[n]["mem"] += (i.get("memory_percent") or 0)
                groups[n]["count"] += 1
            except (psutil.NoSuchProcess, psutil.AccessDenied, psutil.ZombieProcess):
                continue

        out = []
        for name, d in groups.items():
            if d["cpu"] < 0.1 and d["mem"] < 0.3:
                continue
            label = f"{name} ({d['count']})" if d["count"] > 1 else name
            out.append({
                "name": label,
                "cpu_percent": round(d["cpu"], 1),
                "memory_percent": round(d["mem"], 1),
            })

        return sorted(out, key=lambda x: x["cpu_percent"] + x["memory_percent"], reverse=True)[:limit]


    # ── App last-used detection (Mac) ────────────────────
    def _running_mac_apps(self) -> set:
        """Names of .app bundles running right now."""
        names = set()
        try:
            for p in psutil.process_iter(["exe"]):
                exe = p.info.get("exe") or ""
                i = exe.find(".app/")
                if i != -1:
                    names.add(Path(exe[:i + 4]).stem)
        except Exception:
            pass
        return names

    def _mac_last_used(self, app_path: Path, running: set):
        """Return (datetime or None, display text). Uses the most recent of:
        running now, Spotlight last-used date, and app data folder activity."""
        if app_path.stem in running:
            return datetime.now(), "In use now"

        stamps = []

        # Spotlight (often empty on managed/work Macs)
        try:
            r = subprocess.run(
                ["mdls", "-name", "kMDItemLastUsedDate", "-raw", str(app_path)],
                capture_output=True, text=True, timeout=5,
            )
            ds = r.stdout.strip()
            if ds and ds != "(null)":
                lu = datetime.strptime(ds[:25], "%Y-%m-%d %H:%M:%S %z")
                stamps.append(lu.astimezone().replace(tzinfo=None).timestamp())
        except Exception:
            pass

        # App data folders, located via the bundle ID
        bundle_id = None
        try:
            import plistlib
            with open(app_path / "Contents" / "Info.plist", "rb") as fh:
                bundle_id = plistlib.load(fh).get("CFBundleIdentifier")
        except Exception:
            pass

        lib = self.home / "Library"
        candidates = [lib / "Application Support" / app_path.stem]
        if bundle_id:
            candidates += [
                lib / "Containers" / bundle_id / "Data" / "Library" / "Preferences",
                lib / "Containers" / bundle_id / "Data" / "Library" / "Caches",
                lib / "Preferences" / f"{bundle_id}.plist",
                lib / "Caches" / bundle_id,
                lib / "Application Support" / bundle_id,
                lib / "HTTPStorages" / bundle_id,
                lib / "Saved Application State" / f"{bundle_id}.savedState",
            ]
        for c in candidates:
            try:
                stamps.append(c.stat().st_mtime)
                if c.is_dir():
                    for n, child in enumerate(c.iterdir()):
                        if n >= 200:
                            break
                        try:
                            stamps.append(child.stat().st_mtime)
                        except OSError:
                            pass
            except OSError:
                continue

        if stamps:
            lu = datetime.fromtimestamp(max(stamps))
            return lu, lu.strftime("%Y-%m-%d")
        return None, "Not tracked"

    # ── Installed Apps Analysis ──────────────────────────
    def scan_installed_apps(self) -> List[Dict]:
        """Scan installed applications with size and last-used date."""
        apps = []

        if self.is_mac:
            app_dirs = [Path("/Applications"), self.home / "Applications"]
            running_apps = self._running_mac_apps()
            for app_dir in app_dirs:
                if not app_dir.exists():
                    continue
                try:
                    for item in app_dir.iterdir():
                        if not item.name.endswith(".app"):
                            continue
                        if is_sys_file(item):
                            continue
                        try:
                            size = self._dir_size(item)
                            if size < 1024 * 1024:
                                continue
                            lu, last_used_str = self._mac_last_used(item, running_apps)
                            days_unused = max(0, (datetime.now() - lu).days) if lu else -1
                            apps.append({
                                "name": item.stem,
                                "path": str(item),
                                "size_bytes": size,
                                "size_mb": round(size / 1024**2, 1),
                                "size_display": fmt_size(size),
                                "last_used": last_used_str,
                                "days_unused": days_unused,
                                "is_unused": days_unused > 180,
                                "is_unknown": days_unused == -1,
                            })
                        except (PermissionError, OSError):
                            continue
                except (PermissionError, OSError):
                    continue

        elif self.is_win:
            prog_dirs = [
                Path("C:/Program Files"),
                Path("C:/Program Files (x86)"),
                self.home / "AppData" / "Local" / "Programs",
            ]
            for prog_dir in prog_dirs:
                if not prog_dir.exists():
                    continue
                try:
                    for item in prog_dir.iterdir():
                        if not item.is_dir() or is_sys_file(item):
                            continue
                        try:
                            size = self._dir_size(item)
                            if size < 5 * 1024 * 1024:
                                continue
                            last_used_str = "Unknown"
                            days_unused = -1
                            try:
                                newest = 0
                                for f in item.rglob("*.exe"):
                                    st = safe_stat(f)
                                    if st and st.st_atime > newest:
                                        newest = st.st_atime
                                if newest > 0:
                                    lu = datetime.fromtimestamp(newest)
                                    last_used_str = lu.strftime("%Y-%m-%d")
                                    days_unused = (datetime.now() - lu).days
                            except Exception:
                                pass
                            apps.append({
                                "name": item.name,
                                "path": str(item),
                                "size_bytes": size,
                                "size_mb": round(size / 1024**2, 1),
                                "size_display": fmt_size(size),
                                "last_used": last_used_str,
                                "days_unused": days_unused,
                                "is_unused": days_unused > 180,
                                "is_unknown": days_unused == -1,
                            })
                        except (PermissionError, OSError):
                            continue
                except (PermissionError, OSError):
                    continue

        apps.sort(key=lambda x: (
            0 if x["is_unused"] else 1,
            -x["days_unused"] if x["days_unused"] > 0 else 0,
            -x["size_bytes"],
        ))
        return apps

    # ── Helpers ──────────────────────────────────────────
    def _dir_size(self, path: Path) -> int:
        t = 0
        try:
            for f in path.rglob("*"):
                try:
                    if f.is_file():
                        st = safe_stat(f)
                        if st:
                            t += st.st_size
                except (PermissionError, OSError):
                    continue
        except (PermissionError, OSError):
            pass
        return t
