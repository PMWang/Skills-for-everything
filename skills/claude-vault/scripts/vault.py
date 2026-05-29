#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
claude-vault — back up, migrate, and recover Claude Code account data.

Cross-platform (macOS / Linux / Windows). Python 3.8+ standard library only.

Subcommands:
  backup    Snapshot ~/.claude into an archive (login credentials excluded by default).
  restore   One-click restore to a new machine/account (protects the new login; dry-run by default).
  rehome    Re-file mislocated conversations on this machine by their real cwd.
  status    Show snapshot health and whether scheduled backup is installed.
  schedule  Install/remove a daily backup (macOS launchd / Linux cron / Windows prints the command).

Built for the "recover content on a fresh account after a ban" scenario:
  * Credential safety: restore never writes .credentials.json and keeps the target machine's
    account fields (oauthAccount / userID) in .claude.json, so it won't break the new login
    or re-link the banned old account.
  * Cross-machine remap: sessions are filed under a slug derived from their cwd; after moving
    machine/username the old slug no longer matches, so this tool recomputes the slug (and can
    rewrite the in-file cwd) to make `claude --resume` find them again.
  * Safety: every move/rewrite is dry-run by default (prints a plan + writes a manifest);
    --apply performs it; restore auto-creates a pre-restore snapshot for rollback.

All paths are derived at runtime from Path.home(); the distributed source contains no user info.

