#!/usr/bin/env python3
"""Generate the ZFR BSPD (Option A) KiCad 7 schematic + BOM from a single netlist definition.
Net-label style: every pin gets a short stub and a label / power symbol, so connectivity
is exactly what is listed in PARTS below (verified afterwards against kicad-cli netlist export)."""
import re, uuid, math, os, csv, json

LIB = "/usr/share/kicad/symbols"
OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "hardware", "kicad")
os.makedirs(OUT, exist_ok=True)
G = 1.27
def snap(v): return round(round(v / G) * G, 4)
def uid(): return str(uuid.uuid4())

# ----------------------------------------------------------------------------------------------
# Library handling (extract + flatten 'extends')
_cache = {}
def _blocks(lib):
    if lib in _cache: return _cache[lib]
    s = open(f"{LIB}/{lib}.kicad_sym").read()
    out = {}; i = 0
    # top-level symbols are at depth 1 inside (kicad_symbol_lib ...)
    depth = 0; start = None; name = None
    while i < len(s):
        c = s[i]
        if c == '"':
            j = s.index('"', i + 1)
            while s[j - 1] == '\\': j = s.index('"', j + 1)
            i = j + 1; continue
        if c == '(':
            depth += 1
            if depth == 2 and s.startswith('(symbol "', i):
                start = i; name = s[i + 9:s.index('"', i + 9)]
        elif c == ')':
            if depth == 2 and start is not None:
                out[name] = s[start:i + 1]; start = None
            depth -= 1
        i += 1
    _cache[lib] = out; return out

def libsym(lib, name):
    b = _blocks(lib)[name]
    m = re.search(r'\(extends "([^"]+)"\)', b)
    if not m: return b
    parent = libsym(lib, m.group(1))
    pname = m.group(1)
    # parent body with name replaced; child properties replace parent properties
    body = parent.replace(f'(symbol "{pname}"', f'(symbol "{name}"', 1)
    body = re.sub(rf'\(symbol "{re.escape(pname)}_(\d+)_(\d+)"', rf'(symbol "{name}_\1_\2"', body)
    cprops = re.findall(r'(\(property "([^"]+)".*?\n\s*\)\n)', b, re.S)
    for full, key in cprops:
        body = re.sub(r'\(property "%s".*?\n\s*\)\n' % re.escape(key), lambda _: full, body, count=1, flags=re.S)
    return body

def pins_of(lib, name):
    b = libsym(lib, name)
    pins = []
    for sub in re.finditer(r'\(symbol "%s_(\d+)_(\d+)"' % re.escape(name), b):
        unit = int(sub.group(1))
        # find extent of this sub block
        i = sub.start(); d = 0
        for j in range(i, len(b)):
            if b[j] == '(': d += 1
            elif b[j] == ')':
                d -= 1
                if d == 0: break
        seg = b[i:j + 1]
        for p in re.finditer(r'\(pin (\w+) \w+(?: hide)? \(at ([-\d.]+) ([-\d.]+) (\d+)\).*?\(name "([^"]*)".*?\(number "([^"]*)"', seg, re.S):
            pins.append(dict(type=p.group(1), x=float(p.group(2)), y=float(p.group(3)), ang=int(p.group(4)),
                             name=p.group(5), num=p.group(6), unit=unit))
    return pins

# ----------------------------------------------------------------------------------------------
# Design definition
FP = dict(
    R0603="Resistor_SMD:R_0603_1608Metric", R1206="Resistor_SMD:R_1206_3216Metric", R2512="Resistor_SMD:R_2512_6332Metric",
    C0603="Capacitor_SMD:C_0603_1608Metric", C0805="Capacitor_SMD:C_0805_2012Metric", C1206="Capacitor_SMD:C_1206_3216Metric",
    CPE="Capacitor_SMD:CP_Elec_6.3x7.7", TANB="Capacitor_Tantalum_SMD:CP_EIA-3528-21_Kemet-B",
    SMA="Diode_SMD:D_SMA", SMB="Diode_SMD:D_SMB", SOD123="Diode_SMD:D_SOD-123", SOT23="Package_TO_SOT_SMD:SOT-23",
    SOT223="Package_TO_SOT_SMD:SOT-223-3_TabPin2", SOIC14="Package_SO:SOIC-14_3.9x8.7mm_P1.27mm",
    LED0805="LED_SMD:LED_0805_2012Metric", F1812="Fuse:Fuse_1812_4532Metric",
    TRIM="Potentiometer_THT:Potentiometer_Bourns_3296W_Vertical", RELAY="Relay_THT:Relay_SPDT_Omron-G5LE-1",
    MF2="Connector_Molex:Molex_Micro-Fit_3.0_43650-0215_1x02_P3.00mm_Vertical",
    MF3="Connector_Molex:Molex_Micro-Fit_3.0_43650-0315_1x03_P3.00mm_Vertical",
    TP="TestPoint:TestPoint_Pad_D1.5mm",
)

