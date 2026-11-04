#!/usr/bin/env python3
"""Behavioural ngspice verification of the ZFR BSPD (Option A).
Generates one netlist per scenario, runs ngspice in batch mode, parses results.
Component values MUST match the KiCad schematic (see ../tools/gen_sch.py).
Rev B.1: R10/R13/R14 = 0R, D3/D4 = BAV199 (low-leakage clamps)."""
import subprocess, os, random, math, sys, json

HERE = os.path.dirname(os.path.abspath(__file__))
RUN = os.path.join(HERE, "runs"); os.makedirs(RUN, exist_ok=True)

COMP = """
* Open-collector comparator (LM2901-like). vos = input offset, uv = output state when Vcc<2V (1=sinks)
.subckt cmp inp inn out vcc gnd vos=0 uv=0
Bdrv out gnd I = ( (0.5*(1+tanh((V(vcc,gnd)-2)/0.05))) * (0.5*(1+tanh((V(inn,gnd)-V(inp,gnd)+{vos})/0.3m))) + (1-0.5*(1+tanh((V(vcc,gnd)-2)/0.05)))*{uv} ) * min(16m, max(V(out,gnd),0)/40)
Cout out gnd 10p
Iq vcc gnd 0.25m
.ends
.model swm sw vt=0.5 vh=0.1 ron=0.01 roff=1e9
"""

