#!/usr/bin/env python3
"""
qsys_docs.py - generate system design documentation from a qsys_audit model.

Usage:
    python3 qsys_docs.py MODEL.json [--out system-design.md] [--title "Site name"]

Normally called by qsys_audit.py after it has built model.json; can also be run
on its own against an existing model. Produces a Markdown document with
Mermaid signal-flow diagrams that renders in GitHub, Obsidian, VS Code, and
most wikis.
"""
import argparse
import datetime
import json
import os
import re
from collections import Counter, defaultdict

NET_RX = {"soft_dante_input", "dante_input", "input_box", "aes67_input", "qlan_rx"}
NET_TX = {"soft_dante_output", "dante_output", "output_box", "aes67_output", "qlan_tx"}
SCRIPT_CLASSES = {"device_controller_script", "device_controller"}
METERS = {"meter2", "meter", "rta_bandpass", "probe", "injector"}
CLASS_NAMES = {
    "device_controller_script": "Text Controller", "device_controller": "Block Controller",
    "soft_dante_input": "Software Dante RX", "soft_dante_output": "Software Dante TX",
    "input_box": "Network input", "output_box": "Network output", "mixer": "Mixer",
    "gain": "Gain", "audio_file_player": "Audio Player", "page_system": "PA Router",
    "page_station_zone_select": "Virtual Page Station", "status_combiner": "Status Combiner",
    "custom_controls": "Custom Controls", "selector": "Selector", "snapshot_controller": "Snapshot Controller",
    "date_time": "Date/Time", "equalizer_parametric": "Parametric EQ", "filter_highpass": "High-pass",
    "filter_lowpass": "Low-pass", "delay": "Delay", "compressor": "Compressor", "gate": "Gate",
    "router_with_output": "Router", "touch_screen_status": "Touch Screen Status", "core_status": "Core Status",
    "uci_viewer": "UCI Viewer", "uci_layer_controller": "UCI Layer Controller", "press_n_hold": "Press and Hold",
    "blinker": "Blinker", "email": "E-mailer", "ping": "Ping", "system_mute": "System Mute",
    "control_logic": "Control Logic", "flip_flop": "Flip-Flop", "control_delay": "Control Delay",
    "audio_file_recorder2": "Audio Recorder", "event_log": "Event Log", "command_buttons": "Command Buttons",
    "pink": "Pink Noise", "white": "White Noise", "meter2": "Meter",
}
INV_NAMES = {"Core": "Core", "TouchScreenController": "Touch screen", "UciViewer": "UCI viewer",
             "SoftDanteInput": "Software Dante RX", "SoftDanteOutput": "Software Dante TX",
             "Aes67Receiver": "AES67 receiver", "Aes67Transmitter": "AES67 transmitter"}


DIAGRAM = {
    "dante": ("#F39C12", "#7E4E00"), "aes67": ("#2E86C1", "#0B3D62"), "physical": ("#27AE60", "#0E4D2A"),
    "processing": ("#F2F2F2", "#333333"), "source": ("#D6EAF8", "#1B4F72"), "script": ("#8E44AD", "#FFFFFF"),
    "control": ("#E8DAEF", "#4A235A"), "audio_edge": "#4D4D4D", "control_edge": "#8E44AD", "script_edge": "#8E44AD",
}
PHYSICAL_IO = {"mic_line_input", "line_output", "flex_input", "flex_output", "core_mic_line_input", "core_line_output",
               "hd_audio_mic_line_in", "hd_audio_line_out", "usb_audio_in", "usb_audio_out", "aes_input", "aes_output",
               "analog_input", "analog_output", "gpio_input", "gpio_output"}
CONTROL_COMPS = {"selector", "custom_controls", "snapshot_controller", "press_n_hold", "blinker", "control_logic",
                 "flip_flop", "control_delay", "uci_layer_controller", "system_mute", "page_station_zone_select"}
DIAGRAM_SKIP = {"status_combiner", "meter2", "meter", "rta_bandpass", "probe", "injector", "touch_screen_status",
                "core_status", "uci_viewer", "date_time", "email", "ping", "event_log", "command_buttons"}


def category(c, inventory):
    cls = c["class"] or ""
    if cls in ("soft_dante_input", "soft_dante_output", "dante_input", "dante_output"):
        return "dante"
    if cls in ("input_box", "output_box"):
        for i in inventory:
            if i["name"] and i["name"] in (c["code_name"] or ""):
                if i["class"].startswith("Aes67") or "Qlan" in i["class"]:
                    return "aes67"
        return "physical"
    if cls in PHYSICAL_IO or cls.startswith(("core_", "flex_", "mic_line")):
        return "physical"
    if cls in SCRIPT_CLASSES or cls.startswith("%PLUGIN%"):
        return "script"
    if cls in CONTROL_COMPS:
        return "control"
    if cls in ("audio_file_player", "pink", "white", "sine", "tone", "router_with_output"):
        return "source"
    return "processing"


def pretty_class(c):
    if not c:
        return ""
    if c.startswith("%PLUGIN%"):
        return "Plugin"
    return CLASS_NAMES.get(c, c.replace("_", " ").title())


def cname(c):
    if c.get("code_name"):
        return c["code_name"]
    if c.get("user_label"):
        return c["user_label"]
    if c.get("kind") in ("Container", "ChannelGroup"):
        return f"(unnamed {c['kind'].lower()} on {c['path']})"
    return f"#{c['idx']}"


def plugin_info(c):
    src = c["props"].get("plugin_source")
    if not isinstance(src, str):
        return {}
    mm = re.search(r"PluginInfo\s*=\s*\{(.*?)\n\}", src, re.S)
    if not mm:
        return {}
    out = {}
    for k in ("Name", "Version", "Author", "Description"):
        m2 = re.search(k + r'\s*=\s*"([^"]*)"', mm.group(1))
        if m2:
            out[k] = m2.group(1)
    return out


def md_table(headers, rows):
    if not rows:
        return "_none_\n"
    esc = lambda x: str(x if x is not None else "").replace("|", "\\|").replace("\n", " ")
    out = ["| " + " | ".join(headers) + " |", "|" + "|".join("---" for _ in headers) + "|"]
    out += ["| " + " | ".join(esc(x) for x in r) + " |" for r in rows]
    return "\n".join(out) + "\n"


def script_purpose(code):
    """First meaningful comment block, for the scripts table."""
    lines = []
    for ln in code.splitlines()[:40]:
        t = ln.strip()
        if t.startswith("--[[") or t.startswith("]]") or t in ("--", "--]]"):
            continue
        if t.startswith("--"):
            t = t.strip("- ").strip()
            if t and not t.startswith(("QSYS Initialization", "BEGIN", "END", "Available", "Set Up")):
                lines.append(t)
        elif lines:
            break
    text = re.sub(r"-{3,}", "", " ".join(lines)).strip()
    return text[:72] + ("..." if len(text) > 72 else "")