POWER = {"GND": "GND", "+5V": "+5V", "+12V": "+12V"}

# (ref, lib:sym, value, footprint, {pin: net}, block, mpn, alt1, alt2, description, unit)
P = []
def add(ref, sym, val, fp, pins, blk, mpn="", a1="", a2="", desc="", unit=1):
    fp = FP.get(fp, fp)
    if fp: fp = "bspd:" + fp.split(":")[-1]
    if sym.startswith("Comparator") and unit != 5:
        lib, nm = sym.split(":")
        units = {q["unit"] for q in pins_of(lib, nm) if q["num"] in pins}
        assert len(units) == 1, (ref, pins, units); unit = units.pop()
    P.append(dict(ref=ref, sym=sym, val=val, fp=fp, pins=pins, blk=blk, mpn=mpn, a1=a1, a2=a2, desc=desc, unit=unit))

R = "Device:R"; C = "Device:C"; CP = "Device:C_Polarized"
RCODE = {"1k": ("1KL", "1001"), "9.1k": ("9K1L", "9101"), "100k": ("100KL", "1003"), "10k": ("10KL", "1002"),
         "5.1k": ("5K1L", "5101"), "15k": ("15KL", "1502"), "3.3k": ("3K3L", "3301"), "470k": ("470KL", "4703"),
         "1M": ("1ML", "1004"), "100R": ("100RL", "1000"), "2.2k": ("2K2L", "2201"), "62k": ("62KL", "6202"), "11k": ("11KL", "1102")}
def res(ref, val, n1, n2, blk, fp="R0603", desc="", mpn="", a1="", a2=""):
    if not mpn and val in RCODE:
        y, u = RCODE[val]
        mpn, a1, a2 = f"Yageo RC0603FR-07{y}", f"UNI-ROYAL 0603WAF{u}T5E", f"Panasonic ERJ-3EKF{u}V"
    add(ref, R, val, fp, {"1": n1, "2": n2}, blk, mpn, a1, a2, desc)
def cap(ref, val, n1, n2, blk, fp="C0603", desc="", mpn="", a1="", a2=""):
    if not mpn and val.startswith("100nF"):
        val, mpn, a1 = "100nF 50V", "Samsung CL10B104KB8NNNC", "Murata GRM188R71H104KA93D"
    add(ref, C, val, fp, {"1": n1, "2": n2}, blk, mpn, a1, a2, desc)

# ---- POWER INPUT ----
B = "PWR"
add("J1", "Connector_Generic:Conn_01x02", "GLV_12V_IN", "MF2", {"1": "VIN_RAW", "2": "GND"}, B,
    "Molex 43650-0215", "Molex 43650-0221", "Any Micro-Fit 3.0 compatible vertical 2-pin", "GLV 12 V in (after GLVMS, NOT via SDC)")
add("F1", "Device:Polyfuse", "0.5A", "F1812", {"1": "VIN_RAW", "2": "VIN_F"}, B,
    "Bourns MF-MSMF050-2", "Littelfuse 1812L050PR", "", "Resettable fuse 0.5 A hold")
add("D1", "Device:D_Schottky", "SS34", "SMA", {"1": "+12V", "2": "VIN_F"}, B,
    "SS34 (onsemi/MCC)", "B340A-13-F", "SS36", "Reverse-polarity protection, 3 A 40 V")
add("D2", "Device:D_Zener", "SMBJ18A", "SMB", {"1": "+12V", "2": "GND"}, B,
    "Littelfuse SMBJ18A", "Vishay SMBJ18A-E3/52", "Littelfuse P6SMB22A", "Unidirectional TVS 18 V stand-off (load-dump clamp)")
add("C1", CP, "47uF 35V", "CPE", {"1": "+12V", "2": "GND"}, B, "Panasonic EEE-FK1V470P", "Nichicon UWT1V470MCL1GS", "", "Bulk, aluminium electrolytic")
cap("C2", "100nF 50V", "+12V", "GND", B, mpn="Samsung CL10B104KB8NNNC", a1="Murata GRM188R71H104KA93D")
add("U3", "Regulator_Linear:SPX2920M3-5.0_SOT223", "LM2940IMP-5.0", "SOT223", {"1": "+12V", "2": "GND", "3": "+5V"}, B,
    "TI LM2940IMP-5.0/NOPB", "TI LM2937IMP-5.0/NOPB", "", "5 V automotive LDO (reverse-battery & load-dump rated)")
add("C5", CP, "22uF 16V Tant", "TANB", {"1": "+5V", "2": "GND"}, B, "KEMET T491B226K016AT", "AVX TAJB226K016RNJ", "",
    "LDO output cap: tantalum (ESR 0.1-1 ohm needed for LM2940 stability, NOT ceramic)")
