"""Home-tunnel proxy control panel for the TutorialMaker Space.

Runs a local authenticated HTTP forward proxy on your machine (your residential IP) and
exposes it to the internet through a RAW TCP tunnel (ngrok / bore), so the Hugging Face
Space can route youtube-transcript-api + yt-dlp through your home IP and dodge the
datacenter-IP block. One-click sets the resulting URL as the Space's YT_PROXY secret.

Why a TCP tunnel: a forward proxy speaks HTTP CONNECT, which only survives a transparent
TCP tunnel. HTTP reverse tunnels (Cloudflare quick-tunnel, Tailscale Funnel) do NOT work
for this — don't use them here.

Run:  python tools/home_proxy_panel.py
Deps: pip install proxy.py huggingface_hub requests   (ngrok or bore on PATH for tunneling)
"""
from __future__ import annotations

import json
import os
import platform
import queue
import re
import secrets
import shutil
import subprocess
import sys
import tarfile
import threading
import zipfile

CONFIG_PATH = os.path.join(os.path.expanduser("~"), ".tutorialmaker_proxy.json")
DEFAULT_REPO = "vivekchakraverty/TutorialMaker"
_NO_WINDOW = 0x08000000 if os.name == "nt" else 0

# Where to keep a downloaded bore binary. When frozen by PyInstaller sys.frozen is set and
# the binary lives next to the executable; otherwise next to this script.
APP_DIR = os.path.dirname(sys.executable if getattr(sys, "frozen", False)
                          else os.path.abspath(__file__))


def _bore_target_triple() -> tuple[str, str, str] | None:
    """(rust-triple, archive-ext, binary-name) for the current OS/arch, or None."""
    sysname = platform.system()
    mach = platform.machine().lower()
    is_arm = mach in ("arm64", "aarch64")
    if sysname == "Windows":
        return ("x86_64-pc-windows-msvc", ".zip", "bore.exe")
    if sysname == "Darwin":
        triple = "aarch64-apple-darwin" if is_arm else "x86_64-apple-darwin"
        return (triple, ".tar.gz", "bore")
    if sysname == "Linux":
        triple = "aarch64-unknown-linux-musl" if is_arm else "x86_64-unknown-linux-musl"
        return (triple, ".tar.gz", "bore")
    return None


def ensure_bore(log=lambda m: None) -> str | None:
    """Return a runnable bore path: PATH copy, a previously downloaded one, or a freshly
    downloaded release binary for this OS/arch. Returns None if it can't be obtained."""
    found = shutil.which("bore")
    if found:
        return found
    tri = _bore_target_triple()
    if not tri:
        log("[bore] no prebuilt binary for this platform — install bore manually.")
        return None
    triple, ext, binname = tri
    local = os.path.join(APP_DIR, binname)
    if os.path.exists(local):
        return local

    import urllib.request
    api = "https://api.github.com/repos/ekzhang/bore/releases/latest"
    try:
        log("[bore] not found — fetching latest release info…")
        req = urllib.request.Request(api, headers={"User-Agent": "tutorialmaker-panel"})
        with urllib.request.urlopen(req, timeout=30) as r:
            rel = json.load(r)
        asset = next((a for a in rel.get("assets", [])
                      if triple in a["name"] and a["name"].endswith(ext)), None)
        if not asset:
            log(f"[bore] no asset matching {triple}{ext} in the latest release.")
            return None
        url = asset["browser_download_url"]
        archive = os.path.join(APP_DIR, asset["name"])
        log(f"[bore] downloading {asset['name']}…")
        dl = urllib.request.Request(url, headers={"User-Agent": "tutorialmaker-panel"})
        with urllib.request.urlopen(dl, timeout=120) as resp, open(archive, "wb") as fh:
            shutil.copyfileobj(resp, fh)
        if ext == ".zip":
            with zipfile.ZipFile(archive) as z:
                z.extract(binname, APP_DIR)
        else:
            with tarfile.open(archive) as t:
                member = next((m for m in t.getmembers()
                               if os.path.basename(m.name) == binname), None)
                if member is None:
                    log("[bore] archive did not contain the bore binary.")
                    return None
                member.name = binname
                t.extract(member, APP_DIR)
        try:
            os.remove(archive)
        except OSError:
            pass
        if os.name != "nt":
            os.chmod(local, 0o755)
        log(f"[bore] ready at {local}")
        return local
    except Exception as exc:
        log(f"[bore] download failed: {type(exc).__name__}: {str(exc)[:160]}")
        return None


