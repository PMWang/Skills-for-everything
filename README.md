# Skillyard

> **Skillyard** is a curated, open collection of **Claude Code skills** and **agent skills** — reusable, self-contained tools you can drop into [Claude Code](https://claude.com/claude-code), the Claude Agent SDK, Codex, or any agent that supports the open `SKILL.md` format.

## What's a Skill?

A Skill is a folder containing a `SKILL.md` (instructions + metadata) plus any supporting scripts, templates, or reference files. The agent loads a Skill on demand when a task matches its description — so you get specialized behavior without bloating every prompt.

## Repository layout

```
skills/
  <skill-name>/
    SKILL.md        # what the skill does + when to use it
    ...             # optional scripts, templates, assets
```

## Usage

Copy any skill folder into your skills directory:

- **Claude Code (global):** `~/.claude/skills/<skill-name>/`
- **Project-scoped:** `.claude/skills/<skill-name>/`

The agent picks it up automatically on the next run.

## License

_TBD — see repository owner._

---

Maintained by [@PMWang](https://github.com/PMWang).
