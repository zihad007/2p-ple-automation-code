# 2P-PLE Automated Sweep

Automated Two-Photon Photoluminescence Excitation (2P-PLE) sweep for a ultrafast laser spectroscopy setup. The script steps the excitation laser through a wavelength range, converges the optical power to a user-defined target at each step, acquires a spectrum with a CCD detector, and logs all parameters to a CSV file.

---

## Hardware

| Device | Role | Interface |
|---|---|---|
| Coherent Chameleon Ultra II | Tunable Ti:Sapphire excitation laser | Serial (COM5, 19200 baud) |
| Thorlabs PM100D | Optical power meter | USB-TMC (PyVISA) |
| Thorlabs K10CR2 + GL10-C (GLP) | Glan-Laser Calcite Polarizer — power attenuator | USB (Kinesis) |
| Thorlabs K10CR2 + half-wave plate (HWP) | Polarization rotation | USB (Kinesis) |
| Princeton Instruments CCD + LightField | Spectrum acquisition | COM / LightField Automation API |

The GLP and HWP rotation mounts act together as a continuously variable power attenuator. When the GLP angle changes by Δ°, the HWP is automatically moved by Δ/2° to maintain the correct polarization state.

---

## Software Requirements

- **Python** — Anaconda environment `lab-controls`
- **pyserial** ≥ 3.5
- **pyvisa** + **pyvisa-py** + **pyusb** (NI-VISA runtime is *not* required)
- **pylablib** ≥ 1.4.4
- **pythonnet** (`clr`) — for LightField .NET API
- **Princeton Instruments LightField** installed at the default path (`C:\Program Files\Princeton Instruments\LightField`)

Install dependencies into the environment:

```bash
conda activate lab-controls
pip install pyserial pyvisa pyvisa-py pyusb pylablib pythonnet
```

---

## File Structure

```
2p-ple-auto/
├── sweep-ple.py          # Main experiment script (run this)
├── pm100d.py             # Thorlabs PM100D power meter driver
├── k10cr2.py             # Thorlabs K10CR2 rotation mount driver
├── power_convergence.py  # Bisection-based power convergence algorithm
└── Other codes/          # Earlier single-purpose scripts (reference)
```

All four files must be in the same directory.

---

## How to Run

Launch from the `lab-controls` Anaconda environment:

```bash
C:\Users\schul\anaconda3\envs\lab-controls\python.exe sweep-ple.py
```

Or open Spyder from Anaconda Navigator (it uses `lab-controls` automatically) and run `sweep-ple.py` from there.

---

## What the Script Does — Step by Step

### Startup
1. **LightField** is started in the background (no GUI window).
2. An output folder named `<YYYYMMDD>_Zihad` is created in the script directory.
3. The CCD **sensor temperature** is checked — the script warns and asks for confirmation if it has not locked at −70 °C.
4. The user selects a **saved LightField experiment** from a numbered list. The grating moves into position (10 s wait, done once only).
5. The **save directory** and filename settings are applied to LightField *after* the experiment is loaded (loading resets these settings).
6. The user enters the **integration time** in seconds (kept for all wavelengths).

### Pre-sweep setup
7. The **PM100D** power meter is connected.
8. All connected K10CR2 devices are listed by serial number. The user assigns each one the name **GLP** or **HWP**.
9. Current angles of both mounts are displayed. The user optionally sets new starting angles.
10. The user enters the **wavelength range** (start, stop, step — defaults: 1064 → 1056 nm in −4 nm steps), **target power** (mW), power **tolerance** (mW), and GLP **angle search range**.
11. The user chooses whether to enable **alignment mode** and, if so, how many wavelength iterations to wait between alignment checks.

### Sweep loop (repeated for each wavelength)
12. *(If alignment mode is on and the interval has elapsed)* The laser enters alignment mode, the shutter opens, and the script pauses with the prompt: *"Put on ND filter on the laser path before checking focus. Take out ND filter before clicking enter."* The sweep resumes after the user presses Enter.
13. The laser is commanded to the target wavelength. The script polls until the wavelength is confirmed (±0.5 nm) within 20 s. If no movement is detected after 5 s, the command is resent automatically.
14. The PM100D calibration wavelength is updated. The laser stabilizes for 5 s.
15. The shutter opens. Power stabilizes for 4 s, then the power is read.
16. If the power is outside the tolerance, **bisection convergence** adjusts the GLP angle (with HWP tracking at half the delta) until the target power is reached.
17. A LightField frame is acquired and saved as `PLE<wavelength>` (e.g., `PLE1064`) in the dated output folder.
18. Wavelength, power (mW), integration time (s), GLP angle (°), HWP angle (°), and the saved file path are written to the in-memory log.
19. The shutter closes. The loop moves to the next wavelength.

### Shutdown
20. The shutter is closed and alignment mode is turned off (failsafe).
21. The user is prompted to return GLP → 86.0° and HWP → 6.0°. If there is no response within 5 s, the reset happens automatically.
22. All hardware connections (stages, power meter, LightField) are closed.
23. The laser returns to 700 nm, waits 10 s, then enters **Standby** (`L=0`).
24. The sweep log is printed to the console and saved as `sweep_ple_<timestamp>.csv` in the output folder.

---

## Output

Each run produces a folder `<YYYYMMDD>_Zihad/` containing:

- **`PLE<wavelength>.spe`** — LightField spectrum file for each wavelength point
- **`sweep_ple_<timestamp>.csv`** — log table with columns:

| Column | Description |
|---|---|
| `wavelength_nm` | Excitation wavelength (nm) |
| `power_mw` | Optical power at sample (mW) |
| `integration_time_s` | CCD integration time (s) |
| `glp_deg` | Final GLP angle (°) |
| `hwp_deg` | Final HWP angle (°) |
| `lf_file` | Path to the saved LightField file |

---

## Key Parameters (editable at the top of each file)

| Parameter | File | Default | Description |
|---|---|---|---|
| `PORT` | `sweep-ple.py` | `COM5` | Laser serial port |
| `BAUD` | `sweep-ple.py` | `19200` | Serial baud rate |
| `TARGET_TEMP_C` | `sweep-ple.py` | `-70.0` | Expected CCD temperature (°C) |
| `TOLERANCE_MW` | `power_convergence.py` | `0.1` | Power convergence tolerance (mW) |
| `SETTLE_S` | `power_convergence.py` | `2.0` | Settle time after each angle move (s) |

---

## GitHub Recommendations

A few suggestions before publishing:

1. **Add a `.gitignore`** — exclude output data, Python cache, and IDE files:
   ```
   __pycache__/
   *.pyc
   *.spe
   *.csv
   *_Zihad/
   .spyproject/
   ```

2. **Add an `environment.yml`** — makes it easy for others to recreate your conda environment:
   ```bash
   conda env export --name lab-controls > environment.yml
   ```

3. **Add a LICENSE** — MIT or BSD-3-Clause are standard choices for academic lab code. GitHub can generate one for you when creating the repo.

4. **Consider a schematic or photo** — a diagram of the optical path (laser → HWP → GLP → sample → spectrometer) would help readers understand the setup immediately.

5. **Archive `Other codes/`** — these earlier scripts are useful for understanding the development history and for isolated testing of individual components (laser, power meter, rotation mount). Consider keeping them in a subdirectory or a separate `dev` branch.

6. **Tag releases** — when the script is in a state that matches a publication or a set of experimental results, create a git tag (e.g., `v1.0`) so that version is permanently citable.