cap("C6", "100nF 50V", "+5V", "GND", B, mpn="Samsung CL10B104KB8NNNC", a1="Murata GRM188R71H104KA93D")
cap("C3", "100nF 50V", "+12V", "GND", B, desc="U1 decoupling (place at pin 3)", mpn="Samsung CL10B104KB8NNNC", a1="Murata GRM188R71H104KA93D")
cap("C4", "100nF 50V", "+12V", "GND", B, desc="U2 decoupling (place at pin 3)", mpn="Samsung CL10B104KB8NNNC", a1="Murata GRM188R71H104KA93D")
add("U1", "Comparator:LM2901", "LM2901", "SOIC14", {"3": "+12V", "12": "GND"}, B, "TI LM2901DR", "onsemi LM2901DR2G", "TI LM339ADR", "Quad comparator, automotive", unit=5)
add("U2", "Comparator:LM2901", "LM2901", "SOIC14", {"3": "+12V", "12": "GND"}, B, "TI LM2901DR", "onsemi LM2901DR2G", "TI LM339ADR", "Quad comparator, automotive", unit=5)

# ---- REFERENCES & THRESHOLDS ----
B = "REF"
res("R1", "1k", "+5V", "VREF_4V75", B, desc="Ref ladder (1 %)")
res("R2", "9.1k", "VREF_4V75", "VREF_2V5", B)
res("R3", "9.1k", "VREF_2V5", "VREF_0V25", B)
res("R4", "1k", "VREF_0V25", "GND", B)
cap("C7", "100nF", "VREF_4V75", "GND", B); cap("C8", "100nF", "VREF_2V5", "GND", B); cap("C9", "100nF", "VREF_0V25", "GND", B)
res("R5", "100k", "VREF_2V5", "VREF_LATCH", B, desc="Latch-reference lag (power-up immunity)")
cap("C10", "1uF 25V", "VREF_LATCH", "GND", B, fp="C0805", mpn="Samsung CL21B105KAFNNNE", a1="Murata GRM21BR71E105KA99L")
res("R6", "62k", "+5V", "RV1_T", B, desc="Limits RV1 max to 1.27 V = 26.4 bar nom., 28.4 bar worst case (T 11.6.5 <=30 bar)")
add("RV1", "Device:R_Potentiometer_Trim", "10k 25T", "TRIM", {"1": "RV1_T", "2": "BRK_TH", "3": "RV1_B"}, B,
    "Bourns 3296W-1-103LF", "Vishay T93YA103KT20", "Suntan TSR-3296W-103", "Hard-brake threshold trim 0.66-1.27 V = 5.6-26.4 bar")
res("R7", "11k", "RV1_B", "GND", B)
cap("C11", "100nF", "BRK_TH", "GND", B)
res("R8", "15k", "+5V", "RV2_T", B)
add("RV2", "Device:R_Potentiometer_Trim", "10k 25T", "TRIM", {"1": "RV2_T", "2": "TPS_TH", "3": "RV2_B"}, B,
    "Bourns 3296W-1-103LF", "Vishay T93YA103KT20", "Suntan TSR-3296W-103", "Throttle-body 20 % threshold trim 0.58-2.35 V")
res("R9", "3.3k", "RV2_B", "GND", B)
cap("C12", "100nF", "TPS_TH", "GND", B)

# ---- SENSOR INPUTS ----
B = "SENS"
add("J2", "Connector_Generic:Conn_01x02", "BRK_SUPPLY", "MF2", {"1": "BRK_5V", "2": "GND"}, B,
    "Molex 43650-0215", "Molex 43650-0221", "", "MLH02KPSB06A supply: 1=+5V 2=GND")
add("J6", "Connector_Generic:Conn_01x02", "BRK_SIGNAL", "MF2", {"1": "BRK_SIG_IN", "2": "GND"}, B,
    "Molex 43650-0215", "Molex 43650-0221", "", "MLH02KPSB06A output (separately disconnectable, T 11.6.7): 1=signal 2=shield")
res("R10", "47R 1W", "+5V", "BRK_5V", B, fp="R2512", desc="Sensor-supply short protection", mpn="Yageo RC2512FK-0747RL", a1="Vishay CRCW251247R0FKEG")
cap("C13", "100nF", "BRK_5V", "GND", B)
res("R11", "100k", "BRK_SIG_IN", "GND", B, desc="Pull-down: open wire -> 0 V -> fault")
res("R12", "10k", "BRK_SIG_IN", "BRK", B)
cap("C14", "100nF", "BRK", "GND", B)
add("D3", "Diode:BAT54S", "BAT54S", "SOT23", {"1": "GND", "2": "+5V", "3": "BRK"}, B, "Nexperia BAT54S,215", "onsemi BAT54SLT1G", "Diodes BAT54S-7-F", "Input clamp")
add("J3", "Connector_Generic:Conn_01x02", "TPS_SUPPLY", "MF2", {"1": "TPS_5V", "2": "TPS_RTN"}, B,
    "Molex 43650-0215", "Molex 43650-0221", "", "Throttle-body sensor 3382H-1-103 track: 1=CW end 2=CCW end")
