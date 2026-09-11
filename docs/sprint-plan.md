# Meetcap Sprint Plan

## Plano atual — Sprint 5 (proposta, 2026-09-10)

[Capture Safety & Data Integrity — plano detalhado](plans/2026-09-10-sprint-5-capture-integrity.md)

- Triage das 33 issues abertas; proposta de 15 issues em duas semanas, até três lanes paralelas.
- AGY Gemini + AGY Claude + OpenCode, ownership por arquivo, contratos RED, gates e handoff para Core GLM 5.3 Flash.
- Status: aguardando aprovação; nenhuma implementação, commit, push ou alteração do serviço iniciada pelo planejamento.
- [Baseline e limitações da verificação](plans/2026-09-10-sprint-5-baseline.md).

## Histórico — plano original das Sprints 1–4

As premissas abaixo pertencem ao plano original, não ao dimensionamento da Sprint 5.

> Source: review of open issues at https://github.com/BenedettiLucca/meetcap/issues
> Assumptions: 1 engineer, 2-week sprints, ~12-15 points per sprint. Total: ~50 pts across 4 sprints.

## Dependency Order

```
#8 dependency/client cleanup
        |
        v
#3 CI and test foundation
        |
        +--------> #6 error handling ------> #1 doctor/self-heal
        |
        +--------> #2 entity resolver
        |
        +--------> #4 evidence exports ---> #7 QA verifier
                                      \----> #5 room manifest
                                               ^
                                               |
                                      QA results enrich manifest
```

## Sprint 1: Engineering Baseline

Goal: make subsequent changes testable and failures observable.

| Issue | Scope | Estimate |
|---|---|---:|
| #8 Dependencies and HTTP client | Replace `curl` subprocess with `httpx`; define connection/read timeouts; classify transport, HTTP, and response-schema failures; complete runtime/dev requirements; update README | 3 |
| #3 CI and test coverage | Add GitHub Actions for supported Python versions; split tests by module; cover `llm_client`, transcript parsing, exporter orchestration, daemon commands, and failure paths with mocked OS/LLM calls | 5 |
| #6 Exception handling | Replace broad internal catches with `OSError`, `subprocess` errors, JSON errors, socket errors, and HTTP client errors; retain broad catches only at thread/process boundaries with explicit diagnostics | 5 |

**Exit criteria:** CI is required and green, no real API/audio calls occur in tests, every suppressed failure is either intentionally documented or logged.

## Sprint 2: Capture Reliability

Goal: prevent daemon startup and capture failures from requiring terminal diagnosis.

| Issue | Scope | Estimate |
|---|---|---:|
| #1 Doctor and self-healing daemon | Add `src/doctor.py`; process/socket/PID/service/dependency/audio/output checks; `doctor`, `status`, `start`, `restart`, `install-service` subcommands; rofi recovery actions; document service-first setup | 8 |
| — Reliability tests and hardening | Simulate stale socket/PID, missing binaries, inactive service, invalid source, unwritable output, successful recovery | 5 |

**Exit criteria:** `meetcap.sh doctor` emits human-readable and JSON status, rofi recovers the common stopped-daemon case, all health states have deterministic tests.

## Sprint 3: Trusted Export Foundation

Goal: preserve transcript provenance while improving derived artifacts.

| Issue | Scope | Estimate |
|---|---|---:|
| #2 Canonical entity resolver | Load vault/project/participant vocabulary; conservative fuzzy matching; correct summary and tasks only; audited corrections; raw transcript unchanged | 8 |
| #4 Evidence-linked exports | Typed timestamped segment structure; extract 3-5 claims; validate timestamps against real segments; render evidence block; machine-readable evidence | 8 |

**Artifact convention:** sidecars live under a per-meeting directory derived from the source filename:

```
Meetings/.meetcap/meeting-2026-08-14_11-54-59/
├── corrections.json
└── evidence.json
```

**Exit criteria:** raw transcript bytes unchanged, corrections auditable, every exported claim resolves to an actual transcript segment, outputs useful when speaker metadata is unavailable.

## Sprint 4: Quality Gate and Routing

Goal: make exports verifiable and directly consumable by downstream agents.

| Issue | Scope | Estimate |
|---|---|---:|
| #7 Transcript-to-note QA | Advisory verifier: decision coverage, action completeness, attribution risks, unsupported claims; JSON/Markdown output; QA flags block on threshold failure | 8 |
| #5 Meeting room manifest | Build manifest from summary, tasks, evidence, corrections, QA output; authority/freshness classification; decisions, open questions, missing proof, downstream lanes | 5 |

**Exit criteria:** QA never silently rewrites the note, manifest claims reuse validated evidence, routing uses a fixed enum, low-confidence routing falls back to `reference-only`.

## Cross-Sprint Definition of Done

- Tests cover success, degraded operation, malformed model output, and filesystem/network failures.
- All LLM and OS integrations are mocked in CI.
- JSON artifacts have versioned schemas and deterministic serialization.
- Raw transcripts and recordings are never modified or added as fixtures.
- README matches shipped behavior (architecture, configuration, outputs, recovery).
- Each issue closes through its own PR (or clearly linked PR) with acceptance evidence.
- Final release: full suite + manual smoke test (daemon health, recording, transcription, export, sidecars, QA flags, room manifest).

## Risks

- **Export pipeline coupling:** five export features touch `vault_exporter.py`. Sprint 3 must first establish one structured export context and artifact-writing convention before parallel sidecar formats emerge.
- **Stale issue text:** #3 and #8 were partially addressed before this plan; verify remaining scope before starting each sprint.
