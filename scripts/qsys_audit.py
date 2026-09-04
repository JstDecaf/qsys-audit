#!/usr/bin/env python3
"""
qsys_audit.py - static audit of a Q-SYS Designer (.qsys) file.

A .qsys file is a .NET BinaryFormatter (MS-NRBF) header wrapping a gzip stream
that itself contains the full BinaryFormatter object graph of the design
(QSC.QSys.Design.Document). This tool decompresses it, walks the object graph
without touching Q-SYS Designer, builds a flat model (inventory, pages,
components, wires, scripts, UCIs, cached control values) and runs a set of
checks that flag common design-quality problems.

Usage:
    python3 qsys_audit.py DESIGN.qsys [--out DIR]

Outputs (in --out, default ./audit_<design name>/):
    model.json      flat model of the design
    scripts/        every Lua script / block-controller body, one file each
    findings.md     the audit report (also printed to stdout)
    findings.html   the same, styled (default output; see --brand)
    findings.pdf    with --pdf: printed from the HTML by headless Chrome / Chromium / Edge
    system-design.md  system design documentation (inventory, I/O map, signal flow, control, UCIs)
    system-design.html  the same, styled; --brand pxd (or a brand JSON) applies house branding
    system-design.pdf   with --pdf

Output flags: --html (default) writes the styled HTML; --pdf also prints PDFs from it. They combine.

Requires: pip install nrbf      (pure-Python MS-NRBF parser)
Optional: pip install luaparser  (enables Lua syntax checking)
"""
import argparse
import hashlib
import io
import json
import os
import re
import sys
import zlib
from collections import Counter, defaultdict

try:
    import nrbf
except ImportError:
    sys.exit("missing dependency: pip install nrbf")

sys.setrecursionlimit(200_000)

# --------------------------------------------------------------------------
# 1. Unwrap the file and parse the object graph
# --------------------------------------------------------------------------

def load_design(path):
    data = open(path, "rb").read()
    idx = data.find(b"\x1f\x8b\x08")
    if idx < 0:
        sys.exit("no gzip stream found - not a .qsys file?")
    payload = zlib.decompressobj(16 + zlib.MAX_WBITS).decompress(data[idx:])
    parser = nrbf.NRBFParser(payload)
    # parse() reads every record into parser.objects, then tries to resolve
    # references recursively and blows up on the design's cyclic parent links.
    # Run it on a thread with a large stack and keep the raw object table.
    import threading
    threading.stack_size(512 * 1024 * 1024)

    def run():
        try:
            parser.parse()
        except RecursionError:
            pass
    t = threading.Thread(target=run)
    t.start()
    t.join()
    return parser.objects


class Graph:
    """Cycle-safe navigation helpers over the raw NRBF object table."""

    def __init__(self, objects):
        self.O = objects
        self.root = next(oid for oid, v in objects.items()
                         if isinstance(v, dict) and v.get("__class__") == "QSC.QSys.Design.Document")

    def deref(self, v):
        while isinstance(v, dict) and tuple(v) == ("__ref__",):
            v = self.O[v["__ref__"]]
        return v

    @staticmethod
    def is_enum(v):
        return isinstance(v, dict) and set(v) == {"value__", "__class__"}

    def val(self, v):
        v = self.deref(v)
        if self.is_enum(v):
            return v["value__"]
        if isinstance(v, dict):
            c = v.get("__class__", "")
            if c.startswith(("System.Collections.Generic.List`1", "QSC.QSys.Design.ElementList`1",
                             "QSC.QSys.Design.StructList`1", "QSC.QSys.Design.ElementHashSet`1")):
                items = self.deref(v.get("_items", v.get("items")))
                if isinstance(items, dict):
                    items = self.val(items)
                items = items or []
                size = v.get("_size", len(items))
                return [self.deref(x) for x in items[:size] if x is not None]
        return v

    def s(self, v):
        v = self.deref(v)
        return v if isinstance(v, str) else None

    def get(self, obj, *path):
        o = self.deref(obj)
        for p in path:
            if isinstance(o, dict):
                o = self.deref(o.get(p))
            else:
                return None
        return o

    @staticmethod
    def cls(v):
        return v.get("__class__", "").split(".")[-1].split("`")[0] if isinstance(v, dict) else ""


