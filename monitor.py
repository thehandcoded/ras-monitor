#!/usr/bin/env python3
"""
IP monitoring script — pings a list of hosts and notifies via ntfy.sh + macOS
notifications on state changes (up→down, down→up).
"""

import subprocess
import sys
import time
import urllib.request
import logging
from dataclasses import dataclass
from datetime import datetime

import yaml

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-8s  %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
log = logging.getLogger(__name__)


@dataclass
class HostState:
    name: str
    ip: str
    is_up: bool = True
    consecutive_failures: int = 0


# ---------------------------------------------------------------------------
# Ping
# ---------------------------------------------------------------------------

def ping(ip: str, timeout_ms: int) -> bool:
    """Return True if the host replies to a single ping within timeout_ms."""
    result = subprocess.run(
        ["ping", "-c", "1", "-W", str(timeout_ms), "-q", ip],
        capture_output=True,
    )
    return result.returncode == 0


# ---------------------------------------------------------------------------
# Notifications
# ---------------------------------------------------------------------------

def _send_ntfy(server: str, topic: str, title: str, message: str, priority: str) -> None:
    url = f"{server.rstrip('/')}/{topic}"
    req = urllib.request.Request(url, data=message.encode(), method="POST")
    # HTTP headers must be ASCII — strip emojis from the title
    ascii_title = title.encode("ascii", "ignore").decode("ascii").strip()
    req.add_header("Title", ascii_title)
    req.add_header("Priority", priority)
    req.add_header("Tags", "red_circle" if priority == "urgent" else "green_circle")
    try:
        resp = urllib.request.urlopen(req, timeout=10)
        log.info("ntfy response: %s", resp.status)
    except Exception as exc:
        log.error("ntfy send failed: %s", exc, exc_info=True)


def _send_mac_notification(title: str, message: str) -> None:
    subprocess.run(
        ["osascript", "-e", f'display notification "{message}" with title "{title}"'],
        capture_output=True,
    )


def notify(cfg: dict, title: str, message: str, priority: str = "high") -> None:
    ntfy = cfg.get("ntfy", {})
    topic = ntfy.get("topic", "")
    if topic and topic != "ruinart-monitoring-changeme":
        _send_ntfy(ntfy.get("server", "https://ntfy.sh"), topic, title, message, priority)
    else:
        log.warning("ntfy topic not configured — skipping push notification")
    _send_mac_notification(title, message)
    log.info("Notified: %s — %s", title, message)


# ---------------------------------------------------------------------------
# Main loop
# ---------------------------------------------------------------------------

def load_config(path: str) -> dict:
    with open(path) as f:
        return yaml.safe_load(f)


def main() -> None:
    config_path = sys.argv[1] if len(sys.argv) > 1 else "config.yaml"
    cfg = load_config(config_path)

    interval: int = cfg.get("interval", 30)
    timeout_ms: int = cfg["ping"].get("timeout_ms", 2000)
    failures_threshold: int = cfg["ping"].get("failures_before_alert", 2)

    states: dict[str, HostState] = {
        h["ip"]: HostState(name=h.get("name", h["ip"]), ip=h["ip"])
        for h in cfg["hosts"]
    }

    log.info("Monitoring %d host(s) every %ds", len(states), interval)
    notify(cfg, "Monitoring started",
           f"Watching: {', '.join(s.name for s in states.values())}",
           priority="default")

    while True:
        for ip, state in states.items():
            is_up = ping(ip, timeout_ms)
            now = datetime.now().strftime("%H:%M:%S")

            if not is_up:
                state.consecutive_failures += 1
                log.warning("%s (%s) no reply — failure #%d",
                            state.name, ip, state.consecutive_failures)

                if state.is_up and state.consecutive_failures >= failures_threshold:
                    state.is_up = False
                    notify(
                        cfg,
                        f"\U0001f534 {state.name} is DOWN",
                        f"{state.name} ({ip}) stopped responding at {now}.",
                        priority="urgent",
                    )
            else:
                if not state.is_up:
                    downtime_checks = state.consecutive_failures
                    state.is_up = True
                    state.consecutive_failures = 0
                    notify(
                        cfg,
                        f"\U0001f7e2 {state.name} is back UP",
                        f"{state.name} ({ip}) recovered at {now} "
                        f"(missed {downtime_checks} checks).",
                        priority="default",
                    )
                else:
                    state.consecutive_failures = 0
                    log.info("%s (%s) OK", state.name, ip)

        time.sleep(interval)


if __name__ == "__main__":
    main()