def netlist(p):
    """p: dict of scenario params."""
    g = lambda k, d: p.get(k, d)
    vos = g("vos", [0]*8); uv = g("uv", [0]*8)
    R19 = g("R19", 470e3); C17 = g("C17", 1e-6)
    lad = g("lad", [1e3, 9.1e3, 9.1e3, 1e3])
    s = [f"* BSPD scenario {p['name']}", COMP]
    # ---- supply: GLV 12 V (PWL), polyfuse ~ 1 ohm, SS34, TVS ignored (clamp only)
    s.append(f"V12 VIN 0 PWL({g('v12pwl', '0 0 1m 12.6 100 12.6')})")
    s.append("RF1 VIN FOUT 1")
    s.append("D1 FOUT P12 dss34\n.model dss34 D(is=5u n=1.1 rs=0.05)")
    s.append("C1 P12 0 47u\nC2 P12 0 100n")
    # LM2940-5 behavioural: OUT = min(5, Vin-0.5), current-limited, needs Vin>~2
    s.append("BLDO LDOI 0 V = max(0, min(5.0, V(P12)-0.5))\nRLDO LDOI P5 0.2\nC5 P5 0 22u\nC6 P5 0 100n")
    # ---- reference ladder
    s.append(f"R1 P5 VH {lad[0]}\nR2 VH VM {lad[1]}\nR3 VM VL {lad[2]}\nR4 VL 0 {lad[3]}")
    s.append("C7 VH 0 100n\nC8 VM 0 100n\nC9 VL 0 100n")
    s.append("R5 VM VLAT 100k\nC10 VLAT 0 1u")
    # ---- thresholds (trimmer wiper position k: 0..1 from bottom)
    kb = g("kb", 0.35); kt = g("kt", 0.30)
    s.append(f"R6 P5 RV1T 62k\nRV1a RV1T BTH {10e3*(1-kb)+1}\nRV1b BTH RV1B {10e3*kb+1}\nR7 RV1B 0 11k\nC11 BTH 0 100n")
    s.append(f"R8 P5 RV2T 15k\nRV2a RV2T TTH {10e3*(1-kt)+1}\nRV2b TTH RV2B {10e3*kt+1}\nR9 RV2B 0 3.3k\nC12 TTH 0 100n")
    # ---- brake sensor interface  (J2: 1=BRK_5V 2=BRK_SIG 3=GND)
    s.append("R10 P5 B5V 1m\nC13 B5V 0 100n")   # 0R jumper
    s.append("R11 BRAW 0 100k\nR12 BRAW BRK 10k\nC14 BRK 0 100n")
    s.append("D3a 0 BRK dclp\nD3b BRK P5 dclp\n.model dclp D(is=2n n=1.8 rs=1)")   # BAV199 (Si, low leakage)
    # extra clamp reverse leakage at temperature (worst case): lower diode drains TPS, upper diode feeds BRK
    s.append(f"ILT TPS 0 {g('ileak', 20e-9)}\nILB P5 BRK {g('ileak', 20e-9)}")
    # harness wires with fault switches: sensor side nodes  SB5 (supply), SBO (output), SBG (gnd)
    s.append(f"VFB5 FB5 0 PWL({g('f_b5','0 1')})\nS_b5 B5V SB5 FB5 0 swm")      # supply wire open when ctrl=1 -> use inverted
    s.append(f"VFBO FBO 0 PWL({g('f_bo','0 1')})\nS_bo SBO BRAW FBO 0 swm")
    s.append(f"VFBG FBG 0 PWL({g('f_bg','0 1')})\nS_bg SBG 0 FBG 0 swm")
    s.append(f"VFBS FBS 0 PWL({g('f_bsg','0 0')})\nS_bsg BRAW 0 FBS 0 swm")      # signal short to GND
    s.append(f"VFBH FBH 0 PWL({g('f_bs5','0 0')})\nS_bs5 BRAW P5 FBH 0 swm")     # signal short to 5V
    # MLH sensor: draws ~5 mA, out = (0.5+4*P/Pfs)*Vsup/5, 100 ohm out, startup delay
    s.append(f"VP PBAR 0 PWL({g('pbar','0 0')})")
    s.append("RSB SB5 SBG 1k")
    s.append(f"BSB SBX SBG V = (V(SB5,SBG)>4.0 ? 1:0) * (time>{g('bstart',5e-3)} ? 1:0) * (0.5+4*V(PBAR)/138)*V(SB5,SBG)/5")
    s.append("RSBO SBX SBO 100")
    # ---- TPS interface  (J3: 1=TPS_5V 2=TPS_SIG 3=TPS_GND)
    s.append("R13 P5 T5V 1m\nC15 T5V 0 100n\nR14 TGND 0 1m")   # 0R jumpers
    s.append("R15 TRAW P5 470k\nR16 TRAW TPS 10k\nC16 TPS 0 10n")
    s.append("D4a 0 TPS dclp\nD4b TPS P5 dclp")
    s.append(f"VFT5 FT5 0 PWL({g('f_t5','0 1')})\nS_t5 T5V ST5 FT5 0 swm")
    s.append(f"VFTW FTW 0 PWL({g('f_tw','0 1')})\nS_tw STW TRAW FTW 0 swm")
    s.append(f"VFTG FTG 0 PWL({g('f_tg','0 1')})\nS_tg STG TGND FTG 0 swm")
    s.append(f"VFTS FTS 0 PWL({g('f_tsg','0 0')})\nS_tsg TRAW 0 FTS 0 swm")
    s.append(f"VFTH FTH 0 PWL({g('f_ts5','0 0')})\nS_ts5 TRAW P5 FTH 0 swm")
    # 3382H pot 10k (x = wiper position 0..1 from GND end). pedal: x from PWL
    rp = g("rpot", 10e3)
    s.append(f"VX XPOS 0 PWL({g('xpos','0 0.15')})")
    s.append(f"BPT ST5 STW I = V(ST5,STW)/({rp}*(1-V(XPOS))+1)")
    s.append(f"BPB STW STG I = V(STW,STG)/({rp}*V(XPOS)+1)")
    # harness shorts between neighbouring wires of the same cable (T 11.9.2)
    for key, a, b in (("x_bss", "BRAW", "SB5"), ("x_tss", "TRAW", "ST5"), ("x_tsr", "TRAW", "STG"), ("x_t5g", "ST5", "0"), ("x_b5g", "SB5", "0")):
        s.append(f"V{key} N{key} 0 PWL({g(key, '0 0')})\nS{key} {a} {b} N{key} 0 swm")
    s.append("RLK1 ST5 0 1e9\nRLK2 STW 0 1e9\nRLK3 STG 0 1e9\nRLK4 SB5 0 1e9\nRLK5 SBO 0 1e9\nRLK6 SBG 0 1e9")
    # ---- comparators: U1 (A brake-hard, B tps-high, C timer, D latch), U2 (fault window)
    s.append(f"XU1A BRK BTH U1A P12 0 cmp vos={vos[0]} uv={uv[0]}")
    s.append(f"XU1B TPS TTH U1B P12 0 cmp vos={vos[1]} uv={uv[1]}")
    s.append("R17 U1A CT 1k\nR18 U1B CT 1k")
    s.append(f"R19 P5 CT {R19}\nC17 CT 0 {C17}")
    s.append(f"XU1C VM CT TMR P12 0 cmp vos={vos[2]} uv={uv[2]}")
    s.append("R20 TMR LATCH 1k")
    s.append(f"XU1D LATCH VLAT OK P12 0 cmp vos={vos[3]} uv={uv[3]}")
    s.append("R21 P5 LATCH 1meg\nC18 LATCH P5 1u\nR22 FAULTN LATCH 100k")
    s.append("D5 LATCH OK d4148\n.model d4148 D(is=2.5n n=1.75 rs=0.6)")
    s.append("R23 P5 OK 10k")
    s.append(f"XU2A BRK VL FAULTN P12 0 cmp vos={vos[4]} uv={uv[4]}")
    s.append(f"XU2B VH BRK FAULTN P12 0 cmp vos={vos[5]} uv={uv[5]}")
    s.append(f"XU2C TPS VL FAULTN P12 0 cmp vos={vos[6]} uv={uv[6]}")
    s.append(f"XU2D VH TPS FAULTN P12 0 cmp vos={vos[7]} uv={uv[7]}")
    s.append("RFLK FAULTN 0 1e9")
    # ---- output: red LED from 5V into OK, gate drive, AO3400A, relay coil 400R
    s.append("R27 P5 LEDR 1k\nD8 LEDR OK dled\n.model dled D(is=1e-18 n=2 rs=10)")
    s.append("R24 OK GATE 100\nR25 GATE 0 100k")
    s.append("BQ1 COILN 0 I = V(COILN)/ (0.03 + 1e6*(1-0.5*(1+tanh((V(GATE)-1.6)/0.1))))")
    s.append("RK1 P12 COILN 400\nLK1 P12 COILN2 1m\nRK1b COILN2 COILN 1e9\nD6 COILN P12 d4148")
    s.append(f".tran 0.2m {g('tstop',2)} 0 0.5m uic")
    s.append(".options reltol=1e-3 abstol=1e-9 method=gear")
    s.append(".control\nset wr_singlescale\nset wr_vecnames\nrun\n"
             f"wrdata {os.path.join(RUN, p['name'])}.dat v(P12) v(P5) v(BRK) v(TPS) v(BTH) v(TTH) v(CT) v(LATCH) v(FAULTN) v(OK) v(GATE) v(COILN) v(VLAT) v(VM)\nquit\n.endc\n.end")
    return "\n".join(s)

