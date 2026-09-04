# qsys-audit

A Claude skill that audits Q-SYS Designer `.qsys` files and generates system design documentation without opening Designer.

## Install

**Claude Code / Claude desktop:** clone or copy this folder to `~/.claude/skills/qsys-audit/` (the folder name must match), then run the first-time setup:

```bash
git clone <this repo> ~/.claude/skills/qsys-audit
sh ~/.claude/skills/qsys-audit/scripts/setup.sh
```

**claude.ai:** upload `qsys-audit.skill` from the Releases page (or build one with `package_skill`) under Settings > Skills.

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
