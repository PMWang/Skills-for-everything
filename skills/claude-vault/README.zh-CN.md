# claude-vault

> Claude Code 账号内容备份 / 封号恢复 / 会话归位工具 · 跨平台 · 纯 Python 标准库（无需 pip）

[English](./README.md) · **简体中文**

> 界面语言跟随系统 locale（默认英文，`zh*` locale 自动中文）；可用 `CLAUDE_VAULT_LANG=zh` 或 `=en` 强制。

把 Claude Code 的 `~/.claude`（对话历史、`CLAUDE.md`、skills、settings、memory 等）整体备份，并能在 **账号被封禁、换电脑、换账号** 后把内容安全恢复到新环境，自动处理换机 / 中文路径导致的「`claude --resume` 找不到历史对话」问题。

## 为什么需要它

- **封号自救**：Claude 账号被封后注册新号，几秒钟把全部历史对话和配置搬到新号。
- **换机迁移**：旧电脑的对话、技能、记忆一键带到新电脑。
- **会话找回**：中文项目路径会让多个目录在 `~/.claude/projects/` 下塌进同一个名字（slug 碰撞），`claude --resume` 串台或找不到——本工具能按会话真实工作目录把它们归位。

## 安装

需要 Python 3.8+（macOS / 多数 Linux 自带）。无需安装任何依赖。

```bash
git clone <this-repo> claude-vault
cd claude-vault
python3 scripts/vault.py status        # 自检
```

作为 Claude Code skill 使用（可选）：把目录软链进 `~/.claude/skills/`，之后任意会话里描述需求即可触发。

```bash
ln -s "$(pwd)" ~/.claude/skills/claude-vault     # macOS / Linux
```

## 快速上手

```bash
# 立刻备份一份
python3 scripts/vault.py backup

# 开启每天 03:00 自动备份（macOS launchd / Linux cron）
python3 scripts/vault.py schedule --time 03:00

# 查看状态
python3 scripts/vault.py status
```

### 封号 / 换机后恢复（核心场景）

在 **新账号已登录** 的 Claude Code 上：

```bash
# 1) 预演：默认 dry-run，绝不改动 ~/.claude，只打印计划并写 manifest
python3 scripts/vault.py restore ~/Downloads/claude-vault-2026-05-28_030005.tar.zst

# 2) 核对无误后执行
python3 scripts/vault.py restore ~/Downloads/claude-vault-2026-05-28_030005.tar.zst --apply

# latest = 自动取 ~/.claude-vault/snapshots 里最新一份
python3 scripts/vault.py restore latest --apply
```

恢复行为：

- **保护新号登录**：不写 `.credentials.json`；合并 `.claude.json` 时保留新机已有的账号字段（`oauthAccount` / `userID`），旧号绑定不会盖回来。
- **跨机 / 跨用户名重映射**：自动探测归档里的旧 home 前缀（如 `/Users/old`），与本机 home 不同则重算每个会话的项目目录 slug 并改写会话内 `cwd`。可用 `--old-home` / `--new-home` 手动指定，或 `--no-remap` 关闭。
- **可回退**：`--apply` 前自动生成「恢复前快照」（除非 `--no-safety`）。
- **不动现有设置**（`--mode content`，默认）：只补缺失内容，不覆盖新机已有配置；要整体覆盖用 `--mode full`，要逐个覆盖用 `--overwrite`。

### 会话归位（没换机，只是 `--resume` 乱了）

```bash
python3 scripts/vault.py rehome            # dry-run，列出该挪的会话
python3 scripts/vault.py rehome --apply    # 执行
```

## 子命令

| 子命令 | 说明 |
|---|---|
| `backup [--keep N] [--gzip] [--include-credentials]` | 打包 `~/.claude` 到 `~/.claude-vault/snapshots/`，滚动保留 N 份（默认 14），默认 zstd（无则 gzip），默认排除凭证 |
| `restore [archive\|latest] [--apply] [--mode content\|full] [--overwrite] [--no-safety] [--no-remap] [--old-home P] [--new-home P]` | 从快照恢复，默认 dry-run |
| `rehome [--apply]` | 按会话真实 cwd 归位本机错位对话，默认 dry-run |
| `status` | 快照数量 / 体积 / 最新时间 / 自动备份是否安装 / 本机会话数 |
| `schedule [--time HH:MM] [--uninstall]` | 安装 / 卸载每日自动备份 |

## 工作原理

Claude Code 把每个对话存为：

```
~/.claude/projects/<slug>/<session-id>.jsonl
```

`<slug>` = 把启动 `claude` 时的工作目录里**每个非 `[A-Za-z0-9]` 字符（含 `/`、`-`、`.`、中文等）替换成 `-`**（不折叠连续 `-`、不裁剪、不改大小写）。例如：

```
/Users/you/Documents/我的项目/skill-archive
→ -Users-you-Documents------skill-archive
```

两个问题由此而来，本工具都能修：

1. **中文路径碰撞**：不同中文目录可能塌成相同 slug，会话堆在一起、`--resume` 串台。
2. **换机失配**：旧机 slug 含旧用户名 / 旧路径，新机对不上，历史会话「消失」。

每个 `.jsonl` 内部都记录了真实的 `"cwd"`。本工具读出 `cwd` → 重算正确 slug → 把会话放进对的目录（并在跨机时改写 `cwd`），从而恢复 `claude --resume` 的识别。

## 跨平台说明

| 平台 | 备份 / 恢复 / 归位 | 自动备份 |
|---|---|---|
| macOS | ✅ | ✅ launchd |
| Linux | ✅ | ✅ cron |
| Windows | ✅ | 打印 `schtasks` 命令，手动创建 |

压缩：本机有 `zstd` 命令则用 zstd，否则回退 gzip。恢复端两种格式都认；解 `.zst` 时若无 `zstandard` 模块会调用 `zstd` 命令（`brew install zstd` / `apt install zstd`）。

## 安全

- `restore` / `rehome` 默认 **dry-run**，`--apply` 才动文件。
- `restore --apply` 前自动备份当前 `~/.claude`，可回退。
- 备份 / 恢复 **默认都不含登录凭证**，不会把 token 写进归档或新机。
- 每次操作都写 manifest 到 `~/.claude-vault/manifests/`，可审计、可对照。

## 隐私与边界

- **快照里有你的数据**：备份含 `~/.claude` 里的对话、工具输出、记忆、设置、skill 内容。`.tar.*` 文件要当敏感文件对待，别提交到公开 git 或传到不可信的地方。
- **manifest 和日志含本地信息**：`~/.claude-vault/manifests/` 和 `vault.log` 会记录本地路径、项目名、操作历史，分享前先看一眼。
- **不覆盖 macOS Keychain / 浏览器状态**：存在系统钥匙串或浏览器里的登录 token 既不备份也不恢复（有意为之）。
- **迁移的是内容，不是访问权**：claude-vault 帮你把**内容和账号状态文件**搬到新号/新机，**不能**恢复对被封账号的访问——必须先用一个能登录的账号登进去。

## License

MIT，见 [LICENSE](./LICENSE)。