def build_docs(m, source_name, title=None, brand=None):
    if brand and brand.get("diagram"):
        m["_brand_diagram"] = brand["diagram"]
    C = m["components"]
    by_name = {cname(c): c for c in C}
    by_idx = {c["idx"]: c for c in C}
    scripts = {cname(c): c["controls"]["code"]["String"] for c in C if c["controls"].get("code", {}).get("String")}

    # ---- signal names -----------------------------------------------------
    labels = defaultdict(lambda: {"out": [], "in": []})
    for c in C:
        for p in c["pins"]:
            if p.get("label"):
                labels[p["label"]]["out" if p["dir"] == 2 else "in"].append((cname(c), p["pretty"], p["domain"], c))

    # ---- addresses ---------------------------------------------------------
    addresses = []
    core_ip = None
    for c in C:
        for k, v in c["controls"].items():
            sv = str(v.get("String") or "")
            for ip in re.findall(r"\b(\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3})\b", sv):
                if ip.startswith(("239.", "233.", "224.", "0.", "255.")):
                    continue
                if "rtsp://" in sv and core_ip is None:
                    core_ip = ip
                    continue
                if c["class"] in ("touch_screen_status", "core_status") or k == "message":
                    continue
                addresses.append((cname(c), pretty_class(c["class"]), k, ip))
    for g in m["graphics"]:
        mm = re.search(r"Core IP Address:\s*(\d+\.\d+\.\d+\.\d+)", g.get("label") or "")
        if mm:
            core_ip = mm.group(1)

    D = []
    name = title or re.sub(r"\.qsys$", "", source_name)
    meta = m["meta"]
    D.append(f"# {name}\n")
    D.append(f"System design documentation, generated {datetime.date.today().isoformat()} from `{source_name}` "
             f"(Q-SYS Designer {meta.get('designer_version')} build {meta.get('build')}, author {meta.get('author') or 'unknown'}).\n")
    D.append("> Generated from the design file itself. Everything here describes the design as saved; "
             "device status values are the last ones the core reported before the save.\n")

    # ---- 1. overview -------------------------------------------------------
    cores = [i for i in m["inventory"] if i["class"] == "Core"]
    core = cores[0] if cores else {}
    n_comp = sum(1 for c in C if c["kind"] in ("Component", "PlacedComponent"))
    D.append("## 1. System overview\n")
    D.append(f"- Core: **{core.get('name', 'n/a')}**" + (f" at `{core_ip}`" if core_ip else "") +
             (f", redundant with {core.get('_BackupName')}" if core.get("_IsRedundant") else "") + "\n"
             f"- Inventory: {len(m['inventory'])} devices\n"
             f"- Schematic: {len(m['pages'])} pages, {n_comp} components, {len(m['wires'])} wires, {len(labels)} signal names\n"
             f"- Control: {len(scripts)} scripts, {len(m['ucis'])} user control interfaces, {len(m['snapshots'])} snapshot banks\n")
    D.append("Pages:\n")
    for pg in m["pages"]:
        conts = [cname(c) for c in C if c["kind"] in ("Container", "ChannelGroup") and c["path"] == pg["title"] and c["user_label"]]
        ncomp = sum(1 for c in C if c["kind"] in ("Component", "PlacedComponent") and c["path"].startswith(pg["title"]))
        D.append(f"- **{pg['title']}** - {ncomp} components" + (f"; containers: {', '.join(conts)}" if conts else ""))
    D.append("")

    # ---- 2. inventory ------------------------------------------------------
    D.append("## 2. Inventory\n")
    rows = []
    status_by_inv = {}
    for c in C:
        if c["class"] in ("touch_screen_status", "core_status") or c["class"] in NET_RX | NET_TX:
            cv = c["controls"]
            st = cv.get("status", {}).get("String") or cv.get("input_status", {}).get("String")
            ip = cv.get("lan_a_address", {}).get("String")
            for i in m["inventory"]:
                if i["name"] and i["name"] in (c["code_name"] or ""):
                    status_by_inv[i["name"]] = (st, ip)
    model_by_inv = {}
    for c in C:
        if c["class"] == "touch_screen_status":
            mm = re.match(r"Status/Control_(.+?)_(TSC-[\w-]+)$", c["code_name"] or "")
            if mm:
                model_by_inv[mm.group(1)] = mm.group(2)
    for i in m["inventory"]:
        detail = []
        if i["name"] in model_by_inv:
            detail.append(model_by_inv[i["name"]])
        if i.get("_ChannelCount"):
            detail.append(f"{i['_ChannelCount']} ch")
        if i.get("_Latency"):
            detail.append(f"{int(i['_Latency']) / 1000:g} ms latency")
        if i.get("_IsRedundant"):
            detail.append(f"redundant with {i.get('_BackupName')}")
        st, ip = status_by_inv.get(i["name"], (None, None))
        if i["class"] == "Core" and core_ip:
            ip = core_ip
        rows.append((i["name"], INV_NAMES.get(i["class"], i["class"]), i["location"], ip or "", ", ".join(detail), st or ""))
    D.append(md_table(["Device", "Type", "Location", "Address", "Detail", "Last status"], rows))
    ext = [(n, cls, k, ip) for n, cls, k, ip in addresses]
    if ext:
        D.append("Third-party devices addressed from scripts and plugins:\n")
        D.append(md_table(["Component", "Type", "Control", "Address"], sorted(set(ext))))

    # ---- 3. network --------------------------------------------------------
    P = m["properties"]
    D.append("## 3. Network and clocking\n")
    D.append(md_table(["Setting", "Value"], [
        ("PTP domain", P.get("_PtpUserDomain")), ("PTP priority 1 / 2", f"{P.get('_PtpPriority1')} / {P.get('_PtpPriority2')}"),
        ("QoS preset", P.get("_QosPreset")), ("DSCP PTP / Q-LAN / camera", f"{P.get('_PtpDscpValue')} / {P.get('_QlanDscpValue')} / {P.get('_CameraDscpValue')}"),
        ("MTU", P.get("_MtuValue")), ("Software Dante interface / latency", f"{P.get('_SoftwareDanteInterface')} / {P.get('_SoftwareDanteLatency')} us"),
        ("Sample rate", core.get("_ClockFrequency")),
    ]))
    mcast = []
    for c in C:
        for k, v in c["controls"].items():
            sv = str(v.get("String") or "")
            if re.search(r"\b2(2[4-9]|3\d)\.\d+\.\d+\.\d+", sv):
                mcast.append((cname(c), k, sv))
    if mcast:
        D.append("Multicast streams:\n")
        D.append(md_table(["Component", "Control", "Address"], mcast))

    # ---- 4. audio I/O map ---------------------------------------------------
    D.append("## 4. Audio I/O map\n")
    D.append("How every network audio channel enters and leaves the design. Connections are by signal name unless a wire is noted.\n")
    for c in C:
        if c["class"] not in NET_RX:
            continue
        cv = c["controls"]
        rows = []
        for p in sorted([p for p in c["pins"] if p["domain"] == 1 and p["dir"] == 2],
                        key=lambda p: int(re.sub(r"\D", "", p["pretty"]) or 0)):
            n = re.sub(r"\D", "", p["pretty"])
            sub = cv.get(f"channel_{n}_subscription_device", {}).get("String")
            subch = cv.get(f"channel_{n}_subscription_channel", {}).get("String")
            dests = [f"{d[0]} [{d[1]}]" for d in labels[p["label"]]["in"]] if p.get("label") else []
            if not (p.get("label") or p["wires"] or sub):
                continue
            rows.append((p["pretty"], p.get("label") or ("wired" if p["wires"] else ""),
                         f"{sub} / {subch}" if sub else "", ", ".join(dests) or ("-" if not p["wires"] else "wired")))
        D.append(f"### Inputs from {cname(c)}\n")
        D.append(md_table(["Channel", "Signal name", "Subscribed to", "Feeds"], rows))
    for c in C:
        if c["class"] not in NET_TX:
            continue
        cv = c["controls"]
        rows = []
        for p in sorted([p for p in c["pins"] if p["domain"] == 1 and p["dir"] == 1],
                        key=lambda p: int(re.sub(r"\D", "", p["pretty"]) or 0)):
            n = re.sub(r"\D", "", p["pretty"])
            lab = cv.get(f"channel_{n}_label", {}).get("String")
            srcs = [f"{d[0]} [{d[1]}]" for d in labels[p["label"]]["out"]] if p.get("label") else []
            default_label = bool(lab) and bool(re.fullmatch(r"\d+\s+" + re.escape(cname(c).split("_")[-1]) + r".*", lab))
            if not (p.get("label") or p["wires"] or (lab and not default_label)):
                continue
            rows.append((p["pretty"], "" if default_label else (lab or ""), p.get("label") or ("wired" if p["wires"] else ""), ", ".join(srcs) or "-"))
        D.append(f"### Outputs to {cname(c)}\n")
        D.append(md_table(["Channel", "Channel label", "Signal name", "Source"], rows))

    # ---- 5. signal flow diagrams -------------------------------------------
    pal = dict(DIAGRAM); pal.update((brand_diagram := (m.get("_brand_diagram") or {})))
    D.append("## 5. Signal flow\n")
    D.append("One diagram per page. Solid lines are audio, dashed lines are control wires and control signal names, "
             "dotted lines are scripts driving components by name. Edge labels are signal names. "
             "Meters, status combiners and clocks are left out to keep the picture readable.\n")
    D.append("LEGEND: dante=Dante|aes67=AES67 / Q-LAN|physical=Physical I/O|source=Players and generators|processing=Processing|control=Control components|script=Scripts and plugins\n")
    cpin_edges = defaultdict(list)   # (container path, boundary label, pin pretty) -> inner endpoints
    for w in m["wires"]:
        for side, other in (("a", "b"), ("b", "a")):
            o = w[side]["owner"]
            if o and o[0] == "cpin":
                path, lab = o[1].rsplit("#", 1)
                oo = w[other]["owner"]
                if oo and oo[0] == "comp":
                    cpin_edges[(path, lab, w[side]["pretty"])].append((w[side]["dir"], oo[1], w[other]["pretty"]))

    def container_path(c):
        return f"{c['path']}/{c['kind']}:{c['user_label']}" + ("/Schematic" if c["kind"] == "Container" else "")

    def resolve(idx, pin, direction):
        """Map a container/channel-group boundary pin to the inner components behind it."""
        c = by_idx[idx]
        if c["kind"] not in ("Container", "ChannelGroup"):
            return [(idx, pin)]
        mm = re.match(r"(?:Channel (\d+) )?(.*?)(?:\s(\d+))?$", pin)
        chan, base, num = mm.group(1), mm.group(2), mm.group(3)
        if c["kind"] == "ChannelGroup":
            lab = "1"
            inner_pin = f"{base} {num}" if num else base
        else:
            lab = num or "1"
            inner_pin = pin
        out = []
        for (path, l, pp), ends in cpin_edges.items():
            if path.startswith(container_path(c).split("/Schematic")[0]) and l == lab and pp == inner_pin:
                for d, oidx, opin in ends:
                    if (direction == "in" and d == 2) or (direction == "out" and d == 1):
                        out.append((oidx, opin))
        return out or [(idx, pin)]

    def node_id(idx):
        return f"n{idx}"

    script_targets = {}
    for n, code in scripts.items():
        t = set(re.findall(r"Component\.New\(\s*['\"]([^'\"]+)['\"]", code)) | set(re.findall(r"Mixer\.New\(\s*['\"]([^'\"]+)['\"]", code))
        script_targets[n] = [by_name[x]["idx"] for x in t if x in by_name]

    for pg in m["pages"]:
        comps = [c for c in C if c["path"].startswith(pg["title"])]
        page_idx = {c["idx"] for c in comps if c["class"] not in DIAGRAM_SKIP and c["kind"] not in ("Container", "ChannelGroup")}
        net_idx = {c["idx"] for c in C if c["class"] in NET_RX | NET_TX}
        edges = {}   # (a, b, label, kind) -> None

        def add_edge(a, ap, b, bp, lab, kind):
            for ai, _ in resolve(a, ap, "out"):
                for bi, _ in resolve(b, bp, "in"):
                    if ai == bi or by_idx[ai]["kind"] in ("Container", "ChannelGroup") or by_idx[bi]["kind"] in ("Container", "ChannelGroup"):
                        continue
                    if by_idx[ai]["class"] in DIAGRAM_SKIP or by_idx[bi]["class"] in DIAGRAM_SKIP:
                        continue
                    if (ai in page_idx or bi in page_idx) and (ai in page_idx | net_idx) and (bi in page_idx | net_idx):
                        edges[(ai, bi, lab, kind)] = None
        for w in m["wires"]:
            if w["a"]["domain"] not in (1, 2):
                continue
            src, dst = (w["a"], w["b"]) if w["a"]["dir"] == 2 else (w["b"], w["a"])
            if src["owner"] and dst["owner"] and src["owner"][0] == "comp" and dst["owner"][0] == "comp":
                add_edge(src["owner"][1], src["pretty"], dst["owner"][1], dst["pretty"], "", "audio" if w["a"]["domain"] == 1 else "control")
        for L, d in labels.items():
            for _, sp, dom, sc in d["out"]:
                if dom not in (1, 2):
                    continue
                for _, dp, _, dc in d["in"]:
                    add_edge(sc["idx"], sp, dc["idx"], dp, L, "audio" if dom == 1 else "control")
        for n, targets in script_targets.items():
            si = by_name[n]["idx"]
            for ti in targets:
                if (si in page_idx or ti in page_idx) and by_idx[ti]["class"] not in DIAGRAM_SKIP:
                    edges[(si, ti, "", "script")] = None
        used = {e[0] for e in edges} | {e[1] for e in edges}
        if not used:
            continue
        D.append(f"### {pg['title']}\n")
        D.append("```mermaid\nflowchart LR")
        groups = defaultdict(list)
        for i in used:
            c = by_idx[i]
            if not c["path"].startswith(pg["title"]):
                groups["__other__"].append(i)
            else:
                groups[c["path"] if c["path"] != pg["title"] else ""].append(i)
        cats = {}
        for gpath, idxs in sorted(groups.items()):
            if gpath == "__other__":
                D.append('  subgraph g_other["Other pages"]')
            elif gpath:
                glabel = re.sub(r"^(Container|ChannelGroup):", "", gpath.replace("/Schematic", "").split("/")[-1])
                D.append(f'  subgraph g_{re.sub(r"[^A-Za-z0-9]", "_", gpath)}["{glabel}"]')
            for i in sorted(idxs):
                c = by_idx[i]
                cat = category(c, m["inventory"])
                cats[i] = cat
                lab = (c["user_label"] or cname(c)).replace('"', "'")
                if c["class"] in NET_RX | NET_TX:
                    lab = (c["code_name"] or "").split("_")[-1]
                cls_ = pretty_class(c["class"])
                text = f"{lab}<br/><i>{cls_}</i>" if cls_.lower() not in lab.lower() else lab
                shape = ('[("', '")]') if cat in ("dante", "aes67", "physical") else (('(["', '"])') if cat == "script" else ('["', '"]'))
                D.append(f"  {node_id(i)}{shape[0]}{text}{shape[1]}")
            if gpath:
                D.append("  end")
        kinds = defaultdict(list)
        n = 0
        for (a, b, lab, kind) in edges:
            if a not in used or b not in used:
                continue
            if kind == "script":
                D.append(f"  {node_id(a)} -.-> {node_id(b)}")
            elif kind == "control":
                D.append(f'  {node_id(a)} -- "{lab}" --> {node_id(b)}' if lab else f"  {node_id(a)} --> {node_id(b)}")
            else:
                D.append(f'  {node_id(a)} -- "{lab}" --> {node_id(b)}' if lab else f"  {node_id(a)} --> {node_id(b)}")
            kinds[kind].append(n); n += 1
        if kinds["audio"]:
            D.append(f"  linkStyle {','.join(map(str, kinds['audio']))} stroke:{pal['audio_edge']},stroke-width:2px")
        if kinds["control"]:
            D.append(f"  linkStyle {','.join(map(str, kinds['control']))} stroke:{pal['control_edge']},stroke-width:1.5px,stroke-dasharray:6 4")
        if kinds["script"]:
            D.append(f"  linkStyle {','.join(map(str, kinds['script']))} stroke:{pal['script_edge']},stroke-width:1.5px,stroke-dasharray:2 4")
        for cat in ("dante", "aes67", "physical", "source", "processing", "control", "script"):
            fill, fg = pal[cat]
            D.append(f"  classDef {cat} fill:{fill},stroke:{fg},color:{fg}")
        for cat in set(cats.values()):
            D.append(f"  class {','.join(node_id(i) for i, c in cats.items() if c == cat)} {cat}")
        D.append("```\n")

    # ---- 6. control and automation ------------------------------------------
    D.append("## 6. Control and automation\n")
    D.append("### Scripts\n")
    users = defaultdict(list)
    rows = []
    for n, code in scripts.items():
        c = by_name[n]
        targets = sorted(set(re.findall(r"Component\.New\(\s*['\"]([^'\"]+)['\"]", code)))
        for t in targets:
            users[t].append(n)
        ucis = sorted(set(re.findall(r"Uci\.SetLayerVisibility\(\s*['\"]([^'\"]+)['\"]", code)))
        rows.append((n, pretty_class(c["class"]), c["path"], code.count("\n") + 1, script_purpose(code), ", ".join(targets), ", ".join(ucis)))
    D.append(md_table(["Script", "Type", "Page", "Lines", "Purpose (from header comment)", "Controls (Component.New)", "UCIs addressed"], rows))
    plugins = [(cname(c), c["user_label"] or "", plugin_info(c).get("Name", c["class"]), plugin_info(c).get("Version", ""), c["path"]) for c in C if c["class"].startswith("%PLUGIN%")]
    if plugins:
        D.append("### Plugins\n")
        D.append(md_table(["Component", "Label", "Plugin", "Version", "Page"], plugins))
    D.append("### Named components (Script Access enabled)\n")
    rows = [(cname(c), pretty_class(c["class"]), c["path"], ", ".join(users.get(cname(c), [])) or "-")
            for c in C if c["props"].get("ScriptAccess") not in (None, 0) and c["class"] not in SCRIPT_CLASSES]
    D.append(md_table(["Component", "Type", "Page", "Used by"], rows))
    ext_ids = m.get("external_ids") or []
    if ext_ids:
        D.append("### Named controls (external control IDs)\n")
        D.append(md_table(["Named control", "Control", "Component"], [(e["id"], e["name"], e.get("component") or "") for e in ext_ids]))
    if m["snapshots"]:
        D.append("### Snapshot banks\n")
        D.append(md_table(["Bank", "Snapshots", "Auto-load", "Auto-save"],
                          [(s["name"] or "(global)", s["count"], s["autoload"], s["autosave"]) for s in m["snapshots"]]))
    sched = []
    for c in C:
        if c["class"] == "date_time":
            sched.append((cname(c), "Date/Time", c["controls"].get("format_string", {}).get("String") or c["controls"].get("format", {}).get("String") or ""))
        st = c["controls"].get("ShutdownTime", {}).get("String")
        if st:
            sched.append((cname(c), "Scheduled shutdown", f"{st}, enabled={c['controls'].get('EnableAutoshutdown', {}).get('String')}"))
    for n, code in scripts.items():
        for t in sorted(set(re.findall(r"==\s*'(\d{1,2}:\d{2})'", code))):
            sched.append((n, "Time trigger in script", t))
    if sched:
        D.append("### Schedules and time references\n")
        D.append(md_table(["Component", "Kind", "Value"], sched))
    ctl = [(L, ", ".join(f"{n}[{p}]" for n, p, _, _ in d["out"]), ", ".join(f"{n}[{p}]" for n, p, _, _ in d["in"]))
           for L, d in sorted(labels.items()) if (d["out"] + d["in"]) and (d["out"] + d["in"])[0][2] != 1]
    if ctl:
        D.append("### Control signal names\n")
        D.append(md_table(["Signal name", "Source", "Destinations"], ctl))

    # ---- 7. UCIs -----------------------------------------------------------------
    D.append("## 7. User control interfaces\n")
    for u in m["ucis"]:
        drivers = [n for n, code in scripts.items() if f"'{u['title']}'" in code or f'"{u["title"]}"' in code]
        for c in C:
            for k in ("UCI", "UCI Name", "text_1"):
                if c["controls"].get(k, {}).get("String") == u["title"] and cname(c) not in drivers:
                    drivers.append(cname(c))
        res = u.get("res") or ["", ""]
        D.append(f"### {u['title']}\n")
        D.append(f"- Resolution: {int(res[0] or 0)} x {int(res[1] or 0)}\n- Driven by: {', '.join(drivers) or 'static / direct control bindings'}\n")
        for pg in u["pages"]:
            D.append(f"Page **{pg['name'] or 'Page ' + str(u['pages'].index(pg) + 1)}** layers:\n")
            D.append(md_table(["Layer", "Controls"], [(l["name"], l["controls"]) for l in pg["layers"]]))
        if u.get("bound_origins"):
            D.append("Bound to: " + ", ".join(f"{pretty_class(k) or 'script controls'} ({v})" for k, v in u["bound_origins"].items()) + "\n")

    # ---- 8. designer notes -----------------------------------------------------
    notes = [g for g in m["graphics"] if g["class"] in ("TextBlock", "Header") and g.get("label") and len(g["label"]) > 40]
    if notes:
        D.append("## 8. Designer notes (verbatim from the schematic)\n")
        for g in notes:
            D.append(f"**{g['path']}**\n\n> " + g["label"].replace("\r\n", "\n").replace("\n", "\n> ") + "\n")

    # ---- 9. signal name register -----------------------------------------------
    D.append("## 9. Signal name register\n")
    rows = [(L, "audio" if (d["out"] + d["in"])[0][2] == 1 else "control",
             ", ".join(f"{n}[{p}]" for n, p, _, _ in d["out"]) or "**none**",
             ", ".join(f"{n}[{p}]" for n, p, _, _ in d["in"]) or "**none**") for L, d in sorted(labels.items())]
    D.append(md_table(["Signal name", "Domain", "Source", "Destinations"], rows))
    return "\n".join(D)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("model")
    ap.add_argument("--out")
    ap.add_argument("--title")
    ap.add_argument("--source", help="original .qsys file name for the heading")
    ap.add_argument("--brand", help="brand name (brands/<name>.json) or path to a brand JSON; default brand if omitted")
    a = ap.parse_args()
    m = json.load(open(a.model))
    src = a.source or os.path.basename(a.model)
    doc = build_docs(m, src, a.title, load_brand(a.brand))
    out = a.out or os.path.join(os.path.dirname(a.model), "system-design.md")
    open(out, "w").write(doc)
    html = build_html(doc, load_brand(a.brand), a.title or re.sub(r"\.qsys$", "", src), src, m["meta"])
    open(re.sub(r"\.md$", "", out) + ".html", "w").write(html)
    print("wrote", out, "and", re.sub(r"\.md$", "", out) + ".html")


