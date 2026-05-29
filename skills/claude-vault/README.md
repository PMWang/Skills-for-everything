# claude-vault

**English** · [简体中文](./README.zh-CN.md)

> **EN —** **Recover your Claude Code account in seconds after a ban** (also covers device changes and switching to a new account). claude-vault snapshots your whole `~/.claude` — conversation history, `CLAUDE.md`, skills, settings, memory — and safely restores it to a fresh account or machine, then fixes the "`claude --resume` can't find my old conversations" problem caused by moving machines or by non-ASCII (CJK) project paths. Cross-platform, pure Python stdlib, no dependencies.
>
> **中文 —** **账号被封后几秒钟恢复你的 Claude Code 账号**（换机、换号同样适用）。claude-vault 把整个 `~/.claude`——对话历史、`CLAUDE.md`、skills、设置、memory——打包成快照，安全恢复到新号或新机，并修复换机 / 中文路径导致「`claude --resume` 找不到历史对话」的问题。跨平台、纯 Python 标准库、零依赖。

## Why

- **Ban recovery** — when your Claude account is banned, register a new one and move all your history and config over in seconds.
- **Machine migration** — carry conversations, skills, and memory from an old computer to a new one.
- **Lost sessions** — non-ASCII project paths can collapse several real directories into the same name under `~/.claude/projects/` (slug collision), so `claude --resume` mixes them up or loses them. This tool re-files them by each session's true working directory.

## Install

Requires Python 3.8+ (preinstalled on macOS and most Linux). No dependencies to install.

```bash
git clone <this-repo> claude-vault
cd claude-vault
python3 scripts/vault.py status        # self-check
```

Use it as a Claude Code skill (optional) — symlink the folder into `~/.claude/skills/`, then just describe what you need in any session:

```bash
ln -s "$(pwd)" ~/.claude/skills/claude-vault     # macOS / Linux
```

> UI language follows your system locale (English by default, Chinese on a `zh*` locale). Force it with `CLAUDE_VAULT_LANG=en` or `CLAUDE_VAULT_LANG=zh`.

## Quick start

```bash
# Take a snapshot now
python3 scripts/vault.py backup

# Enable a daily 03:00 auto-backup (macOS launchd / Linux cron)
python3 scripts/vault.py schedule --time 03:00

# Check status
python3 scripts/vault.py status
```

### Recover after a ban / on a new machine (the core flow)

On the Claude Code where your **new account is already signed in**:

```bash
# 1) Dry-run (default): touches nothing, just prints a plan + writes a manifest
python3 scripts/vault.py restore ~/Downloads/claude-vault-2026-05-28_030005.tar.zst

# 2) Apply once the plan looks right
python3 scripts/vault.py restore ~/Downloads/claude-vault-2026-05-28_030005.tar.zst --apply

# 'latest' picks the newest snapshot in ~/.claude-vault/snapshots
python3 scripts/vault.py restore latest --apply
```

What restore does:

- **Protects the new login** — never writes `.credentials.json`; when merging `.claude.json` it keeps the account fields already on the new machine (`oauthAccount` / `userID`), so the old account binding is not pasted back.
- **Cross-machine / cross-username remap** — auto-detects the old home prefix in the archive (e.g. `/Users/old`); if it differs from this machine's home, it recomputes each session's project-dir slug and rewrites the in-file `cwd`. Override with `--old-home` / `--new-home`, or disable with `--no-remap`.
- **Reversible** — creates a pre-restore snapshot before applying (unless `--no-safety`).
- **Leaves existing settings alone** (`--mode content`, the default) — only fills in missing content; use `--mode full` to overwrite everything, or `--overwrite` to overwrite file by file.

### Re-home sessions (same machine, `--resume` is just messed up)

```bash
python3 scripts/vault.py rehome            # dry-run: list what would move
python3 scripts/vault.py rehome --apply    # do it
```

## Subcommands

| Subcommand | What it does |
|---|---|
| `backup [--keep N] [--gzip] [--include-credentials]` | Pack `~/.claude` into `~/.claude-vault/snapshots/`, keep newest N (default 14), zstd if available else gzip, credentials excluded by default |
| `restore [archive\|latest] [--apply] [--mode content\|full] [--overwrite] [--no-safety] [--no-remap] [--old-home P] [--new-home P]` | Restore from a snapshot, dry-run by default |
| `rehome [--apply]` | Re-file mislocated sessions by their real cwd, dry-run by default |
| `status` | Snapshot count / size / latest time / whether auto-backup is installed / session count |
| `schedule [--time HH:MM] [--uninstall]` | Install / remove a daily auto-backup |

## How it works

Claude Code stores each conversation at:

```
~/.claude/projects/<slug>/<session-id>.jsonl
```

`<slug>` = the working directory you launched `claude` from, with **every non-`[A-Za-z0-9]` character (including `/`, `-`, `.`, and CJK) replaced by `-`** (no collapsing of runs, no trimming, no case change). For example:

```
/Users/you/Documents/我的项目/skill-archive
→ -Users-you-Documents------skill-archive
```

Two problems follow, both of which this tool fixes:

1. **CJK path collision** — different non-ASCII directories can collapse to the same slug, so conversations pile up together and `--resume` mixes them.
2. **Machine mismatch** — the old slug embeds the old username / path; on a new machine it no longer matches, so history "disappears."

Every `.jsonl` records its real `"cwd"` internally. The tool reads that `cwd` → recomputes the correct slug → files the session in the right directory (rewriting `cwd` on a cross-machine move), restoring what `claude --resume` recognizes.

## Cross-platform

| Platform | backup / restore / rehome | auto-backup |
|---|---|---|
| macOS | ✅ | ✅ launchd |
| Linux | ✅ | ✅ cron |
| Windows | ✅ | prints a `schtasks` command to run manually |

Compression: uses the `zstd` command if present, otherwise falls back to gzip. The restore side reads both; decompressing `.zst` without the `zstandard` module shells out to `zstd` (`brew install zstd` / `apt install zstd`).

## Safety

- `restore` / `rehome` are **dry-run by default**; only `--apply` touches files.
- `restore --apply` snapshots the current `~/.claude` first, so it's reversible.
- Backup / restore **exclude login credentials by default** — no tokens are written into archives or onto the new machine.
- Every operation writes a manifest to `~/.claude-vault/manifests/` for auditing.

## Privacy & limitations

- **Snapshots contain your data.** A backup includes prompts, tool outputs, memory, settings, and skill contents from `~/.claude`. Treat the `.tar.*` files as sensitive — don't commit them to public git or upload them somewhere untrusted.
- **Manifests and logs include local context.** Files under `~/.claude-vault/manifests/` and `vault.log` record local paths, project names, and operation history. Review before sharing.
- **macOS Keychain / browser state is not covered.** Login tokens stored in the system Keychain or a browser are neither backed up nor restored — by design.
- **This migrates content, not access.** claude-vault helps you move your *content and account-state files* to a new account/machine. It does **not** restore access to a banned account — you must sign in with a working account first.

## License

MIT — see [LICENSE](./LICENSE).
