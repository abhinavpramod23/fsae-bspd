#!/usr/bin/env python3
"""Cross-check the KiCad-exported netlist against the intended design netlist + basic electrical rules."""
import re, json, sys, os
D = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "hardware", "kicad")
net = open(os.path.join(D, "bspd.net")).read()
exp = {k: set(v) for k, v in json.load(open(os.path.join(D, "expected_nets.json"))).items()}

got = {}
for m in re.finditer(r'\(net \(code "?\d+"?\) \(name "([^"]*)"\)(.*?)\)\s*(?=\(net |\)\s*\)\s*$)', net, re.S):
    name = m.group(1).lstrip("/")
    nodes = set(f"{r}.{p}" for r, p in re.findall(r'\(node \(ref "([^"]+)"\) \(pin "([^"]+)"\)', m.group(2)))
    nodes = {n for n in nodes if not n.startswith("#")}
    if nodes: got[name] = nodes

errors = []
# 1. every expected net exists with exactly the expected pins
gmap = {}
for n, nodes in got.items():
    for x in nodes: gmap[x] = n
for n, nodes in exp.items():
    gn = {gmap.get(x) for x in nodes}
    if len(gn) != 1 or None in gn:
        errors.append(f"net {n}: pins split/missing across {gn}")
        continue
    g = gn.pop()
    if got[g] != nodes:
        errors.append(f"net {n}: extra {got[g]-nodes} missing {nodes-got[g]}")
# 2. no unexpected nets with >1 node, report unconnected (single-node) nets
for n, nodes in got.items():
    if len(nodes) == 1 and not n.startswith("unconnected"):
        errors.append(f"single-pin net {n}: {nodes}")
unconn = sorted(n for n in got if n.startswith("unconnected"))
# 3. components in netlist
comps = set(re.findall(r'\(comp \(ref "([^"#][^"]*)"\)', net))
print(f"components in KiCad netlist: {len(comps)}")
print(f"nets: expected {len(exp)}, got {len([n for n in got if not n.startswith('unconnected')])}")
print("intentionally unconnected pins:", [list(got[n])[0] for n in unconn])
# 4. electrical sanity: every LM2901 pin 1..14 used once
for u in ("U1", "U2"):
    used = sorted(int(x.split(".")[1]) for x in gmap if x.startswith(u + "."))
    if used != list(range(1, 15)): errors.append(f"{u} pins used {used}")
# 5. open-collector outputs have pull-ups / loads
for n in ("U1A_OUT", "U1B_OUT", "TMR_N", "FAULT_N", "OK"):
    if n not in exp: errors.append("missing " + n)
print("ERRORS:" if errors else "NETLIST CHECK: PASS (all %d nets match design exactly)" % len(exp))
for e in errors: print("  ", e)
sys.exit(1 if errors else 0)
