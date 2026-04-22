# -*- coding: utf-8 -*-
"""
sweep-ple.py
11th–16th Prompts: Merges sweep-alignment.py with lightfield-ple.py.

At the start:
  - Opens LightField remotely (no GUI)
  - Creates the dated output folder
  - Checks sensor temperature
  - Loads a saved experiment (grating wait once only)
  - Sets integration time (kept for all wavelengths)

Then runs the full power-convergence sweep (with optional alignment checks).
After power convergence at each wavelength:
  - Acquires a LightField frame saved as "PLE<wavelength>"
  - Logs wavelength, power (mW), and integration time (s)
  - Closes the shutter
  - Moves to the next wavelength

Usage
-----
    C:\\Users\\schul\\anaconda3\\envs\\lab-controls\\python.exe sweep-ple.py
"""

# ---------------------------------------------------------------------------
# LightField assemblies must be loaded before other local imports
# ---------------------------------------------------------------------------
import clr
import os
import sys

LIGHTFIELD_DIR  = r"C:\Program Files\Princeton Instruments\LightField"
ADDIN_VIEWS_DIR = os.path.join(LIGHTFIELD_DIR, "AddInViews")
SCRIPT_DIR      = os.path.dirname(os.path.abspath(__file__))

# Ensure the project folder (pm100d.py, k10cr2.py, etc.) is on the path
if SCRIPT_DIR not in sys.path:
    sys.path.insert(0, SCRIPT_DIR)

sys.path.append(LIGHTFIELD_DIR)
sys.path.append(ADDIN_VIEWS_DIR)

clr.AddReference("PrincetonInstruments.LightField.AutomationV4")
clr.AddReference("PrincetonInstruments.LightFieldAddInSupportServices")
clr.AddReference("PrincetonInstruments.LightFieldViewV4")

from System.Collections.Generic import List
from System import String

from PrincetonInstruments.LightField.Automation import Automation
from PrincetonInstruments.LightField.AddIns import (
    CameraSettings,
    ExperimentSettings,
    SensorTemperatureStatus,
)

# ---------------------------------------------------------------------------
# Standard and lab imports
# ---------------------------------------------------------------------------
import csv
import serial
import threading
import time
from datetime import datetime

import pm100d
import k10cr2
from power_convergence import converge_power, TOLERANCE_MW, SETTLE_S

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

PORT           = "COM6"
BAUD           = 19200
TARGET_TEMP_C  = -70.0
TEMP_TOLERANCE = 2.0   # °C


# ---------------------------------------------------------------------------
# Laser helpers
# ---------------------------------------------------------------------------

def get_wavelength(ser):
    ser.write(b"?VW\r")
    response = ser.readline().decode("ascii", errors="replace").strip()
    try:
        return float(response.split()[-1])
    except (ValueError, IndexError):
        print(f"  Unexpected wavelength response: {response!r}")
        return None


def set_wavelength(ser, wavelength_nm):
    command = f"VW= {int(wavelength_nm)}\r"
    ser.write(command.encode("ascii"))
    return ser.readline().decode("ascii", errors="replace").strip()


def set_shutter(ser, open_shutter):
    ser.write(b"S=1\r" if open_shutter else b"S=0\r")
    return ser.readline().decode("ascii", errors="replace").strip()


def set_alignment(ser, enable):
    ser.write(b"ALIGN=1\r" if enable else b"ALIGN=0\r")
    return ser.readline().decode("ascii", errors="replace").strip()


# ---------------------------------------------------------------------------
# Alignment check
# ---------------------------------------------------------------------------

def do_alignment_check(ser):
    print(f"\n{'*'*65}")
    print("  ALIGNMENT CHECK")
    print(f"{'*'*65}")

    print("  Turning on alignment mode...")
    ack = set_alignment(ser, True)
    print(f"  Laser response: {ack!r}")

    print("  Waiting 5 seconds...")
    time.sleep(5)

    print("  Opening shutter...")
    ack = set_shutter(ser, True)
    print(f"  Laser response: {ack!r}")

    print("\n  Put on ND filter on the laser path before checking focus. "
          "Take out ND filter before clicking enter")
    input("  Press Enter to continue...")

    print("  Closing shutter...")
    ack = set_shutter(ser, False)
    print(f"  Laser response: {ack!r}")

    print("  Turning off alignment mode...")
    ack = set_alignment(ser, False)
    print(f"  Laser response: {ack!r}")

    print("  Waiting 5 s before resuming...")
    time.sleep(5)

    print("  Resuming sweep...\n")


