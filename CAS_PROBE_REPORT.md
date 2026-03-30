# CAS Probe Report

Date: 2026-03-30

## Summary

The standalone CAS probe works better than the current in-app read path because it is narrower and more predictable:

- it does not try to support multiple serial protocols in the same read cycle
- it does not send prompt commands to the scale
- it does not clear the input buffer between strategies
- it returns after a small bounded read instead of accumulating a large continuous stream
- it can keep a single serial owner instead of opening and closing the port from request paths

The app's current implementation is more generic, but that generality is exactly what makes it brittle for the CAS C1-200A-C4.

## What The Probe Does Differently

### 1. Passive bounded stream reads

The probe only reads a short sample from the stream and then stops. See [scale/cas_scale_probe.py](/opt/intelliscale/source/scale/cas_scale_probe.py#L48).

Important properties:

- no `readline()`
- no `SI\\r\\n`
- no `\\r\\n` trigger writes
- no input-buffer reset
- hard cap on captured bytes

That fits the observed CAS behavior from [CAS_SCALE_AGENT_HANDOFF.md](/opt/intelliscale/source/CAS_SCALE_AGENT_HANDOFF.md): fixed-width packets, often no newline, often repeated continuously.

### 2. One purpose: CAS stream troubleshooting

The probe is not trying to also work for newline scales, prompted scales, or Mettler-specific behavior. The command in [scale/management/commands/run_cas_probe.py](/opt/intelliscale/source/scale/management/commands/run_cas_probe.py#L18) is intentionally CAS-specific.

That reduces protocol cross-talk and makes the output easier to reason about.

### 3. Satellite-style visibility

The probe writes structured status like:

- `weight`
- `timestamp`
- `status`
- `last_attempt`
- `raw_packet`
- `raw_bytes_hex`

That makes it much easier to separate "the serial read returned quickly" from "the payload parsed correctly".

## Why The Current App Path Is Less Reliable For CAS

### 1. The shared reader mixes multiple incompatible strategies

The generic helper in [scale/scale_utils.py](/opt/intelliscale/source/scale/scale_utils.py#L267) does this:

1. short stream read
2. reset input buffer
3. `readline()`
4. send `SI\\r\\n`
5. `readline()`
6. send `\\r\\n`
7. `readline()`
8. finally read whatever is waiting

That is sensible for a mixed fleet, but CAS is a continuously streaming fixed-width device. The extra strategies increase the odds of:

- discarding usable bytes
- waiting on newline behavior that CAS does not use
- writing prompt commands the device does not need
- parsing a partial or overgrown buffer instead of a small packet sample

### 2. The app still has request-driven direct-read paths

Both [scale/views/scale.py](/opt/intelliscale/source/scale/views/scale.py#L317) and [scale/views/scale.py](/opt/intelliscale/source/scale/views/scale.py#L417) still contain direct serial fallback logic that:

- opens the port
- reads once through the generic helper
- closes the port again

For CAS, this is a weaker model than a stable single-owner reader because the scale is already streaming continuously and the CH341 path has historically been sensitive to reopen/recovery behavior.

### 3. The generic path can over-read badly on a continuous CAS stream

Observed on this machine on 2026-03-30 while reading scale `0021`:

- direct shared helper call returned in about `413 ms`
- but it captured `53891` bytes in one read
- the parsed preview was only `'= 0'`

That is a strong sign that the generic helper is reading far more than is useful for CAS and then extracting a value from a noisy aggregate instead of from a clean packet-sized sample.

By contrast, the probe caps its sample size and reports exactly how many bytes it kept.

## Real Observations From This Machine

### Successful probe sample

Running `python manage.py run_cas_probe --scale-id 0021 --samples 1` on 2026-03-30 produced:

- `status: "ok"`
- `weight: 0.0`
- `raw_packet: "= 0000.0"`
- `bytes_captured: 32`

### Shared helper sample

A direct single call through the existing shared helper on 2026-03-30 produced:

- `weight: 0.0`
- `raw_len: 53891`
- parsed preview `'= 0'`

This did not hang, but it is not a healthy shape for CAS parsing.

### Remaining parser edge

A separate one-off probe call on 2026-03-30 also exposed a residual CAS parser gap:

- payload looked like `=-0000.0`
- the bounded read still returned immediately
- but the parser marked that sample as `unparsed`

So the probe is already better at the "do not hang" goal, but it still has one parsing edge around signed zero payloads.

## Assessment: Can This Be Added To The App Safely?

Yes, but only as an explicit protocol-specific path.

Replacing the shared helper globally would be risky because other scale types may depend on the current generic behavior:

- newline-terminated scales
- prompted scales
- Mettler/MT-SICS-like behavior

Those paths are part of the reason `read_weight_from_serial()` currently tries `readline()` and prompt writes.

## Safe Way To Integrate It

### Recommendation

Add a scale protocol selector and dispatch by protocol instead of changing the default reader for every scale.

Best shape:

- add a new `Scale.protocol` field
- keep existing generic behavior as the default
- add a `cas_stream` option for the CAS C1-200A-C4 family
- route CAS scales through a CAS-specific reader based on the probe logic

### Why not infer from manufacturer/model?

The model already has `manufacturer` and `model_number`, but those are descriptive fields, not reliable behavior flags. An explicit protocol field is safer and easier to test.

## Recommended Integration Plan

1. Add `Scale.protocol` with choices like `generic`, `mettler_toledo`, `cas_stream`.
2. Introduce a small dispatch layer such as `read_weight_for_scale(scale, ser)` or `build_scale_reader(scale)`.
3. Keep [scale/scale_utils.py](/opt/intelliscale/source/scale/scale_utils.py#L267) as the generic fallback for existing scales.
4. Reuse the CAS probe's bounded stream logic for `cas_stream`.
5. Prefer using the CAS path inside [scale/satellite_service.py](/opt/intelliscale/source/scale/satellite_service.py#L145) first, because satellite mode is already the safer serial-ownership model.
6. Only use the CAS direct-read fallback in views when satellite mode is off.
7. Add tests for:
   - `= 0000.0`
   - `=-0000.0`
   - concatenated CAS bursts
   - no-data timeout returning promptly
   - existing prompted/non-CAS scale behavior remaining unchanged

## Final Assessment

Implementing this as a path in the app is feasible without breaking other scale models, but only if it is introduced as an opt-in protocol path rather than as a rewrite of the shared serial helper.

The probe proves the main design point:

- CAS behaves better with a small passive stream reader than with a multi-strategy generic reader

The current app should keep its generic reader for the mixed fleet, and add a separate CAS strategy for scales that need it.
