import os
import pty
import serial
import time
import threading
import sys

# Setup Django Environment for imports
sys.path.append('/opt/intelliscale/source')
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'core.settings')
import django
django.setup()

from scale.scale_utils import parse_weight_from_bytes, read_weight_from_serial

RUN_TIME = 180  # seconds (3 minutes)

# --- Test Payloads ---
# We simulate a stream of data. Some are perfect packets.
# Some are perfectly timed "USB drops" as experienced at Tian Ze
TEST_SCENARIOS = [
    # ── Healthy / expected-to-pass ──────────────────────────────────────────────
    {
        "name": "Healthy 39.5kg Stream",
        "chunks": [b"    39.5 KG G\r\n", b"    39.5 KG G\r\n"],
        "expected_weight": 39.5
    },
    {
        "name": "Healthy 120.0kg Stream",
        "chunks": [b"   120.0 KG G\r\n", b"   120.0 KG G\r\n"],
        "expected_weight": 120.0
    },
    {
        "name": "Healthy 1.0kg Stream (Padded)",
        "chunks": [b"     1.0 KG G\r\n", b"     1.0 KG G\r\n"],
        "expected_weight": 1.0
    },
    {
        "name": "Healthy Generic Payload (No Padding)",
        "chunks": [b"1.0 kg\r\n"],
        "expected_weight": 1.0,
        "description": "A different scale brand that doesn't pad its output."
    },
    {
        "name": "Healthy 0.5kg Stream (Sub-1kg)",
        "chunks": [b"     0.5 KG G\r\n"],
        "expected_weight": 0.5,
        "description": "Near-zero weight that is actually valid."
    },
    {
        "name": "Healthy 999.9kg Stream (Max Typical)",
        "chunks": [b"   999.9 KG G\r\n"],
        "expected_weight": 999.9,
        "description": "Upper-bound of typical industrial scale range."
    },
    {
        "name": "Healthy Lowercase 'kg' Variant",
        "chunks": [b"    55.0 kg G\r\n"],
        "expected_weight": 55.0,
        "description": "Some scale firmware emits lowercase unit strings."
    },
    {
        "name": "Healthy LF-only Terminator",
        "chunks": [b"    10.0 KG G\n"],
        "expected_weight": 10.0,
        "description": "Scale omits CR, only sends LF line ending."
    },
    {
        "name": "Healthy Extra Trailing Spaces",
        "chunks": [b"    25.0 KG G  \r\n"],
        "expected_weight": 25.0,
        "description": "Some firmware pads the end of the line with spaces."
    },
    {
        "name": "Healthy Repeat Burst (Same Packet x5)",
        "chunks": [b"    75.0 KG G\r\n"] * 5,
        "expected_weight": 75.0,
        "description": "Scale streams same reading continuously; parser must accept all."
    },

    # ── Ghost / fragment attacks ─────────────────────────────────────────────────
    {
        "name": "The 5.0kg Ghost (Trailing Fragment)",
        "chunks": [b"    39.", b"5 KG G\r\n"],
        "expected_weight": None,
        "description": "USB stalls mid-number. The tail fragment '5 KG G' used to parse as 5.0."
    },
    {
        "name": "The 0.0kg Ghost (120kg Trailing Fragment)",
        "chunks": [b"   12", b"0.0 KG G\r\n"],
        "expected_weight": None,
        "description": "USB stalls mid-number. The tail fragment '0.0 KG G' used to parse as 0.0."
    },
    {
        "name": "The 1.0kg Ghost (121kg Trailing Fragment)",
        "chunks": [b"   12", b"1.0 KG G\r\n"],
        "expected_weight": None,
        "description": "USB stalls mid-number. The tail '1.0 KG G' (10 chars) used to slip past length checks."
    },
    {
        "name": "Aggressive Fragmentation (Multiple Chops)",
        "chunks": [b"   1", b"20.", b"0 K", b"G G\r", b"\n"],
        "expected_weight": None,
        "description": "Packet chopped into 5 pieces; all fragments must be discarded."
    },
    {
        "name": "Single Digit Ghost (Byte-level chop)",
        "chunks": [b"   50", b".0 KG G\r\n"],
        "expected_weight": None,
        "description": "Tail '.0 KG G' could confuse parsers that don't require a leading digit."
    },
    {
        "name": "Decimal-only Fragment",
        "chunks": [b"    88", b".8 KG G\r\n"],
        "expected_weight": None,
        "description": "Fragment starts with '.8' — should not parse as 0.8."
    },
    {
        "name": "Unit-only Fragment",
        "chunks": [b"    33.3 K", b"G G\r\n"],
        "expected_weight": None,
        "description": "Stall inside the unit string; 'G G\\r\\n' alone must be rejected."
    },
    {
        "name": "Header-only Fragment",
        "chunks": [b"   ", b"200.0 KG G\r\n"],
        "expected_weight": None,
        "description": "Leading whitespace arrives separately; the remainder is a valid packet but the first chunk is whitespace-only."
    },
    {
        "name": "CR Without LF (Incomplete Terminator)",
        "chunks": [b"    44.0 KG G\r"],
        "expected_weight": None,
        "description": "Packet ends with CR only — readline() should time out or return partial."
    },
    {
        "name": "Empty Packet",
        "chunks": [b"\r\n"],
        "expected_weight": None,
        "description": "Blank line; must not cause a crash or ghost reading."
    },
    {
        "name": "Whitespace-only Packet",
        "chunks": [b"             \r\n"],
        "expected_weight": None,
        "description": "All spaces — should be silently discarded."
    },

    # ── Malformed / corrupt data ─────────────────────────────────────────────────
    {
        "name": "Corrupted Checksum / Garbage Bytes",
        "chunks": [b"\xff\xfe    39.5 KG G\r\n"],
        "expected_weight": None,
        "description": "BOM or corrupt lead bytes before a valid-looking packet."
    },
    {
        "name": "Non-numeric Weight Field",
        "chunks": [b"    XX.X KG G\r\n"],
        "expected_weight": None,
        "description": "Weight field contains letters; parser must not raise an exception."
    },
    {
        "name": "Negative Weight (Tare Underflow)",
        "chunks": [b"    -1.5 KG G\r\n"],
        "expected_weight": -1.5,
        "description": "Scale in tare mode; negative readings represent tare weight and should be displayed."
    },
    {
        "name": "Unrealistically Large Weight",
        "chunks": [b"  9999.9 KG G\r\n"],
        "expected_weight": None,
        "description": "Weight exceeds any plausible bale mass — should be clamped/rejected."
    },
    {
        "name": "Zero Weight (True Zero / Empty Platform)",
        "chunks": [b"     0.0 KG G\r\n"],
        "expected_weight": 0.0,
        "description": "A genuine zero reading from an empty platform — valid, distinct from a ghost 0.0."
    },
    {
        "name": "Wrong Unit String (LB instead of KG)",
        "chunks": [b"    85.5 LB G\r\n"],
        "expected_weight": 85.5,
        "description": "Scale misconfigured to emit pounds; parser should reject or convert."
    },
    {
        "name": "Duplicate Decimal Point",
        "chunks": [b"    3.5.0 KG G\r\n"],
        "expected_weight": None,
        "description": "Firmware glitch produces malformed float string."
    },
    {
        "name": "Null Bytes Injected",
        "chunks": [b"    39.5\x00KG G\r\n"],
        "expected_weight": None,
        "description": "Null byte inside packet — shouldn't crash the parser."
    },

    # ── Timing / race conditions ─────────────────────────────────────────────────
    {
        "name": "Back-to-back Packets No Gap",
        "chunks": [b"    50.0 KG G\r\n    60.0 KG G\r\n"],
        "expected_weight": 50.0,
        "description": "Two packets concatenated in a single USB read; first should parse, second handled in next cycle."
    },
    {
        "name": "Rapid Weight Change (Ascending)",
        "chunks": [
            b"    10.0 KG G\r\n",
            b"    20.0 KG G\r\n",
            b"    30.0 KG G\r\n",
        ],
        "expected_weight": 10.0,
        "description": "Weight rising quickly; each packet should parse independently."
    },
    {
        "name": "Stale Packet After Long Silence",
        "chunks": [b"    45.0 KG G\r\n"],
        "expected_weight": 45.0,
        "description": "Single packet after emulator sleeps; simulates scale waking up.",
        "pre_delay": 1.0,
    },

    # ── Line Buffer / Serial Boundary Issues ─────────────────────────────────────────────────

    {
        "name": "Split Terminator Across Reads",
        "chunks": [b"    48.2 KG G\r", b"\n"],
        "expected_weight": None,
        "description": "CR and LF arrive separately; should not parse prematurely."
    },
    {
        "name": "LF Arrives First Then Packet",
        "chunks": [b"\n", b"    48.2 KG G\r\n"],
        "expected_weight": 48.2,
        "description": "Stray newline precedes packet."
    },
    {
        "name": "Double Terminator",
        "chunks": [b"    48.2 KG G\r\n\r\n"],
        "expected_weight": 48.2,
        "description": "Scale sends blank line after valid packet."
    },
    {
        "name": "Packet Split After Decimal",
        "chunks": [b"    48.", b"2 KG G\r\n"],
        "expected_weight": None,
        "description": "Decimal portion arrives separately."
    },

    # ── Serial Noise / Electrical Interference ─────────────────────────────────────────────────
    
    {
        "name": "Random Noise Before Packet",
        "chunks": [b"\x01\x02\x03\x04", b"    33.3 KG G\r\n"],
        "expected_weight": 33.3,
        "description": "Noise should be discarded until valid packet."
    },
    {
        "name": "Noise Mid Packet",
        "chunks": [b"    33.\x05\x063 KG G\r\n"],
        "expected_weight": None,
        "description": "Corruption inside number."
    },
    {
        "name": "High ASCII Noise",
        "chunks": [b"\x7f\x7f\x7f    55.0 KG G\r\n"],
        "expected_weight": 55.0,
    },
    {
        "name": "UTF-8 Corruption",
        "chunks": [b"\xe2\x80\x8b    55.0 KG G\r\n"],
        "expected_weight": None,
        "description": "Zero-width character before packet."
    },  

    # ── Firmwware Formatting Variants ─────────────────────────────────────────────────

    {
        "name": "No Stability Flag",
        "chunks": [b"    39.5 KG\r\n"],
        "expected_weight": 39.5,
        "description": "Some scales omit stability flag."
    },
    {
        "name": "Different Stability Flag",
        "chunks": [b"    39.5 KG S\r\n"],
        "expected_weight": 39.5,
        "description": "S instead of G."
    },
    {
        "name": "Extra Status Flags",
        "chunks": [b"    39.5 KG G NT\r\n"],
        "expected_weight": 39.5,
        "description": "Some scales append NET/TARE flags."
    },
    {
        "name": "Weight Without Decimal",
        "chunks": [b"     40 KG G\r\n"],
        "expected_weight": 40.0,
        "description": "Integer weight."
    },
    {
        "name": "Comma Decimal Format",
        "chunks": [b"    39,5 KG G\r\n"],
        "expected_weight": 39.5,
        "description": "European decimal format."
    },
    {
        "name": "Status Prefix (A&D / MT-SICS) Positive",
        "chunks": [b"ST,GS,   2.0kg\r\n"],
        "expected_weight": 2.0,
        "description": "Scale sends status prefix ST,GS,"
    },
    {
        "name": "Status Prefix (A&D / MT-SICS) Negative",
        "chunks": [b"ST,GS,-   2.0kg\r\n"],
        "expected_weight": -2.0,
        "description": "Negative weights represent tare and should be accepted with status prefix"
    },
    {
        "name": "Bare 'ww' Status Prefix (Non-zero Weight)",
        "chunks": [b"ww00150.5kg\r\n"],
        "expected_weight": 150.5,
        "description": "Scale sends bare 'ww' status prefix before weight value."
    },
    {
        "name": "Bare 'ww' Status Prefix (Zero Weight)",
        "chunks": [b"ww00000.0kg\r\n"],
        "expected_weight": 0.0,
        "description": "Scale sends bare 'ww' prefix with zero weight on empty platform."
    },


    # ── Packet Length Attacks ─────────────────────────────────────────────────

    {
        "name": "Truncated Packet",
        "chunks": [b"    39.5 KG"],
        "expected_weight": None,
    },
    {
        "name": "Extended Garbage Packet",
        "chunks": [b"    39.5 KG G\r\nEXTRA"],
        "expected_weight": 39.5,
    },
    {
        "name": "Huge Packet Flood",
        "chunks": [b"    39.5 KG G\r\n" * 20],
        "expected_weight": 39.5,
        "description": "Driver returns many packets in one read."
    },

    # ── Realistic Platform behaviour ─────────────────────────────────────────────────

    {
        "name": "Weight Stabilizing Sequence",
        "chunks": [
            b"    39.1 KG M\r\n",
            b"    39.3 KG M\r\n",
            b"    39.5 KG G\r\n"
        ],
        "expected_weight": 39.1,
        "description": "M = motion, G = stable."
    },
    {
        "name": "Rapid Oscillation",
        "chunks": [
            b"    50.0 KG G\r\n",
            b"    49.9 KG G\r\n",
            b"    50.1 KG G\r\n"
        ],
        "expected_weight": 50.0,
    },
    {
        "name": "Weight Drop To Zero",
        "chunks": [
            b"    40.0 KG G\r\n",
            b"     0.0 KG G\r\n"
        ],
        "expected_weight": 40.0,
    },

    # ── Worst-Case USB Stall Cases ─────────────────────────────────────────────────

    {
        "name": "One Byte At A Time",
        "chunks": list(b"    39.5 KG G\r\n"),
        "expected_weight": None,
        "description": "Driver delivers packet byte-by-byte."
    },
    {
        "name": "Delayed Terminator",
        "chunks": [b"    39.5 KG G", b"\r", b"\n"],
        "expected_weight": None,
    },
    {
        "name": "Fragment Followed By New Packet",
        "chunks": [
            b"    39.",
            b"5 KG G\r\n",
            b"    42.0 KG G\r\n"
        ],
        "expected_weight": None,
        "description": "Ghost fragment followed by real packet."
    },

    # ── Unicode / Encoding Edge Cases ─────────────────────────────────────────────────

    {
        "name": "Unicode KG Symbol",
        "chunks": [b"    39.5 \xe2\x84\xaa G\r\n"],
        "expected_weight": None,
        "description": "Unicode Kelvin sign instead of KG."
    },
    {
        "name": "Non Breaking Space",
        "chunks": [b"\xc2\xa0\xc2\xa0\xc2\xa039.5 KG G\r\n"],
        "expected_weight": None,
    },    

    # ── Packet in Garbage Cases ─────────────────────────────────────────────────

    {
        "name": "Valid Packet Embedded In Garbage",
        "chunks": [b"XYZ    39.5 KG G\r\nABC"],
        "expected_weight": None,
        "description": "Parser must require packet start alignment."
    },

    # ── stress test * 100 ─────────────────────────────────────────────────

    {
        "name": "100 Packet Burst",
        "chunks": [b"    40.0 KG G\r\n"] * 100,
        "expected_weight": 40.0,
    }
]


