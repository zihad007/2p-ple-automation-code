# 2P-PLE Automated Sweep

Automated Two-Photon Photoluminescence Excitation (2P-PLE) sweep for an ultrafast laser spectroscopy setup. The script steps a tunable Ti:Sapphire laser through a user-defined wavelength range, converges the optical power to a target value at each step using a motorized polarizer attenuator, acquires a spectrum with a CCD detector, and logs all experimental parameters to a CSV file.

---

## Hardware

| Device | Role | Interface |
|---|---|---|
| Coherent Chameleon Ultra II | Tunable Ti:Sapphire excitation laser | Serial (COM6, 19200 baud) |
| Thorlabs PM100D | Optical power meter | USB-TMC (PyVISA, no NI-VISA required) |
| Thorlabs K10CR2 + GL10-C polarizer (**GLP**) | Glan-Laser Calcite Polarizer — variable power attenuator | USB (Thorlabs Kinesis) |
| Thorlabs K10CR2 + half-wave plate (**HWP**) | Half-wave plate — maintains polarization state | USB (Thorlabs Kinesis) |
| Thorlabs K10CR2 + output polarizer (**POL**) | Output polarizer — selects p-pol or s-pol | USB (Thorlabs Kinesis) |
| Thorlabs SC30 + SHBH1(T) shutter (**beam shutter**) | Mechanical beam shutter — gates light to sample | Serial (COM7, 115200 baud) |
| Princeton Instruments CCD + LightField | Spectrum acquisition | LightField .NET Automation API |

### Power attenuation scheme

