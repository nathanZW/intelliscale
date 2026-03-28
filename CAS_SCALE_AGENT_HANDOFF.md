# CAS Scale Agent Handoff

This note captures the CAS CI-200A-C4 work completed during this conversation, the observed hardware behavior that drove the changes, and how to continue testing without the physical scale.

## Current Branch Context

- Working branch during the session: `alphastride-rebuilt`
- Key commits made in this conversation:
  - `0fd89d3` `fix CAS streaming packet reads`
  - `f74f72c` `use satellite as the persistent serial reader`
  - `ce2dcb8` `make satellite self-recover after serial drops`

## What We Observed On Real Hardware

The CAS scale did not behave like the newline-terminated scales already handled by the app.

Observed good packets from the real device:

```text
= 0004.0
= 0004.5
= 0014.5
```

Important details:

1. The CAS packet is fixed-width and often has no newline terminator.
2. The device often streams concatenated bursts, for example:

```text
b'= 0004.5= 0004.5= 0004.5'
```

3. The USB adapter is a CH341 (`VID:PID 1a86:7523`).
4. On Linux, the CH341 path sometimes disappears and reappears:
   - `/dev/ttyUSB0` vanishes
   - kernel logs show disconnect and reattach
   - the app then sees `Input/output error` or `No such file or directory`

This means the main problem was never only parsing. It was also serial ownership and recovery from USB resets.

## Why We Changed The Design

The web UI was polling rapidly and opening the serial port on each `/get-weight/` request. With the CAS/CH341 combination, that proved fragile.

The direction chosen here was:

1. Teach the parser and reader to understand the observed CAS stream format.
2. Move serial ownership out of the web request path.
3. Let `run_satellite` be the single serial reader.
4. Make the web UI read cached values when satellite mode is enabled.
5. Add recovery logic for USB drops/reconnects.

The goal was to stop "reinventing the wheel" in request handlers and instead centralize the tricky serial behavior in one place.

## Files Changed And Why

### [scale/scale_utils.py](./scale/scale_utils.py)

Purpose:

- Added safer serial open helper `open_serial_for_scale()`
- Added tolerant reads for the Linux pyserial empty-read error
- Added a short stream-first read phase before newline-style fallbacks
- Added CAS parsing for `=`-prefixed fixed-width packets, including concatenated bursts

Reasoning:

- The real CAS output is not newline-oriented.
- Generic broad matching caused regressions in older scales, so the CAS parser was intentionally narrow and based on the real `=` packet signature.

### [scale/views/scale.py](./scale/views/scale.py)

Purpose:

- Added `_get_satellite_cached_weight()`
- When satellite mode is enabled, `get_weight()` now returns cached values instead of touching serial directly
- `get_current_weight_api()` also uses the same cache-first path
- Cached responses now carry health metadata such as:
  - `status`
  - `last_attempt`
  - `last_error`

Reasoning:

- The web layer should not own the CAS serial port if we want a stable setup.
- A frozen cache should be diagnosable instead of looking like a silent success.

### [scale/satellite_service.py](./scale/satellite_service.py)

Purpose:

- Added module-level persistent serial readers keyed by scale
- Reuse one open serial connection across poll cycles
- Close stale readers when scales disappear or config changes
- Retry once after serial errors
- Auto-redetect when the port disappears
- Write recovery metadata into the cache

Reasoning:

- The CAS/CH341 path is much happier with one serial owner.
- A persistent reader still needs recovery logic because the USB adapter can disappear under it.

### [scale/tests.py](./scale/tests.py)

Purpose:

- Added CAS-specific parser and reader tests
- Added tests for persistent reader reuse
- Added tests for recovery after serial exceptions
- Added tests for cache-backed responses and recovering cache entries

Reasoning:

- The original generic emulator caught regressions, but we also needed targeted tests for the observed CAS behavior.

## What Still Matters

Even with the current self-recovery logic, the hardest real-world failure mode is still the CH341 dropping off the USB bus.

There are two distinct failure states to distinguish:

1. `status: "ok"` with a fresh `timestamp`
   - healthy
2. stale `timestamp` with advancing `last_attempt` and `status: "recovering"` / `no_port`
   - satellite is alive and trying to heal

If the cache stays frozen in the exact same state and `last_attempt` does not move, suspect one of:

- the old satellite process is still running old code
- the current satellite process is not actually the one being watched
- the process wedged before it reached the cache write path

## New CAS Emulator

Two new files were added for continued testing without hardware:

- [cas_scale_emulator.py](./cas_scale_emulator.py)
- [test_cas_scale_emulator.py](./test_cas_scale_emulator.py)

### `cas_scale_emulator.py`

What it does:

- creates a PTY-backed fake serial device
- exposes a stable symlink path such as `/tmp/cas-scale/ttyCAS0`
- emits CAS-style packets like `= 0004.5`
- supports scenarios that mirror the real hardware:
  - `steady`
  - `burst`
  - `bounce`
  - `silence`
  - `ramp`
  - `wobble`

The most useful scenario for regression work is `bounce`, because it simulates the CH341 disappearing and reappearing.

Example:

```bash
cd /opt/intelliscale/source
python cas_scale_emulator.py --link /tmp/cas-scale/ttyCAS0 --scenario bounce
```

Then point the `Scale.com_port` at `/tmp/cas-scale/ttyCAS0`.

### `test_cas_scale_emulator.py`

What it does:

- launches the emulator in a subprocess
- verifies burst packets parse correctly
- verifies a simulated disconnect/reconnect produces a failure window and then recovers

Example:

```bash
cd /opt/intelliscale/source
source /opt/intelliscale/venv/bin/activate
python test_cas_scale_emulator.py
```

## Recommended Debugging Approach For The Next Agent

If CAS issues continue, use this order:

1. Run the emulator in `burst` mode and confirm `read_weight_from_serial()` still parses the fake packets.
2. Run the emulator in `bounce` mode and confirm the cache transitions through recovery instead of freezing silently.
3. Only after emulator behavior is solid, return to real hardware.
4. On real hardware, always inspect:
   - `journalctl -k`
   - `lsof /dev/ttyUSB0`
   - `.satellite_cache.json`

## Useful Commands

```bash
cd /opt/intelliscale/source
source /opt/intelliscale/venv/bin/activate
python manage.py test scale
python test_cas_scale_emulator.py
python cas_scale_emulator.py --link /tmp/cas-scale/ttyCAS0 --scenario burst
python cas_scale_emulator.py --link /tmp/cas-scale/ttyCAS0 --scenario bounce
python manage.py run_satellite --interval 0.3
```

## Short Summary

The CAS work in this session moved the system from:

- request-driven serial ownership
- newline-oriented assumptions
- silent stale-cache behavior

to:

- CAS-aware stream parsing
- satellite-owned persistent serial reads
- explicit recovery metadata
- a local emulator that reproduces the most important hardware behaviors we observed
