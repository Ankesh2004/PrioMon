#!/usr/bin/env python3
"""
PrioMon Standalone Launcher
----------------------------
Run a gossip node independently without the experiment orchestrator.

Usage:
    python standalone.py                          # uses node_config.yaml in cwd
    python standalone.py --config /path/to.yaml   # custom config path
    python standalone.py --port 5001              # override port from CLI

This is the "real-world" entry point — each machine/container runs this
and nodes discover each other via seed addresses.
"""

import sys
import os

# make sure we can import sibling modules
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from priomon import start_standalone


def main():
    config_path = None

    if "--config" in sys.argv:
        idx = sys.argv.index("--config")
        if idx + 1 < len(sys.argv):
            config_path = sys.argv[idx + 1]

    # --port override via env so priomon.py can pick it up
    if "--port" in sys.argv:
        idx = sys.argv.index("--port")
        if idx + 1 < len(sys.argv):
            os.environ["PRIOMON_PORT_OVERRIDE"] = sys.argv[idx + 1]

    start_standalone(config_path)


if __name__ == "__main__":
    main()