def run(p):
    f = os.path.join(RUN, p["name"] + ".cir")
    open(f, "w").write(netlist(p))
    r = subprocess.run(["ngspice", "-b", f], capture_output=True, text=True, timeout=300)
    dat = os.path.join(RUN, p["name"] + ".dat")
    if not os.path.exists(dat):
        print(r.stdout[-2000:], r.stderr[-2000:]); raise SystemExit("sim failed " + p["name"])
    rows = [l.split() for l in open(dat)]
    hdr = rows[0]; data = [list(map(float, r)) for r in rows[1:]]
    col = {h.lower(): i for i, h in enumerate(hdr)}
    return col, data

def analyse(col, data, t_after=0.0):
    """trip time = first time after t_after where relay coil current stops (V(COILN) > 6 V => FET off)."""
    tc = col["time"]; c = col["v(coiln)"]; p12 = col["v(p12)"]
    trip = None
    for r in data:
        if r[tc] > t_after and r[p12] > 8 and r[c] > 0.5 * r[p12]:
            trip = r[tc]; break
    return trip

def latched_at_end(col, data):
    r = data[-1]; return r[col["v(coiln)"]] > 0.5 * r[col["v(p12)"]]

def maxv(col, data, name, t0=0.01):
    return max(r[col[name]] for r in data if r[col["time"]] > t0)

