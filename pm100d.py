# -*- coding: utf-8 -*-
"""
pm100d.py
Standalone control module for the Thorlabs PM100D optical power meter.
Communicates via USB using PyVISA and SCPI commands.

Confirmed hardware:
  VISA address : USB0::0x1313::0x8078::P0007396::INSTR
  Default calibration wavelength: 1070 nm (for 2P-PLE sweep range 1060-1080 nm)
  Averaging     : 500 points
"""

import pyvisa

VISA_ADDRESS = "USB0::0x1313::0x8078::P0007396::INSTR"
DEFAULT_WAVELENGTH_NM = 500
DEFAULT_AVERAGING = 500

power_log = []  # stores all power readings (W) taken during the session


# ---------------------------------------------------------------------------
# Connection helpers
# ---------------------------------------------------------------------------

def list_resources():
    """Print all VISA resources visible to the backend. Useful for discovery."""
    rm = pyvisa.ResourceManager()
    resources = rm.list_resources()
    print("Available VISA resources:")
    for r in resources:
        print(f"  {r}")
    rm.close()
    return resources


def open_meter(resource=VISA_ADDRESS,
               wavelength_nm=DEFAULT_WAVELENGTH_NM,
               averaging=DEFAULT_AVERAGING):
    """Open a connection to the PM100D and apply default settings.

    Returns (rm, instr) — pass both to close_meter() when done.
    """
    rm = pyvisa.ResourceManager()
    instr = rm.open_resource(resource)

    # Configure on connect
    instr.write("SENS:RANGE:AUTO ON")          # auto-range
    set_wavelength(instr, wavelength_nm)        # calibration wavelength
    instr.write("SENS:POW:UNIT W")             # readings in Watts
    set_averaging(instr, averaging)             # number of averages

    idn = instr.query("SYST:SENS:IDN?").strip()
    print(f"PM100D connected: {idn}")
    return rm, instr


def close_meter(rm, instr):
    """Close the instrument connection and resource manager."""
    try:
        instr.close()
    except Exception:
        pass
    try:
        rm.close()
    except Exception:
        pass


# ---------------------------------------------------------------------------
# Instrument control
# ---------------------------------------------------------------------------

def set_wavelength(instr, wavelength_nm):
    """Set the calibration wavelength (nm) so the correct responsivity is used."""
    instr.write(f"SENS:CORR:WAV {int(wavelength_nm)}")


def set_averaging(instr, n):
    """Set the number of measurement averages (1–300000)."""
    instr.write(f"SENS:AVER:COUN {int(n)}")


def get_power(instr):
    """Read a single averaged power measurement in Watts.

    Appends the result to power_log and returns it as a float."""
    response = instr.query("MEAS:POW?").strip()
    try:
        power_w = float(response)
        power_log.append(power_w)
        return power_w
    except ValueError:
        print(f"Unexpected power response: {response!r}")
        return None


def read_power_array(instr, n_readings):
    """Take n_readings successive power measurements.

    Returns a list of floats (W). All values are also appended to power_log."""
    readings = []
    for _ in range(n_readings):
        p = get_power(instr)
        if p is not None:
            readings.append(p)
    return readings


# ---------------------------------------------------------------------------
# Demo — runs when executed directly
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    rm, instr = open_meter()
    try:
        # Take 5 single readings
        print("\nTaking 5 single power readings:")
        for i in range(5):
            p = get_power(instr)
            print(f"  [{i+1}] {p:.6e} W")

        # Take a small array of 10 readings
        print("\nTaking array of 10 readings:")
        arr = read_power_array(instr, 10)
        print(f"  {[f'{v:.6e}' for v in arr]}")

        print(f"\nFull power_log ({len(power_log)} entries): {power_log}")
    finally:
        close_meter(rm, instr)
