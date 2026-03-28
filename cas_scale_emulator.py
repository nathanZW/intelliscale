#!/usr/bin/env python3
"""
CAS CI-200 style serial scale emulator.

Creates a PTY-backed serial endpoint and exposes a stable symlink path that can be
used as a fake scale COM port inside IntelliScale.

Example:
    python cas_scale_emulator.py --link /tmp/cas-scale/ttyCAS0 --scenario burst
    python cas_scale_emulator.py --link /tmp/cas-scale/ttyCAS0 --scenario bounce
"""
import argparse
import math
import os
import pathlib
import pty
import signal
import sys
import threading
import time


def format_cas_packet(weight):
    return f"= {weight:06.1f}".encode("ascii")


class CasScaleEmulator:
    def __init__(
        self,
        link_path,
        scenario,
        base_weight,
        interval,
        burst_count,
        bounce_after,
        bounce_down,
        bounce_every,
        silence_after,
        silence_for,
    ):
        self.link_path = pathlib.Path(link_path)
        self.scenario = scenario
        self.base_weight = base_weight
        self.interval = interval
        self.burst_count = burst_count
        self.bounce_after = bounce_after
        self.bounce_down = bounce_down
        self.bounce_every = bounce_every
        self.silence_after = silence_after
        self.silence_for = silence_for
        self._master_fd = None
        self._slave_fd = None
        self._slave_path = None
        self._running = threading.Event()
        self._thread = None
        self._start_time = None
        self._next_bounce_at = None
        self._silence_until = None
        self._weight_index = 0
        self._weight_sequence = [
            base_weight,
            max(0.0, base_weight - 6.5),
            base_weight - 5.5,
            base_weight,
            base_weight + 0.5,
        ]

    @property
    def slave_path(self):
        return self._slave_path

    def start(self):
        self._running.set()
        self._start_time = time.monotonic()
        if self.scenario == "bounce":
            self._next_bounce_at = self._start_time + self.bounce_after
        if self.scenario == "silence":
            self._silence_until = self._start_time + self.silence_after + self.silence_for
        self._open_transport()
        self._thread = threading.Thread(target=self._run_loop, daemon=True)
        self._thread.start()

    def stop(self):
        self._running.clear()
        if self._thread:
            self._thread.join(timeout=1)
        self._close_transport(remove_link=True)

    def _open_transport(self):
        self._master_fd, self._slave_fd = pty.openpty()
        self._slave_path = os.ttyname(self._slave_fd)
        self.link_path.parent.mkdir(parents=True, exist_ok=True)
        if self.link_path.exists() or self.link_path.is_symlink():
            self.link_path.unlink()
        os.symlink(self._slave_path, self.link_path)
        print(f"[emulator] attached {self.link_path} -> {self._slave_path}", flush=True)

    def _close_transport(self, remove_link):
        for fd_name in ("_master_fd", "_slave_fd"):
            fd = getattr(self, fd_name)
            if fd is not None:
                try:
                    os.close(fd)
                except OSError:
                    pass
                setattr(self, fd_name, None)
        self._slave_path = None
        if remove_link and (self.link_path.exists() or self.link_path.is_symlink()):
            try:
                self.link_path.unlink()
            except FileNotFoundError:
                pass

    def _current_weight(self, now):
        if self.scenario in {"steady", "burst", "bounce", "silence"}:
            return self.base_weight
        if self.scenario == "ramp":
            weight = self._weight_sequence[self._weight_index % len(self._weight_sequence)]
            self._weight_index += 1
            return weight
        if self.scenario == "wobble":
            elapsed = now - self._start_time
            return round(self.base_weight + math.sin(elapsed * 2.0) * 0.5, 1)
        return self.base_weight

    def _payload(self, now):
        packet = format_cas_packet(self._current_weight(now))
        if self.scenario in {"burst", "bounce"}:
            return packet * self.burst_count
        return packet

    def _maybe_bounce(self, now):
        if self.scenario != "bounce" or self._next_bounce_at is None:
            return
        if now < self._next_bounce_at:
            return

        print(
            f"[emulator] simulating USB disconnect for {self.bounce_down:.1f}s",
            flush=True,
        )
        self._close_transport(remove_link=True)
        end_time = time.monotonic() + self.bounce_down
        while self._running.is_set() and time.monotonic() < end_time:
            time.sleep(0.05)
        if self._running.is_set():
            self._open_transport()
            self._next_bounce_at = time.monotonic() + self.bounce_every

    def _maybe_silence(self, now):
        if self.scenario != "silence" or self._silence_until is None:
            return False

        silence_start = self._start_time + self.silence_after
        if silence_start <= now < self._silence_until:
            return True

        if now >= self._silence_until:
            self._silence_until = None
        return False

    def _run_loop(self):
        while self._running.is_set():
            now = time.monotonic()
            self._maybe_bounce(now)
            if self._master_fd is None:
                time.sleep(0.05)
                continue
            if self._maybe_silence(now):
                time.sleep(self.interval)
                continue

            payload = self._payload(now)
            try:
                os.write(self._master_fd, payload)
            except OSError as exc:
                print(f"[emulator] write failed: {exc}", flush=True)
                self._close_transport(remove_link=True)
            time.sleep(self.interval)


def parse_args():
    parser = argparse.ArgumentParser(description="CAS CI-200 serial emulator")
    parser.add_argument(
        "--link",
        default="/tmp/cas-scale/ttyCAS0",
        help="Stable symlink path to expose as the fake serial port",
    )
    parser.add_argument(
        "--scenario",
        choices=["steady", "burst", "bounce", "silence", "ramp", "wobble"],
        default="burst",
        help="Emission pattern to simulate",
    )
    parser.add_argument("--weight", type=float, default=4.5, help="Base weight to emit")
    parser.add_argument("--interval", type=float, default=0.10, help="Seconds between writes")
    parser.add_argument("--burst-count", type=int, default=3, help="Packets per burst")
    parser.add_argument("--bounce-after", type=float, default=8.0, help="Seconds before first USB bounce")
    parser.add_argument("--bounce-down", type=float, default=4.0, help="Seconds the device is unavailable")
    parser.add_argument("--bounce-every", type=float, default=20.0, help="Seconds between repeated bounces")
    parser.add_argument("--silence-after", type=float, default=8.0, help="Seconds before a silence window starts")
    parser.add_argument("--silence-for", type=float, default=5.0, help="Duration of the silence window")
    return parser.parse_args()


def main():
    args = parse_args()
    emulator = CasScaleEmulator(
        link_path=args.link,
        scenario=args.scenario,
        base_weight=args.weight,
        interval=args.interval,
        burst_count=args.burst_count,
        bounce_after=args.bounce_after,
        bounce_down=args.bounce_down,
        bounce_every=args.bounce_every,
        silence_after=args.silence_after,
        silence_for=args.silence_for,
    )

    def _shutdown(_signum=None, _frame=None):
        emulator.stop()
        raise SystemExit(0)

    signal.signal(signal.SIGINT, _shutdown)
    signal.signal(signal.SIGTERM, _shutdown)

    emulator.start()
    print(f"[emulator] scenario={args.scenario} weight={args.weight}", flush=True)
    print(f"[emulator] point IntelliScale at {args.link}", flush=True)

    try:
        while True:
            time.sleep(1)
    finally:
        emulator.stop()


if __name__ == "__main__":
    main()