# ----------------------------------------------------------------- pure helpers (tested)
def build_proxy_url(host: str, port, user: str, pw: str) -> str:
    """Compose an http proxy URL, url-encoding the credentials."""
    from urllib.parse import quote
    auth = ""
    if user or pw:
        auth = f"{quote(user, safe='')}:{quote(pw, safe='')}@"
    return f"http://{auth}{host}:{port}"


def parse_ngrok_tunnels(json_text: str) -> str | None:
    """Return 'host:port' from ngrok's /api/tunnels JSON (first tcp tunnel)."""
    try:
        data = json.loads(json_text)
    except json.JSONDecodeError:
        return None
    for t in data.get("tunnels", []):
        pub = t.get("public_url", "")
        if pub.startswith("tcp://"):
            return pub[len("tcp://"):]
    return None


def parse_bore_endpoint(text: str) -> str | None:
    """Return 'bore.pub:PORT' parsed from bore's log output."""
    m = re.search(r"(bore\.pub:\d+)", text)
    if m:
        return m.group(1)
    m = re.search(r"listening at\s+([\w.-]+:\d+)", text)
    return m.group(1) if m else None


def hf_candidate_tokens() -> list[str]:
    """HF tokens to try: cached token, then any huggingface.co git-credential."""
    out, seen = [], set()
    env = dict(os.environ)
    env.pop("HF_TOKEN", None)
    env.pop("HUGGING_FACE_HUB_TOKEN", None)
    try:
        from huggingface_hub import get_token
        # get_token reads HF_TOKEN env first; temporarily clear it for the cached one
        saved = os.environ.pop("HF_TOKEN", None)
        try:
            t = get_token()
        finally:
            if saved is not None:
                os.environ["HF_TOKEN"] = saved
        if t and t not in seen:
            seen.add(t); out.append(t)
    except Exception:
        pass
    cred = os.path.join(os.path.expanduser("~"), ".git-credentials")
    if os.path.exists(cred):
        with open(cred, encoding="utf-8", errors="ignore") as fh:
            for line in fh:
                m = re.search(r"https://[^:]*:([^@]+)@huggingface\.co", line)
                if m and m.group(1) not in seen:
                    seen.add(m.group(1)); out.append(m.group(1))
    return out


def set_space_secret(repo: str, key: str, value: str, token: str | None = None) -> str:
    """Set a Space secret; return the authenticated username. Raises on failure."""
    from huggingface_hub import HfApi
    tokens = [token] if token else hf_candidate_tokens()
    last = None
    for tok in tokens:
        try:
            api = HfApi(token=tok)
            who = api.whoami()["name"]
            api.add_space_secret(repo_id=repo, key=key, value=value)
            return who
        except Exception as exc:
            last = exc
    raise RuntimeError(f"Could not set secret: {last}")


def test_through_proxy(proxy_url: str) -> dict:
    """Route a couple of requests through proxy_url; report egress IP + YouTube reach."""
    import requests
    proxies = {"http": proxy_url, "https": proxy_url}
    out = {}
    try:
        out["egress_ip"] = requests.get("https://api.ipify.org", proxies=proxies,
                                        timeout=25).text.strip()
    except Exception as exc:
        out["egress_ip"] = f"FAILED: {type(exc).__name__}: {str(exc)[:120]}"
    try:
        r = requests.get("https://www.youtube.com/robots.txt", proxies=proxies, timeout=25)
        out["youtube"] = f"reachable (HTTP {r.status_code})"
    except Exception as exc:
        out["youtube"] = f"FAILED: {type(exc).__name__}: {str(exc)[:120]}"
    return out


