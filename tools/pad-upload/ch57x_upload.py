"""Upload a corrected key mapping to the CH57x macro pad (VID 1189 / PID 8840) via HID.
Protocol reverse-engineered from ch57x-keyboard-tool (k884x.rs, mod.rs, main.rs).
Writes to the vendor HID interface (usage page 0xFF00) with HidD_SetOutputReport.
"""
import sys
import hid

VID, PID = 0x1189, 0x8840

# USB HID keyboard usage codes
CODES = {
    "a": 0x04, "b": 0x05, "c": 0x06, "d": 0x07, "e": 0x08, "f": 0x09, "g": 0x0A,
    "h": 0x0B, "i": 0x0C, "j": 0x0D, "k": 0x0E, "l": 0x0F, "m": 0x10, "n": 0x11,
    "o": 0x12, "p": 0x13, "q": 0x14, "r": 0x15, "s": 0x16, "t": 0x17, "u": 0x18,
    "v": 0x19, "w": 0x1A, "x": 0x1B, "y": 0x1C, "z": 0x1D,
    "1": 0x1E, "2": 0x1F, "3": 0x20, "4": 0x21, "5": 0x22, "6": 0x23, "7": 0x24,
    "8": 0x25, "9": 0x26, "0": 0x27,
    "enter": 0x28, "esc": 0x29, "backspace": 0x2A, "tab": 0x2B, "space": 0x2C,
    "pause": 0x48,
    "right": 0x4F, "left": 0x50, "down": 0x51, "up": 0x52,
}
# Modifier bits (EnumSet declaration order in mod.rs)
MODS = {"ctrl": 0x01, "shift": 0x02, "alt": 0x04, "win": 0x08,
        "rctrl": 0x10, "rshift": 0x20, "ralt": 0x40, "rwin": 0x80}


def parse_combo(spec):
    """'ctrl-alt-shift-r' -> (modifier_byte, code_byte)"""
    mods = 0
    code = 0
    for part in spec.split("-"):
        if part in MODS:
            mods |= MODS[part]
        else:
            code = CODES[part]
    return mods, code


def bind_message(key_id, combo, layer=0):
    """Keyboard macro bind message per k884x.rs bind_key."""
    mods, code = parse_combo(combo)
    msg = [
        0x03,           # message header
        0xFE,           # bind command
        key_id,         # key id
        layer + 1,      # layer
        0x01,           # keyboard macro type
        0, 0, 0, 0, 0,  # reserved
        0x01,           # single press
        mods,
        code,
    ]
    return msg


def terminate():
    return [
        [0x03, 0xAA, 0xAA],
        [0x03, 0xFD, 0xFE, 0xFF],
        [0x03, 0xAA, 0xAA],
    ]


def to_key_id_button(n):
    return n + 1  # buttons 0..5 -> 1..6


def to_key_id_knob(action):
    # KnobAction: CCW=0, Press=1, CW=2 ; knob0 -> 16 + action
    return 16 + {"ccw": 0, "press": 1, "cw": 2}[action]


def build_messages(mapping):
    """mapping: {'buttons': {0: combo, ...}, 'knob': {'ccw':..., 'press':..., 'cw':...}}"""
    out = []
    for btn, combo in sorted((int(k), v) for k, v in mapping["buttons"].items()):
        out.append(bind_message(to_key_id_button(btn), combo))
        out.extend(terminate())
    for action, combo in mapping["knob"].items():
        out.append(bind_message(to_key_id_knob(action), combo))
        out.extend(terminate())
    return out


def main():
    import json
    with open(sys.argv[1], encoding="utf-8") as f:
        mapping = json.load(f)

    msgs = build_messages(mapping)

    dev = None
    for d in hid.enumerate(VID, PID):
        if d["usage_page"] == 0xFF00 and d["interface_number"] == 0:
            dev = hid.device()
            dev.open_path(d["path"])
            break
    if dev is None:
        print("vendor HID interface (MI_00, usage 0xFF00) not found")
        sys.exit(1)

    print(f"opened: {d['path'][:80]}")

    # Init: 64 zero bytes (main.rs open_device). Optional; skipped if unsupported.
    try:
        n = dev.write([0x00] * 64)
        print(f"init write: {n}")
    except Exception as e:
        print(f"init write failed (continuing): {e}")

    sent = 0
    for msg in msgs:
        buf = list(msg) + [0x00] * (64 - len(msg))
        n = dev.write(buf)
        sent += 1
        print(f"msg {sent:02d}: {' '.join(f'{b:02X}' for b in msg)}  (wrote {n})")
        if n <= 0:
            print(f"!! write failed: {n}")
            sys.exit(2)

    print(f"done: {sent} messages sent")


if __name__ == "__main__":
    main()