The GLP and HWP rotation mounts work together as a continuously variable attenuator. The GLP (Glan-Laser Calcite Polarizer) transmits the component of light aligned with its optical axis; rotating it changes the fraction of the beam that passes through (following Malus's law). The HWP ensures the polarization state remains correct after the GLP: whenever the GLP moves by Δ°, the HWP is automatically moved by Δ/2°. This coupling is implemented in `k10cr2.py` and used throughout `power_convergence.py` and `sweep-ple.py`.

### Beam shutter

The SC30 (Thorlabs) drives a high-speed mechanical shutter (SHBH1(T)) on channel 1. It is kept **closed** during laser wavelength changes and power convergence, and is opened only during LightField spectrum acquisition. This prevents the sample from being exposed to unconverged or unstabilized power.

---

## File Structure

```
2p-ple-auto/
├── sweep-ple.py          # Main experiment script — run this
├── pm100d.py             # Thorlabs PM100D power meter driver
├── k10cr2.py             # Thorlabs K10CR2 rotation mount driver
├── power_convergence.py  # Bisection-based power convergence algorithm
├── sc30.py               # Thorlabs SC30 beam shutter driver
└── Other codes/          # Earlier single-purpose scripts kept for reference
```

All five files must be in the same directory when running `sweep-ple.py`.

---

## Module Descriptions

### `pm100d.py`
Driver for the Thorlabs PM100D optical power meter over USB using PyVISA (pyvisa-py backend; NI-VISA is not required). On connection it sets auto-range, configures the calibration wavelength, sets units to Watts, and applies 500-point averaging. Exposes `open_meter()`, `close_meter()`, `set_wavelength()`, `get_power()`, and `read_power_array()`. The calibration wavelength is updated at each sweep step so the meter uses the correct photodiode responsivity curve.

### `k10cr2.py`
Driver for the Thorlabs K10CR2 stepper motor rotation mount using the pylablib Kinesis backend. Handles device discovery, connection, homing, angle read-back, and absolute positioning. The K10CR2 is not in pylablib's built-in unit table, so angle conversion uses the directly derived constant `COUNTS_PER_DEG = 409600 / 3` (≈ 136533 counts per degree). The module also implements `set_glp_angle()`, which moves the GLP to a new angle and automatically shifts the HWP by half the delta in a single coupled call.

### `power_convergence.py`
Implements bisection search to converge the optical power (measured by the PM100D) to a user-specified target by adjusting the GLP angle. The algorithm first probes both ends of the search range to bracket the target power, then repeatedly bisects the angle interval, measuring power at each midpoint, until the reading is within the specified tolerance. Bisection is used rather than a fixed-step walk because Malus's law guarantees the power-vs-angle curve is monotone over any 90° window, making bisection both faster (convergent in ~10 iterations for a 14° range) and more robust. Default tolerance is 0.1 mW; default settle time after each move is 2 s.

### `sc30.py`
Driver for the Thorlabs SC30 optical beam shutter controller over a USB virtual COM port (COM7). Communicates using the SC30's serial command set: `swtrig1=1` opens the shutter and `swtrig1=0` closes it. The SC30 echoes every command before the response; the driver strips the echo and prompt characters to parse the value. Exposes `open_controller()`, `close_controller()`, `open_shutter()`, `close_shutter()`, and `get_status()`.

### `sweep-ple.py`
Main experiment script. Integrates all four modules above together with the Coherent Chameleon Ultra II laser (serial) and Princeton Instruments LightField (headless .NET Automation API) to run a full 2P-PLE sweep. See the step-by-step description below.

---

## Software Requirements

- **Python** — Anaconda environment `lab-controls`
- **pyserial** ≥ 3.5
- **pyvisa** + **pyvisa-py** + **pyusb** (NI-VISA runtime is *not* required)
- **pylablib** ≥ 1.4.4
- **pythonnet** (`clr`) — for the LightField .NET API
- **Princeton Instruments LightField** installed at the default path:
  `C:\Program Files\Princeton Instruments\LightField`

```bash
conda activate lab-controls
pip install pyserial pyvisa pyvisa-py pyusb pylablib pythonnet
```

---

## How to Run

From the Anaconda `lab-controls` environment:

```bash
C:\Users\schul\anaconda3\envs\lab-controls\python.exe sweep-ple.py
```

Or open **Spyder** from Anaconda Navigator (it picks up `lab-controls` automatically) and run `sweep-ple.py` from there.

---

## What the Script Does — Step by Step

### 1. Pre-run checklist

The script opens with a printed checklist and waits for the user to press Enter before touching any hardware:

- 900 nm short-pass dichroic beamsplitter is in position
- 650 nm short-pass filter is in the collection path
- CCD sensor temperature has reached −70 °C in LightField
- Spectrometer slit is centered on the back focal plane image
- Blazed grating is engaged

### 2. Hardware initialization

1. **PM100D pre-check** — opens and reads the power meter to confirm it is connected and responding. Exits with an error message if it is not.
2. **SC30 beam shutter** — connected and immediately closed. The beam shutter remains closed for the rest of startup and throughout wavelength changes and convergence; it only opens during spectrum acquisition.
3. **LightField** — started in background mode (no GUI window) using the .NET Automation API.
4. **Output folder** — a folder named `<YYYYMMDD>_Zihad` is created in the script directory. All spectra and the log CSV are saved here.
5. **CCD sensor temperature** — read from LightField. If the sensor has not locked at −70 °C (within ±2 °C), a warning is printed and the user is asked whether to continue.
6. **LightField experiment** — the user selects a saved experiment from a numbered list. After loading, the grating moves into position (10 s wait, done once). The save directory and base filename are applied to LightField *after* loading, because loading an experiment resets all file settings to what was saved inside it.
7. **Integration time** — the user enters the CCD exposure time in seconds. This value is set in LightField and kept for all wavelengths.
8. **PM100D** — reopened for the sweep.
9. **K10CR2 rotation mounts** — all connected Kinesis devices are listed. The user assigns each K10CR2 one of three names: **GLP**, **HWP**, or **POL**. All three stages are then homed (returned to hardware zero) one by one.
10. **Output polarization** — the user selects p-pol or s-pol. The POL stage moves to 112° for p-pol or 22° for s-pol and stays there for the entire sweep.
11. **Starting angles** — current GLP and HWP angles are shown. The user can optionally override them before the sweep begins.

### 3. Sweep parameters

The user enters:

| Parameter | Default |
|---|---|
| Start wavelength (nm) | 1064 |
| Stop wavelength (nm, inclusive) | 1056 |
| Step size (nm, negative = sweep down) | −4 |
| Target power (mW) | — |
| Power tolerance (mW) | 0.1 |
| GLP angle_min (°) | 86.0 |
| GLP angle_max (°) | 100.0 |
| Enable alignment mode? | No |

### 4. Sweep loop (repeated for each wavelength)

**a. Optional alignment check**

If alignment mode is enabled and the configured interval has elapsed (not before the first wavelength), the script pauses for a focus check:

1. Alignment mode is turned on (`ALIGN=1`) — the Chameleon reduces its output power to a safe level.
2. The SC30 mechanical beam shutter is closed.
3. The laser shutter is opened (`S=1`) so the reduced-power beam exits the laser head.
4. The console prints: *"Put on ND filter on the laser path before checking focus. Take out ND filter before clicking enter."* The script waits for the user to press Enter.
5. After Enter: the SC30 mechanical beam shutter is closed again (ensuring no exposure during the transition), alignment mode is turned off (`ALIGN=0`), and the script waits 5 s before continuing.

**b. Wavelength change**

The laser is commanded to the target wavelength (`VW= <nm>`). The script polls the laser every second for up to 20 s until the reported wavelength is within ±0.5 nm of the target. If no movement is detected after 5 s, the set command is resent automatically (guards against the first serial command after port open being silently dropped by the Chameleon).

**c. Power convergence**

1. The PM100D calibration wavelength is updated to the current excitation wavelength.
2. The laser stabilizes for 5 s.
3. The laser shutter is opened (`S=1`).
4. Power is read after a 4 s stabilization wait.
5. If the reading is outside the tolerance, `converge_power()` is called:
   - The GLP angle is swept to `angle_min`, then to `angle_max`, to bracket the target power.
   - Bisection then narrows the GLP angle until the measured power is within tolerance. The HWP tracks at half the GLP delta on every move.
6. The final GLP and HWP angles and the converged power reading are printed and added to the log.

**d. Spectrum acquisition**

1. The SC30 mechanical beam shutter is opened.
2. LightField acquires one frame with the filename `PLE<wavelength>` (e.g., `PLE1064`) saved to the dated output folder.
3. The script waits for LightField to signal completion.
4. The SC30 mechanical beam shutter is closed.
5. The laser shutter is closed (`S=0`).

### 5. Shutdown

After the last wavelength (or on Ctrl+C / serial error):

1. **Failsafe cleanup** — SC30 closed, SC30 controller disconnected, laser shutter closed, alignment mode turned off.
2. **Stage reset** — the user is asked whether to return GLP → 86.0° and HWP → 6.0°.
3. **All connections closed** — K10CR2 stages, PM100D, LightField (`auto.Dispose()`).
4. **Laser standby** — the user is asked whether to return the laser to 700 nm and activate standby mode (`L=0`). If yes: the wavelength is set to 700 nm, the script waits 10 s for the laser to stabilize, then sends the standby command.
5. **Log saved** — the sweep table is printed to the console and saved as `sweep_ple_<timestamp>.csv` in the output folder.

---

## Output

Each run produces a folder `<YYYYMMDD>_Zihad/` containing:

- **`PLE<wavelength>.spe`** — LightField spectrum file for each wavelength point
- **`sweep_ple_<timestamp>.csv`** — log table with columns:

| Column | Description |
|---|---|
| `wavelength_nm` | Excitation wavelength (nm) |
| `power_mw` | Optical power at acquisition (mW) |
| `integration_time_s` | CCD integration time (s) |
| `glp_deg` | Final GLP angle (°) |
| `hwp_deg` | Final HWP angle (°) |
| `lf_file` | Path to the saved LightField spectrum file |

---

## Key Parameters

| Parameter | File | Default | Description |
|---|---|---|---|
| `PORT` | `sweep-ple.py` | `COM6` | Laser serial port |
| `BAUD` | `sweep-ple.py` | `19200` | Serial baud rate |
| `TARGET_TEMP_C` | `sweep-ple.py` | `−70.0` | Expected CCD temperature (°C) |
| `COM_PORT` | `sc30.py` | `COM7` | SC30 beam shutter serial port |
| `TOLERANCE_MW` | `power_convergence.py` | `0.1` | Power convergence tolerance (mW) |
| `SETTLE_S` | `power_convergence.py` | `2.0` | Settle time after each GLP move (s) |
| `VISA_ADDRESS` | `pm100d.py` | `USB0::0x1313::0x8078::P0007396::INSTR` | PM100D VISA address |
