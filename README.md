# Home Proxy Panel

Run **your own residential proxy** for the
[**TutorialMaker** Hugging Face Space](https://huggingface.co/spaces/vivekchakraverty/TutorialMaker).

The Space turns a YouTube topic into a captioned tutorial `.docx`, but YouTube blocks its
**datacenter IP**, so transcript/stream/screenshot fetches fail. This app runs a small
**password-protected HTTP proxy on your computer** (your home/residential IP) and exposes it
through a **raw TCP tunnel** (`bore` or `ngrok`). You paste the resulting **proxy URL** into
the Space's *Your proxy URL* field — YouTube requests for *your* generation then exit from
*your* IP and stop getting blocked.

**Per-user by design:** every user runs their own panel and uses their own proxy URL, so
there's no single shared proxy to run or bottleneck. Combined with the per-user Hugging Face
token the Space already asks for, each user is fully self-served (their own compute + own IP).

> **Why a raw TCP tunnel (not Cloudflare/Tailscale):** a forward proxy speaks HTTP `CONNECT`,
> which only survives a transparent **TCP** tunnel. HTTP *reverse* tunnels (Cloudflare
> quick-tunnel, Tailscale Funnel) can't carry it — use **`bore`** or **ngrok TCP**.

---

## Get it

- **Prebuilt app (no Python):** download `HomeProxyPanel` for your OS from the
  [Releases](../../releases) page and double-click it.
- **From source:** see [Run from source](#run-from-source).

## Use it with the TutorialMaker Space

1. Launch the app (prebuilt binary, or `python home_proxy_panel.py`).
2. **Proxy port / user / password** are pre-filled (random password). Keep the password — it
   protects your public proxy from strangers.
3. Pick a **Tunnel provider**:
   - **bore** — nothing to install; the app auto-downloads the right `bore` binary on first use
     and uses the public `bore.pub` relay. No account.
   - **ngrok** — steadier; install the ngrok agent and paste your free authtoken.
4. **Start proxy** → **Start tunnel** → the **Public endpoint** and **Your proxy URL** fill in.
5. **Test proxy** — the log's egress IP should be **your home IP** and YouTube should be
   **reachable**.
6. Click **📋 Copy my Proxy URL**, open the
   [TutorialMaker Space](https://huggingface.co/spaces/vivekchakraverty/TutorialMaker), paste it
   into **Your proxy URL**, and click **Generate**. **Keep the panel running** while you generate.

> The **Set on Space (owner)** button is only for the Space owner (it writes the global
> `YT_PROXY` secret). End users don't need it.

## Run from source

```bash
pip install -r requirements.txt          # proxy.py + huggingface_hub + requests
python home_proxy_panel.py
```

## Build a standalone executable

PyInstaller does **not** cross-compile — build **on each target OS** (Windows `.exe` on
Windows, macOS binary on macOS, Linux ELF on Linux):

```bash
pip install pyinstaller -r requirements.txt
pyinstaller home_proxy_panel.spec
```

Output: `dist/HomeProxyPanel` (`.exe` on Windows). Ship that single file.

- `proxy.py` is bundled; the app runs it by re-invoking itself with a hidden `--run-proxy`
  flag (so it works even though `sys.executable` is the packaged app).
- `bore` is auto-downloaded at runtime. To bundle it offline instead, drop `bore`/`bore.exe`
  next to `home_proxy_panel.spec` before building — the spec picks it up automatically.
- macOS: unsigned apps are quarantined; users run
  `xattr -dr com.apple.quarantine HomeProxyPanel` or right-click → Open the first time.

The included GitHub Actions workflow (`.github/workflows/build.yml`) builds all three OSes on
`workflow_dispatch` and uploads the binaries as artifacts.

## Security notes

- The proxy is **public while tunneled**, so it **requires the username/password** — keep them
  non-trivial (use **New password** to rotate). The proxy URL embeds those credentials, so
  treat it as a secret (the Space field is masked and the URL is kept out of logs).
- This routes YouTube traffic through your home connection and uses your bandwidth.
- Stop the tunnel when you're done.
