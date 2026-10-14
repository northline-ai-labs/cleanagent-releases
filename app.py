"""
CleanAgent — Flask Application v2.0
Thread-safe, AI-powered recommendations, auto-browser launch.
"""

import os
import sys
import threading
import webbrowser
import platform
import logging
from flask import Flask, render_template, jsonify, request, send_from_directory
from scanner import SystemScanner
from cleaner import SystemCleaner
from ai_advisor import AIAdvisor

# ── Logging ──────────────────────────────────────────────
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger("cleanagent")

# ── Resolve paths for PyInstaller bundled mode ───────────
def resource_path(relative_path):
    """Get absolute path to resource — works for dev and PyInstaller."""
    if getattr(sys, "_MEIPASS", None):
        return os.path.join(sys._MEIPASS, relative_path)
    return os.path.join(os.path.abspath(os.path.dirname(__file__)), relative_path)


template_dir = resource_path("templates")
static_dir = resource_path("static")
app = Flask(__name__, template_folder=template_dir, static_folder=static_dir)

# ── Thread-safe state ────────────────────────────────────
_lock = threading.Lock()
scan_results = {}
scan_in_progress = False
scan_progress = {"stage": "", "percent": 0}
pre_cleanup_score = None


# ── Routes ───────────────────────────────────────────────

@app.route("/")
def dashboard():
    return render_template("dashboard.html")


@app.route("/api/scan", methods=["POST"])
def start_scan():
    global scan_results, scan_in_progress, scan_progress

    with _lock:
        if scan_in_progress:
            return jsonify({"error": "Scan already in progress"}), 409

    def run_scan():
        global scan_results, scan_in_progress, scan_progress
        with _lock:
            scan_in_progress = True
            scan_progress = {"stage": "Starting...", "percent": 0}

        try:
            scanner = SystemScanner()

            with _lock:
                scan_progress = {"stage": "Collecting system metrics...", "percent": 10}
            metrics = scanner.get_metrics()

            with _lock:
                scan_progress = {"stage": "Analyzing caches...", "percent": 25}
            caches = scanner.analyze_caches()

            with _lock:
                scan_progress = {"stage": "Finding large files...", "percent": 40}
            large_files = scanner.find_large_files()

            with _lock:
                scan_progress = {"stage": "Detecting duplicates...", "percent": 55}
            duplicates = scanner.find_duplicates()

            with _lock:
                scan_progress = {"stage": "Analyzing downloads...", "percent": 70}
            downloads = scanner.analyze_downloads()


            with _lock:
                scan_progress = {"stage": "Scanning installed apps...", "percent": 78}
            installed_apps = scanner.scan_installed_apps()
            unused_apps = [a for a in installed_apps if a.get("is_unused")]

            with _lock:
                scan_progress = {"stage": "Checking processes & startup...", "percent": 85}
            startup = scanner.get_startup_items()
            processes = scanner.get_top_processes()

            # Recoverable space calculation
            recoverable = caches["total_size_mb"]
            recoverable += sum(d["wasted_mb"] for d in duplicates)
            recoverable += sum(f["size_mb"] for f in downloads.get("old_files", []))

            raw_data = {
                "metrics": metrics.to_dict(),
                "caches": caches,
                "large_files": large_files,
                "duplicates": duplicates,
                "downloads": downloads,
                "installed_apps": installed_apps,
                "unused_apps": unused_apps,
                "startup_items": startup,
                "top_processes": processes,
                "summary": {
                    "performance_score": metrics.performance_score,
                    "recoverable_space_mb": round(recoverable, 1),
                    "total_issues": (
                        (1 if caches["total_size_mb"] > 500 else 0)
                        + len(duplicates)
                        + (1 if len(downloads.get("old_files", [])) > 5 else 0)
                        + (1 if metrics.memory_usage > 80 else 0)
                        + (1 if metrics.disk_usage > 85 else 0)
                    ),
                },
            }

            # AI recommendations
            with _lock:
                scan_progress = {"stage": "Generating AI recommendations...", "percent": 95}
            try:
                advisor = AIAdvisor()
                ai_result = advisor.analyze(raw_data)
                raw_data["ai_advice"] = ai_result
            except Exception as e:
                log.warning(f"AI advisor failed (non-fatal): {e}")
                raw_data["ai_advice"] = {"recommendations": [], "summary": {"verdict": "unknown", "verdict_text": "Analysis unavailable."}}

            with _lock:
                scan_progress = {"stage": "Complete!", "percent": 100}
                scan_results = raw_data
                log.info(f"Scan complete — score: {metrics.performance_score}, recoverable: {recoverable:.1f} MB")

        except Exception as e:
            log.error(f"Scan failed: {e}", exc_info=True)
            with _lock:
                scan_results = {"error": str(e)}
        finally:
            with _lock:
                scan_in_progress = False

    threading.Thread(target=run_scan, daemon=True).start()
    return jsonify({"status": "started"})