if __name__ == "__main__":
    main()


# ---------------------------------------------------------------------------
# HTML rendering
# ---------------------------------------------------------------------------

def load_brand(spec):
    """spec: None, a brand name (looked up in ./brands/<name>.json next to this
    file), or a path to a brand JSON file."""
    here = os.path.dirname(os.path.abspath(__file__))
    candidates = []
    if spec:
        candidates += [spec, os.path.join(here, "brands", spec + ".json"), os.path.join(here, "brands", spec)]
    candidates.append(os.path.join(here, "brands", "default.json"))
    for c in candidates:
        if os.path.isfile(c):
            return json.load(open(c))
    return {}


def _inline(s):
    s = s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
    s = re.sub(r"`([^`]+)`", r"<code>\1</code>", s)
    s = re.sub(r"\*\*([^*]+)\*\*", r"<strong>\1</strong>", s)
    s = re.sub(r"(?<![\w*])_([^_]+)_(?![\w*])", r"<em>\1</em>", s)
    return s


def md_to_html(md):
    """Converts the Markdown this module generates (headings, tables, lists,
    blockquotes, mermaid fences, paragraphs) into HTML. Not a general parser."""
    out, i = [], 0
    lines = md.splitlines()
    toc = []
    while i < len(lines):
        ln = lines[i]
        if ln.startswith("```"):
            lang = ln[3:].strip()
            j = i + 1
            block = []
            while j < len(lines) and not lines[j].startswith("```"):
                block.append(lines[j]); j += 1
            body = "\n".join(block)
            if lang == "mermaid":
                out.append(f'<div class="diagram"><button class="fit" type="button" onclick="toggleFit(this)">Actual size</button><pre class="mermaid">{body.replace("&", "&amp;").replace("<", "&lt;")}</pre></div>')
            else:
                out.append(f"<pre><code>{body.replace('&', '&amp;').replace('<', '&lt;')}</code></pre>")
            i = j + 1
            continue
        m = re.match(r"^(#{1,3})\s+(.*)", ln)
        if m:
            lvl = len(m.group(1))
            text = m.group(2)
            slug = re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")
            if lvl == 2:
                toc.append((slug, text))
            out.append(f'<h{lvl} id="{slug}">{_inline(text)}</h{lvl}>')
            i += 1
            continue
        if ln.startswith("|"):
            rows = []
            while i < len(lines) and lines[i].startswith("|"):
                rows.append(lines[i]); i += 1
            cells = [[c.strip() for c in r.strip().strip("|").split(" | ")] for r in rows if not re.match(r"^\|[-|]+\|$", r.replace(" ", ""))]
            if cells:
                head, body = cells[0], cells[1:]
                html = ['<div class="tablewrap"><table><thead><tr>' + "".join(f"<th>{_inline(c)}</th>" for c in head) + "</tr></thead><tbody>"]
                for r in body:
                    html.append("<tr>" + "".join(f"<td>{_inline(c)}</td>" for c in r) + "</tr>")
                html.append("</tbody></table></div>")
                out.append("".join(html))
            continue
        if ln.startswith("LEGEND: "):
            items = [x.split("=", 1) for x in ln[8:].split("|")]
            sw = "".join(f'<span class="sw"><i style="background:{DIAGRAM.get(k, ("#eee", "#000"))[0]};border-color:{DIAGRAM.get(k, ("#eee", "#000"))[1]}"></i>{v}</span>' for k, v in items)
            sw += '<span class="sw"><i class="ln" style="border-top:2px solid #4D4D4D"></i>Audio</span><span class="sw"><i class="ln" style="border-top:2px dashed #8E44AD"></i>Control</span><span class="sw"><i class="ln" style="border-top:2px dotted #8E44AD"></i>Script drives</span>'
            out.append(f'<div class="legend">{sw}</div>')
            i += 1
            continue
        if ln.startswith("> "):
            block = []
            while i < len(lines) and lines[i].startswith(">"):
                block.append(lines[i][1:].strip()); i += 1
            out.append("<blockquote>" + "<br>".join(_inline(b) for b in block) + "</blockquote>")
            continue
        if ln.startswith("- "):
            items = []
            while i < len(lines) and lines[i].startswith("- "):
                items.append(lines[i][2:]); i += 1
            out.append("<ul>" + "".join(f"<li>{_inline(x)}</li>" for x in items) + "</ul>")
            continue
        if ln.strip() == "":
            i += 1
            continue
        para = []
        while i < len(lines) and lines[i].strip() and not re.match(r"^(#|\||- |> |```)", lines[i]):
            para.append(lines[i]); i += 1
        out.append("<p>" + _inline(" ".join(para)) + "</p>")
    return "\n".join(out), toc