add("J7", "Connector_Generic:Conn_01x02", "TPS_SIGNAL", "MF2", {"1": "TPS_SIG_IN", "2": "GND"}, B,
    "Molex 43650-0215", "Molex 43650-0221", "", "3382H wiper (separately disconnectable, T 11.6.7): 1=wiper 2=shield")
res("R13", "1k", "+5V", "TPS_5V", B, desc="Pot top series (current limit + rail margin)")
cap("C15", "100nF", "TPS_5V", "GND", B)
res("R14", "1k", "TPS_RTN", "GND", B, desc="Pot bottom series")
res("R15", "470k", "+5V", "TPS_SIG_IN", B, desc="Pull-UP: open wiper/GND -> 5 V -> fault")
res("R16", "10k", "TPS_SIG_IN", "TPS", B)
cap("C16", "10nF", "TPS", "GND", B, desc="10 nF (not 100 nF) so open-wiper fault trips < 100 ms", mpn="Samsung CL10B103KB8NNNC", a1="Murata GRM188R71H103KA01D")
add("D4", "Diode:BAT54S", "BAT54S", "SOT23", {"1": "GND", "2": "+5V", "3": "TPS"}, B, "Nexperia BAT54S,215", "onsemi BAT54SLT1G", "Diodes BAT54S-7-F", "Input clamp")

# ---- PLAUSIBILITY + 500 ms TIMER ----
B = "LOGIC"
add("U1", "Comparator:LM2901", "LM2901", "SOIC14", {"5": "BRK", "4": "BRK_TH", "2": "U1A_OUT"}, B, unit=1, desc="Hard brake detect")
add("U1", "Comparator:LM2901", "LM2901", "SOIC14", {"7": "TPS", "6": "TPS_TH", "1": "U1B_OUT"}, B, unit=2, desc="Throttle > 20 % over idle detect")
res("R17", "1k", "U1A_OUT", "CT", B, desc="CT discharge")
res("R18", "1k", "U1B_OUT", "CT", B, desc="CT discharge")
res("R19", "470k", "+5V", "CT", B, desc="Timer: 0.693*R19*C17 = 326 ms")
cap("C17", "1uF 50V X7R", "CT", "GND", B, fp="C1206", mpn="Murata GRM31MR71H105KA88L", a1="Samsung CL31B105KBHNNNE", a2="TDK C3216X7R1H105K160AB",
    desc="Timing cap (50 V X7R 1206 for low DC-bias loss)")
add("U1", "Comparator:LM2901", "LM2901", "SOIC14", {"9": "VREF_2V5", "8": "CT", "14": "TMR_N"}, B, unit=3, desc="500 ms timer")
res("R20", "1k", "TMR_N", "LATCH", B)

# ---- FAULT WINDOWS ----
B = "FAULT"
add("U2", "Comparator:LM2901", "LM2901", "SOIC14", {"5": "BRK", "4": "VREF_0V25", "2": "FAULT_N"}, B, unit=1, desc="Brake < 0.25 V")
add("U2", "Comparator:LM2901", "LM2901", "SOIC14", {"7": "VREF_4V75", "6": "BRK", "1": "FAULT_N"}, B, unit=2, desc="Brake > 4.75 V")
add("U2", "Comparator:LM2901", "LM2901", "SOIC14", {"9": "TPS", "8": "VREF_0V25", "14": "FAULT_N"}, B, unit=3, desc="Throttle sensor < 0.25 V")
add("U2", "Comparator:LM2901", "LM2901", "SOIC14", {"11": "VREF_4V75", "10": "TPS", "13": "FAULT_N"}, B, unit=4, desc="Throttle sensor > 4.75 V")

