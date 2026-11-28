"""Install the listener as a background service that starts at login and
restarts itself: launchd (macOS), systemd user service (Linux), Task Scheduler
(Windows). Paths are filled in for this machine, nothing to edit by hand.

Run:  uv run python -m src.service install | uninstall | status
"""
from __future__ import annotations

import os
import subprocess
import sys
import time
from pathlib import Path

from .config import ROOT, claude_bin

NAME = "paper-pipeline"
MAC_LABEL = "com.paperpipeline.listener"
OLD_MAC_LABELS = ["com.user.paperpipeline.listener", "com.user.paperpipeline.catchup"]


def _path_env() -> str:
    """PATH for the service: where python and claude live, plus the usual places."""
    dirs = [str(Path(sys.executable).parent), str(Path(claude_bin()).parent)]
    dirs += os.environ.get("PATH", "").split(os.pathsep)
    seen = []
    for d in dirs:
        if d and d not in seen:
            seen.append(d)
    return os.pathsep.join(seen)


def _run(cmd: list[str], check: bool = False) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", check=check)


# ----- macOS -----

def _mac_plist() -> Path:
    return Path.home() / "Library/LaunchAgents" / f"{MAC_LABEL}.plist"


def _mac_install() -> None:
    from xml.sax.saxutils import escape

    uid = os.getuid()
    for old in OLD_MAC_LABELS:  # earlier hand-made services (listener + separate catch-up timer)
        _run(["launchctl", "bootout", f"gui/{uid}/{old}"])
        (Path.home() / "Library/LaunchAgents" / f"{old}.plist").unlink(missing_ok=True)
    logs = ROOT / ".cache"
    logs.mkdir(exist_ok=True)
    e = lambda v: escape(str(v))
    plist = f"""<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key><string>{MAC_LABEL}</string>
  <key>ProgramArguments</key>
  <array><string>{e(sys.executable)}</string><string>-m</string><string>src.slack_listener</string></array>
  <key>WorkingDirectory</key><string>{e(ROOT)}</string>
  <key>EnvironmentVariables</key>
  <dict><key>PATH</key><string>{e(_path_env())}</string></dict>
  <key>RunAtLoad</key><true/>
  <key>KeepAlive</key><true/>
  <key>StandardOutPath</key><string>{e(logs / "listener.log")}</string>
  <key>StandardErrorPath</key><string>{e(logs / "listener.err.log")}</string>
</dict>
</plist>
"""
    p = _mac_plist()
    p.parent.mkdir(parents=True, exist_ok=True)
    _run(["launchctl", "bootout", f"gui/{uid}/{MAC_LABEL}"])
    p.write_text(plist, encoding="utf-8")
    _run(["launchctl", "bootstrap", f"gui/{uid}", str(p)], check=True)


def _mac_uninstall() -> None:
    _run(["launchctl", "bootout", f"gui/{os.getuid()}/{MAC_LABEL}"])
    _mac_plist().unlink(missing_ok=True)


def _mac_status() -> str:
    r = _run(["launchctl", "print", f"gui/{os.getuid()}/{MAC_LABEL}"])
    if r.returncode:
        return "not installed"
    return "running" if "state = running" in r.stdout else "installed, not running"


# ----- Linux -----

def _unit() -> Path:
    return Path.home() / ".config/systemd/user" / f"{NAME}.service"


def _linux_install() -> None:
    unit = f"""[Unit]
Description=Paper pipeline Slack listener
After=network-online.target

[Service]
WorkingDirectory={ROOT}
ExecStart="{sys.executable}" -m src.slack_listener
Environment="PATH={_path_env()}"
Restart=always
RestartSec=10

[Install]
WantedBy=default.target
"""
    _unit().parent.mkdir(parents=True, exist_ok=True)
    _unit().write_text(unit, encoding="utf-8")
    _run(["systemctl", "--user", "daemon-reload"], check=True)
    _run(["systemctl", "--user", "enable", "--now", f"{NAME}.service"], check=True)


def _linux_uninstall() -> None:
    _run(["systemctl", "--user", "disable", "--now", f"{NAME}.service"])
    _unit().unlink(missing_ok=True)
    _run(["systemctl", "--user", "daemon-reload"])


def _linux_status() -> str:
    r = _run(["systemctl", "--user", "is-active", f"{NAME}.service"])
    return r.stdout.strip() or "not installed"


# ----- Windows -----

def _win_launcher() -> Path:
    return ROOT / ".cache" / "start-paper-pipeline.cmd"


def _win_install() -> None:
    # Task Scheduler has no working-directory option, so start via a tiny .cmd.
    pythonw = Path(sys.executable).with_name("pythonw.exe")
    exe = pythonw if pythonw.exists() else Path(sys.executable)
    _win_launcher().parent.mkdir(exist_ok=True)
    _win_launcher().write_text(
        f'@echo off\r\ncd /d "{ROOT}"\r\nset "PATH={_path_env()}"\r\n"{exe}" -m src.supervise\r\n',
        encoding="utf-8",
    )
    _run(["schtasks", "/Create", "/TN", NAME, "/TR", f'"{_win_launcher()}"',
          "/SC", "ONLOGON", "/RL", "LIMITED", "/F"], check=True)
    _run(["schtasks", "/Run", "/TN", NAME], check=True)


def _win_uninstall() -> None:
    _run(["schtasks", "/End", "/TN", NAME])
    _run(["schtasks", "/Delete", "/TN", NAME, "/F"])
    _win_launcher().unlink(missing_ok=True)


def _win_status() -> str:
    r = _run(["schtasks", "/Query", "/TN", NAME])
    if r.returncode:
        return "not installed"
    return "running" if "Running" in r.stdout else "installed, not running"


# ----- dispatch -----

def _impl():
    if sys.platform == "darwin":
        return _mac_install, _mac_uninstall, _mac_status
    if os.name == "nt":
        return _win_install, _win_uninstall, _win_status
    return _linux_install, _linux_uninstall, _linux_status


def install() -> None:
    _impl()[0]()


def uninstall() -> None:
    _impl()[1]()


def status() -> str:
    return _impl()[2]()


if __name__ == "__main__":
    action = sys.argv[1] if len(sys.argv) > 1 else "status"
    if action == "install":
        install()
        time.sleep(2)  # give it a moment to start before reporting status
        print("Installed. The pipeline now runs in the background and starts at login.")
    elif action == "uninstall":
        uninstall()
        print("Removed the background service.")
    print("Status:", status())