@app.route("/api/scan/progress")
def get_progress():
    with _lock:
        return jsonify({
            "in_progress": scan_in_progress,
            "stage": scan_progress.get("stage", ""),
            "percent": scan_progress.get("percent", 0),
        })


@app.route("/api/scan/results")
def get_results():
    with _lock:
        if scan_in_progress:
            return jsonify({"status": "scanning"})
        if not scan_results:
            return jsonify({"status": "no_scan"})
        return jsonify({"status": "complete", "data": scan_results})


@app.route("/api/cleanup", methods=["POST"])
def run_cleanup():
    global pre_cleanup_score
    try:
        scanner = SystemScanner()
        pre_metrics = scanner.get_metrics()
        pre_cleanup_score = {
            "disk_free_gb": pre_metrics.disk_free_gb,
            "performance_score": pre_metrics.performance_score,
        }

        cleaner = SystemCleaner()
        results = cleaner.run_all()

        post_metrics = scanner.get_metrics()
        results["comparison"] = {
            "disk_before_gb": pre_cleanup_score["disk_free_gb"],
            "disk_after_gb": post_metrics.disk_free_gb,
            "disk_recovered_gb": round(post_metrics.disk_free_gb - pre_cleanup_score["disk_free_gb"], 1),
            "score_before": pre_cleanup_score["performance_score"],
            "score_after": post_metrics.performance_score,
        }

        log.info(f"Cleanup complete — freed {results['total_space_freed_display']}")
        return jsonify({"status": "complete", "data": results})
    except Exception as e:
        log.error(f"Cleanup failed: {e}", exc_info=True)
        return jsonify({"error": str(e)}), 500


@app.route("/api/cleanup/duplicates", methods=["POST"])
def cleanup_duplicates():
    try:
        data = request.get_json()
        groups = data.get("groups", [])
        if not groups:
            return jsonify({"error": "No groups provided"}), 400
        result = SystemCleaner.delete_duplicates(groups)
        return jsonify({"status": "complete", "data": result})
    except Exception as e:
        log.error(f"Duplicate cleanup failed: {e}", exc_info=True)
        return jsonify({"error": str(e)}), 500


@app.route("/api/cleanup/files", methods=["POST"])
def cleanup_files():
    try:
        data = request.get_json()
        paths = data.get("paths", [])
        if not paths:
            return jsonify({"error": "No paths provided"}), 400
        result = SystemCleaner.delete_files(paths)
        return jsonify({"status": "complete", "data": result})
    except Exception as e:
        log.error(f"File cleanup failed: {e}", exc_info=True)
        return jsonify({"error": str(e)}), 500




@app.route("/api/uninstall", methods=["POST"])
def uninstall_app():
    try:
        data = request.get_json()
        app_path = data.get("path", "")
        if not app_path:
            return jsonify({"error": "No app path provided"}), 400
        result = SystemCleaner.uninstall_app(app_path)
        if result.get("success"):
            return jsonify({"status": "complete", "data": result})
        else:
            return jsonify({"error": result.get("error", "Unknown error")}), 500
    except Exception as e:
        log.error(f"Uninstall failed: {e}", exc_info=True)
        return jsonify({"error": str(e)}), 500

@app.route("/api/metrics")
def live_metrics():
    try:
        scanner = SystemScanner()
        return jsonify(scanner.get_metrics().to_dict())
    except Exception as e:
        log.error(f"Metrics failed: {e}")
        return jsonify({"error": str(e)}), 500


@app.route("/api/info")
def app_info():
    return jsonify({
        "name": "CleanAgent",
        "version": "2.0.0",
        "platform": platform.system(),
        "python": platform.python_version(),
    })


# ── Auto-open browser ───────────────────────────────────
def open_browser():
    """Open the dashboard in the default browser after a short delay."""
    import time
    time.sleep(1.5)
    webbrowser.open("http://127.0.0.1:5000")