# ---- LATCH + OUTPUT ----
B = "OUT"
add("U1", "Comparator:LM2901", "LM2901", "SOIC14", {"11": "LATCH", "10": "VREF_LATCH", "13": "OK"}, B, unit=4, desc="Latch")
res("R21", "1M", "+5V", "LATCH", B)
cap("C18", "1uF 25V", "+5V", "LATCH", B, fp="C0805", desc="To +5V (not GND): latch powers up in OK state", mpn="Samsung CL21B105KAFNNNE", a1="Murata GRM21BR71E105KA99L")
res("R22", "100k", "FAULT_N", "LATCH", B, desc="Fault filter ~70 ms")
add("D5", "Device:D", "1N4148W", "SOD123", {"1": "OK", "2": "LATCH"}, B, "Diodes Inc 1N4148W-7-F", "onsemi MMSD4148T1G", "Nexperia 1N4148W,115", "Latch hold diode")
res("R23", "10k", "+5V", "OK", B)
res("R24", "100R", "OK", "GATE", B)
res("R25", "100k", "GATE", "GND", B)
add("Q1", "Device:Q_NMOS_GSD", "AO3400A", "SOT23", {"1": "GATE", "2": "GND", "3": "COIL_N"}, B, "AOS AO3400A", "Vishay Si2302CDS", "Infineon IRLML6344", "Relay driver")
add("K1", "Relay:G5LE-1", "G5LE-1 DC12", "RELAY", {"5": "+12V", "2": "COIL_N", "1": "SDC_A", "3": "SDC_B"}, B,
    "Omron G5LE-1 DC12", "Omron G5LE-14 DC12 (sealed)", "Omron G5LE-1-E DC12", "SDC relay; uses COM(1)+NO(3) - closed only when healthy")
add("D6", "Device:D", "1N4148W", "SOD123", {"1": "+12V", "2": "COIL_N"}, B, "Diodes Inc 1N4148W-7-F", "onsemi MMSD4148T1G", "Nexperia 1N4148W,115", "Coil flyback")
res("R26", "2.2k", "+12V", "LED_G", B)
add("D7", "Device:LED", "GREEN BSPD OK", "LED0805", {"2": "LED_G", "1": "COIL_N"}, B, "Kingbright APT2012SGC", "Lite-On LTST-C171GKT", "", "On = relay closed")
res("R27", "1k", "+5V", "LED_R", B)
add("D8", "Device:LED", "RED TRIPPED", "LED0805", {"2": "LED_R", "1": "OK"}, B, "Kingbright APT2012SURCK", "Lite-On LTST-C171KRKT", "", "On = latched trip")
add("J4", "Connector_Generic:Conn_01x02", "SDC", "MF2", {"1": "SDC_A", "2": "SDC_B"}, B,
    "Molex 43650-0215", "Molex 43650-0221", "", "In series with shutdown circuit")
for i, n in enumerate(["BRK", "BRK_TH", "TPS", "TPS_TH", "CT", "LATCH", "+5V", "GND"]):
    add(f"TP{i+1}", "Connector:TestPoint", n, "TP", {"1": n}, "TP", "PCB pad only (no part)", "", "", "Test pad (no header: T 11.6.4 minimal interfaces)")

# ----------------------------------------------------------------------------------------------
# Placement
LAYOUT = {  # block: (x0, y0, columns, cell_w, cell_h)
    "PWR":   (30.48, 55.88, 9, 20.32, 50.8),
    "REF":   (236.22, 55.88, 11, 17.78, 50.8),
    "SENS":  (30.48, 190.5, 10, 20.32, 50.8),
    "LOGIC": (236.22, 190.5, 6, 0, 0),
    "FAULT": (444.5, 190.5, 4, 0, 0),
    "OUT":   (30.48, 325.12, 0, 0, 0),
    "TP":    (444.5, 104.14, 8, 15.24, 50.8),
}
TITLES = {"PWR": "1. GLV POWER INPUT & 5 V REFERENCE SUPPLY", "REF": "2. REFERENCES & TRIM THRESHOLDS (all ratiometric to +5V)",
          "SENS": "3. SENSOR INPUTS (own sensors; each signal on its own connector, T 11.6.7)", "LOGIC": "4. PLAUSIBILITY (T 11.6.1) + 500 ms TIMER (T 11.6.2)",
          "FAULT": "5. SCS OPEN/SHORT DETECTION (T 11.9.2)", "OUT": "6. LATCH + SDC RELAY OUTPUT", "TP": "7. TEST PADS (no connector)"}

WIDE = {"Diode:BAT54S": 33.02, "Connector_Generic:Conn_01x03": 27.94, "Connector_Generic:Conn_01x02": 22.86,
        "Regulator_Linear:SPX2920M3-5.0_SOT223": 33.02, "Device:D_Schottky": 25.4, "Device:D_Zener": 25.4,
        "Device:LED": 33.02, "Device:D": 30.48, "Connector_Generic:Conn_01x08": 33.02, "Relay:G5LE-1": 35.56,
        "Device:R_Potentiometer_Trim": 25.4, "Device:Q_NMOS_GSD": 27.94}

