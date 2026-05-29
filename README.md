# Skillyard

English | [中文](#chinese)

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

<a name="chinese"></a>

# Skillyard（中文）

[English](#skillyard) | 中文

> **Skillyard** 是一套精选、开放的 **Claude Code 技能 / AI agent 技能** 合集——每个技能即插即用、自包含，可直接放进 [Claude Code](https://claude.com/claude-code)、Claude Agent SDK、Codex，或任意支持开放 `SKILL.md` 标准的 agent。

## 什么是 Skill（技能）？

一个 Skill 就是一个文件夹，里面有一份 `SKILL.md`（说明 + 元数据），外加可选的脚本、模板、参考文件。当任务匹配到它的描述时，agent 才**按需加载**它——这样你既能获得专项能力，又不会让每次对话都背着一堆冗余指令。

## 仓库结构

```
skills/
  <技能名>/
    SKILL.md        # 这个技能做什么、什么时候用
    ...             # 可选的脚本、模板、资源
```

## 怎么用

把任意技能文件夹复制到你的 skills 目录：

- **Claude Code（全局）：** `~/.claude/skills/<技能名>/`
- **项目内：** `.claude/skills/<技能名>/`

下次运行时 agent 会自动识别并加载。

## 许可证

_待定 —— 见仓库所有者。_

---

Maintained by / 维护者 [@PMWang](https://github.com/PMWang)
