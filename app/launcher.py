"""One-stop launcher used by START_HERE.bat.

  START_HERE.bat            -> menu
  START_HERE.bat start      -> fast & light: one API window that also serves the web app, browser opens
  START_HERE.bat start-n8n  -> same, plus the n8n orchestrator (for the demo video / n8n screenshots)
  START_HERE.bat classic    -> the older Streamlit screen
  START_HERE.bat check | bench | evidence | tests | n8n-setup | stop | reset
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import time
import urllib.error
import urllib.request
import webbrowser
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PY = sys.executable
WIN = os.name == "nt"
PIDS = ROOT / "data" / ".launcher_pids.json"

# Everything this project installs or caches stays inside AIB\.tools (nothing extra on C:).
TOOLS = ROOT / ".tools"
for _k, _v in {"PIP_CACHE_DIR": TOOLS / "pip-cache", "MPLCONFIGDIR": TOOLS / "matplotlib",
               "npm_config_cache": TOOLS / "npm-cache", "N8N_USER_FOLDER": TOOLS / "n8n-data",
               "TEMP": TOOLS / "tmp", "TMP": TOOLS / "tmp"}.items():
    Path(_v).mkdir(parents=True, exist_ok=True)
    os.environ[_k] = str(_v)
os.environ.setdefault("N8N_DIAGNOSTICS_ENABLED", "false")
NODE_DIR = TOOLS / "node"            # portable Node.js (downloaded only if no Node.js 24+ is installed)
N8N_DIR = TOOLS / "n8n"              # n8n installed here with npm --prefix
N8N_FLAG = TOOLS / "n8n-data" / ".fraudops_workflows_imported"
API, UI, N8N = "http://127.0.0.1:8000/health", "http://127.0.0.1:8501", "http://127.0.0.1:5678/healthz"
WEB = "http://127.0.0.1:8000/"                 # light web app, served by the API itself
WEB_STATUS = "http://127.0.0.1:8000/ui/status"
WORKFLOWS = [("01_fraud_investigation_orchestrator.json", "FraudInvOrch0001"),
             ("02_hitl_decision_handler.json", "HitlDecision0002"),
             ("03_sla_escalation_monitor.json", "SlaMonitor000003")]
SETS = {
    "1": ("Gemini + Cerebras keys (Google, OpenAI, Alibaba) - recommended", ["gemma-4-31b", "gpt-oss-120b", "qwen-3.8-27b"]),
    "2": ("Gemini key + Ollama on this PC (2 Gemma 4 + local Qwen 2.5 7B)", ["gemma-4-31b", "gemma-4-26b-a4b", "ollama-qwen2.5-7b"]),
}


# ------------------------------------------------------------------------------------------- helpers
def say(msg: str = "") -> None:
    print(msg, flush=True)


def is_up(url: str, timeout: float = 2) -> bool:
    try:
        with urllib.request.urlopen(url, timeout=timeout) as r:
            return r.status < 500
    except Exception:  # noqa: BLE001 - any failure means "not up"
        return False


def wait_up(url: str, seconds: int, label: str) -> bool:
    say(f"   waiting for {label} ...")
    end = time.time() + seconds
    while time.time() < end:
        if is_up(url):
            say(f"   {label} is running.")
            return True
        time.sleep(1.5)
    say(f"   {label} did not start within {seconds}s - check its window for errors.")
    return False


def _node_version(node: str | Path) -> int:
    try:
        out = subprocess.run([str(node), "-v"], capture_output=True, text=True, timeout=15).stdout.strip()
        return int(out.lstrip("v").split(".")[0])
    except (OSError, ValueError, subprocess.SubprocessError):
        return 0


def node_bin_dir() -> Path | None:
    """Folder holding node + npm: the portable copy in AIB\\.tools first, else a system Node.js 24+."""
    local = NODE_DIR / ("node.exe" if WIN else "bin/node")
    if local.exists() and _node_version(local) >= 24:
        return local.parent
    system = shutil.which("node")
    if system and _node_version(system) >= 24:
        return Path(system).parent
    return None


def webhooks_ready(seconds: int = 60) -> bool:
    """n8n answers /healthz before it has registered the workflow webhooks; wait for those too."""
    end = time.time() + seconds
    while time.time() < end:
        try:
            urllib.request.urlopen("http://127.0.0.1:5678/webhook/fraud-investigate", timeout=3)
        except urllib.error.HTTPError as e:
            body = e.read().decode(errors="ignore").lower()
            if "post" in body and "not registered for get" in body or "did you mean" in body:
                return True
        except Exception:  # noqa: BLE001
            pass
        time.sleep(2)
    say("   n8n is up but the workflows are not active - choose option 6 to (re)import them.")
    return False


def node_ok() -> bool:
    return node_bin_dir() is not None


def tool_env() -> dict:
    env = dict(os.environ)
    nb = node_bin_dir()
    if nb:
        env["PATH"] = str(nb) + os.pathsep + env.get("PATH", "")
    return env


def install_portable_node() -> bool:
    """Downloads the latest Node.js 24 (Windows x64 zip, ~30 MB) into AIB\\.tools\\node."""
    import io
    import zipfile
    if not WIN:
        say("Automatic Node.js download is only set up for Windows. Install Node.js 24+ yourself.")
        return False
    say("Downloading portable Node.js 24 into AIB\\.tools\\node (about 30 MB) ...")
    try:
        with urllib.request.urlopen("https://nodejs.org/dist/index.json", timeout=30) as r:
            releases = json.load(r)
        rel = next(x for x in releases if x["version"].startswith("v24.") and "win-x64-zip" in x["files"])
        name = f"node-{rel['version']}-win-x64"
        with urllib.request.urlopen(f"https://nodejs.org/dist/{rel['version']}/{name}.zip", timeout=300) as r:
            data = r.read()
        with zipfile.ZipFile(io.BytesIO(data)) as z:
            z.extractall(TOOLS)
        if NODE_DIR.exists():
            shutil.rmtree(NODE_DIR)
        (TOOLS / name).rename(NODE_DIR)
        say(f"Node.js {rel['version']} ready in {NODE_DIR}")
        return True
    except Exception as e:  # noqa: BLE001
        say(f"Download failed ({e}). Install Node.js 24 from https://nodejs.org instead.")
        return False


def n8n_exe() -> Path:
    return N8N_DIR / "node_modules" / ".bin" / ("n8n.cmd" if WIN else "n8n")


def ensure_n8n(ask: bool = True) -> bool:
    """Makes sure Node.js 24+ and n8n are available, installing both inside AIB\\.tools if needed."""
    if not node_ok():
        if ask:
            ans = input("n8n needs Node.js 24+. Download a portable copy into AIB\\.tools (about 30 MB)? [Y/n]: ")
            if ans.strip().lower() in ("n", "no"):
                return False
        if not install_portable_node():
            return False
    if not n8n_exe().exists():
        say("Installing n8n into AIB\\.tools\\n8n (first time only, about 1 GB, 5-10 minutes) ...")
        npm = "npm.cmd" if WIN else "npm"
        code = subprocess.run(f'{npm} install --prefix "{N8N_DIR}" n8n@2 --no-audit --no-fund', shell=True,
                              cwd=ROOT, env=tool_env()).returncode
        if code != 0 or not n8n_exe().exists():
            say("n8n installation failed - see the messages above. The app still works with the Python engine.")
            return False
    return True


def read_env() -> dict[str, str]:
    env = {}
    f = ROOT / ".env"
    if f.exists():
        for line in f.read_text(encoding="utf-8", errors="replace").splitlines():
            if "=" in line and not line.strip().startswith("#"):
                k, _, v = line.partition("=")
                env[k.strip()] = v.strip().strip('"').strip("'")
    return env


def ensure_env() -> None:
    f = ROOT / ".env"
    if not f.exists():
        shutil.copy(ROOT / ".env.example", f)
        say("Created .env. Paste your GEMINI_API_KEY into it, save, and close Notepad.")
        if WIN:
            subprocess.run(["notepad", str(f)])
    env = read_env()
    if not any(env.get(k) for k in ("GEMINI_API_KEY", "CEREBRAS_API_KEY", "GROQ_API_KEY", "MISTRAL_API_KEY",
                                    "NVIDIA_API_KEY", "OPENROUTER_API_KEY")):
        say("NOTE: no API key in .env - only the offline 'mock-heuristic' model will work.")


def spawn(name: str, command: str, extra_env: dict | None = None) -> int:
    """Starts a long-running service in its own console window (Windows) so its log stays visible."""
    env = {**os.environ, **(extra_env or {})}
    if WIN:
        # A single command string (not a list) so Python does not escape the inner quotes; cmd /k strips only
        # the outer pair, which keeps paths with spaces working.
        p = subprocess.Popen(f'cmd /k "title {name} && {command}"', cwd=ROOT, env=env,
                             creationflags=subprocess.CREATE_NEW_CONSOLE)
    else:
        log = open(ROOT / "data" / f"{name.replace(' ', '_').lower()}.log", "a")  # noqa: SIM115
        p = subprocess.Popen(command, shell=True, cwd=ROOT, env=env, stdout=log, stderr=subprocess.STDOUT,
                             start_new_session=True)
    pids = json.loads(PIDS.read_text()) if PIDS.exists() else {}
    pids[name] = p.pid
    PIDS.parent.mkdir(parents=True, exist_ok=True)
    PIDS.write_text(json.dumps(pids))
    return p.pid


def run(command: str) -> int:
    """Runs a command in this window and waits for it."""
    return subprocess.run(command, shell=True, cwd=ROOT, env=tool_env()).returncode


# ------------------------------------------------------------------------------------------- actions
def workflows_hash() -> str:
    import hashlib
    h = hashlib.sha256()
    for fname, _ in WORKFLOWS:
        h.update((ROOT / "n8n" / fname).read_bytes())
    return h.hexdigest()[:16]


def n8n_setup() -> bool:
    if not ensure_n8n():
        return False
    if is_up(N8N):
        say("Stopping n8n first (workflows can only be imported while it is stopped) ...")
        stop(only="n8n")
        time.sleep(3)
    say("Importing and publishing the 3 n8n workflows ...")
    n8n = f'"{n8n_exe()}"'
    for fname, wid in WORKFLOWS:
        if run(f'{n8n} import:workflow --input="{ROOT / "n8n" / fname}"') != 0:
            say(f"Import of {fname} failed - see the messages above.")
            return False
        run(f"{n8n} publish:workflow --id={wid}")
    N8N_FLAG.parent.mkdir(parents=True, exist_ok=True)
    N8N_FLAG.write_text(workflows_hash())
    say("n8n workflows are ready.")
    return True


def start_api() -> bool:
    if is_up(API) and not is_up(WEB_STATUS):
        say("   an older version of the API is running - restarting it ...")
        stop(only="API")
        time.sleep(2)
        if is_up(API):
            say("   could not stop it: close the old 'FraudOps API' window yourself, then choose this option again.")
            return False
    if is_up(API):
        say("   already running.")
        return True
    # --no-access-log: the page polls for live progress; logging every poll would only slow the console down
    spawn("FraudOps API", f'"{PY}" -m uvicorn app.api:app --host 127.0.0.1 --port 8000 --no-access-log')
    return wait_up(API, 60, "API")


def start_n8n_service() -> bool:
    if is_up(N8N):
        if N8N_FLAG.exists() and N8N_FLAG.read_text().strip() != workflows_hash():
            say("   running, but the workflow files changed: choose 7 (Stop), then 9 again to refresh them.")
        else:
            say("   already running.")
        return True
    if not ensure_n8n():
        say("   n8n is not available - the app keeps using the fast built-in engine (same steps).")
        return False
    if not N8N_FLAG.exists() or N8N_FLAG.read_text().strip() != workflows_hash():
        n8n_setup()
    spawn("FraudOps n8n", f'"{n8n_exe()}" start', tool_env())
    return wait_up(N8N, 240, "n8n") and webhooks_ready()


def start() -> None:
    """Fast & light: only the API process. It serves the web app, so there is no separate UI server."""
    ensure_env()
    say("\n[1/1] Backend API + web app")
    if not start_api():
        return
    webbrowser.open(WEB + "?engine=python")
    say(f"\nAll set. The app is open at {WEB}")
    say("Leave the API window open. Use option 7 (Stop) when you are done."
        + ("" if not is_up(N8N) else "  n8n is also running: pick it under Orchestrator in the app."))


def start_with_n8n() -> None:
    """For the demo video and n8n screenshots: API + n8n, app opens with the n8n engine selected."""
    ensure_env()
    say("\n[1/2] Backend API + web app")
    if not start_api():
        return
    say("\n[2/2] n8n orchestrator")
    use_n8n = start_n8n_service()
    webbrowser.open(WEB + ("?engine=n8n" if use_n8n else "?engine=python"))
    say(f"\nAll set. The app is open at {WEB}" + ("  (n8n editor: http://localhost:5678)" if use_n8n else ""))
    say("Leave the other windows open. Use option 7 (Stop) when you are done.")


def start_classic() -> None:
    """The older Streamlit screen (heavier). Kept for anyone who prefers it."""
    ensure_env()
    say("\n[1/2] Backend API")
    if not start_api():
        return
    say("\n[2/2] Streamlit screen")
    if is_up(UI):
        say("   already running.")
    else:
        spawn("FraudOps UI", f'"{PY}" -m streamlit run ui/streamlit_app.py --server.port 8501 --server.headless true',
              {"FRAUDOPS_ORCHESTRATOR": "n8n" if is_up(N8N) else "python"})
        wait_up(UI, 90, "app screen")
    webbrowser.open(UI)
    say(f"\nThe classic screen is open at {UI}. The light app is still at {WEB}")


def stop(only: str | None = None) -> None:
    pids = json.loads(PIDS.read_text()) if PIDS.exists() else {}
    for name, pid in list(pids.items()):
        if only and only.lower() not in name.lower():
            continue
        if WIN:
            subprocess.run(["taskkill", "/PID", str(pid), "/T", "/F"], capture_output=True)
        else:
            try:
                os.killpg(pid, 15)
            except (ProcessLookupError, PermissionError):
                pass
        pids.pop(name)
        say(f"Stopped {name}.")
    if PIDS.parent.exists():
        PIDS.write_text(json.dumps(pids))
    if not only:
        say("Everything started by this launcher is stopped.")


def bench() -> None:
    ensure_env()
    env = read_env()
    default = "1" if env.get("CEREBRAS_API_KEY") else "2"
    if default == "2":
        say("No CEREBRAS_API_KEY in .env: a Gemini key alone reaches only two open models (Google retired Gemma 3),\n"
            "so the third model must come from Cerebras (free key at cloud.cerebras.ai) or Ollama on this PC.")
    say("Which models should be compared?")
    for k, (label, models) in SETS.items():
        say(f"  {k}. {label}: {' '.join(models)}")
    say("  3. Type my own model labels (see check option)")
    choice = input(f"Choose 1-3 [{default}]: ").strip() or default
    models = SETS[choice][1] if choice in SETS else input("Model labels separated by spaces: ").split()
    say("\nRunning the benchmark. This takes 30-60 minutes; if it stops, run it again - it resumes.\n")
    code = run(f'"{PY}" -m benchmark.run_benchmark --resume --models {" ".join(models)}')
    if code == 0 and (ROOT / "benchmark" / "results" / "summary.json").exists():
        run(f'"{PY}" docs/website/build_site.py')
        say("\nDone. Results: benchmark\\results\\benchmark_report.md and charts\\ - also on the app's Model Comparison page.")
        say("Send benchmark\\results\\summary.json to Claude to fill in the report and website.")


def reset() -> None:
    if is_up(API):
        req = urllib.request.Request("http://127.0.0.1:8000/admin/reset", method="POST")
        urllib.request.urlopen(req, timeout=10)
        say("Cases and audit log cleared (synthetic bank kept).")
    else:
        run(f'"{PY}" -m app.seed_data')


ACTIONS = {
    "1": ("Start the app - fast & light (recommended)", start),
    "2": ("Check which AI models your keys can reach", lambda: run(f'"{PY}" -m app.check_models')),
    "3": ("Run the model comparison (benchmark)", bench),
    "4": ("Run prompt-refinement evidence for Stage 1 (v1 vs v2 vs v3)",
          lambda: run(f'"{PY}" -m benchmark.prompt_evolution --model gemma-4-31b')),
    "5": ("Run the automated tests", lambda: run(f'"{PY}" -m pytest -q')),
    "6": ("Set up / refresh the n8n workflows", n8n_setup),
    "7": ("Stop everything", stop),
    "8": ("Reset demo data (clear cases and audit log)", reset),
    "9": ("Start the app WITH n8n (for the demo video / n8n screenshots)", start_with_n8n),
    "10": ("Classic Streamlit screen (older, heavier)", start_classic),
}
ALIASES = {"start": "1", "check": "2", "bench": "3", "evidence": "4", "tests": "5", "n8n-setup": "6",
           "stop": "7", "reset": "8", "start-n8n": "9", "classic": "10"}


def menu() -> None:
    while True:
        say("\n==============================================")
        say("   FraudOps Copilot - control panel")
        say("==============================================")
        for k, (label, _) in ACTIONS.items():
            say(f"  {k}. {label}")
        say("  0. Exit")
        choice = input("\nChoose an option [1]: ").strip() or "1"
        if choice == "0":
            return
        if choice in ACTIONS:
            ACTIONS[choice][1]()
        else:
            say("Please type a number from the list.")


if __name__ == "__main__":
    os.chdir(ROOT)
    arg = sys.argv[1].lower() if len(sys.argv) > 1 else ""
    if arg:
        ACTIONS[ALIASES.get(arg, arg)][1]()
    else:
        menu()