# ---------------------------------------------------------------------------
# LightField acquisition helper
# ---------------------------------------------------------------------------

def lf_acquire(experiment, wl, folder_path):
    """Set filename to PLE<wl>, acquire, wait for completion, return saved path."""
    wl_str    = str(int(wl)) if wl == int(wl) else str(wl)
    base_name = f"PLE{wl_str}"

    experiment.SetValue(ExperimentSettings.FileNameGenerationBaseFileName, base_name)
    experiment.Acquire()

    while experiment.IsRunning:
        time.sleep(0.2)

    result_path = experiment.GetValue(ExperimentSettings.AcquisitionOutputFilesResult)
    return result_path or folder_path, base_name


# ---------------------------------------------------------------------------
# Timed input helper
# ---------------------------------------------------------------------------

def timed_input(prompt, timeout, default):
    """Show *prompt*, return user input.  If no response within *timeout* seconds,
    print a notice and return *default*."""
    result = [default]

    def _read():
        try:
            result[0] = input(prompt)
        except Exception:
            pass

    t = threading.Thread(target=_read, daemon=True)
    t.start()
    t.join(timeout)
    if t.is_alive():
        print(f"\nNo response in {timeout} s — defaulting to '{default}'.")
    return result[0]


# ---------------------------------------------------------------------------
# Main sweep
# ---------------------------------------------------------------------------

def run_sweep(
    ser, glp_stage, hwp_stage, rm, instr,
    experiment, int_time_s, folder_path,
    wavelengths_nm,
    target_mw,
    tolerance_mw=TOLERANCE_MW,
    angle_min=86.0,
    angle_max=100.0,
    laser_stabilize_s=5,
    shutter_stabilize_s=4,
    alignment_mode=False,
    alignment_interval=2,
):
    # -- Prime the serial connection before the sweep loop --
    # The confirmed-working port-check script always reads the current
    # wavelength first.  Without this, the Chameleon may ignore the very
    # first set command (the serial buffers / port state are not yet
    # fully initialised).
    print("  Priming serial connection — reading current laser wavelength...")
    current_wl = get_wavelength(ser)
    if current_wl is not None:
        print(f"  Laser currently at: {current_wl:.1f} nm")
    time.sleep(0.2)

    WL_TOLERANCE_NM  = 0.5
    WL_POLL_INTERVAL = 1.0
    WL_MAX_WAIT_S    = 20   # allow enough time for a large initial wavelength jump

    log = []

    for i, wl in enumerate(wavelengths_nm):
        # -- Optional alignment check (not before the first wavelength) --
        if alignment_mode and i > 0 and i % alignment_interval == 0:
            do_alignment_check(ser)

        print(f"\n{'='*65}")
        print(f"  Wavelength: {wl} nm  |  Target: {target_mw:.3f} mW  |  [{i+1}/{len(wavelengths_nm)}]")
        print(f"{'='*65}")

        # -- Set laser wavelength --
        ack = set_wavelength(ser, wl)
        print(f"  Laser set response : {ack!r}")

        # -- Poll until wavelength is confirmed (±0.5 nm), up to WL_MAX_WAIT_S --
        confirmed = None
        for poll_n in range(int(WL_MAX_WAIT_S / WL_POLL_INTERVAL)):
            time.sleep(WL_POLL_INTERVAL)
            confirmed = get_wavelength(ser)
            if confirmed is not None and abs(confirmed - wl) <= WL_TOLERANCE_NM:
                print(f"  Wavelength confirmed: {confirmed:.1f} nm")
                break
            if confirmed is not None:
                print(f"  Waiting for wavelength... current: {confirmed:.1f} nm (target {wl:.1f} nm)")
            # If 5 s have passed and the laser hasn't started moving at all,
            # resend the wavelength command — the first one may have been lost.
            if poll_n == 4 and confirmed is not None and abs(confirmed - wl) > 1.0:
                print("  No movement detected — resending wavelength command...")
                ack = set_wavelength(ser, wl)
                print(f"  Laser set response (retry): {ack!r}")
        else:
            print(f"  WARNING: Wavelength did not reach {wl:.1f} nm within {WL_MAX_WAIT_S} s "
                  f"(last reading: {confirmed}). Continuing anyway.")

        # -- Set PM100D calibration wavelength --
        pm100d.set_wavelength(instr, wl)

        # -- Wait for laser to stabilize --
        print(f"  Waiting {laser_stabilize_s} s for laser to stabilize...")
        time.sleep(laser_stabilize_s)

        # -- Open shutter --
        ack = set_shutter(ser, True)
        print(f"  Shutter open: {ack!r}")

        # -- Wait for power to stabilize after shutter open --
        print(f"  Waiting {shutter_stabilize_s} s for power to stabilize...")
        time.sleep(shutter_stabilize_s)

        # -- Read power --
        p_mw = pm100d.get_power(instr) * 1e3
        print(f"  Initial power : {p_mw:.3f} mW")

        # -- Converge if needed --
        if abs(p_mw - target_mw) > tolerance_mw:
            print(f"  Outside tolerance ({tolerance_mw:.3f} mW). Running convergence...")
            try:
                final_angle, p_mw = converge_power(
                    glp_stage, hwp_stage, instr,
                    target_mw=target_mw,
                    tolerance_mw=tolerance_mw,
                    angle_min=angle_min,
                    angle_max=angle_max,
                )
            except (ValueError, RuntimeError) as exc:
                print(f"  WARNING: Convergence failed — {exc}")
                print("  Logging current power and continuing.")
                p_mw = pm100d.get_power(instr) * 1e3
        else:
            print("  Already within tolerance. No adjustment needed.")

        # -- Record final angles --
        glp_deg = k10cr2.get_angle(glp_stage)
        hwp_deg = k10cr2.get_angle(hwp_stage)
        print(f"  Power converged : {p_mw:.3f} mW | GLP {glp_deg:.4f} deg | HWP {hwp_deg:.4f} deg")

        # -- Acquire LightField frame --
        print(f"  Acquiring LightField frame...")
        saved_path, base_name = lf_acquire(experiment, wl, folder_path)
        print(f"  LightField saved: {saved_path}")

        # -- Log entry --
        log.append({
            "wavelength_nm":    wl,
            "power_mw":         round(p_mw, 4),
            "integration_time_s": int_time_s,
            "glp_deg":          round(glp_deg, 4),
            "hwp_deg":          round(hwp_deg, 4),
            "lf_file":          saved_path,
        })
        print(f"  LOGGED: {wl} nm | {p_mw:.3f} mW | {int_time_s} s integration")

        # -- Close shutter --
        ack = set_shutter(ser, False)
        print(f"  Shutter closed: {ack!r}")

    return log