# --------------------------------------------------------------------------
# 2. Flatten into a model
# --------------------------------------------------------------------------

def build_model(g):
    doc = g.O[g.root]
    m = {"meta": {}, "properties": {}, "inventory": [], "pages": [], "components": [],
         "wires": [], "graphics": [], "ucis": [], "snapshots": [], "external_ids": []}

    md = g.deref(doc.get("_Metadata")) or {}
    m["meta"] = {"author": g.s(md.get("_author")), "company": g.s(md.get("_company")),
                 "design_version": g.s(md.get("_version")), "designer_version": g.s(doc.get("_BuildId")),
                 "build": g.s(doc.get("_BuildNumber")), "last_disconnect_was_core": doc.get("_LastDisconnectWasCore")}
    m["properties"] = {k: g.val(v) for k, v in (g.deref(doc.get("_Properties")) or {}).items()
                       if k.startswith("_") and not isinstance(g.deref(v), (dict, list)) or g.is_enum(g.deref(v))}

    for it in g.val(doc.get("_Inventory")) or []:
        rec = {"class": g.cls(it), "name": g.s(it.get("_Name")) or g.s(it.get("Base+_Name")),
               "location": g.s(it.get("_Location")) or g.s(it.get("Base+_Location"))}
        for k, v in it.items():
            dv = g.deref(v)
            if k.startswith("_") and (not isinstance(dv, (dict, list)) or g.is_enum(dv)):
                rec[k] = g.val(v)
        m["inventory"].append(rec)

    def props(c):
        return {g.s(p.get("CodeName")): g.val(p.get("Value"))
                for p in g.val(c.get("_Properties") or c.get("Component+_Properties")) or []
                if isinstance(p, dict)}

    def cvals(c):
        out = {}
        d = g.deref(c.get("_ControlValues") or c.get("Component+_ControlValues"))
        if not isinstance(d, dict):
            return out
        for kv in g.val(d.get("KeyValuePairs")) or []:
            kv = g.deref(kv)
            if not isinstance(kv, dict):
                continue
            v = g.deref(kv.get("value"))
            if isinstance(v, dict):
                out[g.s(kv.get("key"))] = {"String": g.s(v.get("String")), "Value": g.val(v.get("Value"))}
        return out

    def pins(c):
        return [{"code": g.s(p.get("CodeName")), "pretty": g.s(p.get("PrettyName")),
                 "dir": g.val(p.get("Direction")), "domain": g.val(p.get("Domain")),
                 "wires": len(g.val(p.get("_Wires")) or []), "label": g.s(p.get("_Label"))}
                for p in g.val(c.get("PinProvider+_Pins")) or [] if isinstance(p, dict)]

    pin_owner = {}
    raw_wires = []

    def walk(canvas, path):
        title = g.s(canvas.get("_Title"))
        p = path + [title] if title else path
        for layer in g.val(canvas.get("_Layers")) or []:
            for ch in g.val(layer.get("_Children")) or []:
                if not isinstance(ch, dict):
                    continue
                c = g.cls(ch)
                if c in ("Component", "PlacedComponent"):
                    idx = len(m["components"])
                    m["components"].append({
                        "idx": idx, "kind": c, "path": "/".join(p),
                        "class": g.s(ch.get("_ClassName") or ch.get("Component+_ClassName")),
                        "code_name": g.s(ch.get("_CodeName") or ch.get("Component+_CodeName")),
                        "user_label": g.s(ch.get("_UserLabel") or ch.get("Component+_UserLabel")),
                        "props": props(ch), "controls": cvals(ch), "pins": pins(ch)})
                    for pn in g.val(ch.get("PinProvider+_Pins")) or []:
                        pin_owner[id(pn)] = ("comp", idx)
                elif c in ("Container", "ChannelGroup"):
                    idx = len(m["components"])
                    m["components"].append({
                        "idx": idx, "kind": c, "path": "/".join(p), "class": c, "code_name": None,
                        "user_label": g.s(ch.get("_Label")), "props": {}, "controls": {}, "pins": pins(ch)})
                    for pn in g.val(ch.get("PinProvider+_Pins")) or []:
                        pin_owner[id(pn)] = ("comp", idx)
                    for sub in g.val(ch.get("_Canvases")) or []:
                        walk(sub, p + [f"{c}:{g.s(ch.get('_Label'))}"])
                elif c in ("ContainerPin", "ChannelGroupPin"):
                    for pn in g.val(ch.get("PinProvider+_Pins")) or []:
                        pin_owner[id(pn)] = ("cpin", "/".join(p) + "#" + str(g.s(ch.get("_Label"))))
                elif c == "Wire":
                    raw_wires.append(ch)
                elif c != "WireBreakpoint":
                    m["graphics"].append({"class": c, "path": "/".join(p), "label": g.s(ch.get("_Label"))})

    for pg in g.val(doc.get("_Pages")) or []:
        m["pages"].append({"title": g.s(pg.get("_Title")),
                           "layers": [g.s(l.get("_Name")) for l in g.val(pg.get("_Layers")) or []]})
        walk(pg, [])

    def pin_desc(pn):
        pn = g.deref(pn)
        return {"owner": pin_owner.get(id(pn)), "code": g.s(pn.get("CodeName")),
                "pretty": g.s(pn.get("PrettyName")), "dir": g.val(pn.get("Direction")),
                "domain": g.val(pn.get("Domain"))}

    m["wires"] = [{"a": pin_desc(w["_PinA"]), "b": pin_desc(w["_PinB"])} for w in raw_wires]

    for p in g.val(doc.get("_WebPanels")) or []:
        rec = {"title": g.s(p.get("_Title")), "res": [g.val(p.get("_HorizontalResolution2")), g.val(p.get("_VerticalResolution2"))],
               "pages": []}
        origins = Counter()
        for pgp in g.val(p.get("_Pages")) or []:
            layers = []
            for l in g.val(pgp.get("_Layers")) or []:
                n = 0
                stack = [l]
                while stack:
                    node = stack.pop()
                    for ch in g.val(node.get("_Children")) or []:
                        if isinstance(ch, dict):
                            n += 1
                            if g.cls(ch) == "ControlStyle":
                                tgt = g.get(ch, "_Value", "_Link")
                                if isinstance(tgt, dict):
                                    origins[g.s(tgt.get("_OriginClassName")) or ""] += 1
                            for sub in g.val(ch.get("_Canvases")) or []:
                                stack.extend(g.val(sub.get("_Layers")) or [])
                layers.append({"name": g.s(l.get("_Name")), "controls": n})
            rec["pages"].append({"name": g.s(pgp.get("_Name")), "layers": layers})
        rec["bound_origins"] = dict(origins)
        m["ucis"].append(rec)

    for b in g.val(doc.get("_Snapshots")) or []:
        m["snapshots"].append({"class": g.cls(b), "name": g.s(b.get("_Name")), "count": g.val(b.get("_IntCount")),
                               "autoload": b.get("_EnableAutoLoad"), "autosave": b.get("_EnableAutoSave")})
    for e in g.val(doc.get("_ExternalControlIds")) or []:
        comp = g.deref(e.get("_Component"))
        cn = (g.s(comp.get("_UserLabel")) or g.s(comp.get("_CodeName"))) if isinstance(comp, dict) else None
        m["external_ids"].append({"name": g.s(e.get("_Name")), "control": g.s(e.get("_ControlId")), "id": g.s(e.get("_Id")), "component": cn})
    return m


