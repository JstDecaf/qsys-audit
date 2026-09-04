---
name: qsys-audit
description: Audit a Q-SYS Designer (.qsys) file and generate its system design documentation without opening Q-SYS Designer. Use this whenever someone mentions a .qsys file, a Q-SYS design, core, UCI, Dante/AES67 routing, Lua Text Controllers or Block Controllers, or asks to review, check, QA, sign off, hand over, document, or "look over" a Q-SYS programming job - even if they don't say "audit". Also use it when a Q-SYS system is misbehaving in the field and the design file is available, and when a team wants documentation (inventory, I/O map, signal flow, control logic, UCIs) generated from a design.
---

# Q-SYS design audit and documentation

A `.qsys` file holds the whole design: inventory, schematic pages, every component with its properties, every wire and signal name, every UCI, every script's source, and the control values the core last wrote back before the file was saved. That last part matters: script status, error counts, device status, Dante subscription state and IP addresses are all in the file, so a static audit sees real runtime state from the moment of the last save.

The bundled tool reads all of that directly and produces:

- `findings.md` and `findings.html` - a severity-ranked list of problems with the evidence for each; the HTML is a styled, printable report
- `system-design.md` and `system-design.html` - system design documentation with Mermaid signal-flow diagrams; the HTML is styled, has a table of contents, renders the diagrams, and prints to A4
- `model.json` plus `scripts/*.lua` - the extracted design, for anything the checks don't cover

Your job is to run the tool, then read what it found with an engineer's eye and write it up so the designer knows what to fix first and why.

## Run the tool

```bash
sh <skill-path>/scripts/setup.sh                       # first time only
<skill-path>/scripts/venv/bin/python <skill-path>/scripts/qsys_audit.py "/path/to/design.qsys" \
    --out "/path/to/output-folder" --title "Site name Core 01" --brand pxd
```

It takes 15 to 30 seconds on a typical design. Output goes to `--out` (default `audit_<design name>/` in the current directory).

Output flags:

- `--html` (the default when no flag is given) writes `findings.html` and `system-design.html`, styled and printable.
- `--pdf` also prints both to `findings.pdf` and `system-design.pdf` using headless Chrome, Chromium or Edge, whichever is installed (set `QSYS_CHROME` to a browser path if it is somewhere unusual). Printing takes a few seconds per file and needs network access for the fonts and the diagram renderer. The HTML is kept alongside because the PDFs are printed from it.
- `--no-docs` skips the documentation when only the audit is wanted.
- `--html --pdf` together is fine; the Markdown files are always written.

If `--pdf` reports that no browser was found, say so and hand the user the HTML files with a note that any browser's Print to PDF gives the same result.

`--brand` styles both HTML outputs. It takes a name from `scripts/brands/` (`pxd` is Production by Design: greyscale, Avenir/Nunito Sans, logo embedded) or a path to your own brand JSON. With no `--brand` a neutral default is used. To add a company, copy `scripts/brands/default.json`, set the name, email, fonts and colours, paste the logo in as a `data:image/png;base64,...` URI, and set `"greyscale": true` if the brand forbids colour so severities are shown in tones of grey instead. When the user's company has a branding skill, read it first and make the brand file agree with it.

The HTML files load fonts and the Mermaid renderer from the web; open them in a browser and print to PDF when a file to email is wanted. The Markdown versions are the same content for wikis and git.

If `setup.sh` fails, the two Python dependencies are `nrbf` (the .NET BinaryFormatter parser) and `luaparser` (optional, enables Lua syntax checks). Python 3.9 or later.

## Read the findings properly

`findings.md` is evidence, not the report. Before writing anything, open it alongside `model.json` and the extracted scripts and do these things, because the tool is deliberately conservative and some of its output needs judgement:

1. **Confirm every critical and high finding against the extracted data.** For a script fault, open the `.lua` file. For a wiring or signal-name finding, look at the signal-name register in `system-design.md`. For a status finding, check which control carried the value. A finding you can't trace to an object in the file doesn't go in the report.

2. **Read the cached values with care.** A control that is fed by a wire or a signal name can hold a stale cached value, because the file stores what the control held at some earlier save, not what the source now drives into it. The tool already skips fed controls for its UCI-name check; apply the same thinking anywhere you quote a cached value. Conversely, "not in the file" is not proof a control does not exist - the cache only holds controls that have been touched.