def place_all():
    pos = {}
    bypass = {}
    for blk in LAYOUT:
        items = [p for p in P if p["blk"] == blk]
        x0, y0, ncol, cw, ch = LAYOUT[blk]
        if blk in ("PWR", "REF", "SENS", "TP"):
            xs = x0; row = 0
            for i, p in enumerate(items):
                w = WIDE.get(p["sym"], cw)
                if xs + w > x0 + ncol * cw + 1 or (p["ref"] in ("J3", "C5") and xs > x0): xs = x0; row += 1
                pos[id(p)] = (snap(xs + (w - cw) / 2), snap(y0 + row * ch)); xs += w
        elif blk == "LOGIC":
            comps = [p for p in items if p["sym"].startswith("Comparator")]
            others = [p for p in items if not p["sym"].startswith("Comparator")]
            for i, p in enumerate(comps): pos[id(p)] = (snap(x0 + 25.4 + i * 66.04), snap(y0 + 5.08))
            for i, p in enumerate(others): pos[id(p)] = (snap(x0 + 10.16 + i * 22.86), snap(y0 + 50.8))
        elif blk == "FAULT":
            for i, p in enumerate(items): pos[id(p)] = (snap(x0 + 25.4 + (i % 2) * 66.04), snap(y0 + 5.08 + (i // 2) * 40.64))
        elif blk == "OUT":
            comps = [p for p in items if p["sym"].startswith("Comparator")]
            seq = [p for p in items if not p["sym"].startswith("Comparator")]
            pos[id(comps[0])] = (snap(x0 + 25.4), snap(y0 + 12.7))
            xs = x0 + 66.04; row = 0
            for i, p in enumerate(seq):
                w = WIDE.get(p["sym"], 22.86)
                if xs + w > x0 + 330: xs = x0 + 66.04; row += 1
                pos[id(p)] = (snap(xs), snap(y0 + 12.7 + row * 50.8)); xs += w
    return pos

# ----------------------------------------------------------------------------------------------
# Schematic writer
def xf(px, py, X, Y, rot=0):
    r = math.radians(rot)
    rx = px * math.cos(r) - py * math.sin(r); ry = px * math.sin(r) + py * math.cos(r)
    return (round(X + rx, 4), round(Y - ry, 4))

def outward(ang, rot=0):
    a = math.radians(ang + 180 + rot)
    return (round(math.cos(a)), -round(math.sin(a)))   # screen dx, dy

def sexp_str(s): return '"' + s.replace('\\', '\\\\').replace('"', '\\"') + '"'

def build():
    pos = place_all()
    used_syms = {}
    items = []         # strings for symbols / wires / labels
    pwr_count = [0]
    root_uuid = uid()

    def power_sym(net, x, y, rot):
        pwr_count[0] += 1
        ref = f"#PWR{pwr_count[0]:03d}"
        used_syms["power:" + net] = ("power", net)
        pin = pins_of("power", net)[0]
        items.append(f'''  (symbol (lib_id "power:{net}") (at {x} {y} {rot}) (unit 1)
    (in_bom yes) (on_board yes) (dnp no) (uuid {uid()})
    (property "Reference" "{ref}" (at {x} {y} 0) (effects (font (size 1.27 1.27)) hide))
    (property "Value" "{net}" (at {x} {y + (3.81 if net == 'GND' and rot == 0 else -3.81 if rot == 0 else 3.81)} 0) (effects (font (size 1.27 1.27))))
    (property "Footprint" "" (at {x} {y} 0) (effects (font (size 1.27 1.27)) hide))
    (property "Datasheet" "" (at {x} {y} 0) (effects (font (size 1.27 1.27)) hide))
    (pin "{pin['num']}" (uuid {uid()}))
    (instances (project "bspd" (path "/{root_uuid}" (reference "{ref}") (unit 1))))
  )''')

    def wire(x1, y1, x2, y2):
        items.append(f'  (wire (pts (xy {x1} {y1}) (xy {x2} {y2})) (stroke (width 0) (type default)) (uuid {uid()}))')

    def label(net, x, y, dx, dy):
        ang = {(1, 0): 0, (-1, 0): 180, (0, -1): 90, (0, 1): 270}[(dx, dy)]
        just = "left" if ang in (0, 90) else "right"
        items.append(f'  (label {sexp_str(net)} (at {x} {y} {ang}) (fields_autoplaced) (effects (font (size 1.27 1.27)) (justify {just} bottom)) (uuid {uid()}))')

    for p in P:
        lib, name = p["sym"].split(":")
        used_syms[p["sym"]] = (lib, name)
        X, Y = pos[id(p)]
        allpins = pins_of(lib, name)
        upins = [q for q in allpins if q["unit"] in (0, p["unit"])]
        is_flag = p["ref"].startswith("#FLG")
        # properties
        props = [("Reference", p["ref"], (X + 3.81, Y - 6.35), False), ("Value", p["val"], (X + 3.81, Y + 6.35), False),
                 ("Footprint", p["fp"], (X, Y), True), ("Datasheet", "", (X, Y), True)]
        if p["sym"].startswith("Comparator"):
            props[0] = ("Reference", p["ref"], (X, Y - (11.43 if p["unit"] == 5 else 6.35)), False)
            props[1] = ("Value", p["val"], (X, Y + (11.43 if p["unit"] == 5 else 6.35)), False)
        if is_flag:
            props[0] = ("Reference", p["ref"], (X, Y), True); props[1] = ("Value", p["val"], (X, Y), True)
        if p["ref"].startswith("TP"):
            props[0] = ("Reference", p["ref"], (X, Y - 6.35), False); props[1] = ("Value", p["val"], (X, Y), True)
        if p["sym"].startswith("Regulator"):
            props[0] = ("Reference", p["ref"], (X, Y - 7.62), False); props[1] = ("Value", p["val"], (X + 7.62, Y + 7.62), False)
        if p["mpn"]: props += [("MPN", p["mpn"], (X, Y), True), ("Alt1", p["a1"], (X, Y), True), ("Alt2", p["a2"], (X, Y), True)]
        if p["desc"]: props += [("Note", p["desc"], (X, Y), True)]
        ptxt = "\n".join(
            f'    (property {sexp_str(k)} {sexp_str(v)} (at {round(px,4)} {round(py,4)} 0) (effects (font (size 1.27 1.27)){" hide" if h else ""}))'
            for k, v, (px, py), h in props)
        pin_lines = "\n".join(f'    (pin "{q["num"]}" (uuid {uid()}))' for q in upins)
        items.append(f'''  (symbol (lib_id "{p['sym']}") (at {X} {Y} 0) (unit {p['unit']})
    (in_bom {"no" if is_flag else "yes"}) (on_board {"no" if is_flag else "yes"}) (dnp no) (uuid {uid()})
{ptxt}
{pin_lines}
    (instances (project "bspd" (path "/{root_uuid}" (reference "{p['ref']}") (unit {p['unit']}))))
  )''')
        # connections
        for q in upins:
            net = p["pins"].get(q["num"])
            if net is None:
                if q["type"] == "no_connect" or True:
                    px, py = xf(q["x"], q["y"], X, Y)
                    items.append(f'  (no_connect (at {px} {py}) (uuid {uid()}))')
                continue
            px, py = xf(q["x"], q["y"], X, Y)
            dx, dy = outward(q["ang"])
            if is_flag:
                # flag pin at its origin; stub down to a power symbol / label
                ex, ey = px, round(py + 2.54, 4); wire(px, py, ex, ey)
                if net in POWER:
                    power_sym(net, ex, round(ey + (0 if net == "GND" else 2.54), 4), 0 if net == "GND" else 180) if False else None
                    if net == "GND": power_sym(net, ex, ey, 0)
                    else:
                        ex2, ey2 = ex, round(ey + 2.54, 4); wire(ex, ey, ex2, ey2); power_sym(net, ex2, ey2, 180)
                continue
            L = 2.54
            ex, ey = round(px + dx * L, 4), round(py + dy * L, 4)
            wire(px, py, ex, ey)
            if net in POWER:
                want_dy = 1 if net == "GND" else -1        # GND hangs down, supplies point up
                if dx != 0:                                   # horizontal stub -> rotated power symbol
                    if net == "GND": power_sym(net, ex, ey, 270 if dx < 0 else 90)
                    else: power_sym(net, ex, ey, 90 if dx < 0 else 270)
                elif dy == want_dy:
                    power_sym(net, ex, ey, 0)
                else:
                    power_sym(net, ex, ey, 180)
            else:
                label(net, ex, ey, dx, dy)

    # block titles & frames
    texts = []
    for blk, t in TITLES.items():
        x0, y0 = LAYOUT[blk][0], LAYOUT[blk][1]
        texts.append(f'  (text {sexp_str(t)} (at {snap(x0 - 7.62)} {snap(y0 - 22.86)} 0) (effects (font (size 2.54 2.54) (thickness 0.508) bold) (justify left bottom)) (uuid {uid()}))')
    notes = "\\n".join([
        "DESIGN NOTES - checked against FS Rules 2025 v1.0 (T 11.6, T 11.9, T 1.3.1, CV 1.4.3, CV 4.1)",
        "Comparators: U1A hard brake | U1B throttle > 20 % | U1D 500 ms timer | U1C latch",
        "             U2A/U2B brake signal window | U2D/U2C throttle signal window",
        "T 11.6.1  Trip when BRK > BRK_TH AND TPS > TPS_TH. Latched until LVMS power cycle.",
        "T 11.6.2  Delay 0.693 x R19 x C17 = 326 ms (Monte-Carlo 256-350 ms) < 500 ms.",
        "T 11.6.3  J1 wired DIRECTLY from LVMS output (only fuse/connectors in between).",
        "T 11.6.4  Only interfaces: J1 power, J2/J6 brake sensor, J3/J7 throttle sensor, J4 SDC.",
        "T 11.6.5  RV1 range 0.66-1.27 V = 5.6-26.4 bar (MLH02K 0.5-4.5 V = 0-138 bar): always <= 30 bar.",
        "          Set below wheel-lock pressure. BRK_TH = 0.5 V + 0.029 V/bar x P.",
        "T 11.6.7  Each sensor signal on its own connector (J6, J7) - unplug to test.",
        "T 11.9.2  Open / short-GND / short-5V on any sensor wire -> trip in ~70 ms.",
        "CV 1.4.3  Throttle sensor on the THROTTLE BODY shaft. TPS_TH = V_idle + 0.20 x (V_WOT - V_closed).",
        "CV 4.1.1  K1 COM(1)/NO(3) in series with the SDC; SDC must feed >=2 mechanical relays",
        "          (fuel pump relay + ignition/injection relay).  CV 4.1.3: de-energised K1 = SDC open.",
        "C5 must be tantalum (LDO stability). Lock RV1/RV2 with paint after setting.",
    ])

    texts.append(f'  (text "{notes}" (at 436.88 287.02 0) (effects (font (size 1.524 1.524)) (justify left bottom)) (uuid {uid()}))')

    # lib_symbols
    libs = []
    for key, (lib, name) in sorted(used_syms.items()):
        b = libsym(lib, name)
        b = b.replace(f'(symbol "{name}"', f'(symbol "{lib}:{name}"', 1)
        libs.append("    " + b.replace("\n", "\n  "))

    sch = f'''(kicad_sch (version 20230121) (generator eeschema)
  (uuid {root_uuid})
  (paper "A2")
  (title_block
    (title "Brake System Plausibility Device (BSPD) - Option A")
    (date "2026-11-02")
    (rev "B")
    (company "ZFR Formula SAE - Electrical")
    (comment 1 "CV car, Kawasaki ER-6n 650 (stock ECU). Non-programmable, latching, fail-safe.")
    (comment 2 "Verified: ngspice behavioural sim + exported-netlist cross-check")
    (comment 3 "Checked against FS Rules 2025 v1.0: T 11.6, T 11.9, T 1.3.1, CV 1.4.3, CV 4.1")
  )
  (lib_symbols
{chr(10).join(libs)}
  )
{chr(10).join(items)}
{chr(10).join(texts)}
  (sheet_instances (path "/" (page "1")))
)
'''
    return sch

if __name__ == "__main__":
    sch = build()
    open(os.path.join(OUT, "bspd.kicad_sch"), "w").write(sch)
    # project file
    open(os.path.join(OUT, "bspd.kicad_pro"), "w").write(json.dumps({"meta": {"filename": "bspd.kicad_pro", "version": 1}}, indent=2))
    # expected netlist for cross-check
    exp = {}
    for p in P:
        if p["ref"].startswith("#"): continue
        for pin, net in p["pins"].items():
            exp.setdefault(net, set()).add(f'{p["ref"]}.{pin}')
    json.dump({k: sorted(v) for k, v in exp.items()}, open(os.path.join(OUT, "expected_nets.json"), "w"), indent=1)
    # BOM (grouped)
    rows = {}
    seen = set()
    for p in sorted(P, key=lambda q: q["mpn"] == ""):
        if p["ref"].startswith("#") or p["ref"].startswith("TP") or p["ref"] in seen: continue
        seen.add(p["ref"])
        key = (p["val"], p["fp"], p["mpn"])
        r = rows.setdefault(key, dict(refs=[], value=p["val"], footprint=p["fp"].split(":")[-1], mpn=p["mpn"], a1=p["a1"], a2=p["a2"], note=p["desc"]))
        if p["ref"] not in r["refs"]: r["refs"].append(p["ref"])
        if p["desc"] and p["desc"] not in r["note"]: r["note"] = (r["note"] + "; " + p["desc"]).strip("; ")
    def natkey(s): m = re.match(r"([A-Z]+)(\d+)", s); return (m.group(1), int(m.group(2)))
    with open(os.path.join(OUT, "..", "BOM_generated.csv"), "w", newline="") as f:
        w = csv.writer(f); w.writerow(["Qty", "References", "Value", "Footprint", "Primary part", "Alternative 1", "Alternative 2", "Notes"])
        for r in sorted(rows.values(), key=lambda r: natkey(sorted(r["refs"], key=natkey)[0])):
            refs = sorted(r["refs"], key=natkey)
            w.writerow([len(refs), " ".join(refs), r["value"], r["footprint"], r["mpn"], r["a1"], r["a2"], r["note"]])
    print("parts:", len([p for p in P if not p['ref'].startswith('#')]), "nets:", len(exp))
