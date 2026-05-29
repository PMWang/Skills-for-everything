---
name: claude-vault
description: >-
  Back up Claude Code account data and restore it after a ban, machine move, or account switch.
  Use when the user wants to: back up ~/.claude; migrate conversation history, CLAUDE.md, skills,
  settings, and memory to a new machine or new account; recover everything on a fresh account after
  a ban; or when `claude --resume` can't find / is missing past conversations (often because moving
  machines or non-ASCII / CJK project paths cause project-directory slug collisions). 备份 Claude Code
  账号内容并在封号 / 换机 / 换号后一键恢复；当用户想备份 ~/.claude、迁移到新机器或新账号、账号被封后找回
  历史对话、或 `claude --resume` 看不到 / 找不到以前的会话（常因中文路径导致目录 slug 碰撞、或换机后路径变了）
  时使用。Cross-platform (macOS / Linux / Windows), Python stdlib only.
---

# claude-vault — back up, recover, and re-home Claude Code account data

A cross-platform, zero-dependency tool that snapshots Claude Code's `~/.claude` and safely restores
conversation history and config to a new environment after an **account ban, machine move, or account switch**.

## When to use

- "Back up my Claude account / my conversations"
- "My account got banned — how do I move my old content to a new account?"
- "I switched computers, how do I migrate my conversation history?"
- "`claude --resume` can't find / no longer shows my past sessions"
- "Set up a daily automatic backup of Claude"

## Entry point

The script lives at `scripts/vault.py`. Run it with the system Python (3.8+; nothing to `pip install`):

```bash
python3 scripts/vault.py <subcommand> [options]
python3 scripts/vault.py --help        # overview
python3 scripts/vault.py status        # current snapshots + auto-backup status
```

> Symlink the repo into `~/.claude/skills/` to use it as a skill in any session; the script itself runs from anywhere.
> UI language follows the system locale (English default, Chinese on a `zh*` locale); override with `CLAUDE_VAULT_LANG=en|zh`.

## Ban / machine recovery: three steps

After the new account is signed in to Claude Code:

```bash
# 1) Preview first (dry-run by default — never touches ~/.claude)
python3 scripts/vault.py restore /path/to/claude-vault-XXXX.tar.zst

# 2) Check the on-screen summary and the manifest (~/.claude-vault/manifests/), then apply
python3 scripts/vault.py restore /path/to/claude-vault-XXXX.tar.zst --apply

# 3) Restart Claude Code; `claude --resume` should now list the history
```

Restore automatically:
- **Protects the new login** — does not write `.credentials.json`, and keeps the new machine's account
  fields in `.claude.json` (`oauthAccount` / `userID`), so it won't break the new login or re-link the banned account.
- **Remaps across machines** — after moving machine / username, it recomputes each session's project-dir
  slug from the real `cwd` recorded inside the session, rewriting that `cwd` when needed so `claude --resume` finds it.
- **Is reversible** — auto-creates a "pre-restore snapshot" before applying.

## Daily: enable automatic backup

```bash
python3 scripts/vault.py backup                 # snapshot now
python3 scripts/vault.py schedule --time 03:00  # daily at 03:00 (macOS launchd / Linux cron)
python3 scripts/vault.py schedule --uninstall   # turn it off
```

Snapshots go to `~/.claude-vault/snapshots/`, keep the newest 14, and **exclude login credentials by default** (no tokens shipped to git/cloud).

## Sessions "lost" without a machine move: re-home

Non-ASCII project paths often collapse several directories into one slug, so `claude --resume` mixes them up or loses them. Fix it without any backup:

```bash
python3 scripts/vault.py rehome            # dry-run: list what would move where
python3 scripts/vault.py rehome --apply    # apply after review
```

## Subcommand cheat sheet

| Subcommand | Purpose | Safe defaults |
|---|---|---|
| `backup` | Pack `~/.claude` into a snapshot | no credentials; keep newest 14 |
| `restore` | Restore from a snapshot to a new machine / account | dry-run; protects login; auto pre-restore snapshot |
| `rehome` | Re-file mislocated sessions by real cwd | dry-run; writes a manifest |
| `status` | Show snapshot & auto-backup status | read-only |
| `schedule` | Install / remove daily auto-backup | — |

## Safety guarantees (remember these)

- `restore` and `rehome` are **dry-run by default** — they only print a plan + write a manifest; `--apply` performs it.
- `restore --apply` snapshots the current `~/.claude` first, so it's reversible.
- Backup and restore **never touch login credentials by default**; only `backup --include-credentials` (same account, same user, just a new machine) includes them.
- Every change is written to a manifest under `~/.claude-vault/manifests/` for line-by-line auditing.
- **Migrates content, not access**: this moves content/account-state files to a new account/machine; it does NOT restore access to a banned account (sign in with a working account first). Snapshots contain your data — keep the `.tar.*` files private.

## How it works, in one line

Claude stores each conversation as `~/.claude/projects/<slug>/<id>.jsonl`, where `<slug>` is the working
directory with every non-`[A-Za-z0-9]` character (including `/` and CJK) replaced by `-`. Moving machines or
using non-ASCII paths makes the slug mismatch or collide, so sessions become "unfindable" — the tool repairs
this using the real `cwd` recorded inside each session. See `README.md` and `scripts/vault.py --help` for details.