# --------------------------------------------------------------------------
# 3. Checks
# --------------------------------------------------------------------------

SEV = {"CRITICAL": 0, "HIGH": 1, "MEDIUM": 2, "LOW": 3, "INFO": 4}
NET_IO = {"soft_dante_input", "soft_dante_output", "input_box", "output_box", "dante_input", "dante_output",
          "aes67_input", "aes67_output", "qlan_rx", "qlan_tx"}
SCRIPT_CLASSES = {"device_controller_script", "device_controller"}
TEST_CLASSES = {"ping", "command_buttons", "event_log", "pink", "white", "injector", "probe", "rta_bandpass"}


def run_checks(m, out_dir):
    F = []

    def add(sev, area, title, detail, items=None):
        F.append({"severity": sev, "area": area, "title": title, "detail": detail, "items": items or []})

    C = m["components"]
    by_name = {c["code_name"]: c for c in C if c["code_name"]}
    uci_titles = {u["title"] for u in m["ucis"]}
    name = lambda c: c["code_name"] or c["user_label"] or f"#{c['idx']}"

    # --- 3a. connectivity: wires + signal names ----------------------------
    # Q-SYS connects pins without wires when they share a signal name
    # (Pin._Label). Build the name map first, then treat a pin as connected
    # if it has a wire or its name has a partner in the other direction.
    labels = defaultdict(lambda: {"out": [], "in": []})
    for c in C:
        for p in c["pins"]:
            if p.get("label"):
                labels[p["label"]]["out" if p["dir"] == 2 else "in"].append((name(c), p["pretty"], p["domain"]))
    matched = set()
    for L, d in labels.items():
        if d["out"] and d["in"]:
            matched.update((n, pin) for n, pin, _ in d["out"] + d["in"])

    def connected(c, p):
        return p["wires"] > 0 or (name(c), p["pretty"]) in matched

    for c in C:
        if c["class"] in NET_IO:
            audio_pins = [p for p in c["pins"] if p["domain"] == 1]
            used = [p for p in audio_pins if connected(c, p)]
            if audio_pins and not used:
                add("LOW", "Audio", f"Network I/O '{name(c)}' is in the design but unused",
                    f"{c['class']} on page '{c['path']}': {len(audio_pins)} audio pins, none wired and none with a matched signal name. Spare capacity, or a device that should be removed.")

    no_src = [f"'{L}' -> " + ", ".join(f"{n}[{pin}]" for n, pin, _ in d["in"]) for L, d in labels.items() if d["in"] and not d["out"]]
    no_dst = [f"'{L}' <- " + ", ".join(f"{n}[{pin}]" for n, pin, _ in d["out"]) for L, d in labels.items() if d["out"] and not d["in"]]
    multi = [f"'{L}' has {len(d['out'])} sources: " + ", ".join(f"{n}[{pin}]" for n, pin, _ in d["out"]) for L, d in labels.items() if len(d["out"]) > 1]
    if multi:
        add("HIGH", "Signal names", "Signal names with more than one source", "Only one output may drive a named signal.", multi)
    if no_src:
        add("MEDIUM", "Signal names", "Inputs listening to a signal name that nothing drives",
            "The name was probably renamed or removed at the source. These inputs receive nothing.", sorted(no_src))
    audio_no_dst = [x for x in no_dst if any(d["out"] and d["out"][0][2] == 1 and x.startswith(f"'{L}'") for L, d in labels.items())]
    if audio_no_dst:
        add("HIGH", "Signal names", "Audio outputs published on a signal name that nothing listens to",
            "This audio goes nowhere.", sorted(audio_no_dst))
    ctl_no_dst = sorted(set(no_dst) - set(audio_no_dst))
    if ctl_no_dst:
        add("LOW", "Signal names", "Control signal names with no destination",
            "Harmless if the name only exists for a UCI, otherwise a leftover.", ctl_no_dst)

    unfed, dead = [], []
    for c in C:
        if c["kind"] in ("Container", "ChannelGroup") or c["class"] in NET_IO:
            continue
        ins = [p for p in c["pins"] if p["domain"] == 1 and p["dir"] == 1]
        outs = [p for p in c["pins"] if p["domain"] == 1 and p["dir"] == 2]
        if ins and not any(connected(c, p) for p in ins):
            unfed.append(f"{name(c)} ({c['class']})")
        if outs and not any(connected(c, p) for p in outs) and c["class"] not in ("meter2", "meter", "rta_bandpass", "probe", "audio_file_recorder2"):
            dead.append(f"{name(c)} ({c['class']})")
    if dead:
        add("HIGH", "Audio", "Blocks whose audio output goes nowhere", "No wire and no matched signal name on any audio output.", dead)
    if unfed:
        add("HIGH", "Audio", "Blocks with no audio input connected", "No wire and no matched signal name on any audio input.", unfed)

    # Dante subscriptions that are unresolved on channels the design does not use
    for c in C:
        st = c["controls"].get("input_status", {}).get("String") or ""
        chans = re.findall(r"Channel (\d+) Unresolved", st)
        if chans:
            unused = [ch for ch in chans if not any(p["pretty"] == f"Channel {ch}" and connected(c, p) for p in c["pins"])]
            if unused:
                add("HIGH", "Dante", f"'{name(c)}' reports unresolved subscriptions on channels the design does not use",
                    "Channels " + ", ".join(unused) + " are subscribed to a missing transmitter but feed nothing. "
                    "Clear the subscriptions to remove the nuisance Compromised state.")

    # --- 3b. cached runtime status --------------------------------------
    bad_status = []
    for c in C:
        st = c["controls"].get("script_status", {}).get("String")
        err = c["controls"].get("script_error_count", {}).get("String")
        if st and st != "OK":
            bad_status.append(f"{name(c)}: {st} (errors={err})")
    if bad_status:
        add("CRITICAL", "Scripts", "Scripts saved in a non-OK state",
            "Script status and error counts are cached in the file at the moment it was last saved while connected.", bad_status)
    dev_status = []
    for c in C:
        st = c["controls"].get("status", {}).get("String") or c["controls"].get("input_status", {}).get("String")
        if st and not st.startswith(("OK", "Initializing", "Not Present - No clients", "Cannot send e-mail while emulating")):
            dev_status.append(f"{name(c)} ({c['class']}): {st}")
    if dev_status:
        add("HIGH", "Devices", "Devices / status combiners saved in a non-OK state",
            "Last-known status values cached in the design file.", dev_status)

    # --- 3c. scripts -----------------------------------------------------
    os.makedirs(os.path.join(out_dir, "scripts"), exist_ok=True)
    bodies = defaultdict(list)
    scripts = {}
    for c in C:
        code = c["controls"].get("code", {}).get("String")
        if not code:
            continue
        h = hashlib.md5(code.encode()).hexdigest()[:8]
        bodies[h].append(name(c))
        scripts[name(c)] = code
        fn = re.sub(r"[^A-Za-z0-9_.-]", "_", f"{c['idx']:03d}_{name(c)}_{h}.lua")
        open(os.path.join(out_dir, "scripts", fn), "w").write(code)
    dups = [f"{len(v)} copies: {', '.join(v)}" for v in bodies.values() if len(v) > 1]
    if dups:
        add("HIGH", "Scripts", "Duplicated script bodies",
            "Identical (or near-identical) scripts pasted into several components. Every fix has to be applied to each copy and copies drift.", dups)

    try:
        from luaparser import ast as lua_ast
    except ImportError:
        lua_ast = None
    syntax, typos, logerr, prints, missing_targets, bad_uci = [], [], [], [], [], []
    for n, code in scripts.items():
        if lua_ast:
            try:
                lua_ast.parse(code)
            except Exception as e:
                syntax.append(f"{n}: {str(e).splitlines()[0][:120]}")
        for mm in re.finditer(r"\b(flase|ture|nill|fasle|treu)\b", code):
            typos.append(f"{n}: '{mm.group(1)}' at line {code[:mm.start()].count(chr(10)) + 1}")
        if re.search(r"Log\.Error\(\s*['\"]", code):
            logerr.append(n)
        np_ = len(re.findall(r"^\s*print\s*\(", code, re.M))
        if np_ >= 5:
            prints.append(f"{n}: {np_} print() calls")
        for mm in re.finditer(r"Component\.New\(\s*['\"]([^'\"]+)['\"]", code):
            t = mm.group(1)
            if t not in by_name:
                missing_targets.append(f"{n} -> '{t}'")
            elif by_name[t]["props"].get("ScriptAccess") in (None, 0) and by_name[t]["class"] not in SCRIPT_CLASSES:
                missing_targets.append(f"{n} -> '{t}' (Script Access not set)")
        for mm in re.finditer(r"Uci\.SetLayerVisibility\(\s*['\"]([^'\"]+)['\"]", code):
            if mm.group(1) not in uci_titles:
                bad_uci.append(f"{n}: UCI '{mm.group(1)}' does not exist")
    for c in C:
        for k in ("UCI", "UCI Name"):
            v = c["controls"].get(k, {}).get("String")
            fed = any(p["pretty"] == k and p["dir"] == 1 and connected(c, p) for p in c["pins"])
            if v and v not in uci_titles and c["class"] in SCRIPT_CLASSES and not fed:
                bad_uci.append(f"{name(c)}: control '{k}' = '{v}' but no UCI has that name")
    # text controls that feed UCI-name pins by signal name
    for c in C:
        if c["class"] == "custom_controls":
            for k, v in c["controls"].items():
                sv = v.get("String")
                if sv and re.search(r"\bUCI\b", c["code_name"] or "", re.I) and sv not in uci_titles:
                    bad_uci.append(f"{name(c)}: '{k}' = '{sv}' feeds scripts by signal name but no UCI has that name")
    if syntax:
        add("CRITICAL", "Scripts", "Lua syntax errors", "These scripts will not compile on the core.", syntax)
    if missing_targets:
        add("CRITICAL", "Scripts", "Component.New() targets that do not exist or lack Script Access",
            "The script will fault on the first access.", missing_targets)
    if bad_uci:
        add("HIGH", "UCI", "Scripts target a UCI name that is not in the design",
            "Uci.SetLayerVisibility on an unknown UCI raises 'uci, page or layer not found'.", sorted(set(bad_uci)))
    if typos:
        add("HIGH", "Scripts", "Misspelled Lua keywords / literals (evaluate to nil)", "", typos)
    if logerr:
        add("MEDIUM", "Scripts", "Log.Error() used for routine messages",
            "Fills the core error log with non-errors and hides real faults.", logerr)
    if prints:
        add("LOW", "Scripts", "Heavy print() usage", "Debug output left enabled costs CPU and clutters the log.", prints)

    # --- 3d. addressing -------------------------------------------------
    ips = []
    for c in C:
        for k, v in c["controls"].items():
            sv = str(v.get("String") or "")
            for ip in re.findall(r"\b(\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3})\b", sv):
                if not ip.startswith(("239.", "233.", "224.", "0.", "255.")):
                    ips.append((ip, name(c), k))
    subnets = Counter(ip.rsplit(".", 1)[0] for ip, _, _ in ips)
    if len(subnets) > 1:
        main = subnets.most_common(1)[0][0]
        odd = [f"{ip} in {n} [{k}]" for ip, n, k in ips if not ip.startswith(main + ".")]
        add("HIGH", "Network", f"Addresses outside the dominant subnet ({main}.x)",
            "Usually a factory default or an address pasted from another job.", odd)

    # --- 3e. leftovers & housekeeping ------------------------------------
    for c in C:
        if c["class"] == "email":
            cv = c["controls"]
            if cv.get("password", {}).get("String"):
                add("MEDIUM", "Security", f"Email component '{name(c)}' stores an SMTP password in the design file", "")
            msg = cv.get("message", {}).get("String") or ""
            if re.search(r"test|i am a message", msg, re.I) or re.search(r"test", cv.get("subject", {}).get("String") or "", re.I):
                add("LOW", "Housekeeping", f"Email component '{name(c)}' still carries test content",
                    f"subject='{cv.get('subject', {}).get('String')}', message='{msg[:60]}'")
    leftovers = [f"{name(c)} ({c['class']})" for c in C if c["class"] in TEST_CLASSES]
    if leftovers:
        add("LOW", "Housekeeping", "Test / diagnostic components left in the design", "", leftovers)
    unnamed = [f"{c['class']} '{name(c)}' on {c['path']}" for c in C
               if c["kind"] == "Component" and not c["user_label"] and c["class"] not in SCRIPT_CLASSES | TEST_CLASSES | NET_IO
               and c["class"] not in ("date_time", "meter2", "core_status", "touch_screen_status", "uci_viewer", "status_combiner",
                                      "snapshot_controller", "uci_layer_controller", "control_logic", "audio_file_recorder2",
                                      "page_station_zone_select", "audio_file_player", "router_with_output", "custom_controls")
               and re.match(r"^[A-Za-z_/-]+(_\d+)?$", c["code_name"] or "")]
    if unnamed:
        add("MEDIUM", "Naming", "Signal-processing blocks left with default names", "", unnamed)
    blank = [f"{c['kind']} on {c['path']}" for c in C if c["kind"] in ("Container", "ChannelGroup") and not c["user_label"]]
    if blank:
        add("LOW", "Naming", "Containers / channel groups without a label", "", blank)
    tmpl = [f"{name(c)}: {k} = '{v['String']}'" for c in C if c["class"] == "status_combiner"
            for k, v in c["controls"].items() if k.startswith("label_") and re.fullmatch(r"\d+:?", str(v.get("String") or ""))]
    if tmpl:
        add("LOW", "Naming", "Status combiner inputs with placeholder labels", "", tmpl)

    # --- 3f. metadata ----------------------------------------------------
    meta = m["meta"]
    if not meta.get("company") or meta.get("design_version") in (None, "0.0.0"):
        add("LOW", "Housekeeping", "Design metadata not filled in",
            f"author='{meta.get('author')}', company='{meta.get('company')}', design version='{meta.get('design_version')}'")

    F.sort(key=lambda f: SEV[f["severity"]])
    return F