def base_css(brand):
    c = brand.get("colors", {})
    col = lambda k, d: c.get(k, d)
    hw = "600" if brand.get("headings_bold") else "300"
    return f""":root{{--ink:{col('ink', '#000')};--body:{col('body', '#4D4D4D')};--sub:{col('sub', '#5F5F5F')};--muted:{col('muted', '#969696')};
--line:{col('line', '#B2B2B2')};--line-light:{col('line_light', '#DDDDDD')};--offwhite:{col('offwhite', '#F8F8F8')};--white:{col('white', '#fff')};
--th-bg:{col('table_head_bg', '#000')};--th-fg:{col('table_head_fg', '#fff')};--callout:{col('callout_border', '#5F5F5F')};--footer:{col('footer_bg', '#DDDDDD')};}}
*{{box-sizing:border-box}}
html{{background:var(--offwhite)}}
body{{margin:0;color:var(--body);background:var(--white);font:15px/1.6 {brand.get('font_body', 'sans-serif')};max-width:1180px;margin:0 auto}}
.page{{display:grid;grid-template-columns:230px 1fr;min-height:100vh}}
nav{{position:sticky;top:0;align-self:start;height:100vh;overflow:auto;padding:36px 20px;border-right:1px solid var(--line-light);background:var(--white)}}
nav .brand{{margin-bottom:28px}} nav .brand img{{width:120px;display:block}}
nav .brand .co{{font-size:.72rem;letter-spacing:.14em;text-transform:uppercase;color:var(--muted);margin-top:10px}}
nav ol{{list-style:none;padding:0;margin:0}} nav li{{margin:0 0 6px}}
nav a{{color:var(--sub);text-decoration:none;font-size:.86rem;letter-spacing:.02em}} nav a:hover{{color:var(--ink)}}
main{{padding:40px 56px 80px;max-width:940px}}
.cover{{padding:24px 0 36px;border-bottom:1px solid var(--line);margin-bottom:40px}}
.cover .kicker{{font-size:.74rem;letter-spacing:.18em;text-transform:uppercase;color:var(--muted);margin-bottom:14px}}
.cover h1{{font-size:2.4rem;font-weight:{hw};letter-spacing:.01em;color:var(--ink);margin:0 0 6px;line-height:1.15}}
.cover .sub{{font-size:1.05rem;font-weight:300;color:var(--sub);margin:0 0 22px}}
.cover .meta{{display:grid;grid-template-columns:repeat(auto-fit,minmax(170px,1fr));gap:12px 28px;font-size:.86rem}}
.cover .meta div span{{display:block;font-size:.7rem;letter-spacing:.12em;text-transform:uppercase;color:var(--muted)}}
h2{{font-size:1.5rem;font-weight:{hw};letter-spacing:.02em;color:var(--ink);margin:52px 0 14px;padding-top:8px}}
h3{{font-size:1.1rem;font-weight:{hw if brand.get('headings_bold') else '400'};letter-spacing:.02em;color:var(--sub);margin:30px 0 10px}}
p{{margin:0 0 12px;max-width:72ch}} ul{{margin:0 0 14px;padding-left:20px}} li{{margin:2px 0}}
code{{font-family:{brand.get('font_mono', 'monospace')};font-size:.85em;background:var(--offwhite);border:1px solid var(--line-light);padding:0 4px;border-radius:2px;color:var(--ink)}}
blockquote{{margin:0 0 16px;padding:12px 18px;background:var(--offwhite);border-left:3px solid var(--callout);color:var(--sub);font-size:.95rem}}
.tablewrap{{overflow-x:auto;margin:10px 0 22px;border:1px solid var(--line-light)}}
table{{border-collapse:collapse;width:100%;font-size:.86rem}}
th{{background:var(--th-bg);color:var(--th-fg);text-align:left;padding:8px 10px;font-weight:400;letter-spacing:.06em;text-transform:uppercase;font-size:.72rem}}
td{{padding:7px 10px;border-bottom:1px solid var(--line-light);vertical-align:top}}
tbody tr:nth-child(even) td{{background:var(--offwhite)}}
td code{{background:transparent;border:0;padding:0}}
.legend{{display:flex;flex-wrap:wrap;gap:8px 18px;font-size:.8rem;color:var(--sub);margin:6px 0 14px}} .legend .sw{{display:inline-flex;align-items:center;gap:6px}} .legend i{{display:inline-block;width:14px;height:14px;border:1px solid;border-radius:2px}} .legend i.ln{{width:26px;height:0;border-radius:0;border-left:0;border-right:0;border-bottom:0}}
.diagram{{position:relative;overflow-x:auto;border:1px solid var(--line-light);background:var(--white);padding:12px;margin:10px 0 24px}} .diagram svg{{max-width:100%;height:auto}}
.diagram.full svg{{max-width:none!important;width:auto!important}} .diagram .fit{{position:absolute;top:8px;right:8px;z-index:2;font:inherit;font-size:.72rem;letter-spacing:.08em;text-transform:uppercase;color:var(--sub);background:var(--white);border:1px solid var(--line);padding:3px 8px;cursor:pointer}}
@media print{{.diagram .fit{{display:none}}}}
.diagram pre.mermaid{{margin:0;font-size:.8rem;color:var(--muted)}}
footer{{grid-column:1/-1;background:var(--footer);padding:18px 56px;font-size:.8rem;color:var(--sub);display:flex;justify-content:space-between;align-items:center;gap:20px}}
footer img{{height:26px}}
@media (max-width:860px){{.page{{grid-template-columns:1fr}} nav{{position:static;height:auto;border-right:0;border-bottom:1px solid var(--line-light)}} main{{padding:28px 20px}} footer{{padding:16px 20px}}}}
@media print{{html,body{{background:#fff}} .page{{display:block}} nav{{display:none}} main{{padding:0;max-width:none}} h2{{page-break-before:always}} .cover+h2{{page-break-before:avoid}} h3+.tablewrap{{page-break-before:avoid}} .tablewrap{{overflow:visible;border:0}} .diagram{{overflow:visible;page-break-inside:avoid}} thead{{display:table-header-group}} tr{{page-break-inside:avoid}} h2,h3{{page-break-after:avoid}} @page{{size:A4;margin:18mm 17.8mm 20mm}}}}
"""