UI language: English by default. Set CLAUDE_VAULT_LANG=zh (or use a zh* system locale) for Chinese.
"""

from __future__ import annotations

import argparse
import json
import os
import platform
import re
import shutil
import subprocess
import sys
import tarfile
import tempfile
import time
from datetime import datetime
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Tuple


# ----------------------------------------------------------------------------
# UI language (English default; Chinese on CLAUDE_VAULT_LANG=zh or a zh* locale)
# ----------------------------------------------------------------------------
def _detect_lang() -> str:
    forced = os.environ.get("CLAUDE_VAULT_LANG", "").strip().lower()
    if forced.startswith("zh"):
        return "zh"
    if forced.startswith("en"):
        return "en"
    loc = (os.environ.get("LC_ALL") or os.environ.get("LC_MESSAGES")
           or os.environ.get("LANG") or "").lower()
    return "zh" if loc.startswith("zh") else "en"


LANG = _detect_lang()


def L(en: str, zh: str) -> str:
    """Return the message in the active UI language."""
    return zh if LANG == "zh" else en


# ----------------------------------------------------------------------------
# Paths & constants (all derived from HOME at runtime — no hardcoded user info)
# ----------------------------------------------------------------------------
HOME = Path.home()
CLAUDE_DIR = Path(os.environ.get("CLAUDE_CONFIG_DIR", HOME / ".claude"))
VAULT_DIR = Path(os.environ.get("CLAUDE_VAULT_DIR", HOME / ".claude-vault"))
SNAP_DIR = VAULT_DIR / "snapshots"
MANIFEST_DIR = VAULT_DIR / "manifests"
LOG_FILE = VAULT_DIR / "vault.log"

SCHED_LABEL = "com.claudevault.backup"  # generic label, not user-specific

# Claude Code's project-dir slug rule: replace every non-[A-Za-z0-9] char in the
# cwd with '-' (including '/', '-', '.', CJK, ...); no collapsing of runs, no
# trimming, no case change.
SLUG_RE = re.compile(r"[^A-Za-z0-9]")

# Field inside a session .jsonl that records the working directory.
CWD_RE = re.compile(r'"cwd"\s*:\s*"((?:\\.|[^"\\])*)"')

# Directory names / suffixes excluded from snapshots (regenerable or irrelevant bulk).
EXCLUDE_NAMES = {"node_modules", ".build", "DerivedData", "build",
                 "__pycache__", ".git", ".DS_Store"}
EXCLUDE_SUFFIX = {".pyc"}

# The real secret file: never backed up / restored by default.
CREDENTIAL_FILES = {".credentials.json"}
# Account-identity fields in .claude.json: kept from the TARGET machine on restore
# so the freshly logged-in account is not clobbered by the old one.
ACCOUNT_KEYS = {"oauthAccount", "userID", "organizationUuid"}


# ----------------------------------------------------------------------------
# Small helpers
# ----------------------------------------------------------------------------
def log(msg: str) -> None:
    line = f"{datetime.now().isoformat(timespec='seconds')}  {msg}"
    print(line)
    try:
        VAULT_DIR.mkdir(parents=True, exist_ok=True)
        with open(LOG_FILE, "a", encoding="utf-8") as fh:
            fh.write(line + "\n")
    except OSError:
        pass


def human_size(num: float) -> str:
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if num < 1024 or unit == "TB":
            return f"{num:.1f}{unit}" if unit != "B" else f"{int(num)}B"
        num /= 1024
    return f"{num:.1f}TB"


def slugify(path: str) -> str:
    """Reproduce how Claude Code turns a cwd into its projects/ subdirectory name."""
    return SLUG_RE.sub("-", str(path))


def have(binary: str) -> bool:
    return shutil.which(binary) is not None


def ts() -> str:
    return datetime.now().strftime("%Y-%m-%d_%H%M%S")


def find_cwd_in_jsonl(path: Path, max_lines: int = 5000) -> Optional[str]:
    """Stream a session file and return the first cwd (unescaped), or None."""
    try:
        with open(path, "r", encoding="utf-8", errors="ignore") as fh:
            for i, line in enumerate(fh):
                if i >= max_lines:
                    break
                m = CWD_RE.search(line)
                if m:
                    try:
                        return json.loads('"' + m.group(1) + '"')
                    except json.JSONDecodeError:
                        return m.group(1)
    except OSError:
        return None
    return None


def iter_session_files(projects_dir: Path) -> Iterable[Path]:
    if not projects_dir.is_dir():
        return
    for proj in sorted(projects_dir.iterdir()):
        if proj.is_dir():
            for f in sorted(proj.glob("*.jsonl")):
                yield f


def write_manifest(name: str, header: Dict[str, str], rows: List[Tuple[str, ...]]) -> Path:
    MANIFEST_DIR.mkdir(parents=True, exist_ok=True)
    out = MANIFEST_DIR / f"{name}_{ts()}.tsv"
    with open(out, "w", encoding="utf-8") as fh:
        for k, v in header.items():
            fh.write(f"# {k}: {v}\n")
        for row in rows:
            fh.write("\t".join(str(c) for c in row) + "\n")
    return out


def safe_extract(tar: tarfile.TarFile, dest: Path) -> None:
    """Single forward-pass, traversal-safe extraction.

    Works with streaming archives (mode 'r|', which cannot seek backwards) and
    on Python 3.8+ (no reliance on the 3.12 filter='data' argument)."""
    dest = dest.resolve()
    base = str(dest) + os.sep
    for member in tar:  # streaming yields members in archive order; extract as we go
        target = (dest / member.name).resolve()
        if not (str(target).startswith(base) or target == dest):
            raise RuntimeError(f"archive contains an out-of-bounds path, aborting: {member.name}")
        if member.islnk() or member.issym():
            link_target = (target.parent / member.linkname).resolve()
            if not (str(link_target).startswith(base) or link_target == dest):
                continue  # skip links pointing outside the archive
        elif not (member.isfile() or member.isdir()):
            continue  # data-only policy: skip FIFOs, devices, and other special members
        tar.extract(member, dest)


# ----------------------------------------------------------------------------
# Compression / decompression (prefer zstd, fall back to gzip; restore reads both)
# ----------------------------------------------------------------------------
def _tar_filter(tarinfo: tarfile.TarInfo, include_credentials: bool):
    name = tarinfo.name
    base = os.path.basename(name)
    if base in EXCLUDE_NAMES:
        return None
    if any(part in EXCLUDE_NAMES for part in name.split("/")):
        return None
    if os.path.splitext(base)[1] in EXCLUDE_SUFFIX:
        return None
    if not include_credentials and base in CREDENTIAL_FILES:
        return None
    return tarinfo


def create_archive(src_dir: Path, out_path: Path, use_zstd: bool,
                   include_credentials: bool) -> None:
    arcname = src_dir.name  # e.g. ".claude"
    flt = lambda ti: _tar_filter(ti, include_credentials)
    if use_zstd:
        proc = subprocess.Popen(["zstd", "-3", "-T0", "-q", "-o", str(out_path)],
                                stdin=subprocess.PIPE)
        try:
            with tarfile.open(fileobj=proc.stdin, mode="w|") as tar:
                tar.add(str(src_dir), arcname=arcname, filter=flt)
        finally:
            if proc.stdin:
                proc.stdin.close()
            proc.wait()
        if proc.returncode != 0:
            raise RuntimeError("zstd compression failed")
    else:
        with tarfile.open(str(out_path), "w:gz") as tar:
            tar.add(str(src_dir), arcname=arcname, filter=flt)


def open_archive_for_read(archive: Path) -> Tuple[tarfile.TarFile, Optional[subprocess.Popen]]:
    """Return (TarFile, proc). For .zst prefer the zstandard module, else the zstd binary."""
    name = archive.name
    if name.endswith(".tar.gz") or name.endswith(".tgz"):
        return tarfile.open(str(archive), "r:gz"), None
    if name.endswith(".tar"):
        return tarfile.open(str(archive), "r:"), None
    if name.endswith(".tar.zst") or name.endswith(".zst"):
        try:
            import zstandard  # type: ignore
            dctx = zstandard.ZstdDecompressor()
            fh = open(archive, "rb")
            reader = dctx.stream_reader(fh)
            return tarfile.open(fileobj=reader, mode="r|"), None
        except ImportError:
            if not have("zstd"):
                raise RuntimeError(
                    "Need to decompress .zst but neither the 'zstandard' Python module "
                    "nor the 'zstd' command is available.\n"
                    "  macOS:  brew install zstd\n"
                    "  Debian: sudo apt install zstd\n"
                    "  or:     pip install zstandard")
            proc = subprocess.Popen(["zstd", "-d", "-c", str(archive)],
                                    stdout=subprocess.PIPE)
            return tarfile.open(fileobj=proc.stdout, mode="r|"), proc
    raise RuntimeError(f"unrecognized archive format: {archive.name}")


# ----------------------------------------------------------------------------
# backup
# ----------------------------------------------------------------------------
def cmd_backup(args: argparse.Namespace) -> int:
    if not CLAUDE_DIR.is_dir():
        log(L(f"Claude directory not found: {CLAUDE_DIR}", f"找不到 Claude 目录：{CLAUDE_DIR}"))
        return 1
    if args.keep < 1:
        log(L("--keep must be >= 1.", "--keep 必须 >= 1。"))
        return 1
    SNAP_DIR.mkdir(parents=True, exist_ok=True)
    use_zstd = (not args.gzip) and have("zstd")
    ext = "tar.zst" if use_zstd else "tar.gz"
    out = SNAP_DIR / f"claude-vault-{ts()}.{ext}"
    cred_note = "" if args.include_credentials else L(" (credentials excluded)", "（不含登录凭证）")
    log(L(f"Backing up {CLAUDE_DIR}  ->  {out}{cred_note}",
          f"开始备份 {CLAUDE_DIR}  →  {out}{cred_note}"))
    t0 = time.time()
    create_archive(CLAUDE_DIR, out, use_zstd, args.include_credentials)
    size = out.stat().st_size
    log(L(f"Done {human_size(size)} in {time.time()-t0:.1f}s",
          f"完成 {human_size(size)}，用时 {time.time()-t0:.1f}s"))

    # Rotate: keep newest N
    snaps = sorted(SNAP_DIR.glob("claude-vault-*.tar.*"),
                   key=lambda p: p.stat().st_mtime, reverse=True)
    for old in snaps[args.keep:]:
        log(L(f"Rotated out old snapshot: {old.name}", f"滚动删除旧快照：{old.name}"))
        old.unlink(missing_ok=True)
    return 0


# ----------------------------------------------------------------------------
# rehome — re-file sessions on this machine
# ----------------------------------------------------------------------------
def plan_rehome(projects_dir: Path) -> Tuple[List[Tuple[str, str, str, str]], int, int]:
    """Return (rows, no_cwd, conflict). row = (action, src, dst, cwd)."""
    rows: List[Tuple[str, str, str, str]] = []
    no_cwd = 0
    conflict = 0
    for f in iter_session_files(projects_dir):
        cwd = find_cwd_in_jsonl(f)
        if not cwd:
            no_cwd += 1
            continue
        correct = slugify(cwd)
        if f.parent.name == correct:
            continue
        dst = projects_dir / correct / f.name
        if dst.exists():
            rows.append(("SKIP-EXISTS", str(f), str(dst), cwd))
            conflict += 1
        else:
            rows.append(("MOVE", str(f), str(dst), cwd))
    return rows, no_cwd, conflict


def cmd_rehome(args: argparse.Namespace) -> int:
    projects = CLAUDE_DIR / "projects"
    rows, no_cwd, conflict = plan_rehome(projects)
    moves = [r for r in rows if r[0] == "MOVE"]
    log(L(f"Scanning {projects}", f"扫描 {projects}"))
    log(L(f"  to re-home {len(moves)}, name-clash skipped {conflict}, no-cwd skipped {no_cwd}",
          f"  待归位 {len(moves)}，重名跳过 {conflict}，无 cwd 跳过 {no_cwd}"))
    for action, src, dst, cwd in rows[:40]:
        print(f"  [{action}] {Path(src).parent.name}/{Path(src).name}"
              f"  ->  {Path(dst).parent.name}/  (cwd={cwd})")
    if len(rows) > 40:
        print(L(f"  ... {len(rows)-40} more in manifest", f"  …… 其余 {len(rows)-40} 条见 manifest"))

    mf = write_manifest("rehome", {
        "mode": "APPLY" if args.apply else "DRY-RUN",
        "projects_dir": str(projects),
        "moves": str(len(moves)), "conflicts": str(conflict), "no_cwd": str(no_cwd),
    }, rows)
    log(f"manifest: {mf}")

    if not args.apply:
        log(L("Dry-run — nothing changed. Re-run with --apply once the plan looks right.",
              "这是 dry-run，未改动任何文件。确认无误后加 --apply 执行。"))
        return 0
    done = 0
    for action, src, dst, _ in rows:
        if action != "MOVE":
            continue
        Path(dst).parent.mkdir(parents=True, exist_ok=True)
        shutil.move(src, dst)
        done += 1
    log(L(f"Re-homed {done} session files.", f"已归位 {done} 个会话文件。"))
    return 0


# ----------------------------------------------------------------------------
# restore — one-click restore from a snapshot (ban -> fresh account)
# ----------------------------------------------------------------------------
def detect_old_home(cwds: List[str]) -> Optional[str]:
    """Guess the old machine's home prefix (/Users/x, /home/x, C:\\Users\\x) from cwds."""
    pats = [re.compile(r"^(/Users/[^/]+)"),
            re.compile(r"^(/home/[^/]+)"),
            re.compile(r"^([A-Za-z]:\\Users\\[^\\]+)")]
    counts: Dict[str, int] = {}
    for c in cwds:
        for p in pats:
            m = p.match(c)
            if m:
                counts[m.group(1)] = counts.get(m.group(1), 0) + 1
    if not counts:
        return None
    return max(counts, key=counts.get)