# --------------------------------------------------------------------------
# 4. Report
# --------------------------------------------------------------------------

def render(m, F, path):
    L = [f"# Q-SYS design audit: {os.path.basename(path)}", ""]
    meta = m["meta"]
    L += [f"Designer {meta.get('designer_version')} build {meta.get('build')}; author '{meta.get('author')}'.",
          f"{len(m['inventory'])} inventory items, {len(m['pages'])} pages, "
          f"{sum(1 for c in m['components'] if c['kind'] in ('Component', 'PlacedComponent'))} components, "
          f"{len(m['wires'])} wires, {len(m['ucis'])} UCIs.", ""]
    L.append("## Findings")
    counts = Counter(f["severity"] for f in F)
    L.append(", ".join(f"{k}: {counts.get(k, 0)}" for k in SEV))
    L.append("")
    for f in F:
        L.append(f"### [{f['severity']}] {f['area']} - {f['title']}")
        if f["detail"]:
            L.append(f["detail"])
        for it in f["items"]:
            L.append(f"- {it}")
        L.append("")
    L.append("## Inventory")
    for i in m["inventory"]:
        L.append(f"- {i['class']}: {i['name']} ({i['location']})")
    L.append("")
    L.append("## UCIs")
    for u in m["ucis"]:
        L.append(f"- {u['title']} {u['res']}: " + ", ".join(f"{pg['name']} [{sum(l['controls'] for l in pg['layers'])} controls]" for pg in u["pages"]))
    return "\n".join(L)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("design")
    ap.add_argument("--out")
    ap.add_argument("--title", help="site / system name for the documentation heading")
    ap.add_argument("--no-docs", action="store_true", help="skip system design documentation")
    ap.add_argument("--brand", help="brand for the HTML/PDF outputs: a name from brands/ (e.g. pxd) or a path to a brand JSON")
    ap.add_argument("--html", action="store_true", help="write styled HTML report and documentation (default when no output flag is given)")
    ap.add_argument("--pdf", action="store_true", help="also print the HTML report and documentation to PDF with headless Chrome / Chromium / Edge")
    a = ap.parse_args()
    out = a.out or f"audit_{os.path.splitext(os.path.basename(a.design))[0]}"
    os.makedirs(out, exist_ok=True)
    objects = load_design(a.design)
    m = build_model(Graph(objects))
    json.dump(m, open(os.path.join(out, "model.json"), "w"), indent=1, default=str)
    F = run_checks(m, out)
    report = render(m, F, a.design)
    open(os.path.join(out, "findings.md"), "w").write(report)
    print(report)
    want_html = a.html or not a.pdf      # HTML is the default output; --pdf prints from it
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    import qsys_docs
    brand = qsys_docs.load_brand(a.brand)
    src = os.path.basename(a.design)
    title = a.title or os.path.splitext(src)[0]
    written = []
    if want_html or a.pdf:
        open(os.path.join(out, "findings.html"), "w").write(qsys_docs.build_audit_html(m, F, brand, title, src))
        written.append("findings.html")
    if not a.no_docs:
        doc = qsys_docs.build_docs(m, src, a.title, brand)
        open(os.path.join(out, "system-design.md"), "w").write(doc)
        written.append("system-design.md")
        if want_html or a.pdf:
            open(os.path.join(out, "system-design.html"), "w").write(qsys_docs.build_html(doc, brand, title, src, m["meta"]))
            written.append("system-design.html")
    if a.pdf:
        for base in (["findings"] + ([] if a.no_docs else ["system-design"])):
            ok, msg = qsys_docs.html_to_pdf(os.path.join(out, base + ".html"), os.path.join(out, base + ".pdf"))
            if ok:
                written.append(base + ".pdf")
            else:
                print(f"PDF for {base} failed: {msg}", file=sys.stderr)
    print("\nWritten to " + out + ": " + ", ".join(["findings.md"] + written))


if __name__ == "__main__":
    main()