def build_html(md, brand, title, source_name, meta):
    body, toc = md_to_html(md)
    body = re.sub(r"^<h1[^>]*>.*?</h1>\n?", "", body, count=1)  # the cover carries the title
    body = re.sub(r"^<p>System design documentation, generated.*?</p>\n?", "", body, count=1)
    body = re.sub(r"^<blockquote>Generated from the design file.*?</blockquote>\n?", "", body, count=1)
    c = brand.get("colors", {})
    col = lambda k, d: c.get(k, d)
    logo = brand.get("logo_data_uri")
    hw = "600" if brand.get("headings_bold") else "300"
    today = datetime.date.today().strftime("%-d %B %Y")
    toc_html = "".join(f'<li><a href="#{s}">{t}</a></li>' for s, t in toc)
    mm = brand.get("mermaid", {"theme": "neutral"})
    return f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>{title} - System Design</title>
<link rel="stylesheet" href="{brand.get('font_link', '')}">
<style>{base_css(brand)}</style></head>
<body><div class="page">
<nav><div class="brand">{f'<img src="{logo}" alt="{brand.get("name", "")} logo">' if logo else ''}<div class="co">{brand.get('name', '')}</div></div>
<ol>{toc_html}</ol></nav>
<main>
<div class="cover"><div class="kicker">System design documentation</div><h1>{title}</h1>
<p class="sub">Q-SYS design as saved in <code>{source_name}</code></p>
<div class="meta"><div><span>Prepared</span>{today}</div><div><span>Designer version</span>{meta.get('designer_version', '')} ({meta.get('build', '')})</div><div><span>Design author</span>{meta.get('author') or 'not recorded'}</div><div><span>Prepared by</span>{brand.get('name') or 'generated from the design file'}</div></div></div>
{body}
</main>
<footer><div>{f'<img src="{logo}" alt="">' if logo else ''}</div><div>{brand.get('footer_note', '')}</div><div>{brand.get('email', '')}</div></footer>
</div>
<script src="https://cdn.jsdelivr.net/npm/mermaid@10.9.1/dist/mermaid.min.js"></script>
<script>function toggleFit(b){{const d=b.parentElement;d.classList.toggle('full');b.textContent=d.classList.contains('full')?'Fit to page':'Actual size';}}
if(window.mermaid){{mermaid.initialize({{startOnLoad:true,securityLevel:'loose',flowchart:{{useMaxWidth:true,htmlLabels:true,nodeSpacing:28,rankSpacing:60,padding:8}},{json.dumps(mm)[1:-1]}}});}}</script>
</body></html>"""


SEVERITY_DEFAULT = {
    "colour": {"CRITICAL": ("#B3261E", "#FBE4E2"), "HIGH": ("#C0570A", "#FBE9DA"), "MEDIUM": ("#8A6D0F", "#F8EFCF"), "LOW": ("#456A7C", "#E1EBF0"), "INFO": ("#2E7D5B", "#DDF0E6")},
    "greyscale": {"CRITICAL": ("#FFFFFF", "#000000"), "HIGH": ("#FFFFFF", "#4D4D4D"), "MEDIUM": ("#FFFFFF", "#808080"), "LOW": ("#4D4D4D", "#DDDDDD"), "INFO": ("#4D4D4D", "#F8F8F8")},
}


def build_audit_html(m, findings, brand, title, source_name):
    """Styled audit report: verdict strip, findings ordered by severity with
    their evidence, inventory and UCI summaries, and how the audit was done."""
    from collections import Counter as _C
    sev = brand.get("severity") or (SEVERITY_DEFAULT["greyscale"] if brand.get("greyscale") else SEVERITY_DEFAULT["colour"])
    counts = _C(f["severity"] for f in findings)
    meta = m["meta"]
    C = m["components"]
    n_comp = sum(1 for c in C if c["kind"] in ("Component", "PlacedComponent"))
    n_scripts = sum(1 for c in C if c["controls"].get("code", {}).get("String"))
    n_names = len({p["label"] for c in C for p in c["pins"] if p.get("label")})
    today = datetime.date.today().strftime("%-d %B %Y")
    logo = brand.get("logo_data_uri")
    esc = lambda s: str(s).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
    code = lambda s: re.sub(r"'([^']{2,60})'", r"<code>\1</code>", esc(s))

    def chip(s):
        fg, bg = sev.get(s, ("#000", "#eee"))
        return f'<span class="chip" style="color:{fg};background:{bg}">{s.title()}</span>'

    order = ["CRITICAL", "HIGH", "MEDIUM", "LOW", "INFO"]
    strip = "".join(f'<div class="sev"><b>{counts.get(s, 0)}</b>{chip(s)}</div>' for s in order if counts.get(s, 0))
    if counts.get("CRITICAL"):
        verdict = "This design has faults the core itself reported or that will stop a function working. Fix the critical items before handover."
    elif counts.get("HIGH"):
        verdict = "Nothing is broken outright as saved, but there are conditions under which functions will fail or faults are being hidden. Clear the high items before handover."
    else:
        verdict = "No critical or high findings. The remaining items are maintainability and tidiness."
    cards = []
    for i, f in enumerate(findings, 1):
        fg, bg = sev.get(f["severity"], ("#000", "#eee"))
        items = "".join(f"<li>{code(x)}</li>" for x in f["items"])
        cards.append(f'''<section class="finding" style="border-left-color:{bg if f["severity"] != "LOW" else "#B2B2B2"}">
<div class="head"><span class="fid">F-{i:02d}</span>{chip(f["severity"])}<span class="area">{esc(f["area"])}</span></div>
<h3>{esc(f["title"])}</h3>{f"<p>{code(f['detail'])}</p>" if f["detail"] else ""}{f"<ul>{items}</ul>" if items else ""}</section>''')
    inv_rows = "".join(f"<tr><td>{esc(i['name'])}</td><td>{esc(INV_NAMES.get(i['class'], i['class']))}</td><td>{esc(i['location'] or '')}</td></tr>" for i in m["inventory"])
    uci_rows = "".join(f"<tr><td>{esc(u['title'])}</td><td>{int((u.get('res') or [0, 0])[0])} x {int((u.get('res') or [0, 0])[1])}</td><td>{sum(len(p['layers']) for p in u['pages'])}</td><td>{sum(l['controls'] for p in u['pages'] for l in p['layers'])}</td></tr>" for u in m["ucis"])
    toc = "".join(f'<li><a href="#f{i}">F-{i:02d} {esc(f["title"])[:48]}</a></li>' for i, f in enumerate(findings, 1))
    return f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>{esc(title)} - Design Audit</title>
<link rel="stylesheet" href="{brand.get('font_link', '')}">
<style>{base_css(brand)}
.verdict{{border-left:3px solid var(--callout);background:var(--offwhite);padding:14px 18px;margin:0 0 20px;font-size:1rem;color:var(--ink)}}
.strip{{display:flex;gap:26px;flex-wrap:wrap;margin:0 0 8px}} .sev{{display:flex;align-items:baseline;gap:8px}} .sev b{{font-size:1.9rem;font-weight:300;color:var(--ink)}}
.chip{{display:inline-block;padding:2px 10px;border-radius:2px;font-size:.7rem;letter-spacing:.12em;text-transform:uppercase}}
.stats{{display:grid;grid-template-columns:repeat(auto-fit,minmax(140px,1fr));border:1px solid var(--line-light);margin:18px 0 0}}
.stats div{{padding:10px 14px;border-right:1px solid var(--line-light)}} .stats div:last-child{{border-right:0}}
.stats span{{display:block;font-size:.68rem;letter-spacing:.12em;text-transform:uppercase;color:var(--muted)}} .stats b{{font-weight:300;font-size:1.3rem;color:var(--ink)}}
.finding{{border:1px solid var(--line-light);border-left:5px solid var(--line);padding:16px 20px 12px;margin:0 0 14px;background:var(--white)}}
.finding .head{{display:flex;gap:12px;align-items:center;margin-bottom:6px}} .fid{{font-family:{brand.get('font_mono', 'monospace')};font-size:.78rem;color:var(--muted)}}
.finding .area{{font-size:.72rem;letter-spacing:.12em;text-transform:uppercase;color:var(--muted)}} .finding h3{{margin:0 0 8px;color:var(--ink)}}
.finding ul{{margin:6px 0 4px}} .finding li{{font-size:.92rem}}
nav a{{font-size:.8rem}}
@media print{{.finding{{page-break-inside:avoid}} h2{{page-break-before:auto}}}}
</style></head>
<body><div class="page">
<nav><div class="brand">{f'<img src="{logo}" alt="{brand.get("name", "")} logo">' if logo else ''}<div class="co">{brand.get('name', '')}</div></div>
<ol><li><a href="#verdict">Verdict</a></li>{toc}<li><a href="#inventory">Inventory</a></li><li><a href="#method">Method</a></li></ol></nav>
<main>
<div class="cover"><div class="kicker">Q-SYS design audit</div><h1>{esc(title)}</h1>
<p class="sub">Static audit of <code>{esc(source_name)}</code></p>
<div class="meta"><div><span>Prepared</span>{today}</div><div><span>Designer version</span>{meta.get('designer_version', '')} ({meta.get('build', '')})</div><div><span>Design author</span>{meta.get('author') or 'not recorded'}</div><div><span>Prepared by</span>{brand.get('name') or 'generated from the design file'}</div></div></div>
<h2 id="verdict">Verdict</h2>
<div class="strip">{strip or '<div class="sev"><b>0</b> findings</div>'}</div>
<div class="verdict">{verdict}</div>
<div class="stats"><div><span>Components</span><b>{n_comp}</b></div><div><span>Wires</span><b>{len(m['wires'])}</b></div><div><span>Signal names</span><b>{n_names}</b></div><div><span>Scripts</span><b>{n_scripts}</b></div><div><span>UCIs</span><b>{len(m['ucis'])}</b></div><div><span>Inventory</span><b>{len(m['inventory'])}</b></div></div>
<h2>Findings</h2>
<p>Ordered by severity. "Saved status" values are what the core wrote into the file the last time it was saved while online, so they reflect real runtime state at that moment.</p>
{"".join(f'<a id="f{i}"></a>' + c for i, c in enumerate(cards, 1))}
<h2 id="inventory">Inventory as saved</h2>
<div class="tablewrap"><table><thead><tr><th>Device</th><th>Type</th><th>Location</th></tr></thead><tbody>{inv_rows}</tbody></table></div>
<h3>User control interfaces</h3>
<div class="tablewrap"><table><thead><tr><th>UCI</th><th>Resolution</th><th>Layers</th><th>Controls</th></tr></thead><tbody>{uci_rows}</tbody></table></div>
<h2 id="method">Method</h2>
<p>A <code>.qsys</code> file holds the complete serialised design: inventory, pages, components, wires, signal names, UCIs, snapshots and the text of every script, plus the control values the core last reported. The audit reads that object graph directly, merges wires with matched signal names into one connection graph, and runs the checks whose results appear above. The full model, every script and a Markdown copy of these findings sit alongside this report.</p>
<p>Two limits apply. The audit sees the design as saved, not the core as it runs today. And a control fed by a wire or a signal name can hold a stale cached value, so the audit reads the source of a signal rather than the cached copy wherever it can.</p>
</main>
<footer><div>{f'<img src="{logo}" alt="">' if logo else ''}</div><div>{brand.get('footer_note', '')}</div><div>{brand.get('email', '')}</div></footer>
</div></body></html>"""


