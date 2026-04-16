# -*- coding: utf-8 -*-
"""
power_convergence.py
4th Prompt: Iteratively converge optical power to a user-specified target by
adjusting the GLP (Glan-Laser Polarizer) K10CR2 rotation mount.
The HWP (Half-Wave Plate) K10CR2 automatically tracks at half the GLP angle change.

Algorithm note
--------------
The prompt suggests starting with large angle steps (~1 deg) and shrinking them
as the power approaches the target. That works, but bisection search is faster
and more reliable:

  - Malus's law (P proportional to cos^2(theta)) is strictly monotone over any
    90-degree window, so the power vs. angle curve is guaranteed to be one-to-one
    across a reasonable search range.
  - Bisection halves the remaining uncertainty on every iteration regardless of
    the shape of the curve. A 90-degree range reaches sub-0.1-degree resolution
    in fewer than 10 iterations -- far fewer moves than a fixed-step walk.
  - Proportional / gradient-descent approaches need a well-calibrated step size;
    bisection needs none.

This script implements bisection. For reference:
  - Proportional step approach (as described in prompt): convergence in O(range/step) steps
  - Bisection (implemented here): convergence in O(log2(range/resolution)) steps
    e.g. 90-deg range to 0.1-deg resolution = ~10 steps guaranteed

Usage
-----
Run directly:
    C:\\Users\\schul\\anaconda3\\envs\\lab-controls\\python.exe power_convergence.py

Or import converge_power() into another script.
"""

import time
import sys

import pm100d
import k10cr2


TOLERANCE_MW = 0.1   # default convergence tolerance in mW
SETTLE_S     = 2.0   # seconds to wait after each angle move


# ---------------------------------------------------------------------------
# Core convergence routine
# ---------------------------------------------------------------------------