3. **Follow signal names, always.** Q-SYS connects pins without wires when they share a signal name. A design that looks unwired is usually one that uses signal names throughout. The tool merges wires and matched names into one graph; if you do any connectivity reasoning of your own, do the same. This is the single easiest way to produce a false "nothing is connected" finding.

4. **Look for copy-paste residue.** Another site's name in an email subject, a factory-default IP on one device, a status-combiner label from a template, notes that describe hardware the design does not have. The tool flags addresses outside the dominant subnet and placeholder labels, but a human reading the notes and the email subjects will catch more. These are the findings that most often explain "random" field faults.

5. **Read the scripts the tool didn't flag.** It checks syntax, missing targets, typos, duplicated bodies and logging misuse. It does not judge logic. Skim every unique script for: handlers defined but never called, control names written two different ways for the same component, third-party code with a "last tested on" older than the running Designer version, timers rebuilt inside reset functions, TCP handlers that ignore what the device sends back.

6. **Separate broken from untidy.** A missing UCI, a wrong IP, a script saved in Fault, an audio output that goes nowhere: broken. Default names, unlabelled containers, test utilities left in, empty metadata: untidy. Both belong in the report, but the reader needs to know which is which at a glance. Use severity for that and do not inflate it - a report with everything marked critical gets ignored.

`references/checks.md` lists every check the tool runs, what evidence it uses and why the severity is what it is. Read it the first time you use the skill.

## Write the report

Lead with the verdict in one or two sentences: would this file pass a handover check, and what is broken outright. Then findings in severity order, then what is in good shape, then the order to fix things. A designer reading only the first paragraph should know whether to worry.

For each finding give: what the tool or you found, the exact evidence (component name, control, cached value, line number), why it matters in the room, and what to do. Keep component names in code formatting so they can be searched for in Designer. Quote the saved status string verbatim where one exists; "Fault - 35: uci, page or layer not found" is more useful than "the script has an error".

Include what is in good shape. Every design has some, and a review that only lists faults teaches nothing about what to keep doing.

If the user wants the report as a page or document, build it from this structure; do not paste `findings.md` in as-is. `findings.md` is a tool output and reads like one. If you made a mistake in an earlier version of a report, say so in the report and keep the correction visible; the reader may have already acted on the wrong version.

## Documentation

`system-design.md` is generated, not written, so it is complete but flat. It has: overview, inventory with addresses and last status, network and clocking settings, an audio I/O map (every network channel, its signal name, what it feeds or what feeds it), one Mermaid signal-flow diagram per page (colour-coded: Dante orange, AES67 / Q-LAN blue, physical I/O green, players light blue, processing grey, control components lilac, scripts and plugins purple; audio flows are solid lines, control wires and control signal names dashed, and a script driving a component by name is a dotted line; these colours are deliberate even for greyscale brands, because telling signal types apart is what the diagram is for), scripts with their purpose and the components they control, plugins and versions, named components, named controls, snapshot banks, schedules, UCIs with layers and what drives them, the designer's own schematic notes verbatim, and the full signal-name register.

When the user wants documentation to hand to a client or keep in the job folder, use it as the source of truth and add the narrative it lacks: what the system is for, how a normal day runs (startup, modes, shutdown), and how the venues relate. Do not restate the tables; point to them. If a branding skill applies to the user's company, use it for the client-facing version.

Mermaid diagrams render in GitHub, Obsidian, VS Code, Confluence and claude.ai artifacts. If the target does not render Mermaid, keep the diagram source in an appendix and describe the flow in a sentence per page.

## Running it on every design

For a team that wants this as a development gate: run the tool on the file before it leaves the office, require zero critical or high findings, and keep `system-design.md` with the job. Re-run after changes; the model and findings are fast to regenerate and the diffs between runs are themselves useful.

If the user asks for something the checks do not cover, the model in `model.json` has every component's class, properties, cached controls, pins with wires and signal names, every wire, every UCI's layers and bindings, snapshots and named controls. Write the check against the model, and if it is generally useful, add it to `run_checks()` in `scripts/qsys_audit.py` so the next audit gets it for free. `references/file-format.md` explains the object model if you need to go below `model.json`.
