#!/usr/bin/env python3
"""
Integration harness for the CAS scale emulator.

This launches the PTY-backed emulator and verifies the current serial helpers can:
1. parse healthy CAS burst packets
2. recover after a simulated USB disconnect/reconnect
"""
import os
import subprocess
import sys
import tempfile
import time
from types import SimpleNamespace

sys.path.append("/opt/intelliscale/source")
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "core.settings")

import django

django.setup()

from scale.scale_utils import open_serial_for_scale, parse_weight_from_bytes, read_weight_from_serial


REPO_ROOT = "/opt/intelliscale/source"
EMULATOR_PATH = os.path.join(REPO_ROOT, "cas_scale_emulator.py")


def wait_for_path(path, timeout=5.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if os.path.exists(path):
            return True
        time.sleep(0.05)
    return False


def read_once(scale):
    ser = open_serial_for_scale(scale, timeout=scale.timeout)
    try:
        raw = read_weight_from_serial(ser)
        return raw, parse_weight_from_bytes(raw)
    finally:
        if ser.is_open:
            ser.close()


def run_burst_test(link_path):
    proc = subprocess.Popen(
        [
            sys.executable,
            EMULATOR_PATH,
            "--link",
            link_path,
            "--scenario",
            "burst",
            "--weight",
            "4.5",
            "--interval",
            "0.1",
            "--burst-count",
            "3",
        ],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    try:
        assert wait_for_path(link_path), "emulator link path did not appear"
        scale = SimpleNamespace(
            com_port=link_path,
            baud_rate=9600,
            timeout=1,
            parity="N",
            stop_bits=1,
            data_bits=8,
        )
        raw, parsed = read_once(scale)
        assert parsed[0] == 4.5, f"expected 4.5, got raw={raw!r} parsed={parsed!r}"
        print(f"PASS burst: raw={raw!r} parsed={parsed!r}")
    finally:
        proc.terminate()
        proc.wait(timeout=5)


def run_bounce_recovery_test(link_path):
    proc = subprocess.Popen(
        [
            sys.executable,
            EMULATOR_PATH,
            "--link",
            link_path,
            "--scenario",
            "bounce",
            "--weight",
            "4.5",
            "--interval",
            "0.1",
            "--burst-count",
            "3",
            "--bounce-after",
            "2.0",
            "--bounce-down",
            "2.0",
            "--bounce-every",
            "99.0",
        ],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    try:
        assert wait_for_path(link_path), "emulator link path did not appear"
        scale = SimpleNamespace(
            com_port=link_path,
            baud_rate=9600,
            timeout=1,
            parity="N",
            stop_bits=1,
            data_bits=8,
        )

        hits = 0
        misses = 0
        recovered_after_bounce = False
        saw_failure_window = False
        start = time.monotonic()
        while time.monotonic() - start < 8.0:
            try:
                raw, parsed = read_once(scale)
                if parsed[0] == 4.5:
                    hits += 1
                    if saw_failure_window:
                        recovered_after_bounce = True
                else:
                    misses += 1
            except Exception:
                misses += 1
                saw_failure_window = True
            time.sleep(0.2)

        assert hits > 0, "never received a healthy CAS reading"
        assert saw_failure_window, "bounce scenario never caused a failure window"
        assert recovered_after_bounce, "reader never recovered after the simulated bounce"
        print(
            f"PASS bounce: hits={hits} misses={misses} recovered_after_bounce={recovered_after_bounce}"
        )
    finally:
        proc.terminate()
        proc.wait(timeout=5)


def main():
    with tempfile.TemporaryDirectory(prefix="cas-scale-emulator-") as tmpdir:
        link_path = os.path.join(tmpdir, "ttyCAS0")
        run_burst_test(link_path)
        run_bounce_recovery_test(link_path)


if __name__ == "__main__":
    main()
