#!/usr/bin/env python3
"""Phase 4 hardware validation — print Focusrite channel count.

Run on Mac Mini BEFORE implementing 4-channel ContinuousRecorder.

Exit code 0 if a device matching 'Focusrite' has max_input_channels >= 4.
Exit code 1 if Focusrite present but max_input_channels < 4 (must enable
    4-channel mode in Focusrite Control).
Exit code 2 if no Focusrite device found.
"""
import sys
import sounddevice as sd

SUBSTRING = "Focusrite"
REQUIRED_CHANNELS = 4


def main() -> int:
    print(f"Probing audio devices for substring '{SUBSTRING}'...\n")
    devices = sd.query_devices()
    print(f"{'Idx':<5}{'Name':<40}{'In':<5}{'Out':<5}{'SampleRate':<12}")
    print("-" * 70)
    focusrite_idx = None
    focusrite_channels = 0
    for i, dev in enumerate(devices):
        print(f"{i:<5}{dev['name'][:40]:<40}{dev['max_input_channels']:<5}{dev['max_output_channels']:<5}{dev['default_samplerate']:<12}")
        if SUBSTRING in dev["name"] and dev["max_input_channels"] > focusrite_channels:
            focusrite_idx = i
            focusrite_channels = dev["max_input_channels"]
    print()
    if focusrite_idx is None:
        print(f"FAIL: No device matching '{SUBSTRING}' found.")
        return 2
    if focusrite_channels < REQUIRED_CHANNELS:
        print(f"FAIL: Focusrite device #{focusrite_idx} reports max_input_channels={focusrite_channels} (< {REQUIRED_CHANNELS}).")
        print("Open Focusrite Control and switch to a multi-channel preset, then re-run.")
        return 1
    print(f"PASS: Focusrite device #{focusrite_idx} reports max_input_channels={focusrite_channels} (>= {REQUIRED_CHANNELS}).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