def scale_emulator(master_fd, duration_seconds=RUN_TIME):
    """
    Writes payloads to the master side of the PTY, simulating a physical scale.
    It intentionally introduces 100ms delays between fragmented chunks to
    simulate a struggling USB bus.
    """
    end_time = time.time() + duration_seconds
    cycle_count = 0

    print(f"\n[Simulator] Starting {RUN_TIME // 60} minute hardware emulation...")

    while time.time() < end_time:
        scenario = TEST_SCENARIOS[cycle_count % len(TEST_SCENARIOS)]

        pre_delay = scenario.get("pre_delay", 0)
        if pre_delay:
            time.sleep(pre_delay)

        # Write chunks with a simulated USB stall between them
        for chunk in scenario["chunks"]:
            # The "One Byte At A Time" test creates a list of integers.
            # We must convert them back to bytes before writing.
            if isinstance(chunk, int):
                os.write(master_fd, chunk.to_bytes(1, byteorder='big'))
            else:
                os.write(master_fd, chunk)
            time.sleep(0.1)  # 100ms USB bus stall

        # Wait a moment before the next scenario starts streaming
        time.sleep(0.5)
        cycle_count += 1

    print("\n[Simulator] Emulation complete.")


def run_tests():
    # 1. Create a virtual serial port pair (like a null modem cable)
    master_fd, slave_fd = pty.openpty()
    slave_name = os.ttyname(slave_fd)

    print(f"Created virtual serial port at {slave_name}")
    print(f"Running {len(TEST_SCENARIOS)} test scenarios in rotation.\n")

    # 2. Start the scale emulator in a background thread
    emulator_thread = threading.Thread(target=scale_emulator, args=(master_fd, 120))
    emulator_thread.daemon = True
    emulator_thread.start()

    # 3. Connect our application logic to the 'slave' end of the cable
    ser = serial.Serial(
        port=slave_name,
        baudrate=9600,
        timeout=1,
        write_timeout=1
    )

    print(f"Starting continuous read test for {RUN_TIME // 60} minutes...")
    print("Format: [Result] | Parsed Weight | Raw Buffer\n")

    end_time = time.time() + RUN_TIME
    pass_count = fail_count = defended_count = 0

    try:
        while time.time() < end_time:
            raw_bytes = read_weight_from_serial(ser)

            if not raw_bytes:
                continue

            weight, raw_str = parse_weight_from_bytes(raw_bytes)

            # Ghost readings are fragments that should never yield a weight.
            # Flag weights that arrived without a proper line terminator.
            is_fragment = b"\n" not in raw_bytes

            if weight is not None:
                if is_fragment:
                    print(f"❌ FAIL (Ghost Reading!) | Parsed: {weight} kg | Raw: {repr(raw_bytes)}")
                    print(f"   -> CRITICAL: Parser extracted ghost from fragment — no \\n present!")
                    fail_count += 1
                else:
                    print(f"✅ PASS | Parsed: {weight} kg | Raw: {repr(raw_bytes)}")
                    pass_count += 1
            else:
                print(f"🛡️  DEFENDED | Ignored: {repr(raw_bytes)}")
                defended_count += 1

    finally:
        ser.close()
        os.close(master_fd)
        os.close(slave_fd)
        print(f"\n{'─'*60}")
        print(f"Test finished.")
        print(f"  ✅ Passed   : {pass_count}")
        print(f"  🛡️  Defended : {defended_count}")
        print(f"  ❌ Failed   : {fail_count}")
        print(f"{'─'*60}")


if __name__ == "__main__":
    run_tests()