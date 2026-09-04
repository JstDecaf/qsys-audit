# qsys-audit

A Claude skill that audits Q-SYS Designer `.qsys` files and generates system design documentation without opening Designer.

## Prerequisites

- Python 3.9 or newer (`python3 --version`). macOS ships it; Windows users install it from python.org and use `python` in place of `python3`.
- Git, to clone and update.
- For `--pdf` only: Google Chrome, Chromium or Microsoft Edge installed. The tool finds the usual locations; set `QSYS_CHROME` to the browser path otherwise.
- Network access on first setup (two small Python packages) and when opening the HTML (fonts and the diagram renderer load from the web).

Nothing else. Q-SYS Designer is not needed; the tool reads the `.qsys` file directly.

## Install

**Claude Code / Claude desktop:** clone or copy this folder to `~/.claude/skills/qsys-audit/` (the folder name must match), then run the first-time setup:

```bash
git clone <this repo> ~/.claude/skills/qsys-audit
sh ~/.claude/skills/qsys-audit/scripts/setup.sh
```

**claude.ai:** download `qsys-audit.skill` from the [Releases](../../releases) page and upload it under Settings > Skills. That route runs the tool inside claude.ai's sandbox, so `--pdf` is not available there; use the HTML.

**Windows:** the paths above use `~/.claude/skills/`, which on Windows is `%USERPROFILE%\.claude\skills\`. Run `python -m venv scripts\venv` and `scripts\venv\Scripts\pip install nrbf luaparser` instead of `setup.sh`.

## Use

Ask Claude to audit, review, check or document a `.qsys` file. Or run the tool directly:

```bash
~/.claude/skills/qsys-audit/scripts/venv/bin/python ~/.claude/skills/qsys-audit/scripts/qsys_audit.py \
    "/path/to/design.qsys" --out "Site audit" --title "Site name Core 01" --brand pxd --pdf
```

Outputs: `findings.md/.html/.pdf` (the audit), `system-design.md/.html/.pdf` (documentation with colour-coded signal-flow diagrams), `model.json`, and every script as `.lua`. `--html` is the default; `--pdf` adds PDFs printed with headless Chrome, Chromium or Edge.

## Layout

- `SKILL.md` - instructions Claude follows
- `scripts/qsys_audit.py` - parser, model, checks
- `scripts/qsys_docs.py` - documentation, HTML and PDF rendering
- `scripts/brands/` - brand files (`pxd.json`, `default.json`)
- `references/checks.md` - every check, its evidence and severity rationale
- `references/file-format.md` - the `.qsys` object model

## Updating

Edit, run the tool on a couple of designs, bump nothing (there is no version field), commit, push. Team members `git pull` in `~/.claude/skills/qsys-audit`.
