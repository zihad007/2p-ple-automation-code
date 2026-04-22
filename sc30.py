# -*- coding: utf-8 -*-
"""
sc30.py
Standalone control module for the Thorlabs SC30 Optical Beam Shutter Controller
driving an SHBH1(T) high-speed shutter on channel 1.

Communicates over USB virtual COM port (COM7) using pyserial.

Serial settings:
  Baud rate : 115200
  Data bits : 8
  Stop bits : 1
  Parity    : None
  Terminator: \\r

Commands (channel 1) — from SC30 manual section 5.7.5
------------------------------------------------------
  shutterstate1?  -> query shutter state ("0" = closed, "1" = open)
  swtrig1=1       -> open shutter (software trigger active)
  swtrig1=0       -> close shutter (software trigger inactive)

The SC30 echoes every command back before the response.
Response format: "COMMAND VALUE\\r>" — we parse the last token before the prompt.
"""

import time
import serial

COM_PORT  = "COM7"
BAUD_RATE = 115200
TIMEOUT_S = 2.0
SETTLE_S  = 0.2


# ---------------------------------------------------------------------------
# Connection
# ---------------------------------------------------------------------------

def open_controller(port=COM_PORT, baud=BAUD_RATE):
    """Open a serial connection to the SC30. Returns the serial.Serial object."""
    ser = serial.Serial(
        port=port,
        baudrate=baud,
        bytesize=serial.EIGHTBITS,
        stopbits=serial.STOPBITS_ONE,
        parity=serial.PARITY_NONE,
        timeout=TIMEOUT_S,
    )
    time.sleep(0.5)
    ser.reset_input_buffer()
    ser.reset_output_buffer()
    print(f"SC30 connected on {port} at {baud} baud.")
    return ser


def close_controller(ser):
    """Close the serial connection."""
    try:
        ser.close()
    except Exception:
        pass


# ---------------------------------------------------------------------------
# Shutter control
# ---------------------------------------------------------------------------

def _send(ser, cmd):
    """Send a command (appends \\r) and return the raw response string.

    The SC30 echoes the command then appends the value and a '>' prompt.
    Example: send 'shutterstate1?' -> receive 'SHUTTERSTATE1? 0\\r>'
    """
    ser.reset_input_buffer()
    ser.write((cmd + "\r").encode())
    time.sleep(SETTLE_S)
    return ser.read_all().decode(errors="replace")


def _parse(raw):
    """Extract the value token from an echoed SC30 response."""
    # Strip trailing '>' prompt and whitespace, then take the last token
    cleaned = raw.replace(">", "").strip()
    tokens = cleaned.split()
    return tokens[-1] if tokens else ""


def open_shutter(ser):
    """Open channel 1 shutter via software trigger."""
    _send(ser, "swtrig1=1")
    print("Shutter: OPEN")


def close_shutter(ser):
    """Close channel 1 shutter via software trigger."""
    _send(ser, "swtrig1=0")
    print("Shutter: CLOSED")


def get_status(ser):
    """Query channel 1 shutter state. Returns True if open, False if closed."""
    raw = _send(ser, "shutterstate1?")
    val = _parse(raw)
    if val == "1":
        return True
    elif val == "0":
        return False
    else:
        print(f"Unrecognized status response: {raw!r}")
        return None


# ---------------------------------------------------------------------------
# Demo — runs when executed directly
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    ser = open_controller()
    try:
        print(f"Initial shutter state — open: {get_status(ser)}")

        open_shutter(ser)
        print("Waiting 5 seconds...")
        time.sleep(5)

        close_shutter(ser)
        print(f"Final shutter state — open: {get_status(ser)}")
    finally:
        close_controller(ser)
        print("SC30 disconnected.")