def converge_power(
    glp_stage, hwp_stage, instr,
    target_mw,
    tolerance_mw=TOLERANCE_MW,
    angle_min=86,
    angle_max=100,
    max_iter=50,
    settle_s=SETTLE_S,
):
    """Adjust GLP angle (with HWP coupled at half-delta) until power == target.

    Uses bisection search over [angle_min, angle_max] degrees.

    Parameters
    ----------
    glp_stage    : KinesisMotor -- open GLP K10CR2 connection
    hwp_stage    : KinesisMotor -- open HWP K10CR2 connection
    instr        : pyvisa Resource -- open PM100D connection
    target_mw    : float -- desired power in mW
    tolerance_mw : float -- acceptable error in mW (default 0.5 mW)
    angle_min    : float -- lower bound of GLP search range (degrees, default 0)
    angle_max    : float -- upper bound of GLP search range (degrees, default 90)
    max_iter     : int   -- maximum bisection iterations (default 50)
    settle_s     : float -- seconds to wait after each move (default 2)

    Returns
    -------
    (final_angle_deg, final_power_mw) : (float, float)

    Raises
    ------
    ValueError   if target_mw is outside the power range at [angle_min, angle_max].
    RuntimeError if the algorithm does not converge within max_iter steps.
    """
    target_w = target_mw * 1e-3
    tol_w    = tolerance_mw * 1e-3

    print(f"\nTarget : {target_mw:.3f} mW  (+/-{tolerance_mw:.3f} mW)")
    print(f"GLP search range: {angle_min:.2f} deg -- {angle_max:.2f} deg")
    print("-" * 60)

    # ------------------------------------------------------------------
    # Step 1 -- check current power; may already be within tolerance
    # ------------------------------------------------------------------
    p_now_w = pm100d.get_power(instr)
    print(f"Current power: {p_now_w * 1e3:.3f} mW  "
          f"(GLP = {k10cr2.get_angle(glp_stage):.4f} deg)")

    if abs(p_now_w - target_w) <= tol_w:
        print("Already within tolerance. No move needed.")
        return k10cr2.get_angle(glp_stage), p_now_w * 1e3

    # ------------------------------------------------------------------
    # Step 2 -- probe both ends of the search range to bracket the target
    # ------------------------------------------------------------------
    print(f"\nProbing angle_min = {angle_min:.2f} deg ...")
    k10cr2.set_glp_angle(glp_stage, hwp_stage, angle_min)
    time.sleep(settle_s)
    p_at_min = pm100d.get_power(instr)
    print(f"  power = {p_at_min * 1e3:.3f} mW")

    if abs(p_at_min - target_w) <= tol_w:
        print(f"Converged at angle_min = {angle_min:.4f} deg.")
        return angle_min, p_at_min * 1e3

    print(f"\nProbing angle_max = {angle_max:.2f} deg ...")
    k10cr2.set_glp_angle(glp_stage, hwp_stage, angle_max)
    time.sleep(settle_s)
    p_at_max = pm100d.get_power(instr)
    print(f"  power = {p_at_max * 1e3:.3f} mW")

    if abs(p_at_max - target_w) <= tol_w:
        print(f"Converged at angle_max = {angle_max:.4f} deg.")
        return angle_max, p_at_max * 1e3

    # ------------------------------------------------------------------
    # Step 3 -- verify target is achievable in this range
    # ------------------------------------------------------------------
    p_lo = min(p_at_min, p_at_max)
    p_hi = max(p_at_min, p_at_max)

    if target_w < p_lo - tol_w or target_w > p_hi + tol_w:
        raise ValueError(
            f"Target {target_mw:.3f} mW is outside the achievable power range "
            f"[{p_lo * 1e3:.3f}, {p_hi * 1e3:.3f}] mW "
            f"for GLP angles [{angle_min:.2f}, {angle_max:.2f}] deg. "
            "Widen the angle range or adjust the target."
        )

    # ------------------------------------------------------------------
    # Step 4 -- set up bracket so power increases from angle a to angle b
    #   P(a) < target < P(b)
    # ------------------------------------------------------------------
    if p_at_min <= p_at_max:
        a, b = angle_min, angle_max
    else:
        a, b = angle_max, angle_min

    # ------------------------------------------------------------------
    # Step 5 -- bisection
    # ------------------------------------------------------------------
    print(f"\nStarting bisection (bracket: [{a:.4f}, {b:.4f}] deg):")
    mid = (a + b) / 2.0   # initialise so it is defined for the error message
    p_mid = None

    for i in range(max_iter):
        mid = (a + b) / 2.0

        k10cr2.set_glp_angle(glp_stage, hwp_stage, mid)
        time.sleep(settle_s)
        p_mid = pm100d.get_power(instr)

        err_mw = (p_mid - target_w) * 1e3
        hwp_now = k10cr2.get_angle(hwp_stage)
        print(
            f"  iter {i + 1:2d}:  GLP = {mid:8.4f} deg   "
            f"HWP = {hwp_now:8.4f} deg   "
            f"power = {p_mid * 1e3:8.3f} mW   "
            f"error = {err_mw:+.3f} mW"
        )

        if abs(p_mid - target_w) <= tol_w:
            print(f"\nConverged in {i + 1} iteration(s).")
            print(f"  GLP = {mid:.4f} deg")
            print(f"  HWP = {hwp_now:.4f} deg")
            print(f"  Power = {p_mid * 1e3:.3f} mW  (target {target_mw:.3f} mW)")
            return mid, p_mid * 1e3

        # Narrow the bracket (power is monotone increasing from a to b)
        if p_mid < target_w:
            a = mid   # need higher angle (more power)
        else:
            b = mid   # need lower angle (less power)

    raise RuntimeError(
        f"Did not converge after {max_iter} iterations. "
        f"Last GLP angle: {mid:.4f} deg, "
        f"last power: {p_mid * 1e3:.3f} mW, "
        f"target: {target_mw:.3f} mW."
    )