def merge_claude_json(backup_json: Path, target_json: Path, apply: bool,
                      mode: str, overwrite: bool) -> List[str]:
    """Reconcile .claude.json without clobbering the target machine's account/settings.

    content mode (default): target-first — keep ALL existing target keys, only fill keys
      that are MISSING on the target from the backup. Existing settings are never replaced.
    full / --overwrite: backup content wins, but the account identity is always taken from
      the target so a fresh login is never broken or re-linked to the old account.
    """
    notes = []
    try:
        bj = json.loads(backup_json.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return [L("skip .claude.json (unparseable in backup)", "跳过 .claude.json（备份内不可解析）")]
    if not isinstance(bj, dict):
        return [L("skip .claude.json (not an object)", "跳过 .claude.json（不是对象）")]
    tj = {}
    target_exists = target_json.exists()
    if target_exists:
        try:
            loaded = json.loads(target_json.read_text(encoding="utf-8"))
            tj = loaded if isinstance(loaded, dict) else {}
        except (OSError, json.JSONDecodeError):
            tj = {}
    # The old account's identity must never enter the restored config.
    for k in ACCOUNT_KEYS:
        bj.pop(k, None)

    additive = target_exists and mode == "content" and not overwrite
    if additive:
        merged = {**bj, **tj}   # target wins on every conflict; backup only fills gaps
        notes.append(L("content mode: kept all existing .claude.json keys, filled only missing ones",
                       "content 模式：保留目标机 .claude.json 全部已有键，仅补缺失键"))
    else:
        merged = dict(bj)
        if target_exists:
            notes.append(L("full/overwrite mode: backup .claude.json wins (account identity kept from target)",
                           "full/overwrite 模式：以备份为准（账号身份仍取自目标机）"))
    # Account identity always comes from the target machine, never the backup.
    for k in ACCOUNT_KEYS:
        if k in tj:
            merged[k] = tj[k]
    if not any(k in tj for k in ACCOUNT_KEYS):
        notes.append(L("WARNING: target .claude.json has no account identity — sign in to the new account first",
                       "警告：目标机 .claude.json 无账号身份字段 —— 请先登录新账号再恢复"))
    if apply:
        if target_exists:
            bak = target_json.with_suffix(f".json.pre-vault-{ts()}")
            shutil.copy2(target_json, bak)
            notes.append(L(f"backed up existing .claude.json as {bak.name}",
                           f"原 .claude.json 备份为 {bak.name}"))
        target_json.write_text(json.dumps(merged, ensure_ascii=False, indent=2), encoding="utf-8")
        notes.append(L("wrote .claude.json", "已写入 .claude.json"))
    else:
        notes.append(L("[dry-run] would write .claude.json (account identity protected)",
                       "[dry-run] 将写入 .claude.json（账号身份受保护）"))
    return notes


def cmd_restore(args: argparse.Namespace) -> int:
    # 1) pick the archive
    if args.archive == "latest":
        snaps = sorted(SNAP_DIR.glob("claude-vault-*.tar.*"),
                       key=lambda p: p.stat().st_mtime, reverse=True)
        if not snaps:
            log(L(f"No snapshot found in {SNAP_DIR}. Use: restore <archive-path>.",
                  f"在 {SNAP_DIR} 找不到任何快照。请用 restore <归档路径>。"))
            return 1
        archive = snaps[0]
    else:
        archive = Path(args.archive).expanduser()
    if not archive.exists():
        log(L(f"Archive not found: {archive}", f"归档不存在：{archive}"))
        return 1
    mode_tag = "APPLY" if args.apply else "DRY-RUN"
    log(L(f"Restoring from {archive.name}  mode={args.mode}  {mode_tag}",
          f"准备从归档恢复：{archive.name}  模式={args.mode}  {mode_tag}"))

    # 2) pre-restore safety snapshot (only when applying)
    if args.apply and not args.no_safety and CLAUDE_DIR.is_dir():
        SNAP_DIR.mkdir(parents=True, exist_ok=True)
        safety = SNAP_DIR / f"pre-restore-{ts()}.tar.gz"
        log(L(f"Creating pre-restore snapshot first: {safety.name}",
              f"先生成恢复前快照：{safety.name}"))
        create_archive(CLAUDE_DIR, safety, use_zstd=False, include_credentials=False)

    # 3) extract to a temp dir
    tmp = Path(tempfile.mkdtemp(prefix="claude-vault-"))
    tar, proc = open_archive_for_read(archive)
    try:
        safe_extract(tar, tmp)
    except BaseException:
        shutil.rmtree(tmp, ignore_errors=True)  # never leave a temp dir behind on failure
        raise
    finally:
        tar.close()
        if proc:
            proc.wait()
    src_root = tmp / ".claude"
    if not src_root.is_dir():
        # tolerate archives packed without a top-level .claude
        src_root = tmp
    src_projects = src_root / "projects"

    rows: List[Tuple[str, ...]] = []

    # 4) work out the home remap
    sample_cwds: List[str] = []
    for f in iter_session_files(src_projects):
        c = find_cwd_in_jsonl(f)
        if c:
            sample_cwds.append(c)
        if len(sample_cwds) >= 200:
            break
    old_home = args.old_home or detect_old_home(sample_cwds)
    new_home = args.new_home or str(HOME)
    remap = bool(old_home) and old_home != new_home and not args.no_remap
    if old_home:
        log(L(f"Detected old-machine home prefix: {old_home}",
              f"检测到旧机 home 前缀：{old_home}"))
    log(L(f"This machine home: {new_home}  ->  {'remap ON' if remap else 'no remap'}",
          f"本机 home：{new_home}  ->  {'启用重映射' if remap else '不重映射'}"))

    # 5) walk archive contents, decide each file's destination
    if args.apply:  # dry-run must not create the target config directory
        CLAUDE_DIR.mkdir(parents=True, exist_ok=True)
    sessions = 0
    field_changes = 0
    for f in iter_session_files(src_projects):
        cwd = find_cwd_in_jsonl(f) or ""
        new_cwd = _remap_path(cwd, old_home, new_home) if (remap and cwd) else cwd
        target_slug = slugify(new_cwd) if new_cwd else f.parent.name
        dst = CLAUDE_DIR / "projects" / target_slug / f.name
        action = "SESSION"
        if dst.exists():
            action = "SESSION-SKIP"
        rows.append((action, str(f.relative_to(src_root)), str(dst), cwd, new_cwd))
        if args.apply and action == "SESSION":
            dst.parent.mkdir(parents=True, exist_ok=True)
            if remap and new_cwd != cwd:
                field_changes += _copy_jsonl_remapped(f, dst, old_home, new_home)
            else:
                shutil.copy2(f, dst)
        sessions += 1

    # 6) everything else (skills / memory / CLAUDE.md / settings ...) per mode
    for path in _walk_non_projects(src_root):
        rel = path.relative_to(src_root)
        base = path.name
        if base in CREDENTIAL_FILES:
            rows.append(("SKIP-CRED", str(rel), "", "", ""))
            continue
        if base == ".claude.json":
            for note in merge_claude_json(path, CLAUDE_DIR / ".claude.json",
                                          args.apply, args.mode, args.overwrite):
                rows.append(("CLAUDE-JSON", str(rel), note, "", ""))
            continue
        dst = CLAUDE_DIR / rel
        exists = dst.exists()
        if exists and args.mode == "content" and not args.overwrite:
            # content mode keeps existing config so the fresh account's settings stay intact
            rows.append(("KEEP-TARGET", str(rel), str(dst), "", ""))
            continue
        rows.append(("FILE-OVERWRITE" if exists else "FILE-NEW", str(rel), str(dst), "", ""))
        if args.apply:
            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(path, dst)

    # 7) manifest + wrap up
    new_sessions = sum(1 for r in rows if r[0] == "SESSION")
    skip_sessions = sum(1 for r in rows if r[0] == "SESSION-SKIP")
    log(L(f"Sessions: {new_sessions} new, {skip_sessions} already present skipped (scanned {sessions})",
          f"会话：新增 {new_sessions}，已存在跳过 {skip_sessions}（共扫描 {sessions}）"))
    if remap:
        log(L(f"Remap: rewrote cwd in {field_changes} session field(s)",
              f"重映射：改写了 {field_changes} 处会话 cwd 字段"))
    mf = write_manifest("restore", {
        "archive": str(archive), "mode": args.mode,
        "apply": str(args.apply), "remap": str(remap),
        "old_home": old_home or "", "new_home": new_home,
        "new_sessions": str(new_sessions),
        "remap_field_changes": str(field_changes),
    }, rows)
    log(f"manifest: {mf}")
    shutil.rmtree(tmp, ignore_errors=True)

    if not args.apply:
        log(L("Dry-run — ~/.claude not touched. Re-run with --apply after checking the manifest.",
              "这是 dry-run，未改动 ~/.claude。确认 manifest 无误后加 --apply 执行。"))
    else:
        log(L("Restore complete. Restart Claude Code; `claude --resume` should list your history.",
              "恢复完成。重启 Claude Code，用 `claude --resume` 应能看到历史对话。"))
    return 0


def _walk_non_projects(root: Path) -> Iterable[Path]:
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if d not in EXCLUDE_NAMES]
        rel = Path(dirpath).relative_to(root)
        if rel.parts and rel.parts[0] == "projects":
            continue
        for fn in filenames:
            if os.path.splitext(fn)[1] in EXCLUDE_SUFFIX:
                continue
            yield Path(dirpath) / fn


