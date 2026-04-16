# -*- coding: utf-8 -*-
"""
k10cr2.py
Control module for the Thorlabs K10CR2 Stepper Motor Rotation Mount.
Uses pylablib (Kinesis backend) to communicate via USB.

Two K10CR2 mounts are in use:
  GLP -- Glan-Laser Calcite Polarizer (GL10-C), acts as a power attenuator.
  HWP -- Half-Wave Plate, used to rotate polarization.

When the GLP angle is changed by delta degrees, the HWP is automatically
moved by delta/2 degrees to maintain the correct polarization relationship.

Usage (standalone):
    python k10cr2.py

    Lists all connected K10CR2 devices, lets you assign each a name
    (GLP or HWP), reads both current angles, prompts for a new GLP angle,
    moves GLP and automatically adjusts HWP, then prints final angles.
"""

from pylablib.devices import Thorlabs

# Serial-number prefix for K10CR2 devices (Thorlabs convention: 55xxxxxxx)
K10CR2_PREFIX = "55"

# K10CR1/K10CR2 encoder counts per degree (confirmed from pylablib source)
COUNTS_PER_DEG = 409600 / 3  # ≈ 136533.333...


# ---------------------------------------------------------------------------
# Discovery
# ---------------------------------------------------------------------------

def list_k10cr2_devices():
    """Return a list of (serial_number, description) for all connected K10CR2s."""
    all_devices = Thorlabs.list_kinesis_devices()
    k10cr2_devices = [
        (sn, desc) for sn, desc in all_devices
        if str(sn).startswith(K10CR2_PREFIX) or "K10CR" in str(desc).upper()
    ]
    return k10cr2_devices


def print_all_kinesis_devices():
    """Print every Kinesis device currently visible (for diagnostics)."""
    devs = Thorlabs.list_kinesis_devices()
    if not devs:
        print("No Kinesis devices found. Check USB connections and Thorlabs Kinesis drivers.")
        return []
    print("All connected Kinesis devices:")
    for sn, desc in devs:
        print(f"  Serial: {sn}  |  Description: {desc}")
    return devs


# ---------------------------------------------------------------------------
# Connection helpers
# ---------------------------------------------------------------------------

def open_stage(serial_number):
    """Open a connection to the K10CR2 identified by serial_number.

    scale=COUNTS_PER_DEG converts encoder counts to degrees automatically.
    Returns a KinesisMotor instance. Call close_stage() when done.
    """
    stage = Thorlabs.KinesisMotor(str(serial_number))
    stage.wait_for_stop()
    print(f"K10CR2 connected  (serial: {serial_number})")
    return stage


def close_stage(stage):
    """Close the connection to the stage."""
    try:
        stage.close()
    except Exception:
        pass


# ---------------------------------------------------------------------------
# Motion control
# ---------------------------------------------------------------------------

def get_angle(stage):
    """Return the current angle of the stage in degrees (float)."""
    return stage.get_position(scale=False) / COUNTS_PER_DEG


def set_angle(stage, angle_deg, wait=True):
    """Move the stage to angle_deg (absolute, degrees).

    If wait=True (default) the call blocks until the move completes.
    """
    stage.move_to(angle_deg * COUNTS_PER_DEG, scale=False)
    if wait:
        stage.wait_for_stop()


def home_stage(stage):
    """Home the stage (return to hardware zero) and wait for completion."""
    print("Homing stage...")
    stage.home(sync=True)
    print("Homing complete.")