# ---------------------------------------------------------------------------
# PDF via headless Chrome / Chromium / Edge
# ---------------------------------------------------------------------------

CHROME_CANDIDATES = [
    "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
    "/Applications/Chromium.app/Contents/MacOS/Chromium",
    "/Applications/Microsoft Edge.app/Contents/MacOS/Microsoft Edge",
    "/Applications/Brave Browser.app/Contents/MacOS/Brave Browser",
    r"C:\Program Files\Google\Chrome\Application\chrome.exe",
    r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
    r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
    "google-chrome", "google-chrome-stable", "chromium", "chromium-browser", "microsoft-edge",
]


def find_chrome():
    """A Chromium-based browser for printing. Set QSYS_CHROME to override."""
    import shutil
    env = os.environ.get("QSYS_CHROME")
    if env and (os.path.isfile(env) or shutil.which(env)):
        return env
    for c in CHROME_CANDIDATES:
        if os.path.isfile(c):
            return c
        w = shutil.which(c)
        if w:
            return w
    return None


def html_to_pdf(html_path, pdf_path, timeout=180):
    """Print an HTML file to PDF with headless Chrome. Returns (ok, message).
    The browser needs network access for the fonts and the Mermaid renderer;
    --virtual-time-budget gives the diagrams time to draw before printing."""
    import subprocess
    import tempfile
    chrome = find_chrome()
    if not chrome:
        return False, "no Chrome, Chromium or Edge found - install one or set QSYS_CHROME to its path"
    profile = tempfile.mkdtemp(prefix="qsys-pdf-")
    url = "file://" + os.path.abspath(html_path).replace(" ", "%20")
    cmd = [chrome, "--headless=new", "--disable-gpu", "--no-first-run", "--no-default-browser-check",
           f"--user-data-dir={profile}", "--run-all-compositor-stages-before-draw", "--virtual-time-budget=20000",
           "--no-pdf-header-footer", f"--print-to-pdf={os.path.abspath(pdf_path)}", url]
    import shutil as _sh
    import time
    if os.path.exists(pdf_path):
        os.remove(pdf_path)
    # Headless Chrome sometimes keeps running after the PDF is written, so
    # watch for the file to appear and stop growing, then close the browser.
    proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    t0, last, stable = time.time(), -1, 0
    try:
        while time.time() - t0 < timeout:
            if proc.poll() is not None:
                break
            if os.path.isfile(pdf_path):
                size = os.path.getsize(pdf_path)
                stable = stable + 1 if size == last and size > 1000 else 0
                last = size
                if stable >= 2:
                    break
            time.sleep(1)
        else:
            proc.kill()
            _sh.rmtree(profile, ignore_errors=True)
            return False, f"{os.path.basename(chrome)} timed out after {timeout}s"
        if proc.poll() is None:
            proc.terminate()
            try:
                proc.wait(10)
            except subprocess.TimeoutExpired:
                proc.kill()
        err = (proc.stderr.read() or "")[-400:] if proc.stderr else ""
    finally:
        _sh.rmtree(profile, ignore_errors=True)
    if os.path.isfile(pdf_path) and os.path.getsize(pdf_path) > 1000:
        return True, pdf_path
    return False, err.strip() or "unknown error"