def _remap_path(value: str, old: str, new: str) -> str:
    """Remap only when `old` is a real path prefix of `value` at a separator boundary.

    Prevents the substring-corruption class of bug, e.g. old=/Users/ann new=/Users/bob must
    NOT touch /Users/ann2/shared (different user) — only /Users/ann or /Users/ann/... is remapped.
    """
    if not old or not value:
        return value
    if value == old:
        return new
    for sep in ("/", "\\"):
        if value.startswith(old + sep):
            return new + value[len(old):]
    return value


def _copy_jsonl_remapped(src: Path, dst: Path, old_home: str, new_home: str) -> int:
    """Copy a session, rewriting ONLY known path fields (cwd) whose value is under old_home
    at a path boundary. Conversation text, code snippets, and unrelated paths are never touched.

    Each line is parsed as JSON and only re-serialized when a path field actually changed;
    non-JSON lines are passed through verbatim. Returns the number of fields changed.
    """
    path_fields = ("cwd",)
    changed = 0
    with open(src, "r", encoding="utf-8", errors="ignore") as fin, \
         open(dst, "w", encoding="utf-8") as fout:
        for line in fin:
            stripped = line.strip()
            if stripped:
                try:
                    obj = json.loads(stripped)
                except json.JSONDecodeError:
                    fout.write(line)
                    continue
                if isinstance(obj, dict):
                    line_changed = False
                    for field in path_fields:
                        v = obj.get(field)
                        if isinstance(v, str):
                            nv = _remap_path(v, old_home, new_home)
                            if nv != v:
                                obj[field] = nv
                                changed += 1
                                line_changed = True
                    if line_changed:
                        fout.write(json.dumps(obj, ensure_ascii=False) + "\n")
                    else:
                        fout.write(line)
                    continue
            fout.write(line)
    return changed


