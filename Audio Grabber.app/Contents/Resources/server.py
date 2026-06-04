#!/usr/bin/env python3
"""Audio Grabber — local GUI server for yt-dlp.
Cross-platform (macOS, Windows, Linux), Python 3 stdlib only.
Manages its own yt-dlp copy in the per-user app data folder.
https://github.com/ — MIT license
"""
import json
import os
import re
import shlex
import shutil
import subprocess
import sys
import threading
import time
import urllib.request
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

APP_NAME = "AudioGrabber"
IS_MAC = sys.platform == "darwin"
IS_WIN = os.name == "nt"
IS_LIN = not IS_MAC and not IS_WIN

if IS_MAC:
    SUPPORT_DIR = os.path.expanduser(f"~/Library/Application Support/{APP_NAME}")
elif IS_WIN:
    SUPPORT_DIR = os.path.join(os.environ.get("APPDATA",
                               os.path.expanduser("~")), APP_NAME)
else:
    SUPPORT_DIR = os.path.join(os.environ.get("XDG_CONFIG_HOME",
                               os.path.expanduser("~/.config")), APP_NAME)

YTDLP_PATH = os.path.join(SUPPORT_DIR, "yt-dlp")  # zipimport build, runs with python3
SETTINGS_PATH = os.path.join(SUPPORT_DIR, "settings.json")
PORT_PATH = os.path.join(SUPPORT_DIR, "port")
RESOURCES = os.path.dirname(os.path.abspath(__file__))
YTDLP_URL = "https://github.com/yt-dlp/yt-dlp/releases/latest/download/yt-dlp"
DEFAULT_PORT = 8765
NO_WINDOW = 0x08000000 if IS_WIN else 0  # subprocess.CREATE_NO_WINDOW

os.makedirs(SUPPORT_DIR, exist_ok=True)

# ---------------------------------------------------------------- state
LOCK = threading.Lock()
JOBS = []
JOB_SEQ = [0]
LAST_POLL = [time.time()]
INSTALL = {"state": "idle", "msg": ""}   # idle|working|done|error


def run_quiet(args, **kw):
    kw.setdefault("capture_output", True)
    kw.setdefault("text", True)
    if IS_WIN:
        kw.setdefault("creationflags", NO_WINDOW)
    return subprocess.run(args, **kw)


def load_settings():
    try:
        with open(SETTINGS_PATH) as f:
            return json.load(f)
    except Exception:
        return {}


def save_settings(s):
    try:
        with open(SETTINGS_PATH, "w") as f:
            json.dump(s, f, indent=2)
    except Exception:
        pass


def find_ffmpeg():
    cands = [shutil.which("ffmpeg"), os.path.join(SUPPORT_DIR, "ffmpeg")]
    if IS_MAC:
        cands += ["/opt/homebrew/bin/ffmpeg", "/usr/local/bin/ffmpeg"]
    if IS_WIN:
        cands += [r"C:\ffmpeg\bin\ffmpeg.exe",
                  os.path.expandvars(r"%LOCALAPPDATA%\Microsoft\WinGet\Links\ffmpeg.exe")]
    for c in cands:
        if c and os.path.isfile(c) and os.access(c, os.X_OK):
            return c
    return None


def ytdlp_cmd():
    """Return argv prefix for invoking yt-dlp, or None if unavailable."""
    if os.path.isfile(YTDLP_PATH):
        return [sys.executable, YTDLP_PATH]
    cands = [shutil.which("yt-dlp")]
    if IS_MAC:
        cands += ["/opt/homebrew/bin/yt-dlp", "/usr/local/bin/yt-dlp",
                  os.path.expanduser("~/.local/bin/yt-dlp")]
    if IS_LIN:
        cands += [os.path.expanduser("~/.local/bin/yt-dlp")]
    for c in cands:
        if c and os.path.isfile(c) and os.access(c, os.X_OK):
            return [c]
    return None


def ytdlp_version():
    cmd = ytdlp_cmd()
    if not cmd:
        return None
    try:
        out = run_quiet(cmd + ["--version"], timeout=60)
        v = out.stdout.strip()
        return v if re.match(r"^[\d.]+$", v or "") else (v or None)
    except Exception:
        return None


