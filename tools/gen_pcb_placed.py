#!/usr/bin/python3
"""Pre-placed BSPD PCB: 90 x 70 mm, 2 layers, every footprint placed in a floorplan, nets assigned,
board outline on Edge.Cuts. Checks courtyard overlaps and that everything sits inside the board."""
import os, re, sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import pcbnew
import gen_sch as G

K = os.path.normpath(G.OUT); PRETTY = os.path.join(K, "bspd.pretty")
MM = pcbnew.FromMM
OX, OY = 100.0, 50.0           # board top-left in KiCad coordinates
W, H = 90.0, 70.0

REGIONS = {  # name: (x0, y0, x1, y1) in board mm, refs in placement order
    "PWR":  ((2, 2, 43, 18),  ["F1", "D1", "D2", "C1", "U3", "C5", "C6", "C2"]),
    "REF":  ((2, 19, 43, 40), ["RV1", "R6", "R7", "C11", "RV2", "R8", "R9", "C12", "R1", "R2", "R3", "R4", "C7", "C8", "C9"]),
    "SENS": ((2, 41, 46, 60), ["R10", "C13", "R11", "R12", "C14", "D3", "R13", "C15", "R14", "R15", "R16", "C16", "D4"]),
    "CORE": ((44, 2, 68, 35), ["U1", "C3", "U2", "C4", "C17", "R19", "R17", "R18", "R20", "R21", "C18", "R22", "D5",
                               "R23", "R5", "C10"]),
    "OUT":  ((69, 2, 88, 33), ["Q1", "R24", "R25", "D6", "R26", "D7", "R27", "D8"]),
    "RELAY": ((69, 34, 88, 59), ["K1"]),
    "TP":   ((47, 37, 68, 50), ["TP1", "TP2", "TP3", "TP4", "TP5", "TP6", "TP7", "TP8"]),
    "CONN": ((2, 61, 88, 69.5), ["J2", "J6", "J3", "J7", "J1", "J4"]),
}

parts = {}
for p in G.P:
    if p["ref"].startswith("#"): continue
    d = parts.setdefault(p["ref"], dict(fp=p["fp"], val=p["val"], pins={}))
    d["pins"].update(p["pins"])
allrefs = [r for _, (_, rs) in REGIONS.items() for r in rs]
assert sorted(allrefs) == sorted(parts), (set(parts) - set(allrefs), set(allrefs) - set(parts))

board = pcbnew.BOARD(); board.SetCopperLayerCount(2)
nets = {}
def net(n):
    if n not in nets:
        ni = pcbnew.NETINFO_ITEM(board, n); board.Add(ni); nets[n] = ni
    return nets[n]

def cbox(fp):
    cy = fp.GetCourtyard(pcbnew.F_CrtYd)
    return cy.BBox() if cy.OutlineCount() else fp.GetBoundingBox(False, False)

GAP = 1.0
placed = []
for name, ((x0, y0, x1, y1), refs) in REGIONS.items():
    x, y, rowh = x0, y0, 0.0
    if name == "CONN":   # spread connectors evenly along the bottom edge
        gap = ((x1 - x0) - len(refs) * 10.69) / (len(refs) - 1)
    for r in refs:
        d = parts[r]; nm = d["fp"].split(":")[1]
        fp = pcbnew.FootprintLoad(PRETTY, nm)
        fp.SetReference(r); fp.SetValue(d["val"]); fp.SetFPIDAsString("bspd:" + nm)
        board.Add(fp)
        bb = cbox(fp); w, h = pcbnew.ToMM(bb.GetWidth()), pcbnew.ToMM(bb.GetHeight())
        if x + w > x1 + 1e-6:
            x = x0; y += rowh + GAP; rowh = 0.0
        assert y + h <= y1 + 1e-6, f"region {name} overflow at {r}"
        # move courtyard top-left to (x, y)
        fp.Move(pcbnew.VECTOR2I(MM(OX + x) - bb.GetX(), MM(OY + y) - bb.GetY()))
        for pad in fp.Pads():
            n = d["pins"].get(pad.GetNumber())
            if n: pad.SetNet(net(n))
        placed.append((r, x, y, x + w, y + h))
        x += w + (gap if name == "CONN" else GAP); rowh = max(rowh, h)

# overlap + inside-board checks
errs = 0
for i in range(len(placed)):
    a = placed[i]
    if a[1] < 0 or a[2] < 0 or a[3] > W or a[4] > H: errs += 1; print("OUTSIDE", a)
    for b in placed[i + 1:]:
        if a[1] < b[3] - 1e-6 and b[1] < a[3] - 1e-6 and a[2] < b[4] - 1e-6 and b[2] < a[4] - 1e-6:
            errs += 1; print("OVERLAP", a[0], b[0])

# outline
pts = [(0, 0), (W, 0), (W, H), (0, H)]
for i in range(4):
    (ax, ay), (bx, by) = pts[i], pts[(i + 1) % 4]
    s = pcbnew.PCB_SHAPE(board); s.SetShape(pcbnew.SHAPE_T_SEGMENT)
    s.SetStart(pcbnew.VECTOR2I(MM(OX + ax), MM(OY + ay))); s.SetEnd(pcbnew.VECTOR2I(MM(OX + bx), MM(OY + by)))
    s.SetLayer(pcbnew.Edge_Cuts); s.SetWidth(MM(0.1)); board.Add(s)
# block labels on F.Fab for orientation
for name, ((x0, y0, x1, y1), _) in REGIONS.items():
    t = pcbnew.PCB_TEXT(board); t.SetText(name); t.SetLayer(pcbnew.F_Fab)
    t.SetPosition(pcbnew.VECTOR2I(MM(OX + x0 + 1), MM(OY + y1 - 0.5))); t.SetTextSize(pcbnew.VECTOR2I(MM(1), MM(1)))
    board.Add(t)

out = os.path.join(K, "bspd.kicad_pcb")
pcbnew.SaveBoard(out, board)

# verify nets after reload
b2 = pcbnew.LoadBoard(out); bad = 0; npads = 0
for fp in b2.GetFootprints():
    exp = parts[fp.GetReference()]["pins"]
    for pad in fp.Pads():
        npads += 1
        if pad.GetNetname() != exp.get(pad.GetNumber(), ""): bad += 1; print("NET", fp.GetReference(), pad.GetNumber())
print(f"placed {len(placed)} footprints on {W:.0f} x {H:.0f} mm, pads {npads}, net errors {bad}, overlap/outside errors {errs}")
sys.exit(1 if (bad or errs) else 0)