# ----------------------------------------------------------------------------
# status
# ----------------------------------------------------------------------------
def scheduler_installed() -> Optional[str]:
    sysname = platform.system()
    try:
        if sysname == "Darwin":
            plist = HOME / "Library" / "LaunchAgents" / f"{SCHED_LABEL}.plist"
            return str(plist) if plist.exists() else None
        if sysname == "Linux":
            out = subprocess.run(["crontab", "-l"], capture_output=True, text=True)
            return "cron" if SCHED_LABEL in out.stdout else None
        if sysname == "Windows":
            out = subprocess.run(["schtasks", "/query", "/tn", SCHED_LABEL],
                                 capture_output=True, text=True)
            return "schtasks" if out.returncode == 0 else None
    except (OSError, FileNotFoundError):
        return None
    return None


def cmd_status(args: argparse.Namespace) -> int:
    exists = L("exists", "存在") if CLAUDE_DIR.is_dir() else L("missing", "不存在")
    print(L(f"Claude dir     : {CLAUDE_DIR}  ({exists})",
            f"Claude 目录   : {CLAUDE_DIR}  ({exists})"))
    print(L(f"Vault dir      : {VAULT_DIR}", f"Vault 目录    : {VAULT_DIR}"))
    snaps = sorted(SNAP_DIR.glob("claude-vault-*.tar.*"),
                   key=lambda p: p.stat().st_mtime, reverse=True) if SNAP_DIR.is_dir() else []
    total = sum(p.stat().st_size for p in snaps)
    print(L(f"Snapshots      : {len(snaps)}  total {human_size(total)}",
            f"快照数量      : {len(snaps)}  合计 {human_size(total)}"))
    if snaps:
        newest = snaps[0]
        age_h = (time.time() - newest.stat().st_mtime) / 3600
        print(L(f"Latest snapshot: {newest.name}  ({human_size(newest.stat().st_size)}, {age_h:.1f}h ago)",
                f"最新快照      : {newest.name}  ({human_size(newest.stat().st_size)}, {age_h:.1f}h 前)"))
    sched = scheduler_installed()
    sched_txt = (L("installed -> ", "已安装 → ") + sched) if sched else \
        L("not installed (run `schedule` to enable)", "未安装（运行 schedule 开启）")
    print(L(f"Auto-backup    : {sched_txt}", f"自动备份      : {sched_txt}"))
    if CLAUDE_DIR.is_dir():
        proj = CLAUDE_DIR / "projects"
        nproj = sum(1 for _ in proj.iterdir()) if proj.is_dir() else 0
        nsess = sum(1 for _ in iter_session_files(proj))
        print(L(f"Sessions       : {nsess} conversations across {nproj} project dirs",
                f"本机会话      : {nsess} 个对话，分布在 {nproj} 个项目目录"))
    return 0