def install_ytdlp():
    INSTALL.update(state="working", msg="Downloading latest yt-dlp…")
    tmp = YTDLP_PATH + ".tmp"
    err = ""
    curl = shutil.which("curl")
    if curl:
        try:
            r = run_quiet([curl, "-fsSL", "--retry", "2", "-o", tmp, YTDLP_URL],
                          timeout=300)
            if r.returncode != 0:
                err = (r.stderr or "").strip() or f"curl exit {r.returncode}"
        except Exception as e:
            err = str(e)
    else:
        err = "no curl"
    if err or not os.path.isfile(tmp) or os.path.getsize(tmp) < 100000:
        try:  # fallback: urllib
            req = urllib.request.Request(YTDLP_URL,
                                         headers={"User-Agent": "AudioGrabber/1.0"})
            with urllib.request.urlopen(req, timeout=120) as r, open(tmp, "wb") as f:
                shutil.copyfileobj(r, f)
            err = ""
        except Exception as e:
            INSTALL.update(state="error",
                           msg=f"Download failed ({err or 'curl'} / {e})")
            return
    try:
        os.chmod(tmp, 0o755)
        os.replace(tmp, YTDLP_PATH)
        v = ytdlp_version()
        if not v:
            os.remove(YTDLP_PATH)
            INSTALL.update(state="error",
                           msg="Downloaded file did not run — Python 3.9+ required.")
            return
        INSTALL.update(state="done", msg=f"yt-dlp {v} ready")
    except Exception as e:
        INSTALL.update(state="error", msg=f"Install failed: {e}")