def save_log(log, out_dir):
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    path = os.path.join(out_dir, f"sweep_ple_{timestamp}.csv")
    fields = ["wavelength_nm", "power_mw", "integration_time_s", "glp_deg", "hwp_deg", "lf_file"]
    with open(path, "w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fields)
        writer.writeheader()
        writer.writerows(log)
    return path


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    # ------------------------------------------------------------------
    # 1. Start LightField in background
    # ------------------------------------------------------------------
    print("Starting LightField in background (no GUI)...")
    auto       = Automation(False, List[String]())
    app        = auto.LightFieldApplication
    experiment = app.Experiment
    print("LightField background instance ready.\n")

    # ------------------------------------------------------------------
    # 2. Create output folder (LightField save settings applied after Load)
    # ------------------------------------------------------------------
    date_str    = datetime.now().strftime("%Y%m%d")
    folder_name = f"{date_str}_Zihad"
    folder_path = os.path.join(SCRIPT_DIR, folder_name)

    os.makedirs(folder_path, exist_ok=True)
    print(f"Output folder created: {folder_path}\n")

    # ------------------------------------------------------------------
    # 3. Check sensor temperature
    # ------------------------------------------------------------------
    temp   = experiment.GetValue(CameraSettings.SensorTemperatureReading)
    status = experiment.GetValue(CameraSettings.SensorTemperatureStatus)

    print(f"Sensor temperature : {temp:.1f} °C")
    print(f"Temperature status : {status}")

    if status == SensorTemperatureStatus.Locked and abs(temp - TARGET_TEMP_C) <= TEMP_TOLERANCE:
        print(f"Sensor is locked at {temp:.1f} °C. OK.\n")
    else:
        print(
            f"\nWARNING: Sensor is NOT locked at {TARGET_TEMP_C} °C "
            f"(current: {temp:.1f} °C, status: {status})."
        )
        proceed = input("Continue anyway? [y/N]: ").strip().lower()
        if proceed != "y":
            print("Exiting.")
            auto.Dispose()
            sys.exit(0)
        print()

    # ------------------------------------------------------------------
    # 4. Load experiment — grating wait happens here, once only
    # ------------------------------------------------------------------
    saved = list(experiment.GetSavedExperiments())

    if not saved:
        print("No saved experiments found in LightField. Exiting.")
        auto.Dispose()
        sys.exit(1)

    print("Saved experiments:")
    for i, name in enumerate(saved):
        print(f"  [{i + 1}] {name}")

    while True:
        choice_str = input("\nSelect experiment number: ").strip()
        try:
            choice = int(choice_str) - 1
            if 0 <= choice < len(saved):
                break
            print(f"  Please enter a number between 1 and {len(saved)}.")
        except ValueError:
            print("  Invalid input — enter a number.")

    experiment.Load(saved[choice])
    print(f"Loaded experiment: {saved[choice]}")
    print("Waiting 10 seconds for the grating to move into place...")
    time.sleep(10)
    print("Grating ready.\n")

    # Set save directory AFTER Load() — loading an experiment resets LightField's
    # file settings back to whatever was saved inside it, so these must come after.
    # FileNameGenerationBaseFileName must also be set or LightField will not save.
    experiment.SetValue(ExperimentSettings.FileNameGenerationDirectory, folder_path)
    experiment.SetValue(ExperimentSettings.FileNameGenerationBaseFileName, "data")
    experiment.SetValue(ExperimentSettings.FileNameGenerationAttachDate, False)
    experiment.SetValue(ExperimentSettings.FileNameGenerationAttachIncrement, True)
    print(f"Save directory set to: {folder_path}\n")

    # ------------------------------------------------------------------
    # 5. Set integration time (kept for all wavelengths)
    # ------------------------------------------------------------------
    while True:
        it_str = input("Enter integration time (seconds): ").strip()
        try:
            int_time_s  = float(it_str)
            int_time_ms = int_time_s * 1000.0
            if int_time_ms <= 0:
                print("  Integration time must be positive.")
                continue
            break
        except ValueError:
            print("  Invalid input — enter a number.")

    experiment.SetValue(CameraSettings.ShutterTimingExposureTime, int_time_ms)
    print(f"Integration time set to {int_time_s} s ({int_time_ms:.0f} ms)\n")

    # ------------------------------------------------------------------
    # 6. Connect to PM100D
    # ------------------------------------------------------------------
    rm, instr = pm100d.open_meter()

    # ------------------------------------------------------------------
    # 7. List K10CR2s and name GLP / HWP / POL
    # ------------------------------------------------------------------
    all_devs = k10cr2.print_all_kinesis_devices()
    if not all_devs:
        pm100d.close_meter(rm, instr)
        auto.Dispose()
        sys.exit(1)

    k10cr2_devs = [
        (sn, desc) for sn, desc in all_devs
        if str(sn).startswith(k10cr2.K10CR2_PREFIX) or "K10CR" in str(desc).upper()
    ]

    print("\nNaming convention reminder:")
    print("  GLP = Glan Calcite Polarizer (power attenuator)")
    print("  HWP = Half-Wave Plate")
    print("  POL = Output polarizer")

    if not k10cr2_devs:
        print("\nNo K10CR2 devices matched. Enter serial numbers manually.")
        glp_sn = input("GLP serial number: ").strip()
        hwp_sn = input("HWP serial number: ").strip()
        pol_sn = input("POL serial number: ").strip()
    else:
        print("\nAssign a name to each K10CR2 (type GLP, HWP, or POL):")
        names = {}
        for sn, desc in k10cr2_devs:
            raw = input(f"  Serial {sn}  ({desc})  -> name: ").strip().upper()
            if raw:
                names[raw] = str(sn)

        if not all(k in names for k in ("GLP", "HWP", "POL")):
            print(f"\nGLP, HWP, and POL must all be named. Named so far: {names}")
            pm100d.close_meter(rm, instr)
            auto.Dispose()
            sys.exit(1)

        glp_sn = names["GLP"]
        hwp_sn = names["HWP"]
        pol_sn = names["POL"]

    print(f"\nConnecting to GLP (serial {glp_sn}) ...")
    glp_stage = k10cr2.open_stage(glp_sn)
    print(f"Connecting to HWP (serial {hwp_sn}) ...")
    hwp_stage = k10cr2.open_stage(hwp_sn)
    print(f"Connecting to POL (serial {pol_sn}) ...")
    pol_stage = k10cr2.open_stage(pol_sn)

    # -- Home all three stages --
    print("\nHoming all K10CR2 stages...")
    k10cr2.home_stage(glp_stage)
    k10cr2.home_stage(hwp_stage)
    k10cr2.home_stage(pol_stage)
    print("All stages homed.\n")

    # -- Polarization selection --
    print("Select output polarization:")
    print("  [1] P-pol  (POL -> 112 deg)")
    print("  [2] S-pol  (POL -> 22 deg)")
    while True:
        pol_choice = input("Enter 1 or 2: ").strip()
        if pol_choice == "1":
            pol_target_deg = 112.0
            pol_label = "P-pol"
            break
        elif pol_choice == "2":
            pol_target_deg = 22.0
            pol_label = "S-pol"
            break
        print("  Invalid choice — enter 1 or 2.")

    print(f"Moving POL to {pol_target_deg:.1f} deg ({pol_label})...")
    k10cr2.set_angle(pol_stage, pol_target_deg)
    print(f"  POL : {k10cr2.get_angle(pol_stage):.4f} deg\n")

    # ------------------------------------------------------------------
    # 8. Display current angles, prompt for starting angles
    # ------------------------------------------------------------------
    glp_angle = k10cr2.get_angle(glp_stage)
    hwp_angle = k10cr2.get_angle(hwp_stage)
    print(f"\nCurrent angles:")
    print(f"  GLP : {glp_angle:.4f} deg")
    print(f"  HWP : {hwp_angle:.4f} deg")

    glp_start_input = input(f"\nStarting angle for GLP (deg) [Enter to keep {glp_angle:.4f}]: ").strip()
    hwp_start_input = input(f"Starting angle for HWP (deg) [Enter to keep {hwp_angle:.4f}]: ").strip()

    glp_start = float(glp_start_input) if glp_start_input else glp_angle
    hwp_start = float(hwp_start_input) if hwp_start_input else hwp_angle

    if glp_start != glp_angle:
        print(f"Moving GLP to {glp_start:.4f} deg ...")
        k10cr2.set_angle(glp_stage, glp_start)
    if hwp_start != hwp_angle:
        print(f"Moving HWP to {hwp_start:.4f} deg ...")
        k10cr2.set_angle(hwp_stage, hwp_start)

    if glp_start_input or hwp_start_input:
        print(f"Starting angles set:")
        print(f"  GLP : {k10cr2.get_angle(glp_stage):.4f} deg")
        print(f"  HWP : {k10cr2.get_angle(hwp_stage):.4f} deg")

    # ------------------------------------------------------------------
    # 9. Sweep parameters
    # ------------------------------------------------------------------
    print("\n--- Wavelength sweep parameters ---")
    wl_start_input = input("Start wavelength (nm) [default 1064]: ").strip()
    wl_stop_input  = input("Stop  wavelength (nm, inclusive) [default 1056]: ").strip()
    wl_step_input  = input("Step size (nm, negative to sweep down) [default -4]: ").strip()

    wl_start = float(wl_start_input) if wl_start_input else 1064.0
    wl_stop  = float(wl_stop_input)  if wl_stop_input  else 1056.0
    wl_step  = float(wl_step_input)  if wl_step_input  else -4.0

    wavelengths = []
    wl = wl_start
    while (wl >= wl_stop if wl_step < 0 else wl <= wl_stop):
        wavelengths.append(wl)
        wl += wl_step

    if not wavelengths:
        print("No wavelengths in range. Check start/stop/step values.")
        k10cr2.close_stage(glp_stage)
        k10cr2.close_stage(hwp_stage)
        k10cr2.close_stage(pol_stage)
        pm100d.close_meter(rm, instr)
        auto.Dispose()
        sys.exit(1)

    print(f"Wavelengths to sweep: {wavelengths}")

    target_mw  = float(input("\nTarget power (mW): ").strip())
    tol_input  = input(f"Tolerance (mW) [default {TOLERANCE_MW}]: ").strip()
    tolerance  = float(tol_input) if tol_input else TOLERANCE_MW

    amin_input = input("GLP angle_min (deg) [default 86.0]: ").strip()
    amax_input = input("GLP angle_max (deg) [default 100.0]: ").strip()
    angle_min  = float(amin_input) if amin_input else 86.0
    angle_max  = float(amax_input) if amax_input else 100.0

    # ------------------------------------------------------------------
    # 10. Alignment mode
    # ------------------------------------------------------------------
    print("\n--- Alignment mode ---")
    align_input    = input("Enable alignment mode? [y/N]: ").strip().lower()
    alignment_mode = align_input == "y"

    alignment_interval = 2
    if alignment_mode:
        interval_input     = input("Wavelength iterations between alignment checks [default 2]: ").strip()
        alignment_interval = int(interval_input) if interval_input else 2
        print(f"Alignment check every {alignment_interval} iteration(s).")
    else:
        print("Alignment mode disabled.")

    # ------------------------------------------------------------------
    # 11. Run sweep
    # ------------------------------------------------------------------
    log = []
    try:
        with serial.Serial(PORT, BAUD, timeout=2) as ser:
            # Flush any stale data in the buffer that accumulated when the port
            # was opened (e.g. Chameleon status/welcome bytes).  Without this
            # the first readline() in set_wavelength() consumes that garbage
            # instead of the laser's real ACK, causing the first wavelength to
            # be silently ignored while all subsequent ones work correctly.
            ser.reset_input_buffer()
            ser.reset_output_buffer()
            time.sleep(0.5)   # Let the port settle before the first command

            log = run_sweep(
                ser, glp_stage, hwp_stage, rm, instr,
                experiment, int_time_s, folder_path,
                wavelengths_nm=wavelengths,
                target_mw=target_mw,
                tolerance_mw=tolerance,
                angle_min=angle_min,
                angle_max=angle_max,
                alignment_mode=alignment_mode,
                alignment_interval=alignment_interval,
            )

    except KeyboardInterrupt:
        print("\n\nSweep interrupted by user.")

    except serial.SerialException as exc:
        print(f"\nSerial error on {PORT}: {exc}")

    finally:
        try:
            with serial.Serial(PORT, BAUD, timeout=2) as ser:
                set_shutter(ser, False)
                set_alignment(ser, False)
                print("Shutter closed and alignment mode off (cleanup).")
        except Exception:
            pass

        reset = timed_input(
            "\nReturn GLP to 86.0 deg and HWP to 6.0 deg? (auto-yes in 5 s) [Y/n]: ",
            timeout=5, default="y",
        ).strip().lower()
        if reset != "n":
            print("Returning GLP to 86.0 deg and HWP to 6.0 deg ...")
            k10cr2.set_angle(glp_stage, 86.0)
            k10cr2.set_angle(hwp_stage, 6.0)
            print(f"  GLP : {k10cr2.get_angle(glp_stage):.4f} deg")
            print(f"  HWP : {k10cr2.get_angle(hwp_stage):.4f} deg")
        else:
            print(f"Angles unchanged — GLP : {k10cr2.get_angle(glp_stage):.4f} deg  |  "
                  f"HWP : {k10cr2.get_angle(hwp_stage):.4f} deg")

        k10cr2.close_stage(glp_stage)
        k10cr2.close_stage(hwp_stage)
        k10cr2.close_stage(pol_stage)
        pm100d.close_meter(rm, instr)
        auto.Dispose()
        print("All connections closed.")

        # Return laser to 700 nm, then Standby
        try:
            with serial.Serial(PORT, BAUD, timeout=2) as ser:
                print("\nReturning laser to 700 nm...")
                ack = set_wavelength(ser, 700)
                print(f"  Laser response: {ack!r}")
                print("Waiting 10 seconds for laser to stabilize...")
                time.sleep(10)
                ser.write(b"L=0\r")
                ack = ser.readline().decode("ascii", errors="replace").strip()
                print(f"Laser set to Standby (L=0): {ack!r}")
        except Exception as exc:
            print(f"Warning: could not return laser to standby — {exc}")

    # ------------------------------------------------------------------
    # 12. Print and save log
    # ------------------------------------------------------------------
    if log:
        print(f"\n{'='*65}")
        print(f"  Sweep complete  —  {len(log)} wavelength(s) logged")
        print(f"{'='*65}")
        print(f"  {'Wavelength (nm)':>18}  {'Power (mW)':>12}  {'Int. time (s)':>14}  {'GLP (deg)':>10}  {'HWP (deg)':>10}")
        print(f"  {'-'*18}  {'-'*12}  {'-'*14}  {'-'*10}  {'-'*10}")
        for row in log:
            print(
                f"  {row['wavelength_nm']:>18.1f}  "
                f"{row['power_mw']:>12.3f}  "
                f"{row['integration_time_s']:>14.3f}  "
                f"{row['glp_deg']:>10.4f}  "
                f"{row['hwp_deg']:>10.4f}"
            )
        csv_path = save_log(log, folder_path)
        print(f"\nLog saved to: {csv_path}")
    else:
        print("\nNo data was logged.")