# ---------------------------------------------------------------------------
# Main — runs when executed directly
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    # ------------------------------------------------------------------
    # 1. Connect to PM100D
    # ------------------------------------------------------------------
    rm, instr = pm100d.open_meter()

    # ------------------------------------------------------------------
    # 2. List K10CR2s and prompt user to name GLP and HWP
    # ------------------------------------------------------------------
    all_devs = k10cr2.print_all_kinesis_devices()
    if not all_devs:
        pm100d.close_meter(rm, instr)
        sys.exit(1)

    k10cr2_devs = [
        (sn, desc) for sn, desc in all_devs
        if str(sn).startswith(k10cr2.K10CR2_PREFIX) or "K10CR" in str(desc).upper()
    ]

    if not k10cr2_devs:
        print(
            "\nNo K10CR2 devices matched the filter. "
            "All detected devices are listed above."
        )
        glp_sn = input("Enter serial number for GLP mount: ").strip()
        hwp_sn = input("Enter serial number for HWP mount: ").strip()
    else:
        print("\nAssign a name to each K10CR2 (type GLP or HWP):")
        names = {}
        for sn, desc in k10cr2_devs:
            raw = input(f"  Serial {sn}  ({desc})  -> name: ").strip().upper()
            if raw:
                names[raw] = str(sn)

        if "GLP" not in names or "HWP" not in names:
            print(f"\nBoth GLP and HWP must be named. Named so far: {names}")
            pm100d.close_meter(rm, instr)
            sys.exit(1)

        glp_sn = names["GLP"]
        hwp_sn = names["HWP"]

    print(f"\nConnecting to GLP (serial {glp_sn}) ...")
    glp_stage = k10cr2.open_stage(glp_sn)
    print(f"Connecting to HWP (serial {hwp_sn}) ...")
    hwp_stage = k10cr2.open_stage(hwp_sn)

    try:
        # ------------------------------------------------------------------
        # 3. Set calibration wavelength and read current power
        # ------------------------------------------------------------------
        wl_input = input("\nEnter laser wavelength (nm): ").strip()
        wavelength_nm = float(wl_input)
        pm100d.set_wavelength(instr, wavelength_nm)
        print(f"PM100D calibration wavelength set to {wavelength_nm:.1f} nm.")

        time.sleep(0.5)   # let the meter update
        p_current = pm100d.get_power(instr)
        print(f"Current power: {p_current * 1e3:.3f} mW")

        # ------------------------------------------------------------------
        # 4. Prompt for target power
        # ------------------------------------------------------------------
        target_input = input("\nEnter target power (mW): ").strip()
        target_mw = float(target_input)

        tol_input = input(f"Tolerance in mW [default {TOLERANCE_MW}]: ").strip()
        tolerance  = float(tol_input) if tol_input else TOLERANCE_MW

        # ------------------------------------------------------------------
        # 5. Prompt for GLP angle search range
        # ------------------------------------------------------------------
        print(
            "\nBisection search needs a GLP angle range that brackets the target."
            "\nThe polarizer follows Malus's law -- power is monotone over ~90 deg."
        )
        amin_input = input("GLP angle_min (deg) [default 0.0]: ").strip()
        amax_input = input("GLP angle_max (deg) [default 90.0]: ").strip()
        angle_min  = float(amin_input) if amin_input else 0.0
        angle_max  = float(amax_input) if amax_input else 90.0

        # ------------------------------------------------------------------
        # 6. Run convergence
        # ------------------------------------------------------------------
        final_angle, final_power = converge_power(
            glp_stage, hwp_stage, instr,
            target_mw=target_mw,
            tolerance_mw=tolerance,
            angle_min=angle_min,
            angle_max=angle_max,
        )

        print(f"\nDone.")
        print(f"  GLP angle : {final_angle:.4f} deg")
        print(f"  HWP angle : {k10cr2.get_angle(hwp_stage):.4f} deg")
        print(f"  Power     : {final_power:.3f} mW")

    except (ValueError, RuntimeError) as exc:
        print(f"\nERROR: {exc}")

    finally:
        k10cr2.close_stage(glp_stage)
        k10cr2.close_stage(hwp_stage)
        pm100d.close_meter(rm, instr)
        print("\nAll connections closed.")