def set_glp_angle(glp_stage, hwp_stage, new_glp_angle_deg, wait=True):
    """Move GLP to new_glp_angle_deg, then shift HWP by half the GLP change.

    The HWP rotates polarization; moving it by half the GLP change keeps the
    polarization state aligned after the attenuator is adjusted.

    Parameters
    ----------
    glp_stage        : KinesisMotor -- open connection to the GLP mount
    hwp_stage        : KinesisMotor -- open connection to the HWP mount
    new_glp_angle_deg: float -- target angle for the GLP (degrees)
    wait             : bool  -- block until both moves complete (default True)

    Returns
    -------
    (glp_final, hwp_final) : (float, float) -- measured angles after the move
    """
    current_glp = get_angle(glp_stage)
    current_hwp = get_angle(hwp_stage)

    delta_glp = new_glp_angle_deg - current_glp
    new_hwp_angle = current_hwp + delta_glp / 2.0

    print(f"GLP: {current_glp:.4f} deg -> {new_glp_angle_deg:.4f} deg  "
          f"(delta = {delta_glp:+.4f} deg)")
    print(f"HWP: {current_hwp:.4f} deg -> {new_hwp_angle:.4f} deg  "
          f"(delta = {delta_glp / 2.0:+.4f} deg)")

    set_angle(glp_stage, new_glp_angle_deg, wait=wait)
    set_angle(hwp_stage, new_hwp_angle, wait=wait)

    glp_final = get_angle(glp_stage)
    hwp_final = get_angle(hwp_stage)
    return glp_final, hwp_final


# ---------------------------------------------------------------------------
# Main — runs when executed directly
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    # 1. Show all Kinesis devices
    all_devs = print_all_kinesis_devices()
    if not all_devs:
        raise SystemExit(1)

    # 2. Filter to K10CR2 candidates
    k10cr2_devs = [
        (sn, desc) for sn, desc in all_devs
        if str(sn).startswith(K10CR2_PREFIX) or "K10CR" in str(desc).upper()
    ]

    if not k10cr2_devs:
        print(
            "\nNo K10CR2 devices found in the filtered list.\n"
            "All detected devices are listed above — enter serial numbers manually."
        )
        glp_sn = input("Enter serial number for GLP mount: ").strip()
        hwp_sn = input("Enter serial number for HWP mount: ").strip()
    else:
        # 3. Prompt user to name each device
        print("\nAssign a name to each K10CR2 (type GLP or HWP, or leave blank to skip):")
        names = {}   # name -> serial_number
        for sn, desc in k10cr2_devs:
            raw = input(f"  Serial {sn}  ({desc})  -> name: ").strip().upper()
            if raw:
                names[raw] = str(sn)

        if "GLP" not in names or "HWP" not in names:
            print(
                "\nBoth GLP and HWP must be named to continue.\n"
                f"Named so far: {names}"
            )
            raise SystemExit(1)

        glp_sn = names["GLP"]
        hwp_sn = names["HWP"]

    # 4. Connect to both stages
    print(f"\nConnecting to GLP (serial {glp_sn}) ...")
    glp_stage = open_stage(glp_sn)

    print(f"Connecting to HWP (serial {hwp_sn}) ...")
    hwp_stage = open_stage(hwp_sn)

    try:
        # 5. Read and display current angles
        glp_angle = get_angle(glp_stage)
        hwp_angle = get_angle(hwp_stage)
        print(f"\nCurrent angles:")
        print(f"  GLP: {glp_angle:.4f} deg")
        print(f"  HWP: {hwp_angle:.4f} deg")

        # 6. Prompt for new GLP angle
        raw = input(
            "\nEnter new target angle for GLP in degrees (0-360), "
            "or press Enter to skip: "
        ).strip()

        if raw:
            target_glp = float(raw)
            print(f"\nMoving GLP to {target_glp:.4f} deg (HWP will follow automatically)...")
            glp_final, hwp_final = set_glp_angle(glp_stage, hwp_stage, target_glp)
            print(f"\nFinal angles:")
            print(f"  GLP: {glp_final:.4f} deg")
            print(f"  HWP: {hwp_final:.4f} deg")
        else:
            print("No move requested.")

    finally:
        close_stage(glp_stage)
        close_stage(hwp_stage)
        print("Stage connections closed.")
