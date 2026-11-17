# Deploying the Skills Shipped with stellarlib

The stellarlib package ships Claude Agent Skills inside the installed
package, under its `skills/` tree. This guide is for the agent (or
person) deploying them on any machine — no dedicated tooling is needed
beyond this file.

## Where the skills live on this machine

The skills root is inside the installed stellarlib package. Print it:

```bash
python -m stellarlib.skills
```

This writes the absolute skills directory and the location of this
guide. The equivalent one-liner, if the module is unavailable:

```bash
python -c "from importlib.resources import files; print(files('stellarlib') / 'skills')"
```

With an editable install (`pip install -e .`) that path is the
repository checkout; with a regular install it is inside the
environment's site-packages — either works as a link target.

## What a skill entry looks like

Each skill occupies two layers:

```text
skills/<name>/            container (a packaging concept)
├── skill/                the skill proper — SKILL.md plus supporting files
└── meta.yaml             external-dependency metadata — read, never deployed
```

Only `skills/<name>/skill/` is deployed. `meta.yaml` stays behind: it
declares what the skill expects from its environment, and you read it
before linking.

## Deployment is a directory link

Make a skill visible to Claude Code by linking its `skill/` directory
into a skills directory:

| target | link path | points at |
| ------ | --------- | --------- |
| user level (every project) | `~/.claude/skills/<name>` | `<skills-root>/<name>/skill` |
| project level (one project) | `<project>/.claude/skills/<name>` | same target |

Prefer a link over a copy — a link reflects the installed package's
state; a copy drifts. When a link is impossible (privilege,
filesystem), copy the directory instead and note that package updates
will not reach the copy.

Commands — replace `<skills-root>` with the path printed above:

Linux / macOS:

```bash
ln -s <skills-root>/stellarlib/skill ~/.claude/skills/stellarlib
```

Windows (PowerShell):

```powershell
New-Item -ItemType SymbolicLink -Path "$HOME\.claude\skills\stellarlib" -Target "<skills-root>\stellarlib\skill"
```

Windows requires Developer Mode or the "Create symbolic links" account
right to create a symlink; a failure mentioning privilege or WinError
1314 means neither is present — enable one, or fall back to a copy:

```powershell
Copy-Item -Recurse <skills-root>\stellarlib\skill "$HOME\.claude\skills\stellarlib"
```

Create a directory symlink, never a Windows junction: recursive
removers walk through a junction into its target and can delete the
package's content, while a symlink is removed as the link itself.

## Before linking: read meta.yaml

`skills/<name>/meta.yaml` declares the skill's expectations of its
environment (`depends-on`, `requires`, `claude-md`, `integrates-with`,
`notes`). The stellarlib skill declares only the stellarlib package
itself, which any machine that has this package installed already
satisfies — nothing to do beyond confirming the import works
(`python -c "import stellarlib"`).

An unsatisfied entry is a report to the user — the user fixes the gap
or consciously continues; never a silent skip.

## Safety rules

- Show the plan first — which skills, which links — before creating
  anything.
- A real directory or file already sitting at a link destination is a
  conflict: report it, never delete it. The most you may do, with the
  user's knowledge, is rename it aside (`<name>.bak.<timestamp>`).
- The procedure is idempotent — re-running it leaves an existing
  correct link alone.

## After linking

- Verify the deployment: `~/.claude/skills/stellarlib/SKILL.md` is
  reachable through the link, and the frontmatter `name:` equals
  `stellarlib`.
- To remove a deployment, remove the link itself — on Windows, confirm
  first that it is a symlink rather than a junction:
  `Get-Item <path> | Select-Object LinkType` should show
  `SymbolicLink`.
