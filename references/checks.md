# What the audit checks, and why

Each check in `scripts/qsys_audit.py` (`run_checks()`), the evidence it uses, and the reasoning behind its severity. Severities: CRITICAL means the system cannot do something it is meant to do; HIGH means something will fail under a condition that will occur in normal use, or the fault is being hidden; MEDIUM means it works but will bite whoever maintains it; LOW is tidiness.

## Connectivity (wires and signal names)

Q-SYS connects pins either with a wire or by giving two pins the same signal name (stored on each pin as `_Label`). The tool builds one graph from both. A pin is "connected" if it has a wire or its name has a partner in the opposite direction.

| Check | Evidence | Severity | Why |
|---|---|---|---|
| Network I/O device with no connections at all | No audio pin on a Dante / AES67 / Q-LAN / analogue box is wired or name-matched | LOW | It is in the inventory but unused: spare capacity or a device that should be removed. It used to be CRITICAL before signal names were traced; a device that genuinely passes no audio shows up in the dead-end checks instead. |
| Signal name with more than one source | Two output pins share a name | HIGH | Only one output may drive a name; the design will not compile or the wrong source wins. |
| Input listening to a name nothing drives | Input pin named, no output with that name | MEDIUM | Usually the source was renamed. That input receives silence. |
| Audio output published on a name nothing listens to | Output pin named, no input with that name | HIGH | That audio goes nowhere. Frequently a half-built feature (a second bell player, a spare zone). |
| Control name with no destination | As above, control domain | LOW | Often harmless (a name kept for a UCI), sometimes a leftover. |
| Block whose audio output goes nowhere | No audio output pin connected; meters, probes and recorders excluded | HIGH | A mixer or EQ whose output is unused is either dead code or a missing connection. |
| Block with no audio input connected | No audio input pin connected; generators and players excluded | HIGH | Processing that processes nothing. |

## Runtime state cached in the file

When Designer is connected to a core and the file is saved, the current control values come with it. These are the last-known states.

| Check | Evidence | Severity | Why |
|---|---|---|---|
| Script saved in a non-OK state | `script_status` not "OK"; `script_error_count` | CRITICAL | The core itself reported the fault. The status string names the cause (for example "Fault - 35: uci, page or layer not found"). |
| Device / status combiner saved non-OK | `status` or `input_status` not OK / Initializing / No clients | HIGH | The client's status page showed this. Trace it to the source device. |
| Unresolved Dante subscription on an unused channel | `input_status` names "Channel N Unresolved" and channel N has no connection | HIGH | A nuisance alarm that keeps the whole system "Compromised" for nothing. Clear the subscription. |

## Scripts

All Text Controller and Block Controller bodies are extracted to `scripts/` in the output folder.

| Check | Evidence | Severity | Why |
|---|---|---|---|
| Lua syntax error | `luaparser` fails to parse | CRITICAL | Will not run. |
| `Component.New()` target missing or without Script Access | Name not in design, or `ScriptAccess` property unset on a non-script component | CRITICAL | Faults on first access. |
| Script or text control names a UCI that does not exist | String literal in `Uci.SetLayerVisibility`, or a UCI / UCI Name control whose value is not a UCI title and which is not fed by a wire or signal name | HIGH | Raises fault 35. Fed controls are skipped because their cached value may be stale. |
| Misspelled keyword or literal (`flase`, `ture`, `nill`) | Regex on the source | HIGH | Evaluates to nil silently; the call does something other than intended. |
| Duplicated script bodies | Identical MD5 of the code across components | HIGH | Every fix has to be applied N times and copies drift. The most common root cause of "we fixed that already" faults. |
| `Log.Error()` for routine messages | Call with a string literal | MEDIUM | Buries real errors in the core log. |
| Heavy `print()` use | Five or more calls | LOW | CPU and log noise. |

## Addressing and configuration

| Check | Evidence | Severity | Why |
|---|---|---|---|
| Address outside the dominant subnet | IPs found in control values; the /24 with the most devices is taken as the site subnet | HIGH | Almost always a factory default (192.168.1.x) or an address pasted from another job. |
| Email component stores a password | `password` control non-empty | MEDIUM | Credentials travel with the design file. |
| Email component with test content | "test" in subject, or the default "I am a message" body | LOW | Left over from commissioning. |
| Test / diagnostic components left in | Ping, Command Buttons, Event Log, generators, probes | LOW | Noise; signal generators on an I/O page are often intentional. |

## Naming and metadata

| Check | Evidence | Severity | Why |
|---|---|---|---|
| Processing blocks with default names | Code name matches `Class_N` and no user label; classes that are normally left unnamed are excluded | MEDIUM | Slows down fault-finding. |
| Containers / channel groups without a label | Empty label | LOW | Same. |
| Status combiner inputs with placeholder labels | Label is just a number | LOW | Template residue; the status page shows "4" instead of a device name. |
| Design metadata not filled in | No company, version 0.0.0 | LOW | The file cannot identify itself. |

## What the tool does not check

Logic. It will not tell you a handler is never called, that a selector control is addressed as `selector.0` in one place and `selector_0` in another, that a projector script ignores the device's replies, or that an auto-shutdown was left disabled. Those come from reading the scripts and the cached values with the design's purpose in mind, which is the reviewer's job. When you find one that could be expressed as a rule, add it.