results = []
def rec(name, ok, detail):
    results.append((name, "PASS" if ok else "FAIL", detail)); print(f"{'PASS' if ok else 'FAIL'}  {name}: {detail}")

if __name__ == "__main__":
    # Static threshold report (nominal)
    PB = lambda bar: 0.5 + 4 * bar / 138
    lo, hi = 5*11/83, 5*21/83
    print("brake trimmer range: %.3f..%.3f V  => %.1f..%.1f bar" % (lo, hi, (lo-0.5)/4*137.9, (hi-0.5)/4*137.9))
    for pt in (0.9, 1.1):
        for rt, rb in ((62e3*1.01, 11e3*0.99), (62e3*0.99, 11e3*1.01)):
            v = 5*(rb+10e3*pt)/(rt+10e3*pt+rb); print("  worst-case max (pot %.0f%%): %.3f V = %.1f bar" % (pt*100, v, (v-0.5)/4*137.9))
    print("set point kb=0.9: %.3f V = %.1f bar" % (5*(11e3+9e3)/83e3, (5*(20/83)-0.5)/4*137.9))
    print("TPS trimmer range:   %.3f..%.3f V" % (5*3.3/28.3, 5*13.3/28.3))

    # Idle state: brake 0 bar, pedal x=0.15
    base = dict(pbar="0 0", xpos="0 0.15", kb=0.9, kt=0.30)
    # kb=0.35 -> BTH = 5*(5.1+3.5)/25.1 = 1.713 V = 41.9 bar ; kt=0.30 -> TTH = 5*(3.3+3)/28.3 = 1.113V
    # pedal: idle x=0.15 -> TPS ~0.9V ; full x=0.35 (pedal uses ~66 deg of the 330 deg track)

    # 1. normal idle + power-up
    col, d = run(dict(base, name="s01_powerup_idle", tstop=1.5)); t = analyse(col, d)
    rec("Power-up, idle, nominal", t is None, f"trip={t}; Vlatch end={d[-1][col['v(latch)']]:.2f}")
    # 2. brake hard only
    col, d = run(dict(base, name="s02_brake_only", pbar="0 0 0.3 0 0.31 40", tstop=2)); t = analyse(col, d)
    rec("Hard brake (40 bar), throttle idle", t is None, f"trip={t}")
    # 3. throttle only
    col, d = run(dict(base, name="s03_throttle_only", xpos="0 0.15 0.3 0.15 0.35 0.45", tstop=2)); t = analyse(col, d)
    rec("Full throttle, no brake", t is None, f"trip={t}")
    # 4. both, 200 ms (left-foot overlap) -> no trip
    col, d = run(dict(base, name="s04_both_200ms", pbar="0 0 0.5 0 0.501 40 0.7 80 0.701 0",
                      xpos="0 0.15 0.5 0.15 0.501 0.45", tstop=2)); t = analyse(col, d)
    rec("Brake+throttle overlap 200 ms", t is None, f"trip={t}")
    # 5. both continuous -> trip, release, stays latched
    col, d = run(dict(base, name="s05_both_trip_latch", pbar="0 0 0.5 0 0.501 40 1.5 80 1.501 0",
                      xpos="0 0.15 0.5 0.15 0.501 0.45 1.5 0.45 1.501 0.15", tstop=3)); t = analyse(col, d)
    rec("Brake+throttle continuous -> trip", t is not None and 0.5 + 0.2 < t < 0.5 + 0.5, f"trip delay={(t-0.5)*1000 if t else None:.0f} ms")
    rec("Stays latched after release", latched_at_end(col, d), f"OK node end={d[-1][col['v(ok)']]:.2f} V")
    # 5b. threshold accuracy: slow ramp of throttle with brake held, find TPS voltage at which timer starts
    # 6. faults
    faults = [("f_bo", "Brake signal wire open"), ("f_b5", "Brake 5V wire open"), ("f_bsg", "Brake signal short GND"),
              ("f_bs5", "Brake signal short 5V"), ("f_tw", "TPS wiper wire open"), ("f_t5", "TPS 5V wire open"),
              ("f_tg", "TPS GND wire open"), ("f_tsg", "TPS signal short GND"), ("f_ts5", "TPS signal short 5V")]
    for key, label in faults:
        # open faults: switch normally closed -> ctrl 1 normally, 0 at fault. shorts: ctrl 0 normally, 1 at fault
        opn = key in ("f_bo", "f_b5", "f_bg", "f_tw", "f_t5", "f_tg")
        sw = "0 1 0.5 1 0.5001 0" if opn else "0 0 0.5 0 0.5001 1"
        p = dict(base, name="s06_" + key, tstop=1.2)
        for k in ("f_bo", "f_b5", "f_bg", "f_tw", "f_t5", "f_tg"):
            p[k] = "0 1"
        p[key] = sw
        col, d = run(p); t = analyse(col, d)
        rec("Fault: " + label, t is not None and 0.5 < t < 0.65, f"trip delay={(t-0.5)*1000 if t else None} ms")
    # 6b. harness shorts to the sensor's OWN supply / return wire (neighbouring wires), pot tolerance 7k/13k
    SW = "0 0 0.5 0 0.5001 1"
    nb = [("x_bss", "Brake signal short to sensor supply wire", {})]
    for rp in (7e3, 13e3):
        nb += [("x_tss", f"TPS signal short to sensor supply wire (pot {rp/1e3:.0f}k)", dict(rpot=rp)),
               ("x_tsr", f"TPS signal short to sensor return wire (pot {rp/1e3:.0f}k)", dict(rpot=rp))]
    for i, (key, label, extra) in enumerate(nb):
        p = dict(base, name=f"s06b_{key}_{i}", tstop=1.2, **extra); p[key] = SW
        for k in ("f_bo", "f_b5", "f_bg", "f_tw", "f_t5", "f_tg"): p[k] = "0 1"
        col, d = run(p); t = analyse(col, d)
        rec("Fault: " + label, t is not None and 0.5 < t < 0.65 and latched_at_end(col, d), f"trip delay={(t-0.5)*1000 if t else None} ms")
    # 6c. sensor supply wire short to GND: 5 V rail collapses -> SDC must open at once (safe state)
    for key, label in (("x_t5g", "TPS supply wire short to GND"), ("x_b5g", "Brake supply wire short to GND")):
        p = dict(base, name=f"s06c_{key}", tstop=1.0); p[key] = SW
        for k in ("f_bo", "f_b5", "f_bg", "f_tw", "f_t5", "f_tg"): p[k] = "0 1"
        col, d = run(p); t = analyse(col, d)
        rec("Fault: " + label + " -> SDC opens", t is not None and 0.5 <= t < 0.52, f"open after={(t-0.5)*1000 if t is not None else None} ms")
    # 6d. open-wire detection with hot clamp leakage (0.2 uA = 10x BAV199 worst case at 85 C)
    for key, label in (("f_tw", "TPS wiper open"), ("f_bo", "Brake signal open")):
        p = dict(base, name=f"s06d_{key}", tstop=1.2, ileak=0.2e-6)
        for k in ("f_bo", "f_b5", "f_bg", "f_tw", "f_t5", "f_tg"): p[k] = "0 1"
        p[key] = "0 1 0.5 1 0.5001 0"
        col, d = run(p); t = analyse(col, d)
        rec(f"Fault at temperature (clamp leak 0.2 uA): {label}", t is not None and 0.5 < t < 0.65, f"trip delay={(t-0.5)*1000 if t else None} ms")
    # 7. 20 ms glitch on brake signal (short to GND) -> must not trip
    p = dict(base, name="s07_glitch", f_bsg="0 0 0.5 0 0.5001 1 0.52 1 0.5201 0", tstop=1.2)
    for k in ("f_bo", "f_b5", "f_bg", "f_tw", "f_t5", "f_tg"): p[k] = "0 1"
    col, d = run(p); t = analyse(col, d)
    rec("20 ms wiring glitch rejected", t is None, f"trip={t}")
    # 8. cranking dip: 12.6 -> 7 V for 300 ms, and -> 6 V
    for vmin in (8.0, 7.0, 6.0):
        p = dict(base, name=f"s08_crank_{vmin}", v12pwl=f"0 0 1m 12.6 0.5 12.6 0.52 {vmin} 0.8 {vmin} 0.85 12.6 3 12.6", tstop=1.5)
        for k in ("f_bo", "f_b5", "f_bg", "f_tw", "f_t5", "f_tg"): p[k] = "0 1"
        col, d = run(p); t = analyse(col, d, 0.9)
        rec(f"Supply dip to {vmin} V (300 ms), no false latch", t is None, f"trip after recovery={t}")
    # 9. supply 16 V and 9 V steady with trip check
    for vs in (9.0, 16.0):
        p = dict(base, name=f"s09_supply_{vs}", v12pwl=f"0 0 1m {vs} 5 {vs}", pbar="0 0 0.5 0 0.501 40",
                 xpos="0 0.15 0.5 0.15 0.501 0.45", tstop=1.5)
        for k in ("f_bo", "f_b5", "f_bg", "f_tw", "f_t5", "f_tg"): p[k] = "0 1"
        col, d = run(p); t = analyse(col, d)
        rec(f"Supply {vs} V: trips correctly", t is not None and 0.7 < t < 1.0, f"trip delay={(t-0.5)*1000 if t else None:.0f} ms")
    # 10. power-up robustness: random offsets and undervoltage output states
    random.seed(7); bad = 0; N = 40
    for i in range(N):
        vos = [random.uniform(-7e-3, 7e-3) for _ in range(8)]
        uv = [random.choice([0, 1]) for _ in range(8)]
        if i == 0: uv = [1]*8
        if i == 1: uv = [0]*8
        p = dict(base, name=f"s10_pu_{i}", vos=vos, uv=uv, tstop=0.6,
                 v12pwl=random.choice(["0 0 1m 12.6 5 12.6", "0 0 50m 12.6 5 12.6", "0 0 0.2m 12.6 5 12.6"]))
        for k in ("f_bo", "f_b5", "f_bg", "f_tw", "f_t5", "f_tg"): p[k] = "0 1"
        col, d = run(p); t = analyse(col, d)
        if t is not None: bad += 1; print("   power-up trip in run", i, uv, t)
    rec(f"Power-up x{N} (random offsets, worst-case UV output states, 0.2-50 ms ramps)", bad == 0, f"false trips={bad}")
    # 11. brake sensor slow startup (worst case 50 ms)
    p = dict(base, name="s11_slow_sensor", bstart=0.05, tstop=0.8)
    for k in ("f_bo", "f_b5", "f_bg", "f_tw", "f_t5", "f_tg"): p[k] = "0 1"
    col, d = run(p); t = analyse(col, d)
    rec("Brake sensor 50 ms startup, no false trip", t is None, f"trip={t}")
    # 12. Monte-Carlo trip time (R19 1%, C17 +-10% & -15% DC-bias, ladder 1%, offsets)
    random.seed(3); times = []
    for i in range(30):
        lad = [v * random.uniform(0.99, 1.01) for v in (1e3, 9.1e3, 9.1e3, 1e3)]
        p = dict(base, name=f"s12_mc_{i}", R19=470e3 * random.uniform(0.99, 1.01),
                 C17=1e-6 * random.uniform(0.9, 1.1) * random.uniform(0.85, 1.0), lad=lad,
                 vos=[random.uniform(-7e-3, 7e-3) for _ in range(8)],
                 pbar="0 0 0.5 0 0.501 40", xpos="0 0.15 0.5 0.15 0.501 0.45", tstop=1.2)
        for k in ("f_bo", "f_b5", "f_bg", "f_tw", "f_t5", "f_tg"): p[k] = "0 1"
        col, d = run(p); t = analyse(col, d)
        times.append((t - 0.5) * 1000 if t else 1e9)
    rec("Monte-Carlo trip delay (30 runs)", max(times) < 500 and min(times) > 150, f"min={min(times):.0f} ms max={max(times):.0f} ms")
    json.dump(results, open(os.path.join(HERE, "results.json"), "w"), indent=1)
