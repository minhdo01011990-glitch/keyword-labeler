# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Memory

Project knowledge is stored in `memory.md` at the root of this repository.

### When to read memory.md

- **Always read `memory.md` first** at the start of each session or after a `/compact` command — before doing anything else.
- When the user explicitly asks Claude to check, recall, or read memory.
- Before taking any action that may depend on prior decisions, context, or project state (e.g., adding a feature, changing architecture, modifying a workflow).

### Directory structure reads

- Do **not** read the directory structure unless it is genuinely necessary for the task at hand (e.g., a new file needs to be placed, or you are unsure which files exist).
- Before reading the directory structure, **ask the user for permission** and explain why it is needed. Proceed only after the user agrees.

### When to update memory.md

- When the user explicitly says to save or update something into memory — do it immediately.
- When Claude identifies new project knowledge worth preserving (decisions, architecture choices, key context), **propose the update first** and only write to `memory.md` after the user agrees.

### memory.md format

Organize `memory.md` with clear sections so it remains scannable:

```markdown
# Project Memory

## Project Overview
<!-- What this project does, its goals, and scope -->

## Architecture & Key Decisions
<!-- Major design decisions and the reasoning behind them -->

## Data & Workflow
<!-- Data sources, processing pipeline, labeling logic -->

## In Progress
<!-- Current tasks, open questions, unresolved issues -->

## Conventions
<!-- Naming, structure, patterns specific to this project -->
```
