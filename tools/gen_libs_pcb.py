#!/usr/bin/python3
"""Create the project symbol/footprint libraries and an unrouted .kicad_pcb (all footprints placed,
all pads assigned to nets) so Altium's KiCad Import Wizard brings in schematic + PCB + libraries."""
import os, re, shutil, sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import pcbnew
import gen_sch as G

K = os.path.normpath(G.OUT)
FPDIR = "/usr/share/kicad/footprints"
PRETTY = os.path.join(K, "bspd.pretty")
shutil.rmtree(PRETTY, ignore_errors=True); os.makedirs(PRETTY)

# ---- footprint library
src = {}
for p in G.P:
    if not p["fp"]: continue
    name = p["fp"].split(":")[1]
    lib = [v for v in G.FP.values() if v.split(":")[1] == name][0].split(":")[0]
    src[name] = os.path.join(FPDIR, lib + ".pretty", name + ".kicad_mod")
for name, f in src.items():
    shutil.copy(f, os.path.join(PRETTY, name + ".kicad_mod"))

# ---- symbol library (flattened, no 'extends', Altium-friendly)
syms = {}
for p in G.P:
    syms[p["sym"]] = p["sym"].split(":")
body = []
for key, (lib, name) in sorted(syms.items()):
    if lib == "power": continue
    body.append("  " + G.libsym(lib, name))
open(os.path.join(K, "bspd.kicad_sym"), "w").write(
    "(kicad_symbol_lib (version 20220914) (generator kicad_symbol_editor)\n" + "\n".join(body) + "\n)\n")

# ---- library tables
open(os.path.join(K, "fp-lib-table"), "w").write(
    '(fp_lib_table\n  (lib (name "bspd")(type "KiCad")(uri "${KIPRJMOD}/bspd.pretty")(options "")(descr "BSPD project footprints"))\n)\n')
open(os.path.join(K, "sym-lib-table"), "w").write(
    '(sym_lib_table\n  (lib (name "bspd")(type "KiCad")(uri "${KIPRJMOD}/bspd.kicad_sym")(options "")(descr "BSPD project symbols"))\n)\n')

# ---- PCB
board = pcbnew.BOARD()
nets = {}
def net(n):
    if n not in nets:
        ni = pcbnew.NETINFO_ITEM(board, n); board.Add(ni); nets[n] = ni
    return nets[n]

parts = {}
for p in G.P:
    if p["ref"].startswith("#"): continue
    d = parts.setdefault(p["ref"], dict(fp=p["fp"], val=p["val"], pins={}, blk=p["blk"]))
    d["pins"].update(p["pins"])
    if p["blk"] != "PWR": d["blk"] = p["blk"]

MM = pcbnew.FromMM
order = ["PWR", "REF", "SENS", "LOGIC", "FAULT", "OUT", "TP"]
x0, y0 = 105.0, 55.0
x, y, rowh = x0, y0, 0
W = 120.0
maxx = x0
for blk in order:
    refs = [r for r, d in parts.items() if d["blk"] == blk]
    for r in sorted(refs, key=lambda s: (re.match(r"[A-Z]+", s).group(), int(re.search(r"\d+", s).group()))):
        d = parts[r]
        name = d["fp"].split(":")[1]
        fp = pcbnew.FootprintLoad(PRETTY, name)
        fp.SetReference(r); fp.SetValue(d["val"])
        fp.SetFPIDAsString("bspd:" + name)
        bb = fp.GetBoundingBox(False, False)
        w, h = pcbnew.ToMM(bb.GetWidth()) + 2.5, pcbnew.ToMM(bb.GetHeight()) + 2.5
        if x + w > x0 + W: x = x0; y += rowh; rowh = 0
        fp.SetPosition(pcbnew.VECTOR2I(MM(x + w / 2), MM(y + h / 2)))
        board.Add(fp)
        # centre the footprint bbox in its cell
        nb = fp.GetBoundingBox(False, False); c = nb.GetCenter()
        fp.Move(pcbnew.VECTOR2I(MM(x + w / 2) - c.x, MM(y + h / 2) - c.y))
        for pad in fp.Pads():
            n = d["pins"].get(pad.GetNumber())
            if n: pad.SetNet(net(n))
        x += w; rowh = max(rowh, h); maxx = max(maxx, x)
    x = x0; y += rowh + 4; rowh = 0

# board outline
bx1, by1, bx2, by2 = x0 - 5, y0 - 5, maxx + 5, y + 5
for (a, b) in [((bx1, by1), (bx2, by1)), ((bx2, by1), (bx2, by2)), ((bx2, by2), (bx1, by2)), ((bx1, by2), (bx1, by1))]:
    seg = pcbnew.PCB_SHAPE(board); seg.SetShape(pcbnew.SHAPE_T_SEGMENT)
    seg.SetStart(pcbnew.VECTOR2I(MM(a[0]), MM(a[1]))); seg.SetEnd(pcbnew.VECTOR2I(MM(b[0]), MM(b[1])))
    seg.SetLayer(pcbnew.Edge_Cuts); seg.SetWidth(MM(0.1)); board.Add(seg)
board.SetCopperLayerCount(2)
pcbnew.SaveBoard(os.path.join(K, "bspd.kicad_pcb"), board)

# ---- verify every pad net against the design
b2 = pcbnew.LoadBoard(os.path.join(K, "bspd.kicad_pcb"))
errs = 0; npads = 0
for fp in b2.GetFootprints():
    r = fp.GetReference(); exp = parts[r]["pins"]
    for pad in fp.Pads():
        num = pad.GetNumber(); got = pad.GetNetname(); want = exp.get(num, "")
        if num == "" : continue
        npads += 1
        if got != want:
            errs += 1; print("PAD MISMATCH", r, num, "got", got, "want", want)
    missing = set(exp) - {pad.GetNumber() for pad in fp.Pads()}
    if missing: errs += 1; print("PINS WITHOUT PAD", r, missing)
print(f"PCB: {len(b2.GetFootprints())} footprints, {npads} pads, {len(nets)} nets, board {bx2-bx1:.0f} x {by2-by1:.0f} mm, errors={errs}")
sys.exit(1 if errs else 0)