# ---------------------------------------------------------------- os glue
def notify(msg):
    msg = msg.replace('"', "'")[:120]
    try:
        if IS_MAC:
            run_quiet(["osascript", "-e",
                       f'display notification "{msg}" with title "Audio Grabber"'],
                      timeout=10)
        elif IS_LIN and shutil.which("notify-send"):
            run_quiet(["notify-send", "Audio Grabber", msg], timeout=10)
        elif IS_WIN:
            ps = ("Add-Type -AssemblyName System.Windows.Forms;"
                  "Add-Type -AssemblyName System.Drawing;"
                  "$n=New-Object System.Windows.Forms.NotifyIcon;"
                  "$n.Icon=[System.Drawing.SystemIcons]::Information;"
                  "$n.Visible=$true;"
                  f"$n.ShowBalloonTip(5000,'Audio Grabber','{msg}',"
                  "[System.Windows.Forms.ToolTipIcon]::Info);"
                  "Start-Sleep 6;$n.Dispose()")
            subprocess.Popen(["powershell", "-NoProfile", "-Command", ps],
                             creationflags=NO_WINDOW,
                             stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    except Exception:
        pass


def choose_folder():
    try:
        if IS_MAC:
            script = ('tell application "System Events" to activate\n'
                      'POSIX path of (choose folder with prompt "Choose download folder")')
            r = run_quiet(["osascript", "-e", script], timeout=300)
            return r.stdout.strip()
        if IS_WIN:
            ps = ("Add-Type -AssemblyName System.Windows.Forms;"
                  "$f=New-Object System.Windows.Forms.FolderBrowserDialog;"
                  "$f.Description='Choose download folder';"
                  "if($f.ShowDialog() -eq 'OK'){Write-Output $f.SelectedPath}")
            r = run_quiet(["powershell", "-NoProfile", "-Command", ps], timeout=300)
            return r.stdout.strip()
        if shutil.which("zenity"):
            r = run_quiet(["zenity", "--file-selection", "--directory",
                           "--title=Choose download folder"], timeout=300)
            return r.stdout.strip()
        if shutil.which("kdialog"):
            r = run_quiet(["kdialog", "--getexistingdirectory",
                           os.path.expanduser("~")], timeout=300)
            return r.stdout.strip()
    except Exception:
        pass
    return ""


def read_clipboard():
    try:
        if IS_MAC:
            return run_quiet(["pbpaste"], timeout=5).stdout
        if IS_WIN:
            return run_quiet(["powershell", "-NoProfile", "-Command",
                              "Get-Clipboard"], timeout=10).stdout
        if shutil.which("wl-paste"):
            return run_quiet(["wl-paste", "-n"], timeout=5).stdout
        if shutil.which("xclip"):
            return run_quiet(["xclip", "-selection", "clipboard", "-o"],
                             timeout=5).stdout
    except Exception:
        pass
    return ""


def open_path(path):
    try:
        if IS_MAC:
            subprocess.run(["open", path])
        elif IS_WIN:
            os.startfile(path)  # noqa
        else:
            subprocess.run(["xdg-open", path])
    except Exception:
        pass


def open_ui(url):
    """Open the UI in a Chrome-style app window (no tabs/address bar) if
    possible, otherwise fall back to the default browser."""
    cands = []
    if IS_MAC:
        cands = ["/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
                 "/Applications/Brave Browser.app/Contents/MacOS/Brave Browser",
                 "/Applications/Microsoft Edge.app/Contents/MacOS/Microsoft Edge"]
    elif IS_WIN:
        for env in ("PROGRAMFILES", "PROGRAMFILES(X86)", "LOCALAPPDATA"):
            base = os.environ.get(env)
            if base:
                cands += [os.path.join(base, r"Google\Chrome\Application\chrome.exe"),
                          os.path.join(base, r"Microsoft\Edge\Application\msedge.exe"),
                          os.path.join(base, r"BraveSoftware\Brave-Browser\Application\brave.exe")]
    else:
        for name in ("google-chrome", "google-chrome-stable", "chromium",
                     "chromium-browser", "brave-browser", "microsoft-edge"):
            p = shutil.which(name)
            if p:
                cands.append(p)
    for app in cands:
        if app and os.path.isfile(app):
            try:
                subprocess.Popen([app, f"--app={url}", "--window-size=940,980"],
                                 creationflags=NO_WINDOW,
                                 stdout=subprocess.DEVNULL,
                                 stderr=subprocess.DEVNULL)
                return
            except Exception:
                pass
    webbrowser.open(url)


# ---------------------------------------------------------------- jobs
def build_args(url, o, ffmpeg):
    settings_folder = o.get("folder") or os.path.expanduser("~/Downloads")
    template = o.get("template") or "%(title)s.%(ext)s"
    args = ["--newline", "--no-colors", "--ignore-config",
            "-o", os.path.join(settings_folder, template)]
    if ffmpeg:
        args += ["--ffmpeg-location", ffmpeg]

    if o.get("mode", "audio") == "audio":
        args += ["-x"]
        fmt = o.get("audioFormat", "mp3")
        if fmt != "best":
            args += ["--audio-format", fmt]
        args += ["--audio-quality", str(o.get("audioQuality", "0"))]
    else:
        h = o.get("maxHeight", "")
        if h and h != "best":
            args += ["-f", f"bv*[height<={h}]+ba/b[height<={h}]"]
        container = o.get("container", "")
        if container:
            args += ["--remux-video", container]
        if o.get("subtitles"):
            args += ["--write-subs", "--sub-langs", o.get("subLangs") or "en"]

    if o.get("embedThumbnail"):
        args += ["--embed-thumbnail"]
    if o.get("embedMetadata"):
        args += ["--embed-metadata"]
    if o.get("sponsorblock"):
        args += ["--sponsorblock-remove", "sponsor,selfpromo"]
    args += ["--yes-playlist"] if o.get("playlist") else ["--no-playlist"]
    if o.get("timeRange"):
        args += ["--download-sections", f"*{o['timeRange']}"]
    if o.get("rateLimit"):
        args += ["--limit-rate", o["rateLimit"]]
    if o.get("cookiesBrowser"):
        args += ["--cookies-from-browser", o["cookiesBrowser"]]
    if o.get("proxy"):
        args += ["--proxy", o["proxy"]]
    if o.get("extraArgs"):
        args += shlex.split(o["extraArgs"])
    args.append(url)
    return args


PCT_RE = re.compile(r"\[download\]\s+([\d.]+)%(?:\s+of\s+~?\s*([\d.]+\w+))?"
                    r"(?:\s+at\s+([\d.]+\w+/s))?(?:\s+ETA\s+([\d:]+))?")
DEST_RE = re.compile(r"(?:\[download\] Destination:|\[ExtractAudio\] Destination:"
                     r"|\[Merger\] Merging formats into) \"?(.+?)\"?$")


def run_job(job):
    cmd = ytdlp_cmd()
    if not cmd:
        job.update(status="error", log=job["log"] + ["yt-dlp is not installed yet."])
        return
    args = cmd + build_args(job["url"], job["options"], find_ffmpeg())
    job["log"].append("$ yt-dlp " + " ".join(shlex.quote(a) for a in args[len(cmd):]))
    try:
        proc = subprocess.Popen(args, stdout=subprocess.PIPE,
                                stderr=subprocess.STDOUT, text=True, bufsize=1,
                                creationflags=NO_WINDOW)
    except Exception as e:
        job.update(status="error")
        job["log"].append(f"Failed to start: {e}")
        return
    job["proc"] = proc
    for line in proc.stdout:
        line = line.rstrip()
        if not line:
            continue
        job["log"].append(line)
        if len(job["log"]) > 400:
            del job["log"][1:100]
        m = PCT_RE.search(line)
        if m:
            job["percent"] = float(m.group(1))
            job["speed"] = m.group(3) or ""
            job["eta"] = m.group(4) or ""
        m = DEST_RE.search(line)
        if m:
            job["dest"] = m.group(1)
            job["title"] = os.path.basename(m.group(1))
    proc.wait()
    job["proc"] = None
    if job["status"] == "cancelled":
        return
    if proc.returncode == 0:
        job.update(status="done", percent=100.0, speed="", eta="")
        notify(f"Done: {job.get('title') or job['url']}")
    else:
        job["status"] = "error"
        notify(f"Failed: {job.get('title') or job['url']}")


def worker_loop():
    while True:
        job = None
        with LOCK:
            running = sum(1 for j in JOBS if j["status"] == "running")
            limit = int(load_settings().get("parallel", 1))
            if running < max(1, limit):
                for j in JOBS:
                    if j["status"] == "queued":
                        j["status"] = "running"
                        job = j
                        break
        if job:
            threading.Thread(target=run_job, args=(job,), daemon=True).start()
        time.sleep(0.5)


def reaper_loop():
    """Exit when UI has been closed for a while and nothing is downloading."""
    while True:
        time.sleep(15)
        with LOCK:
            active = any(j["status"] in ("queued", "running") for j in JOBS)
        if not active and time.time() - LAST_POLL[0] > 180:
            os._exit(0)


def public_job(j):
    return {k: j[k] for k in ("id", "url", "title", "status", "percent",
                              "speed", "eta", "dest")}


# ---------------------------------------------------------------- http
class Handler(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def _send(self, code, body, ctype="application/json"):
        data = body if isinstance(body, bytes) else json.dumps(body).encode()
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def _body(self):
        n = int(self.headers.get("Content-Length", 0) or 0)
        return json.loads(self.rfile.read(n) or b"{}")

    def do_GET(self):
        if self.path in ("/", "/index.html"):
            with open(os.path.join(RESOURCES, "index.html"), "rb") as f:
                self._send(200, f.read(), "text/html; charset=utf-8")
        elif self.path == "/api/status":
            LAST_POLL[0] = time.time()
            s = load_settings()
            self._send(200, {
                "ytdlp": ytdlp_version(),
                "ffmpeg": bool(find_ffmpeg()),
                "install": INSTALL,
                "settings": s,
                "defaultFolder": s.get("folder") or os.path.expanduser("~/Downloads"),
            })
        elif self.path == "/api/jobs":
            LAST_POLL[0] = time.time()
            with LOCK:
                self._send(200, [public_job(j) for j in JOBS])
        elif self.path == "/api/clipboard":
            self._send(200, {"text": read_clipboard()[:2000]})
        elif self.path.startswith("/api/log/"):
            jid = int(self.path.rsplit("/", 1)[1])
            with LOCK:
                job = next((j for j in JOBS if j["id"] == jid), None)
            self._send(200, {"log": job["log"] if job else []})
        else:
            self._send(404, {"error": "not found"})

    def do_POST(self):
        try:
            self._route_post()
        except Exception as e:
            self._send(500, {"error": str(e)})

    def _route_post(self):
        if self.path == "/api/queue":
            b = self._body()
            urls = [u.strip() for u in b.get("urls", []) if u.strip()]
            opts = b.get("options", {})
            s = load_settings()
            s.update(folder=opts.get("folder", s.get("folder")), lastOptions=opts)
            save_settings(s)
            added = []
            with LOCK:
                for u in urls:
                    JOB_SEQ[0] += 1
                    job = {"id": JOB_SEQ[0], "url": u, "title": u, "status": "queued",
                           "percent": 0.0, "speed": "", "eta": "", "dest": "",
                           "options": opts, "log": [], "proc": None}
                    JOBS.append(job)
                    added.append(job["id"])
            self._send(200, {"added": added})
        elif self.path == "/api/cancel":
            jid = self._body().get("id")
            with LOCK:
                job = next((j for j in JOBS if j["id"] == jid), None)
            if job:
                job["status"] = "cancelled"
                p = job.get("proc")
                if p:
                    try:
                        p.terminate()
                    except Exception:
                        pass
            self._send(200, {"ok": True})
        elif self.path == "/api/clear":
            with LOCK:
                JOBS[:] = [j for j in JOBS if j["status"] in ("queued", "running")]
            self._send(200, {"ok": True})
        elif self.path == "/api/retry":
            jid = self._body().get("id")
            with LOCK:
                job = next((j for j in JOBS if j["id"] == jid), None)
                if job and job["status"] in ("error", "cancelled"):
                    job.update(status="queued", percent=0.0, log=[])
            self._send(200, {"ok": True})
        elif self.path == "/api/install-ytdlp":
            if INSTALL["state"] != "working":
                threading.Thread(target=install_ytdlp, daemon=True).start()
            self._send(200, {"ok": True})
        elif self.path == "/api/choose-folder":
            folder = choose_folder()
            if folder:
                s = load_settings()
                s["folder"] = folder
                save_settings(s)
            self._send(200, {"folder": folder})
        elif self.path == "/api/open-folder":
            folder = self._body().get("folder") or load_settings().get("folder") \
                or os.path.expanduser("~/Downloads")
            open_path(folder)
            self._send(200, {"ok": True})
        elif self.path == "/api/settings":
            s = load_settings()
            s.update(self._body())
            save_settings(s)
            self._send(200, {"ok": True})
        elif self.path == "/api/quit":
            self._send(200, {"ok": True})
            threading.Thread(target=lambda: (time.sleep(0.3), os._exit(0)),
                             daemon=True).start()
        else:
            self._send(404, {"error": "not found"})


# ---------------------------------------------------------------- main
def already_running():
    try:
        with open(PORT_PATH) as f:
            port = int(f.read().strip())
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/api/status", timeout=2) as r:
            if r.status == 200:
                return port
    except Exception:
        pass
    return None


def main():
    port = already_running()
    if port:
        open_ui(f"http://127.0.0.1:{port}/")
        return
    port = DEFAULT_PORT
    while True:
        try:
            server = ThreadingHTTPServer(("127.0.0.1", port), Handler)
            break
        except OSError:
            port += 1
    with open(PORT_PATH, "w") as f:
        f.write(str(port))
    if not ytdlp_cmd():
        threading.Thread(target=install_ytdlp, daemon=True).start()
    threading.Thread(target=worker_loop, daemon=True).start()
    threading.Thread(target=reaper_loop, daemon=True).start()
    open_ui(f"http://127.0.0.1:{port}/")
    server.serve_forever()


if __name__ == "__main__":
    main()
