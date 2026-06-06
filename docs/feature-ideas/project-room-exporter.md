# Project Room Exporter

- **Date captured:** 2026-06-01
- **Source:** Synapse Diff cron (`7725790d0f44`)
- **Status:** idea
- **Repo remote:** none (local-only fallback)
- **Project:** `/home/lucca/Projects/meetcap`

## Summary

Turn each exported meeting from a standalone note into a reusable **project room** artifact that other local-first agents can inspect later.

Current loop:
- record
- transcribe
- summarize
- suggest tasks in the vault

Proposed next loop:
- record
- transcribe
- summarize
- extract tasks/entities/context
- persist everything under a project-scoped room bundle
- keep a rolling latest context per project

## Why this matters

The value is moving up one layer: not just "better meeting summaries", but **better context packaging**.

Instead of treating a meeting as a dead file, Meetcap would turn it into reusable infrastructure for Hermes/Alexandria and any file-system agent that needs project memory later.

## Proposed feature

**Name:** Project Room Exporter

### Core behavior

Extend the vault export flow so Meetcap can accept a project target and write a bundle like:

```text
vault/Projects/<project>/Meetings/<date-slug>/
```

Bundle contents:
- main meeting note
- raw transcript copy
- `context.json`
- `context.md`
- extracted participants
- matched existing tasks
- newly suggested tasks
- cited entities
- suggested wiki links / follow-ups

### Rolling context

Also maintain:

```text
vault/Projects/<project>/latest-context.md
```

That file should point to or summarize the latest 3 meetings for fast downstream consumption.

## Suggested implementation

### Input / routing

- add `--project <slug>` to the export path, **or**
- support config-based project mapping from transcript/export context

### Reuse existing parts

Lean on what already exists:
- transcript parser
- summarizer
- task extractor
- current vault exporter flow

### Likely code touchpoints

- `export_to_vault.py`
- `src/exporter/vault_exporter.py`
- `src/exporter/task_extractor.py`
- maybe `src/exporter/config.py` for project mapping / defaults

## Scope estimate

**Effort:** M

## Constraints

- keep it local-first
- do not introduce a new database
- keep the plain-file artifact inspectable by humans and agents
- avoid breaking current default export flow for non-project meetings

## Good first slice

1. Add optional project slug support to exporter
2. Write bundle folder for project meetings
3. Generate `context.md` first
4. Add `latest-context.md` updater
5. Backfill `context.json` only if it adds clear downstream value

## Open questions

- How should Meetcap infer the project when `--project` is absent?
- Should `latest-context.md` be a full summary or just a pointer index to the last 3 rooms?
- Should task matching happen against existing project task notes before suggesting new ones?
- Should wiki-link suggestions stay advisory-only or be emitted in a machine-readable block?

## Origin excerpt

> Hoje o Meetcap já fecha o loop básico: grava → transcreve → resume → sugere tasks no vault. O próximo salto é parar de salvar reunião como arquivo solto e começar a salvar como room de projeto reutilizável por Hermes/Alexandria dias depois.