# ----------------------------------------------------------------------------
# schedule — daily auto-backup
# ----------------------------------------------------------------------------
def cmd_schedule(args: argparse.Namespace) -> int:
    sysname = platform.system()
    try:
        hh, mm = (int(x) for x in args.time.split(":"))
        if not (0 <= hh <= 23 and 0 <= mm <= 59):
            raise ValueError
    except (ValueError, TypeError):
        log(L(f"Invalid --time '{args.time}', expected HH:MM (00:00-23:59).",
              f"--time '{args.time}' 不合法，应为 HH:MM（00:00-23:59）。"))
        return 1
    py = sys.executable
    script = str(Path(__file__).resolve())

    if args.uninstall:
        if sysname == "Darwin":
            plist = HOME / "Library" / "LaunchAgents" / f"{SCHED_LABEL}.plist"
            subprocess.run(["launchctl", "unload", str(plist)], check=False)
            plist.unlink(missing_ok=True)
            log(L("Uninstalled launchd auto-backup.", "已卸载 launchd 自动备份。"))
        elif sysname == "Linux":
            cur = subprocess.run(["crontab", "-l"], capture_output=True, text=True).stdout
            kept = "".join(l for l in cur.splitlines(keepends=True) if SCHED_LABEL not in l)
            subprocess.run(["crontab", "-"], input=kept, text=True)
            log(L("Removed auto-backup from crontab.", "已从 crontab 移除自动备份。"))
        else:
            print(L(f"On Windows, delete the task manually: schtasks /delete /tn {SCHED_LABEL} /f",
                    f"Windows 请手动删除计划任务：schtasks /delete /tn {SCHED_LABEL} /f"))
        return 0

    if sysname == "Darwin":
        plist_dir = HOME / "Library" / "LaunchAgents"
        plist_dir.mkdir(parents=True, exist_ok=True)
        plist = plist_dir / f"{SCHED_LABEL}.plist"
        plist.write_text(f"""<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0"><dict>
  <key>Label</key><string>{SCHED_LABEL}</string>
  <key>ProgramArguments</key>
  <array><string>{py}</string><string>{script}</string><string>backup</string></array>
  <key>StartCalendarInterval</key>
  <dict><key>Hour</key><integer>{hh}</integer><key>Minute</key><integer>{mm}</integer></dict>
  <key>StandardOutPath</key><string>{LOG_FILE}</string>
  <key>StandardErrorPath</key><string>{LOG_FILE}</string>
</dict></plist>
""", encoding="utf-8")
        subprocess.run(["launchctl", "unload", str(plist)], check=False)
        subprocess.run(["launchctl", "load", str(plist)], check=False)
        log(L(f"Installed launchd auto-backup, daily at {args.time}: {plist}",
              f"已安装 launchd 自动备份，每天 {args.time} 运行：{plist}"))
    elif sysname == "Linux":
        cur = subprocess.run(["crontab", "-l"], capture_output=True, text=True).stdout
        cur = "".join(l for l in cur.splitlines(keepends=True) if SCHED_LABEL not in l)
        cron_line = f'{mm} {hh} * * * "{py}" "{script}" backup  # {SCHED_LABEL}\n'
        subprocess.run(["crontab", "-"], input=cur + cron_line, text=True)
        log(L(f"Wrote crontab entry, daily auto-backup at {args.time}.",
              f"已写入 crontab，每天 {args.time} 运行自动备份。"))
    else:
        print(L("On Windows, run in an elevated PowerShell (daily auto-backup):",
                "Windows 请在管理员 PowerShell 执行（每日自动备份）："))
        print(f'  schtasks /create /tn {SCHED_LABEL} /tr "\\"{py}\\" \\"{script}\\" backup" '
              f"/sc daily /st {args.time}")
    return 0