# ------------------------------------------------------------------- subprocess manager
class Proc:
    """A subprocess whose stdout/stderr lines are streamed into a queue."""

    def __init__(self, log_q: "queue.Queue", tag: str):
        self.p = None
        self.log_q = log_q
        self.tag = tag

    def start(self, cmd: list[str]):
        self.stop()
        self.log_q.put(f"[{self.tag}] $ " + " ".join(cmd))
        self.p = subprocess.Popen(
            cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            text=True, bufsize=1, creationflags=_NO_WINDOW)
        threading.Thread(target=self._pump, daemon=True).start()

    def _pump(self):
        for line in self.p.stdout:
            self.log_q.put(f"[{self.tag}] " + line.rstrip())

    def running(self) -> bool:
        return self.p is not None and self.p.poll() is None

    def stop(self):
        if self.running():
            try:
                self.p.terminate()
            except Exception:
                pass
        self.p = None


# ---------------------------------------------------------------------------- GUI shell
def main():
    import tkinter as tk
    from tkinter import ttk, messagebox

    cfg = {}
    if os.path.exists(CONFIG_PATH):
        try:
            cfg = json.load(open(CONFIG_PATH, encoding="utf-8"))
        except Exception:
            cfg = {}

    root = tk.Tk()
    root.title("TutorialMaker — Home Proxy Panel")
    root.geometry("760x620")
    log_q: "queue.Queue" = queue.Queue()
    proxy_proc = Proc(log_q, "proxy")
    tunnel_proc = Proc(log_q, "tunnel")
    state = {"endpoint": None}

    def v(name, default=""):
        return tk.StringVar(value=str(cfg.get(name, default)))

    port = v("port", "8899")
    user = v("user", "tm")
    pw = v("pw", secrets.token_urlsafe(10))
    provider = v("provider", "ngrok")
    ngrok_token = v("ngrok_token", "")
    manual_ep = v("manual_ep", "")
    repo = v("repo", DEFAULT_REPO)
    hf_token = v("hf_token", "")
    endpoint_var = tk.StringVar(value="(not running)")
    yturl_var = tk.StringVar(value="")
    status_var = tk.StringVar(value="idle")

    def log(msg):
        log_q.put(msg)

    def save_cfg():
        json.dump({"port": port.get(), "user": user.get(), "pw": pw.get(),
                   "provider": provider.get(), "ngrok_token": ngrok_token.get(),
                   "manual_ep": manual_ep.get(), "repo": repo.get()},
                  open(CONFIG_PATH, "w", encoding="utf-8"))
        log(f"[cfg] saved to {CONFIG_PATH}")

    def compute_yturl():
        ep = state["endpoint"]
        if not ep:
            yturl_var.set("")
            return None
        host, _, p = ep.partition(":")
        url = build_proxy_url(host, p, user.get(), pw.get())
        yturl_var.set(url)
        return url

    # --- proxy ---
    def start_proxy():
        auth = f"{user.get()}:{pw.get()}"
        pargs = ["--hostname", "127.0.0.1", "--port", port.get(), "--basic-auth", auth]
        if getattr(sys, "frozen", False):
            # In a PyInstaller build sys.executable is THIS app, not python, so `-m proxy`
            # won't work — re-invoke ourselves with the --run-proxy sentinel (see __main__).
            cmd = [sys.executable, "--run-proxy", *pargs]
        else:
            cmd = [sys.executable, "-m", "proxy", *pargs]
        proxy_proc.start(cmd)
        status_var.set("proxy starting…")

    def stop_proxy():
        proxy_proc.stop(); log("[proxy] stopped")

    # --- tunnel ---
    def start_tunnel():
        prov = provider.get()
        if prov == "manual":
            ep = manual_ep.get().strip()
            if not ep:
                messagebox.showwarning("Manual", "Enter host:port for the manual endpoint.")
                return
            state["endpoint"] = ep
            endpoint_var.set(ep); compute_yturl(); log(f"[tunnel] manual endpoint {ep}")
            return
        if prov == "ngrok":
            if ngrok_token.get().strip():
                subprocess.run(["ngrok", "config", "add-authtoken", ngrok_token.get().strip()],
                               creationflags=_NO_WINDOW)
            tunnel_proc.start(["ngrok", "tcp", port.get(), "--log", "stdout"])
            root.after(2500, _grab_ngrok)
        elif prov == "bore":
            status_var.set("preparing bore…")
            def work():
                bore = ensure_bore(log)
                if not bore:
                    log("[tunnel] bore unavailable — download bore.exe from "
                        "https://github.com/ekzhang/bore/releases or pick ngrok.")
                    status_var.set("bore unavailable")
                    return
                root.after(0, lambda: tunnel_proc.start(
                    [bore, "local", port.get(), "--to", "bore.pub"]))
                status_var.set("tunnel starting…")
            threading.Thread(target=work, daemon=True).start()
            return
        status_var.set("tunnel starting…")

    def _grab_ngrok():
        try:
            import requests
            j = requests.get("http://127.0.0.1:4040/api/tunnels", timeout=5).text
            ep = parse_ngrok_tunnels(j)
            if ep:
                state["endpoint"] = ep; endpoint_var.set(ep); compute_yturl()
                log(f"[tunnel] ngrok endpoint {ep}")
            else:
                log("[tunnel] ngrok: no tcp tunnel yet, retrying…"); root.after(2000, _grab_ngrok)
        except Exception as exc:
            log(f"[tunnel] ngrok api: {exc}"); root.after(2000, _grab_ngrok)

    def stop_tunnel():
        tunnel_proc.stop(); state["endpoint"] = None
        endpoint_var.set("(not running)"); yturl_var.set(""); log("[tunnel] stopped")

    # --- actions ---
    def copy_url():
        url = compute_yturl()
        if url:
            root.clipboard_clear(); root.clipboard_append(url); log("[yturl] copied")

    def do_test():
        url = compute_yturl()
        if not url:
            messagebox.showinfo("Test", "Start the tunnel first."); return
        status_var.set("testing…")
        def work():
            res = test_through_proxy(url)
            log(f"[test] egress IP: {res['egress_ip']}")
            log(f"[test] youtube: {res['youtube']}")
            status_var.set("test done")
        threading.Thread(target=work, daemon=True).start()

    def do_set_secret():
        url = compute_yturl()
        if not url:
            messagebox.showinfo("Secret", "Start the tunnel first."); return
        status_var.set("setting YT_PROXY…")
        def work():
            try:
                who = set_space_secret(repo.get().strip(), "YT_PROXY", url,
                                       hf_token.get().strip() or None)
                log(f"[hf] YT_PROXY set on {repo.get()} (as {who})")
                status_var.set("YT_PROXY set ✓")
            except Exception as exc:
                log(f"[hf] FAILED: {exc}"); status_var.set("secret failed")
        threading.Thread(target=work, daemon=True).start()

    # --- layout ---
    pad = {"padx": 6, "pady": 3}
    frm = ttk.Frame(root); frm.pack(fill="x", **pad)
    r = 0
    def row(label, var, show=None, width=42):
        nonlocal r
        ttk.Label(frm, text=label).grid(row=r, column=0, sticky="w", **pad)
        e = ttk.Entry(frm, textvariable=var, width=width, show=show)
        e.grid(row=r, column=1, columnspan=3, sticky="w", **pad); r += 1
        return e

    row("Proxy port", port, width=10)
    row("Proxy user", user, width=18)
    row("Proxy password", pw, width=24)
    ttk.Label(frm, text="Tunnel provider").grid(row=r, column=0, sticky="w", **pad)
    ttk.Combobox(frm, textvariable=provider, values=["ngrok", "bore", "manual"],
                 width=10, state="readonly").grid(row=r, column=1, sticky="w", **pad); r += 1
    row("ngrok authtoken (ngrok only)", ngrok_token, show="*")
    row("Manual endpoint host:port (manual only)", manual_ep, width=28)
    row("HF Space repo", repo, width=34)
    row("HF token (blank = auto-detect)", hf_token, show="*")

    btns = ttk.Frame(root); btns.pack(fill="x", **pad)
    for txt, fn in [("Start proxy", start_proxy), ("Stop proxy", stop_proxy),
                    ("Start tunnel", start_tunnel), ("Stop tunnel", stop_tunnel)]:
        ttk.Button(btns, text=txt, command=fn).pack(side="left", **pad)

    btns2 = ttk.Frame(root); btns2.pack(fill="x", **pad)
    for txt, fn in [("📋 Copy my Proxy URL", copy_url), ("Test proxy", do_test),
                    ("Save config", save_cfg),
                    ("New password", lambda: pw.set(secrets.token_urlsafe(10))),
                    ("Set on Space (owner)", do_set_secret)]:
        ttk.Button(btns2, text=txt, command=fn).pack(side="left", **pad)

    info = ttk.Frame(root); info.pack(fill="x", **pad)
    ttk.Label(info, text="Public endpoint:").grid(row=0, column=0, sticky="w", **pad)
    ttk.Label(info, textvariable=endpoint_var, foreground="#0a7").grid(row=0, column=1, sticky="w", **pad)
    ttk.Label(info, text="Your proxy URL:").grid(row=1, column=0, sticky="w", **pad)
    ttk.Entry(info, textvariable=yturl_var, width=70, state="readonly").grid(row=1, column=1, sticky="w", **pad)
    ttk.Label(info, text="Status:").grid(row=2, column=0, sticky="w", **pad)
    ttk.Label(info, textvariable=status_var, foreground="#06c").grid(row=2, column=1, sticky="w", **pad)

    txt = tk.Text(root, height=16, wrap="word", bg="#111", fg="#ddd")
    txt.pack(fill="both", expand=True, **pad)

    def poll():
        try:
            while True:
                line = log_q.get_nowait()
                txt.insert("end", line + "\n"); txt.see("end")
        except queue.Empty:
            pass
        # refresh running status
        s = []
        s.append("proxy:on" if proxy_proc.running() else "proxy:off")
        s.append("tunnel:on" if tunnel_proc.running() else "tunnel:off")
        if status_var.get() in ("idle", "proxy starting…", "tunnel starting…"):
            status_var.set(" ".join(s))
        root.after(400, poll)

    def on_close():
        proxy_proc.stop(); tunnel_proc.stop(); root.destroy()

    root.protocol("WM_DELETE_WINDOW", on_close)
    log("Ready. 1) Start proxy  2) Start tunnel  3) Test proxy  4) Copy my Proxy URL")
    log("     → paste that URL into the TutorialMaker Space's 'Your proxy URL' field, then Generate.")
    log("Tip: 'Test proxy' egress IP should be your HOME IP, and YouTube should be reachable.")
    log("(bore auto-downloads on first use; 'Set on Space (owner)' is only for the Space owner.)")
    poll()
    root.mainloop()


if __name__ == "__main__":
    # Frozen (PyInstaller) re-entry: act as `python -m proxy` when asked, so the packaged
    # executable can run the forward proxy as a normal killable subprocess of itself.
    if "--run-proxy" in sys.argv:
        _i = sys.argv.index("--run-proxy")
        import proxy
        sys.argv = ["proxy", *sys.argv[_i + 1:]]
        proxy.main()
    else:
        main()
