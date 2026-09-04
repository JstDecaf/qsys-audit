# The .qsys file format

For when `model.json` is not enough and you need to go into the object graph.

## Container

A `.qsys` file is a .NET BinaryFormatter (MS-NRBF) stream. The first ~400 bytes are a `QSC.QSys.Design.FileIO+FileHeader` object carrying a compression mode and a version; then comes a gzip stream (starts `1f 8b 08`). Decompress it and you get a second BinaryFormatter stream, tens of megabytes, holding the whole design as one object graph rooted at `QSC.QSys.Design.Document`.

The tool uses the PyPI `nrbf` package to read the records. Its built-in reference resolver recurses forever on the design's cyclic parent/owner links, so the tool keeps the raw object table (`parser.objects`, keyed by object id, references as `{"__ref__": id}`) and navigates it itself with a cycle-safe `deref`. The parse runs on a thread with a large stack because the resolver's recursion is what raises the `RecursionError` that the tool then swallows.

## Document

`QSC.QSys.Design.Document` fields worth knowing:

- `_BuildId`, `_BuildNumber` - Designer version that saved the file
- `_Metadata` - `_author`, `_company`, `_version`
- `_Properties` - `DesignProperties`: PTP domain and priorities, QoS preset, DSCP values, MTU, software Dante interface and latency
- `_Inventory` - `ElementList` of `Inventory.*` items (Core, TouchScreenController, SoftDanteInput/Output, Aes67Receiver/Transmitter, UciViewer, ...) with `_Name`, `_Location`, model enums
- `_Pages` - `ElementList` of `Canvas`; each has `_Title` and `_Layers`, each layer has `_Children`
- `_WebPanels` - UCIs (`WebPanels.Panel`): `_Title`, resolution, `_Pages` → `_Layers` → children (`ControlStyle` etc.)
- `_Snapshots`, `_ExternalControlIds`, `_LinkBuses` (empty in designs seen so far), `_LuaModules`

Collections: `ElementList`, `ElementHashSet` and `StructList` wrap an `items` List; `System.Collections.Generic.List` has `_items` and `_size`; `Dictionary` has `KeyValuePairs`. Enums are `{value__, __class__}`.

## Schematic children

Inside a layer's `_Children`:

- `Component` / `Snapshot.PlacedComponent` - `_ClassName` (component type, e.g. `mixer`, `device_controller_script`, `%PLUGIN%_<id>`), `_CodeName` (the name shown in Designer), `_UserLabel`, `_Properties` (StructList of `ComponentProperty` with `CodeName` / `Value`; plugin source lives in property `plugin_source`, Script Access in `ScriptAccess`), `_ControlValues` (Dictionary of control name → `CachedControlValue` with `String`, `Value`, `Position`; script source is control `code`), `PinProvider+_Pins`
- `Container` and `ChannelGroup` - have `_Canvases` (nested pages), their own boundary pins, and `_Jumpers` (wires from boundary pins to inner components)
- `ContainerPin` / `ChannelGroupPin` - the inner side of a boundary pin, labelled by number
- `Wire` - `_PinA`, `_PinB`
- Graphics: `TextBlock`, `Header`, `GroupBox`, `ControlStyle` (a placed control; `_Value._Link` → `ControlValue` with `_Name` and `_OriginClassName`)

Inherited fields are serialised with the base class name as a prefix, e.g. `Component+_CodeName`, `PinProvider+_Pins`, `Object+_Label`. Check both forms.

## Pins

`QSC.QSys.Design.Pin`: `Provider` (the component, or an `Inventory.Component` whose `_Owner` is the inventory item), `CodeName`, `PrettyName`, `Direction` (1 = input, 2 = output), `Domain` (1 = audio, 2 = control, others for video/link), `_Wires` (hash set of Wire), and `_Label` - the signal name. Pins that share a `_Label` are connected without a wire, one output to many inputs. The pin set on a component only contains pins that are materialised (wired, named, or user-created), so an absent pin is not evidence of anything.

## Cached values

`CachedControlValue` entries are what the core reported at the last connected save. Script components carry `script_status`, `script_error_count`, `script_memory`, `script_load`. Devices carry `status`, Dante receivers carry `input_status` and per-channel `channel_N_subscription_device` / `_channel` / `_status`, touch screens carry `lan_a_address`, and so on. Only touched controls are cached, and a control fed by a wire or signal name may hold an older value than its source now drives.

## Strings you can grep for

Running `strings` on the decompressed payload is a quick way to find things the model does not surface: an inventory XML block (`<device type="core" ...>`), the paging configuration XML (`<PagingConfig CoreModel=... Version=...>`, which can be stale), UCI export JSON (`"DesignName": ..., "Ucis": [...]` with target device per UCI), and the full text of every script.
