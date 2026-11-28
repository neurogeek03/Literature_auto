"""Keep the Slack listener running: restart it whenever it exits (the watchdog
exits on purpose when the connection is stuck). Used on Windows, where Task
Scheduler has no "keep alive"; macOS (launchd) and Linux (systemd) restart it
themselves.

Run:  python -m src.supervise
"""
from __future__ import annotations

import subprocess
import sys
import time

from .config import ROOT, resolve_path


def main() -> None:
    while True:
        with open(resolve_path(".cache/listener.log"), "a", encoding="utf-8") as log:
            subprocess.run([sys.executable, "-m", "src.slack_listener"], cwd=ROOT, stdout=log, stderr=log)
        time.sleep(10)  # don't spin if it fails instantly


if __name__ == "__main__":
    main()
