# Meetcap Sprint Plan

## Sprint 5: Capture Safety & Data Integrity (Executada — 2026-09)

[Capture Safety & Data Integrity — plano detalhado](plans/2026-09-10-sprint-5-capture-integrity.md)

- **Status:** **Executada e integrada em `main` (local)** — suíte completa **523 passed** e gates estáticos (ruff/compileall/shellcheck) verificados localmente; CI remota roda no primeiro push.
- **Entregas integradas do épico de integridade:**
  - **Runtime & Lifecycle (A):** runtime isolado em `$XDG_RUNTIME_DIR/meetcap`, single-instance via `flock`, verificação de identidade de processos (pidfd/start ticks sem sinais destrutivos), IPC com budgets e deadlines bounded (#13, #18, #19, #28).
  - **Captura & Liveness (D):** monitoramento ativo do ffmpeg, detecção de crash com marcação `partial`/`failed`, elapsed time monotônico, nomes de WAV resistentes a colisão (`-n`), descarte seguro com confirmação (#11, #33, #42).
  - **Publicação Atômica & No-Clobber (B):** export via staging `.partial` antes de `os.replace`, nota publicada por último como barreira de commit, sidecars versionados, detecção de colisão sem sobrescrever notas ou edições humanas (#12, #17).
  - **Privacidade de Tarefas (C):** matching conservador de tarefas do Obsidian executado estritamente local; tarefas da daily note nunca são enviadas ao LLM remoto (#25).
  - **Serviço & Least Privilege (S):** `meetcap.service` portável com `%h`, sem credenciais reais versionadas; allowlist estrita de variáveis de ambiente para subprocessos (#14).
  - **Transcrição, Leases & Segmentos (E):** lease exclusiva de transcrição, agregação de segmentos do Whisper para pausas <0.5s em turnos de fala, parse resiliente de timestamps (`MM:SS` com minutos > 59 e `H:MM:SS`), completion markers sem retranscrição implícita (#15, #26, #37).
  - **CLI Standalone:** comando `python src/meetcap.py transcribe <wav>` standalone in-process sem necessidade de daemon ativo; `transcribe` (sem args) e `transcribe-last` mantidos para o daemon (#29).
  - **Fila Durável de Exports:** persistência durável em `export_jobs.json` com retry bounded, status `exporting` e `last_export` no daemon, reconciliação automática no startup (#16).
  - **Contrato de Outcome & Estágios:** status explícito `ok`, `degraded` e `failed` por estágio (`summary`, `tasks`, `claims`, `qa`, `manifest`, `artifacts`); exit != 0 quando summary ou artefatos obrigatórios falham; daemon notifica `Export degraded` (#38).
  - **Grounding Mecânico & Qualidade Semântica:** QA advisory executado em chunks cobrindo a reunião completa; weighted coverage score; `action_item_gaps` acionando `needs_human_review`; speaker attribution marcada como `not_assessable` sem fingir diarização (#20, #21, #22, #39).
  - **Evidências & Decisões:** claims com grounding `exact` vs `fuzzy` no `evidence.json`; seção `## Decisões` na nota com categorização `verified` (com timestamps), `candidate` e `unresolved` (`[unverified]`) (#4, #23).
  - **Resolução Canônica de Entidades:** resolução com wiki e glossário; auto-aplicação restrita a regras `alias` e `fuzzy`; matching por substring tratado como audit-only em `corrections.json` (#2, #53).
  - **Retenção & Preflight de Disco:** opt-in via `MEETCAP_RETENTION_DAYS` (expurgo restrito a arquivos WAV com transcrição completa, sem nunca apagar textos ou notas); preflight `MEETCAP_MIN_FREE_GB` recusando captura se o disco estiver abaixo do limite (#36).
  - **Concorrência Bounded:** `MEETCAP_EXPORT_MAX_CONCURRENCY` limitando paralelismo de chunks e claims (1–4, default 2) (#31).
  - **Desktop & UI (U):** módulo Waybar com blank-when-idle, estados `recording` (`🎙 MM:SS`) e `transcribing` (`📝`), piscar via CSS com `--blink-fallback` opcional; rofi com status sanitizado e classificado como opcional no doctor (`optional-missing`); `notify-send` fixado com app `-a Meetcap` e ≤ 2 posicionais (#27, #32, #42, #44, #46).
  - **Frontmatter YAML Seguro:** serialização determinística com escape seguro; métricas de saúde no frontmatter (`qa_needs_review`, `qa_coverage_score`, `outcome`, `qa_audited`, `entity_corrections_count`) (#40, #51).
  - **CI Robusto:** matriz de testes em Python 3.11, 3.12, 3.13 mais job `static` (ruff, compileall, shellcheck, pip-audit) (#3, #35).
- [Baseline e limitações da verificação histórica](plans/2026-09-10-sprint-5-baseline.md).

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