@app.route("/api/archive/pick-folder", methods=["POST"])
def archive_pick_folder():
    """Open native folder picker; return chosen path."""
    result = SystemCleaner.pick_folder()
    return jsonify(result)


# Background archive job state
_archive_jobs = {}
_archive_lock = threading.Lock()

@app.route("/api/archive", methods=["POST"])
def archive_files():
    """Start an archive job in a background thread."""
    import uuid
    data = request.get_json(silent=True) or {}
    paths = data.get("paths", [])
    dest = data.get("dest", "")
    delete_originals = bool(data.get("delete_originals", False))

    if not paths:
        return jsonify({"success": False, "error": "No files selected"}), 400
    if not dest:
        return jsonify({"success": False, "error": "No destination provided"}), 400

    job_id = uuid.uuid4().hex
    with _archive_lock:
        _archive_jobs[job_id] = {"status": "running", "result": None}

    def worker():
        try:
            result = SystemCleaner.archive_files(paths, dest, delete_originals=delete_originals)
            with _archive_lock:
                _archive_jobs[job_id] = {"status": "done", "result": result}
        except Exception as e:
            with _archive_lock:
                _archive_jobs[job_id] = {"status": "done", "result": {"success": False, "error": str(e)}}

    threading.Thread(target=worker, daemon=True).start()
    return jsonify({"success": True, "job_id": job_id})


@app.route("/api/archive/status/<job_id>", methods=["GET"])
def archive_status(job_id):
    with _archive_lock:
        job = _archive_jobs.get(job_id)
    if not job:
        return jsonify({"status": "unknown"}), 404
    return jsonify(job)


class _Bridge:
    """In-process bridge: the window calls Flask routes directly. No server, no port."""
    def request(self, method, path, body=None):
        import json as _json
        try:
            with app.test_client() as c:
                kw = {"method": method}
                if body is not None:
                    kw["data"] = body
                    kw["content_type"] = "application/json"
                r = c.open(path, **kw)
                return {"status": r.status_code,
                        "body": r.get_data(as_text=True),
                        "ctype": r.content_type or "application/json"}
        except Exception as e:
            log.error(f"bridge error {method} {path}: {e}", exc_info=True)
            return {"status": 500, "body": _json.dumps({"error": str(e)}),
                    "ctype": "application/json"}


_FETCH_SHIM = """<script>
/* Route same-app fetch('/...') calls to Python in-process (no local server) */
(function () {
  var realFetch = window.fetch ? window.fetch.bind(window) : null;
  function bridgeReady() {
    return new Promise(function (res) {
      if (window.pywebview && window.pywebview.api && window.pywebview.api.request) return res();
      window.addEventListener('pywebviewready', function () { res(); }, { once: true });
    });
  }
  window.fetch = async function (input, init) {
    var url = typeof input === 'string' ? input : (input && input.url) || '';
    if (url.charAt(0) !== '/') return realFetch(input, init);
    init = init || {};
    await bridgeReady();
    var body = init.body == null ? null : (typeof init.body === 'string' ? init.body : JSON.stringify(init.body));
    var r = await window.pywebview.api.request((init.method || 'GET').toUpperCase(), url, body);
    return new Response(r.body, { status: r.status, headers: { 'Content-Type': r.ctype } });
  };
})();
</script>"""


def _render_index() -> str:
    import re
    with app.test_client() as c:
        html = c.get("/").get_data(as_text=True)
    m = re.search(r"<head[^>]*>", html, re.I)
    if m:
        return html[:m.end()] + _FETCH_SHIM + html[m.end():]
    return _FETCH_SHIM + html


def _free_port() -> int:
    import socket
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


def _run_server_with_browser():
    """Dev/fallback mode only: local server + default browser."""
    port = int(os.environ.get("PORT", 0)) or _free_port()
    url = f"http://127.0.0.1:{port}"
    log.info(f"CleanAgent (server mode) on {url}")
    threading.Timer(1.0, lambda: webbrowser.open(url)).start()
    app.run(host="127.0.0.1", port=port, debug=False, use_reloader=False)


if __name__ == "__main__":
    try:
        import webview
    except ImportError:
        webview = None

    if webview and not os.environ.get("CLEANAGENT_SERVER"):
        log.info("CleanAgent starting (native window, no local server)")
        webview.create_window(
            "CleanAgent",
            html=_render_index(),
            js_api=_Bridge(),
            width=1200, height=800, min_size=(900, 600),
        )
        webview.start()
    else:
        _run_server_with_browser()