# ----------------------------------------------------------------------------
# CLI
# ----------------------------------------------------------------------------
def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="vault.py",
        description=L("Back up / recover Claude Code account data and re-home sessions "
                      "(cross-platform, stdlib only).",
                      "Claude Code 账号内容备份 / 封号恢复 / 会话重映射（跨平台，纯标准库）。"),
        formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest="cmd", required=True)

    b = sub.add_parser("backup", help=L("snapshot ~/.claude", "把 ~/.claude 打包成快照"))
    b.add_argument("--keep", type=int, default=14,
                   help=L("rolling snapshots to keep (default 14)", "滚动保留份数（默认 14）"))
    b.add_argument("--gzip", action="store_true",
                   help=L("force gzip (default uses zstd if available)",
                          "强制用 gzip（默认有 zstd 则用 zstd）"))
    b.add_argument("--include-credentials", action="store_true",
                   help=L("only for same-account same-user machine moves: include .credentials.json",
                          "同账号同用户换机时才需要：连同 .credentials.json 一起打包"))
    b.set_defaults(func=cmd_backup)

    r = sub.add_parser("restore",
                       help=L("restore from a snapshot (dry-run + new-login protection by default)",
                              "从快照一键恢复（默认 dry-run + 保护新号登录）"))
    r.add_argument("archive", nargs="?", default="latest",
                   help=L("archive path, or 'latest' (newest in the vault)",
                          "归档路径，或 latest（取 vault 里最新一份）"))
    r.add_argument("--apply", action="store_true",
                   help=L("actually perform it (default only prints a dry-run plan)",
                          "真正执行（默认仅 dry-run 打印计划）"))
    r.add_argument("--mode", choices=["content", "full"], default="content",
                   help=L("content=add content without touching existing settings (default); "
                          "full=overwrite everything",
                          "content=只补内容不动现有设置(默认)；full=整体覆盖"))
    r.add_argument("--overwrite", action="store_true",
                   help=L("in content mode, also overwrite existing config files",
                          "content 模式下也覆盖已存在的配置文件"))
    r.add_argument("--no-safety", action="store_true",
                   help=L("skip the pre-restore snapshot", "跳过恢复前安全快照"))
    r.add_argument("--no-remap", action="store_true",
                   help=L("do not remap the home path", "不做 home 路径重映射"))
    r.add_argument("--old-home", help=L("old machine home prefix, e.g. /Users/old",
                                        "手动指定旧机 home 前缀，如 /Users/old"))
    r.add_argument("--new-home", help=L("new machine home prefix (default: current HOME)",
                                        "手动指定新机 home 前缀（默认当前 HOME）"))
    r.set_defaults(func=cmd_restore)

    h = sub.add_parser("rehome",
                       help=L("re-file mislocated sessions by their real cwd (dry-run by default)",
                              "按会话真实 cwd 把本机错位的对话归位（默认 dry-run）"))
    h.add_argument("--apply", action="store_true",
                   help=L("actually move files (default dry-run only)", "真正执行移动（默认仅 dry-run）"))
    h.set_defaults(func=cmd_rehome)

    s = sub.add_parser("status", help=L("show snapshot & auto-backup status", "查看快照与自动备份状态"))
    s.set_defaults(func=cmd_status)

    sc = sub.add_parser("schedule", help=L("install/remove daily auto-backup", "安装/卸载每日自动备份"))
    sc.add_argument("--time", default="03:00",
                    help=L("daily run time HH:MM (default 03:00)", "每日运行时间 HH:MM（默认 03:00）"))
    sc.add_argument("--uninstall", action="store_true",
                    help=L("remove the auto-backup", "卸载自动备份"))
    sc.set_defaults(func=cmd_schedule)
    return p


def main(argv: Optional[List[str]] = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        return args.func(args)
    except KeyboardInterrupt:
        log(L("Interrupted.", "已中断。"))
        return 130
    except Exception as exc:  # noqa: BLE001
        log(L(f"Error: {exc}", f"出错：{exc}"))
        return 1


if __name__ == "__main__":
    sys.exit(main())
