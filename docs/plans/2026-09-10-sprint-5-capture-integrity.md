# Meetcap Sprint 5 — Capture Safety & Data Integrity

> **Status: PROPOSTA, aguardando aprovação. Nenhuma lane de implementação foi iniciada.**
> **For Hermes:** carregar `sprint-execution`, `acp-dev-lane`, `writing-plans` e `subagent-driven-development` antes da execução. Usar a disciplina de contratos/review da última skill, mas os executores deste plano são AGY e OpenCode, não children no modelo do Core.
> Planejamento: GPT 6 Astra, conforme solicitado. Execução futura: Core em GLM 5.3 Flash, com troca feita pelo Lucca. Core mantém plano, coordenação, gates e síntese; não implementa código de produto.

**Goal:** impedir perda/sobrescrita silenciosa de reuniões, recovery que mate processos alheios e captura que continue invisível; provar lifecycle com processos e sockets reais, sem hardware ou dados reais.

**Architecture:** manter Python/stdlib e o daemon atual. Um writer por arquivo e por onda; contratos congelados antes do fan-out. Publicação de artifacts no-clobber, metadata explícita e fail-closed nas fronteiras de processos/arquivos. Sem reescrever o daemon, criar broker de jobs ou adicionar framework de UI.

**Tech Stack:** Python 3.11–3.13, pytest, httpx, UNIX sockets, Linux `/proc`/pidfd/flock, systemd user, ffmpeg/ffprobe, Bash/rofi e módulo custom JSON do Waybar. Whisper continua local. Nenhuma nova dependência Python de produção é necessária por desenho; ffprobe precisa entrar no preflight/documentação se adotado na validação de WAV.

**Janela proposta:** duas semanas úteis, até três lanes ativas em paralelo. Não é prazo informado pelo Lucca nem previsão baseada em throughput medido. O compromisso recomendado é **15 issues**, agrupadas em oito pacotes, mais preparação/review. Sem stretch goals automáticos.

---

## 1. Fontes e baseline verificada

- Remoto: <https://github.com/BenedettiLucca/meetcap>.
- Issues: <https://github.com/BenedettiLucca/meetcap/issues>; inventário completo via REST paginado, excluindo PRs, em 2026-09-10. Corpos das 33 issues e os comentários de #9 e #35 examinados. Nova listagem confirmou o conjunto e os updates.
- `HEAD == origin/main == 124f9d1f2bc433c21ba2f7d3bd3eae8669e3d132` após fetch. Nenhuma PR aberta no momento da inspeção.
- Prioridades **nos títulos**: 3 P0, 22 P1, 7 P2, 1 sem prioridade. Não confundir com labels atribuídas no GitHub; nenhuma prioridade remota foi alterada.
- CI observado verde nesse SHA: <https://github.com/BenedettiLucca/meetcap/actions/runs/33989244045>. Workflow atual executa pytest em Python 3.11, 3.12 e 3.13.
- Baseline local executada em Python **3.11.14**, em cópias temporárias isoladas:
  - source de `origin/main`: **188 passed, 10 subtests passed**, exit 0;
  - source incorporando apenas os dois arquivos Python do WIP: **188 passed, 10 subtests passed**, exit 0.
- Isolamento da baseline: HOME/vault sintéticos, ambiente sem credenciais, plugins pytest externos desabilitados, conexões INET bloqueadas e log legado do teste de spawn redirecionado. Os testes de sockets UNIX continuaram reais. Não houve gravação, transcrição Whisper, export real, instalação de serviço nem chamada real de LLM.
- Evidência adicional: `2026-09-10-sprint-5-baseline.md`, ao lado deste arquivo. Verde atual prova a suíte existente, **não** prova ausência dos bugs reportados.

### WIP local — não é a base aprovada

Antes do planejamento já havia modificações em `.env.example`, `README.md`, `meetcap.service`, `src/exporter/config.py`, `src/meetcap.py`, além de `.omh/` e `IDEA.md` não rastreados. Não limpar, stagear, sobrescrever ou incorporar implicitamente.

O diff Python contém, entre outras mudanças, transcrição HTTP por router com fallback in-process, configurações de Whisper e modelo de resumo. A suite antiga passa com isso, mas não fornece cobertura específica dessas alterações.

**Gate G0:** usar worktrees de uma base limpa de `origin/main` por default. Se Lucca quiser incluir WIP, primeiro revisão separada e commit explicitamente autorizado/selecionado; congelar novo SHA e revalidar os contratos. Não transportar `.env`, recordings, vault, `.omh/` ou `IDEA.md` para lanes. Se a rota `transcribe_via_router` for incorporada, #15/#37 e identidade/no-clobber devem cobrir **ambas** as rotas antes de GREEN. Merge futuro sobre o checkout sujo está bloqueado até reconciliação explícita.

### O que é fato, inferência e proposta

- **Fato:** o código possui unlink do socket antes de lock, PID numérico usado em stop, `-y`, polling de ffmpeg limitado ao início, gravação direta dos artifacts e envio das tasks pendentes ao LLM.
- **Inferência fundamentada:** os cenários de race/crash/ENOSPC descritos são compatíveis com essas rotas. Não foram todos reproduzidos nesta etapa de planejamento.
- **Proposta:** seleção da sprint, prazos, modelos por pacote e novos contratos abaixo. A reprodução RED faz parte da execução; ninguém pode transformar texto de issue em evidência de fix.

## 2. Tese, contraponto e decisão recomendada

**Tese:** o produto precisa primeiro preservar áudio, texto e notas humanas, e deixar inequívoco quando está capturando. Esses bugs podem causar dano antes de qualquer resumo existir.

**Contraponto:** QA/evidence também tem P1 reais (#20–#24/#26/#39), alguns pequenos e paralelizáveis. Caberiam vários easy wins se a métrica fosse quantidade de tickets fechados. Mas isso dispersaria a sprint e competiria por prompts/exporter/review sem eliminar o risco de perder a reunião original.

**Recomendação:** uma sprint de segurança operacional e integridade, com #25 como correção independente de privacidade e #42 por incidente de overrun relatado na issue. Depois, uma sprint de confiabilidade semântica/LLM. QA atual continua advisory; não alegar que esta entrega torna claims, coverage ou routing confiáveis por si só.

**Limitação deliberada:** não resolver #16 (fila durável/retry de exports) nem #38 (status semântico completo de falha LLM) por tabela. #17 garante publicação íntegra, não durabilidade do job inteiro nem qualidade do conteúdo. Um restart ainda pode interromper um export pendente; a saída anterior deve ficar preservada e o reprocessamento ser explícito. Não instalar a sprint durante captura/transcrição/export em andamento.

## 3. Triage das 33 issues

Legenda: P0 = bloqueador de integridade/segurança operacional; P1 = alta prioridade; P2 = higiene/performance/UX secundária. A coluna recomendação é local ao plano, não muda o remoto. `Agora` significa alvo integral, condicionado aos gates; `Próxima` não significa sem importância.

| Issue | Prioridade remota → recomendada | Decisão | Motivo e dependências |
|---|---|---|---|
| [#9](https://github.com/BenedettiLucca/meetcap/issues/9) — `.omh/` e `IDEA.md` | sem → P2 | Próxima | Higiene válida, mas decidir destino de arquivo pessoal não integra runtime. Isolar WIP resolve o bloqueio desta sprint sem apagar conteúdo. |
| [#11](https://github.com/BenedettiLucca/meetcap/issues/11) — morte do ffmpeg vira sucesso | P0 → P0 | **Agora / D** | Perda silenciosa da função central. Liveness real, WAV parcial, stop e timeout. Base A; UI U depende disso. |
| [#12](https://github.com/BenedettiLucca/meetcap/issues/12) — overwrite de nota/sidecars | P0 → P0 | **Agora / B + E** | Pode destruir edição humana. No-clobber e identity em B; eliminar retranscrição implícita em E. Só fecha com ambos. |
| [#13](https://github.com/BenedettiLucca/meetcap/issues/13) — PID reuse mata processo alheio | P0 → P0 | **Agora / A** | Recovery potencialmente destrutivo. Identidade verificável/pidfd e fail-closed, nunca PID isolado. |
| [#14](https://github.com/BenedettiLucca/meetcap/issues/14) — unit pessoal e ambiente Hermes | P1 → P1 | **Agora / S + E** | Portabilidade e least privilege. Template/env em S; wiring de subprocessos em E. Não migrar unit ativa agora. |
| [#15](https://github.com/BenedettiLucca/meetcap/issues/15) — watchdog libera job vivo | P1 → P1 | **Agora / E** | Duplicação de modelos/jobs e estado mentiroso; cola natural de #37/#12. |
| [#16](https://github.com/BenedettiLucca/meetcap/issues/16) — auto-export não durável | P1 → P1 | Próxima, início | Exige state machine/fila/retry e política de shutdown; cresce além do commit de arquivos de B. Depende dos IDs/status desta sprint. |
| [#17](https://github.com/BenedettiLucca/meetcap/issues/17) — writes parciais e sucesso falso | P1 → P1 | **Agora / B** | Mesma unidade de trabalho de #12; não separar política de colisão da publicação íntegra. |
| [#18](https://github.com/BenedettiLucca/meetcap/issues/18) — runtime global em `/tmp` | P1 → P1 bloqueador | **Agora / A** | Pré-requisito de #13/#28, testes isolados e clientes alinhados. |
| [#19](https://github.com/BenedettiLucca/meetcap/issues/19) — IPC sem deadlines | P1 → P1 bloqueador | **Agora / A** | CLI trava, clientes idle acumulam. Precisa budgets e concorrência limitada, não só timeout no accept. |
| [#20](https://github.com/BenedettiLucca/meetcap/issues/20) — QA ignora meio de reunião | P1 → P1 | Próxima | Coverage parcial não é cobertura total. Resolver em bloco de honestidade semântica, antes de otimizar #31. |
| [#21](https://github.com/BenedettiLucca/meetcap/issues/21) — action gaps não pedem review | P1 → P1 | Próxima | Bug pequeno e real, mas mantém ownership do verifier fora desta sprint. Não usar como stretch silencioso. |
| [#22](https://github.com/BenedettiLucca/meetcap/issues/22) — speaker QA sem identidade | P1 → P1 | Próxima | Corrigir claim de capacidade: `not_verifiable` sem metadados, não implementar diarização inteira. |
| [#23](https://github.com/BenedettiLucca/meetcap/issues/23) — fuzzy tratado como verified | P1 → P1 | Próxima, início | Evidência falsa é grave. Priorizar no próximo bloco junto de #26/#39 e fixtures verbatim. |
| [#24](https://github.com/BenedettiLucca/meetcap/issues/24) — prompt injection | P1 → P1 | Próxima, início | Endurecer fronteiras de todos os prompts e validações num escopo próprio; #25 não é fix de injection. Nunca executar conteúdo de reunião. |
| [#25](https://github.com/BenedettiLucca/meetcap/issues/25) — tasks privadas enviadas | P1 → P1 | **Agora / C** | Vazamento desnecessário; correção pequena e independente: matching local, nenhum contexto diário remoto. |
| [#26](https://github.com/BenedettiLucca/meetcap/issues/26) — timestamps acima de 99 min | P1 → P1 | Próxima | Parser perde segmentos. Fix junto da proveniência, sem alterar transcript bruto. |
| [#27](https://github.com/BenedettiLucca/meetcap/issues/27) — rofi esconde erros | P1 → P1 | **Agora / U** | Sem isso falhas detectadas pelo backend seguem invisíveis no uso real. Base A/D. |
| [#28](https://github.com/BenedettiLucca/meetcap/issues/28) — segundo daemon split-brain | P1 → P1 bloqueador | **Agora / A** | Mesma raiz de lifecycle de #13/#18; single-instance antes de qualquer metadata/unlink. |
| [#29](https://github.com/BenedettiLucca/meetcap/issues/29) — CLI standalone falsa | P1 → P1 | Próxima | Precisa fechar dispatch/semântica de caminho explícito e docs. Não confundir com remover retranscrição implícita em E. |
| [#30](https://github.com/BenedettiLucca/meetcap/issues/30) — retry duplica request/custo | P2 → P2 | Próxima | Reconciliar exceções estruturadas/retry depois dos contratos de falha semântica. |
| [#31](https://github.com/BenedettiLucca/meetcap/issues/31) — LLM serial/performance | P2 → P2 | Próxima, por último | Paralelizar pipeline inseguro amplifica races/custo. Primeiro integrity e durabilidade. |
| [#32](https://github.com/BenedettiLucca/meetcap/issues/32) — rofi opcional unhealthy | P2 → P2 | Próxima | Corrigir classificação, não bloquear o foco de service/security. Testes existentes mostram rofi entre dependências obrigatórias. |
| [#33](https://github.com/BenedettiLucca/meetcap/issues/33) — WAV colisão em 1s | P1 → P1 | **Agora / D** | Sobrescrita de raw audio; mesmo pacote de captura de #11. |
| [#34](https://github.com/BenedettiLucca/meetcap/issues/34) — README desatualizado | P2 → P2 | Próxima / parcial | Esta sprint documenta somente seus novos contratos. Auditoria integral de README/CLI/test count continua aberta. |
| [#35](https://github.com/BenedettiLucca/meetcap/issues/35) — CI só pytest | P2 → P2 | Próxima / parcial | #41 entra em `pytest tests/` automaticamente. Lint/dependency/security gates completos são outra entrega; não inflar CI aqui. |
| [#36](https://github.com/BenedettiLucca/meetcap/issues/36) — sem retention/budget | P2 → P2 | Próxima | #42 reduz esquecimentos, não resolve retenção. Deleção automática de áudio exige política explícita; proibida nesta sprint. |
| [#37](https://github.com/BenedettiLucca/meetcap/issues/37) — transcript parcial parece pronto | P1 → P1 | **Agora / E** | Commit do transcript e completion marker, recuperação sem truncar completos. |
| [#38](https://github.com/BenedettiLucca/meetcap/issues/38) — LLM falha e export verde | P1 → P1 | Próxima, início | #17 trata integridade física. Status de qualidade end-to-end deve acompanhar #16/#20; manter limitação explícita. |
| [#39](https://github.com/BenedettiLucca/meetcap/issues/39) — QA inventa timestamps | P1 → P1 | Próxima | Validar contra segmentos reais, em conjunto com #23/#26. |
| [#40](https://github.com/BenedettiLucca/meetcap/issues/40) — YAML/nome sem escaping | P2 → P2 | Próxima / parcial | B deve garantir confinamento/no-clobber de seus paths; serializer YAML completo não é requisito oculto para fechar #12. |
| [#41](https://github.com/BenedettiLucca/meetcap/issues/41) — integração lifecycle real | P1 → P1 bloqueador | **Agora / Q** | Gate transversal, não tarefa opcional no fim. Processos/socket/restart reais e hardware fake. |
| [#42](https://github.com/BenedettiLucca/meetcap/issues/42) — captura invisível/overrun | P1 → P1 | **Agora / D + U + Q** | Há incidente relatado, não só melhoria cosmética. Precisa liveness, indicator, elapsed, reminders e descarte confirmado. Sem auto-stop default. |

**Conferência:** 15 alvos; 18 fora do compromisso. #12/#14/#42 são cross-packet e não fecham no primeiro PR que mencionar a issue.

## 4. Contratos que o Core deve congelar antes do fan-out

Estes são contratos **propostos para implementar**, não símbolos que já existem. APIs internas podem ser menores, mas alterações destes comportamentos exigem registrar impacto e atualizar testes antes de continuar.

### C1 — Runtime, processo e IPC

- Um resolvedor compartilhado em **novo** `src/runtime_paths.py`: runtime sob `$XDG_RUNTIME_DIR/meetcap`, diretório 0700, arquivos regulares 0600 e socket restrito. Validar owner/type e recusar symlinks. Fallback determinístico em diretório privado sob HOME quando XDG não existir; não criar novo namespace global inseguro em `/tmp`.
- Logs em diretório privado de state, com limite/rotação stdlib. Não varrer/remover runtime legado nem logs de outra instalação. Não manter fallback automático para conectar ao socket global antigo.
- Manual/systemd disputam o **mesmo flock**, adquirido antes de escrever PID/state, limpar/bindar socket ou inicializar recursos com efeitos colaterais. Cleanup só do owner da lease. Um perdedor não escreve estado nem remove nada.
- Persistir identidade com PID, UID, start ticks e caminho/modo do processo. PID file antigo numérico é legado não confiável, nunca autorização para signal. `doctor` distingue unrelated/stale de daemon wedged.
- No Linux, preferir pidfd para sinalizar o processo já validado, evitando TOCTOU entre ler `/proc` e kill. Se identidade/pidfd não puder ser comprovada, manual stop falha de forma explícita, sem raw `os.kill(pid, SIGTERM/SIGKILL)` como fallback. Service-managed usa systemd.
- IPC JSON terminado em newline; manter comandos existentes e respostas `ok/error` nas operações. `status` preserva as chaves existentes e adiciona campos sem removê-las. Limites propostos: comando 4 KiB; resposta 64 KiB; read/idle 2s; no máximo 16 handlers ativos, sem fila ilimitada de threads. `stop` pode precisar do budget maior abaixo — não aplicar 2s cegamente a ffmpeg stop.
- Para `stop`/`discard` síncronos: budget do cliente 15s; shutdown de ffmpeg até 10s + reap até 2s, com margem de IPC. Notificação ocorre fora do caminho crítico. Outros comandos normais devem responder em até 3s. Se usar ACK assíncrono, alterar contrato e UI explicitamente antes de codar, não mascarar timeout com success.
- Timeout de resposta **não prova que o comando não executou**: rofi mostra resultado desconhecido e reconcilia via status, não reenvia `record`/`toggle`/`discard` automaticamente.
- `rofi-meetcap.sh` deixa de manter resolução própria de paths e preferencialmente usa o cliente Python existente para comandos. Um novo `status --json` deve produzir somente JSON em stdout, inclusive unavailable com exit não-zero; diagnósticos em stderr. O formato humano padrão continua.

### C2 — Identidade, gravação e UI

- `recording_id` corresponde ao stem de WAV novo: timestamp completo + componente aleatório resistente a colisão; congelar relógio não colide. ffmpeg usa no-clobber (`-n`), nunca `-y` no artifact canônico.
- `recording_status`: `idle|recording|complete|partial|failed|discarded`; `recording` só true enquanto captura confirmadamente viva. `recording_started_at` ISO para display + relógio monotônico interno para elapsed; `elapsed_seconds` calculado, não estado regravado a cada segundo.
- Monitor único de ffmpeg, compartilhado por stop/erro/reminder. Mudança inesperada detectada em no máximo 2s. Exit inesperado, timeout/kill ou WAV inválido não é complete e não auto-transcreve. Diagnóstico sanitizado/bounded, sem credentials/transcript em logs.
- Stop solicitado registra intenção antes de sinalizar; um exit esperado pelo protocolo de ffmpeg não é automaticamente falha, e um exit 0 inesperado não prova sucesso. Validar stream/duração e classificar ausência de ffprobe como erro de validação explícito, não aceitar `size > 44`.
- `Stop & Save` preserva áudio, inclusive parcial; partial permanece recuperável e não desaparece da listagem. Descarte é separado, exige confirmação de UI e ID esperado na requisição. Confirmação antiga não pode descartar a nova gravação. Backend só apaga o artifact da gravação ativa identificada, depois de reap; sem path arbitrário fornecido pelo cliente, sem deleção de históricos/export/vault.
- Reminders defaults propostos: primeiro aos 60 min, depois a cada 30 min; configuráveis/desligáveis. Config inválida falha claramente. Timer monotônico, sem duplicar aviso, sem auto-stop. Não depender de polling do Waybar para emitir reminder.
- Indicador Waybar JSON: `REC + HH:MM:SS` quando recording vivo; failed/partial/unavailable distinguidos de idle. Clique abre rofi. Fornecer script/snippet no repo; não editar configuração de Hyprland/Waybar real sem aprovação.

### C3 — Export no-clobber e commit

- Separar `recording_id` estável de `export_id` único da revisão. Para novos WAVs, usar o stem preservado no `File:` do transcript. Legado sem ID usa identidade derivada deterministicamente da fonte disponível; se ambígua, erro de conflito em vez de adivinhar identidade/overwrite. Validar/confinar tokens de path, nunca confiar no header/title como caminho.
- Nome default inclui identidade da gravação, não só data/duração. Título humano não define identidade de sidecars. Título explícito que resolve para nota existente retorna conflito; não acrescentar sufixo silencioso e chamar isso de resolução explícita.
- Export repetido não sobrescreve a nota canônica nem sidecars. MVP: erro de conflito por default e **flag proposta `--new-revision`** para gerar outro artifact/revisão preservando tudo que existia. Não implementar overwrite destrutivo nesta sprint. Mudança de API de `export_note` deve manter callers antigos compatíveis.
- Preparar nota e sidecars em staging no mesmo filesystem, validar JSON, flush/fsync e publicar arquivos completos. Sidecars usam diretório único de export; nota é publicada **por último**, como fronteira de commit visível, com criação atômica no-clobber (não `exists()` seguido de `replace()` sobre nota humana).
- Não existe transação atômica de múltiplos paths por chamar `os.replace` várias vezes. A garantia é: nenhum JSON parcial em path final; nota final só aparece após sidecars íntegros; export anterior intacto. Crash antes da nota pode deixar revisão órfã de sidecars, nunca sucesso nem referência canônica publicada. Lock/identidade impedem concorrência entre exports do mesmo source; publicação da nota ainda precisa no-clobber mesmo contra editor externo.
- Cleanup só remove staging comprovadamente pertencente à tentativa, sem apagar revisão completa ou staging de job vivo. Não criar garbage collector global. Deixar órfão diagnosticável é melhor que apagar algo ambíguo.
- Resultado expõe `success`, `status` de integridade (`complete|failed`), `recording_id`, `export_id`, expected/written/missing artifacts e erro. `success=True` somente após publicação completa dos artifacts habilitados. QA/manifests desabilitados não são missing. `corrections.json` só é obrigatório quando houver correções.
- Falha de LLM e qualidade semântica continuam no escopo aberto de #38; **não** descrever `status=complete` físico como QA aprovada. CLI respeita failure físico com exit != 0; daemon não notifica exported quando publicação falhar.

### C4 — Transcrição e privacidade

- Apenas um job de transcrição ativo, reservado sob lock **antes** de responder started. Identidade própria do job, started_at, elapsed e estado long-running. Só o próprio job limpa sua lease; watchdog atrasado de job antigo não limpa job novo.
- Thread ainda viva após join(timeout) permanece busy; não carregar segundo modelo. Timeout não é cancelamento de thread Python. Cancelamento forçado, subprocess worker e broker durável ficam fora do escopo.
- Transcript escrito em staging; completar bytes antes de publicar `.txt`; completion metadata associa recording/job e digest/tamanho do transcript completo. Existência isolada de `.txt` não basta. Crash entre publicar texto e completion marker deixa estado pending/unverified, não done.
- Preservar transcript preexistente, especialmente legado sem marker: classificar legacy/unverified e exigir reprocessamento explícito em revisão separada; não truncar, não considerar automaticamente pronto e não auto-retranscrever. WAV sem transcript commitado continua pending. Todos já completos => `nothing_pending`, sem retranscrever o último. Isso conclui a parte daemon de #12.
- Áudio partial/failed de C2 não entra automaticamente no job. Retry explícito não pode substituir transcript completo anterior. Se WIP router entrar, escritor/marker comum aos dois caminhos e nenhuma remessa de WAV a endpoint externo sem autorização.
- `MEETCAP_TASK_CONTEXT=local|off` proposto, default `local`: matching conservador atual permanece local; **nenhum conteúdo de daily tasks/raw note vai ao LLM** em nenhum dos modos. `off` também dispensa leitura local da daily note. Não criar modo remote/retrieval sem necessidade. Summary/transcript seguem a fronteira remota já documentada do exporter; geração remota de novos action items continua usando apenas conteúdo da reunião.
- Unit/env exclusivos do Meetcap; remover fallback de leitura de credentials Hermes em `llm_client`. Sem copiar secrets automaticamente de outro app. Usuário provisiona chave dedicada quando autorizar deploy. Child env filtrado: ffmpeg/notify não herdam chave OpenRouter nem secrets Hermes; exporter recebe só chave/config necessárias; preservar variáveis de PipeWire/display e libraries indispensáveis com allowlist explícita.

## 5. Lanes, modelos e ownership

Modelos disponíveis em `agy models` durante o planejamento incluem `gemini-3.8-flash-high`, `gemini-3.1-pro-high`, `claude-sonnet-4-6`, `claude-opus-4-6-thinking`. `opencode models` oferece `omniroute/oc-executor-free` e `omniroute/oc-planner-free`; não presumir que exista uma rota `oc-reviewer-free`. Disponibilidade de catálogo não prova quota/auth de um run nem throughput. Os slugs foram conferidos novamente ao finalizar o documento.

**AGY headless com pin explícito é o default desta sprint para Gemini e Claude.** Claude não deve ser mandado ao ACP por um model hint presumido. OpenCode usa ACP. Um warning de plugin Vertex com SyntaxError apareceu ao carregar o helper ACP `--help`, embora exit 0; tratar smoke do runner como gate, não abrir manutenção de Hermes nesta sprint.

| Pacote | Writer | Escopo | Dono exclusivo dos arquivos de produto |
|---|---|---|---|
| A — runtime/lifecycle | OpenCode `omniroute/oc-executor-free` | #13 #18 #19 #28 | `src/runtime_paths.py` (novo), `src/meetcap.py`, `src/doctor.py`; ajuste mínimo de transporte em `rofi-meetcap.sh` |
| B — commit de export | AGY `claude-sonnet-4-6` | #12 exporter, #17 | `src/exporter/vault_exporter.py`, `export_to_vault.py`; helper privado dentro desses módulos por default |
| C — task privacy | AGY `gemini-3.8-flash-high` | #25 | `src/exporter/task_extractor.py`, `src/exporter/prompts.py` somente prompt de tasks, `src/exporter/config.py` somente task-context |
| D — captura/ID/estado | AGY `claude-sonnet-4-6` | #11 #33 e backend #42 | `src/meetcap.py` **depois de A**, testes próprios; sem editar rofi |
| S — serviço/least privilege | AGY `gemini-3.8-flash-high` | #14 core de service/env | `meetcap.service`, `src/doctor.py` **depois de A**, `src/exporter/llm_client.py`, `src/process_env.py` (novo) |
| E — jobs/transcript + wiring | OpenCode `omniroute/oc-executor-free` | #15 #37, hook #12 e wiring #14 | `src/meetcap.py` **depois de D**, imports/calls para `process_env` de S; sem editar módulos de S/B |
| U — desktop/controles | AGY `gemini-3.8-flash-high` | #27 e UI #42 | `rofi-meetcap.sh` **depois de A**, `waybar-meetcap.py` (novo), `docs/examples/waybar-meetcap.jsonc` (novo) |
| Q — integração/regressões | OpenCode; review cruzado AGY Claude | #41; gates dos demais | `tests/conftest.py` (novo), `tests/test_daemon_integration.py` (novo), `tests/test_desktop_integration.py` (novo), `tests/fixtures/` sintéticas |

**Regras para arquivos compartilhados:**

1. `src/meetcap.py`: **A → D → E**. Nunca dois writers nele ao mesmo tempo, nem em worktrees divergentes para depois “resolver no merge”. U pede mudança de contrato ao owner, não mexe nele.
2. `src/doctor.py`: **A → S**. `rofi-meetcap.sh`: **A → U**. `prompts.py` só C nesta sprint. Exporter orquestrador só B.
3. Testes antigos compartilhados (`test_meetcap_daemon.py`, `test_doctor.py`) acompanham o dono da respectiva fase. Q escreve testes novos, não disputa os antigos. RED tests congelados não podem ser afrouxados pelos writers.
4. `README.md` e `.env.example`: **um único consolidator Gemini, depois do fan-in funcional**. Cada pacote entrega seu delta documental no summary; não edita README em paralelo. Mesmo cuidado com CI.
5. Reviews rodam em worktrees próprios. AGY pode editar apesar de pedido review-only; qualquer diff desses worktrees exige revisão e aplicação seletiva, nunca entra automaticamente.
6. Um pacote pode gerar mais de um commit focado, mas **nenhuma issue fecha por implementação parcial**. Testes do contrato cross-packet são gates finais.

### DAG e ondas

```text
G0: aprovação + base/WIP + models + harness seguro
  └─ C0: RED contract lane (OC, serial, sem código de produto)
        └─ W1: A [OC] || B [AGY Claude] || C [AGY Gemini]
              └─ G1: integrar A → B → C, contratos W1 verdes
                    └─ W2: D [AGY Claude] || S [AGY Gemini] || Q1 [OC]
                          └─ G2: integrar D → S → Q1, gates da onda
                                └─ W3: E [OC] || U [AGY Gemini]
                                      └─ G3: integrar E → U; cross-packet #12/#14/#42
                                            └─ W4: Q2 integração final + docs serial
                                                  └─ reviews independentes + CI
                                                        └─ autorização de deploy + QA real
```

C0 produz testes agrupados por onda. **Não executar asserts de feature futura como gate de onda anterior**, nem esconder isso como suite inteira verde: manter testes RED ainda não ativados como artifacts/commits separados da branch de contratos e só integrá-los com sua implementação. Todos os testes existentes + contratos já ativados precisam passar a cada pick. Q1 fecha lifecycle A e captura D onde disponível; Q2 completa transcript/desktop/wiring.

**Budget de calendário (hipótese):** dia 1 G0/C0; dias 2–3 W1; dias 4–5 W2; dias 6–7 W3; dia 8 W4; dias 9–10 reviews, correções e QA autorizada. Não prometer “15 issues em uma tarde” por existirem três agentes. Reavaliar após G1: se A/B ainda tiverem blockers ou a fase consumir mais que a janela, renegociar escopo. Ordem de corte proposta: U/#42 completo (não fechar issue só com backend) e então S/#14 completo (não fechar só com template). Nunca cortar #41 ou os três P0 para preservar quantidade.

## 6. Preparação e tarefas detalhadas

### C0 — Contratos RED e sandbox (OpenCode, serial)

**Objetivo:** transformar os critérios abaixo em testes que falham pela razão certa, sem acionar runtime do host.

**Arquivos:** criar `tests/conftest.py` apenas com fixtures de isolamento; corrigir `tests/test_doctor.py::BootstrapCliTests::test_spawn_manual_daemon_detaches` para não abrir o log global; elaborar novos testes por pacote em commits separados numa branch de contratos. Nomes propostos: `tests/test_runtime_safety.py`, `tests/test_recording_integrity.py`, `tests/test_export_commit.py`, `tests/test_task_privacy.py`, `tests/test_transcript_commit.py`, `tests/test_service_env.py`, `tests/test_desktop_controls.py`.

- [ ] Revalidar SHA, worktree limpa, AGENTS e manifest. Não instalar pacotes no venv compartilhado das lanes.
- [ ] Criar HOME/runtime/state/vault temporários por teste; limpar credenciais e bloquear rede real. Subprocessos herdam isolamento explicitamente. Não monkeypatchar o daemon/socket/lifecycle que #41 precisa exercitar.
- [ ] Criar os testes de C1–C4 usando APIs existentes quando possível; registrar novas interfaces como propostas, não imports “descobertos”.
- [ ] Rodar cada grupo RED e registrar assert falho esperado. ImportError por typo/fixture quebrada não conta como reprodução do bug.
- [ ] Classificar assertions que já passam como regression locks, não novas correções. Congelar hashes dos contratos por onda.
- [ ] Core revisa testes e executa baseline de novo no harness. Writers recebem arquivos e não podem enfraquecer assertions/xfail/skip para produzir GREEN.

**Saída:** baseline segura + mapa `contrato → teste → falha observada → onda`. Nenhum fix de produto ainda.

### A — Runtime privado, single-instance, identidade e deadlines

**Âncoras atuais:** `src/meetcap.py` constantes runtime, `save_state`, `run_server`, `daemon`, `client_handler`, `_send`, `_spawn_manual_daemon`, `restart_cmd`; `src/doctor.py` `read_pid`, `pid_alive`, `stop_pid`, `clean_stale_files`, `diagnose`; `rofi-meetcap.sh` transporte. A releitura de definições/usos precede edição; linhas do WIP diferem do remoto.

**Passos pequenos, na ordem:**

- [ ] A1: RED para diretório insecure/symlink, modes, state atomic e client/doctor usando mesmo endpoint.
- [ ] A2: criar resolvedor mínimo em `runtime_paths.py`; ajustar os callers e log privado/rotativo; GREEN A1.
- [ ] A3: RED com dois processos concorrentes e snapshot dos bytes/inode da metadata da primeira instância.
- [ ] A4: adquirir flock antes de PID/state/socket; distinguir stale próprio de estranho; cleanup condicionado à lease; GREEN A3.
- [ ] A5: RED de PID unrelated/start ticks diferentes e identity ausente; provar zero sinais destrutivos.
- [ ] A6: metadata de identidade e stop seguro pidfd/service; `doctor` reporta classes distintas; GREEN A5. Não conceder identidade a PID file legado só por cmdline “parecido”.
- [ ] A7: RED de peer silencioso, slow sender, payload excessivo, resposta truncada e mais que 16 clientes idle.
- [ ] A8: framing/budgets/limite handlers; cliente `status --json`; rofi usa transporte com deadline. Reservar budget de stop de C1. GREEN A7.
- [ ] A9: testar falhas entre adquirir lock, criar PID e bind; soltar recursos próprios sem apagar outro runtime. Não aguardar I/O externo segurando state_lock.
- [ ] A10: tests antigos + contratos de A, revisão completa do diff, summary de API e commit local após gate do Core.

**Comandos de aceite:** `$PY -m pytest tests/test_runtime_safety.py tests/test_meetcap_daemon.py tests/test_doctor.py -v`; `bash -n rofi-meetcap.sh`; suíte ativa completa. Gate cross-process definitivo em Q.

**DoD issues:** #13 zero signal ao unrelated/reused; #18 owner/mode/path/write atomic; #19 timeout client+server+rofi e handlers bounded; #28 segundo daemon rejeitado sem alterar o primeiro. Todos exigem regressão real em Q, não apenas mocks.

### B — Export sem overwrite e commit íntegro

**Âncoras:** `src/exporter/vault_exporter.py` `export_note`, `build_note_content`, bloco `write_text` da nota e sidecars; `export_to_vault.py` dispatch/exit. `transcript_parser.parse_meetcap_transcript` já retorna `meta`/texto/segmentos: consumir shape existente, não alterar parser por conveniência.

- [ ] B1: RED — duas reuniões mesmo dia/duração, `--title` duplicado, re-export e nota editada manualmente. Registrar digest/bytes do original.
- [ ] B2: identity/path confinado e conflito explícito; flag `--new-revision` preserva anteriores. Sidecars ligados ao mesmo export_id; GREEN B1.
- [ ] B3: RED — OSError/ENOSPC no terceiro sidecar, write parcial de JSON, crash antes da nota, dois exporters concorrentes e nota criada externamente entre validação/publicação.
- [ ] B4: staging/validação/publicação sidecars-first e nota-last no-clobber, sem alegar transação multi-file. GREEN B3.
- [ ] B5: RED — QA/manifests on/off, correções presentes/ausentes, resultado expected/written/missing e exit CLI quando falha física.
- [ ] B6: contrato de resultado e caller compatível; preservar sidecars de revisão anterior; limpar apenas staging próprio. GREEN B5.
- [ ] B7: export offline completo com respostas LLM mockadas, arquivos reais em vault sintético e JSON readback; verificar transcript bruto byte-identical e old-note byte-identical.
- [ ] B8: review adversarial específico de atomicidade/no-clobber, testes ativos completos, commit local após gate.

**Comandos:** `$PY -m pytest tests/test_export_commit.py tests/test_vault_exporter_artifacts.py tests/test_export_to_vault.py tests/test_room_manifest.py tests/test_note_verifier.py -v`.

**Provas obrigatórias:** não basta `exists()`; ler bytes, JSON e IDs, comparar versões e simular falhas em cada fronteira de commit. No-clobber precisa resistir à corrida, não só caso serial. #12 permanece aberta até E provar `nothing_pending` sem retranscrição implícita.

### C — Daily task matching local, payload mínimo

**Âncoras:** `src/exporter/task_extractor.py` `generate_task_suggestions`, `load_daily_task_context`, `compute_explicit_task_matches`; prompt de tasks em `prompts.py`; `config.py`.

- [ ] C1: RED com daily note sintética contendo task relevante e marcador privado não relacionado; capturar todos os requests mockados, inclusive fallback structured/plain.
- [ ] C2: remover task context dos prompts remotos; gerar novas sugestões somente de reunião/summary e fazer matching exato localmente como hoje.
- [ ] C3: implementar `MEETCAP_TASK_CONTEXT=local|off`, default local. `off` não lê daily note. Valor desconhecido não vira remote.
- [ ] C4: GREEN — nenhum marcador de task/nome de arquivo da daily/raw note no payload ou logs; matching local preservado, tasks vazias/ausentes e Unicode funcionam.
- [ ] C5: entregar seção de privacidade para consolidator; suíte ativa e review de payload antes de commit.

**Comandos:** `$PY -m pytest tests/test_task_privacy.py tests/test_export_to_vault.py tests/test_llm_client.py -v`.

**Não fazer:** embeddings/retriever, novas chamadas LLM para filtering, mudança em prompts de QA/claims/manifest, remoção dos action items da própria reunião.

### D — ffmpeg liveness, WAV no-clobber e backend de overrun

**Âncoras:** `src/meetcap.py` `State`, `start_recording`, `stop_recording`, `save_state`, `handle_command`, `daemon` cleanup; contrato de status A.

- [ ] D1: RED com relógio congelado e dois stop/start sequenciais; WAV antigo imutável. ID resistente + `-n`; GREEN.
- [ ] D2: RED fake ffmpeg que inicia saudável e morre depois; status muda sem precisar mandar stop. Repetir exit 0 inesperado e nonzero.
- [ ] D3: monitor único, intenção de stop e classificação complete/partial/failed; drenar logs sem bloquear pipe; sanitizar diagnóstico. GREEN D2.
- [ ] D4: RED stop normal, timeout seguido de kill/reap, arquivo header-only, duração/stream inválidos, ffprobe ausente. Validação do WAV e nenhum auto-transcribe de partial; GREEN.
- [ ] D5: RED `start → elapsed → stop → reset`, ajuste de wall-clock não altera elapsed monotônico; status inclui started_at/elapsed sem escrita por segundo. GREEN.
- [ ] D6: RED reminder aos 60/30 com fake clock; configuração off/inválida; um aviso por janela, reset de job, zero auto-stop. Implementar no monitor existente; GREEN.
- [ ] D7: RED descarte separado: cancelar confirmação preserva áudio; confirmation/recording_id stale não apaga nova gravação; confirmar para ID atual só remove aquele WAV após reap; nada em vault/histórico muda. Implementar comando protocolar de descarte e semantics Stop & Save; GREEN.
- [ ] D8: tests próprios + antigos; comunicar schema final a E/U/Q e commit após gate.

**Comandos:** `$PY -m pytest tests/test_recording_integrity.py tests/test_meetcap_daemon.py -v`. O teste de morte aos 30s da issue pode usar fake clock/polling controlado no unitário, mas Q precisa executar ao menos um exit atrasado real depois do startup; não simular o monitor inteiro.

**Prova adicional em Q:** timer não duplica monitor e uma gravação ativa não perde socket numa segunda inicialização. Descobertas sobre autenticidade do áudio real aguardam QA autorizada, não são provadas por fake ffmpeg.

### S — Unit portável e credenciais de escopo mínimo

**Âncoras:** `meetcap.service`, `src/doctor.py` `install_service`/service helpers, `src/exporter/llm_client.py` `load_openrouter_key`. Novo `src/process_env.py` só se necessário para compartilhar allowlists/derivação entre installer e callers.

- [ ] S1: RED render da unit sob paths sintéticos distintos, inclusive espaços, minor Python diferente e configuração sem CUDA; nenhuma string pessoal/credential real na unit versionada.
- [ ] S2: template derivado do repo/venv atual, EnvironmentFile dedicado opcional/configurável, CUDA via override local/detecção explícita; sem pin `python3.11` no template.
- [ ] S3: RED falhas em daemon-reload/enable/start; instalar/renderizar não reporta sucesso quando systemctl falha. GREEN com subprocessos mockados, sem instalar nada no host.
- [ ] S4: RED env com secret sentinela Hermes + chave OpenRouter fake; construir env por finalidade e remover leitura de `~/.hermes/.env` do llm client. Não inspecionar arquivo de credenciais real.
- [ ] S5: GREEN helper/loader; preservar PATH/HOME/runtime/audio/display/library vars necessários e somente credentials exigidas pelo exporter. Entregar contrato `_child_env` equivalente a E, que fará o wiring real de `src/meetcap.py`.
- [ ] S6: validar unit renderizada com `systemd-analyze --user verify <unit-sintetica>` quando ferramenta disponível; falha indisponível é BLOCKED desse gate, não “unit test pass prova systemd”. Documentar provisionamento dedicado manual e rollback.

**Comandos:** `$PY -m pytest tests/test_service_env.py tests/test_doctor.py tests/test_llm_client.py -v`.

**DoD #14:** template/loader sozinhos não bastam. E deve provar que ffmpeg, notify, export e manual spawn efetivamente recebem o env correto. Deploy real permanece autorização separada.

### E — Job lease, transcript commit e wiring dos P0/security

**Âncoras:** `src/meetcap.py` `transcribe`, `do_transcribe_last`, `transcribe_cmd`, `_reset_transcribing_on_fail`, `auto_export`, `start_recording`, `notify`, `_spawn_manual_daemon`; se WIP aprovado, também `transcribe_via_router`.

- [ ] E1: RED duas requisições simultâneas antes do worker iniciar; apenas uma recebe started e apenas um modelo/job nasce. Reservar lease antes de iniciar thread; GREEN.
- [ ] E2: RED fake join timeout com thread viva; busy e elapsed mantidos. Job antigo finalizando não limpa o novo. Atualizar watchdog/finally pelo job id, com prova de long-running e término real; GREEN.
- [ ] E3: RED erro no write do transcript, crash entre texto/marker, transcript antigo existente e .partial; nenhum caso incompleto vira complete nem trunca anterior.
- [ ] E4: escritor único staging+commit+completion metadata, validação mínima/digest e seleção pending/legacy explicada em C4; GREEN. Não comparar último timestamp com duração exigindo igualdade, pois silêncio final é legítimo.
- [ ] E5: RED todos completos => nothing_pending; WAV partial/failed não entra; reprocessamento explícito cria revisão e não sobrescreve. Completar hook #12 e export contract B, sem implementar a CLI inteira #29.
- [ ] E6: RED env efetivamente passado em todos os spawn/run relevantes (ffmpeg, notify, exporter, daemon manual e probes), usando helper de S. Aplicar allowlists; não quebrar áudio/display por remover variáveis essenciais; GREEN #14 wiring.
- [ ] E7: RED publicação do exporter falha => não notifica exported; caminhos de exceção limpam somente o job correto. Não introduzir retry/fila #16 ou alegar status semântico #38 resolvido.
- [ ] E8: suite ativa completa, Q integra recovery do marker, review de races/legacy migration, commit após gate.

**Comandos:** `$PY -m pytest tests/test_transcript_commit.py tests/test_recording_integrity.py tests/test_export_commit.py tests/test_service_env.py tests/test_meetcap_daemon.py -v`.

### U — Erros no rofi e indicador persistente

**Âncoras:** `rofi-meetcap.sh`; criar `waybar-meetcap.py` + snippet JSONC de exemplo. Não editar desktop do Lucca.

- [ ] U1: RED scripts com PATH fake: daemon responde `ok:false`, timeout, JSON inválido, unavailable e recuperação malsucedida. Observar exit e notificações, não só mock de função Python.
- [ ] U2: rofi mostra erro sanitizado e resultado desconhecido; não descarta resposta nem reenvia comandos mutantes cegamente. Transport deadline vem de A. GREEN.
- [ ] U3: RED menu em recording com duração, busy, partial/failed e unavailable; labels `Stop & Save` e `Discard Recording` distintas. Nada de “Start Recording (queued)” se não existe queue real.
- [ ] U4: wiring de confirmação de descarte ligada ao ID atual; cancelar não manda discard. Mostrar resultado e reconciliação; GREEN.
- [ ] U5: RED formatter Waybar de recording/idle/partial/unavailable/stale e elapsed acima de 1h; JSON válido/escaping, sem vazamento de paths/logs em tooltip. Implementar leitura live com timeout e desconhecido distinto de idle; GREEN.
- [ ] U6: snippet com intervalo de polling moderado, retorno JSON, clique abre controles. Estado autoritativo vem do socket, não de cache isolado. Disponibilizar script executável/forma invocável comprovada.
- [ ] U7: doc de instalação opt-in, variables e reminders; teste `bash -n`, suites de scripts e handoff para QA real. Não declarar indicador instalado no desktop.

**Comandos:** `$PY -m pytest tests/test_desktop_controls.py tests/test_recording_integrity.py -v`; `bash -n rofi-meetcap.sh meetcap.sh`; `$PY -m py_compile waybar-meetcap.py`.

### Q — Integração real, docs e fechamento técnico

**Q1 após A, em paralelo a D/S:**

- [ ] Subprocesso real do daemon com HOME/XDG/runtime/recordings temporários, fake pactl/ffmpeg/notify/Whisper na fronteira. Sem importar um daemon mockado no processo pai e chamar isso de integração.
- [ ] `start → wait socket → status`, segundo start rejeitado, primeiro socket/metadata intactos, restart com PID novo e ping, shutdown limpa só seus arquivos, stale socket próprio recuperado.
- [ ] Processo sentinela unrelated criado pelo teste permanece vivo e não recebe signal destrutivo. Simular metadata com start ticks divergentes; não esperar PID reuse real nem usar processos do host como cobaias.
- [ ] Peer silencioso/slow sender real e saturation > limite de handlers; medir budgets com monotonic sem sleeps arbitrários. Leitores concorrentes de state nunca observam JSON parcial.

**Q2 após E/U:**

- [ ] Fake ffmpeg morre após startup: status/indicator deixam recording e informam partial/failed. Timeout/kill não auto-transcreve.
- [ ] Crash/restart em commit de transcript preserva WAV pending e transcript anterior; completion marker válido reconhecido após restart. Durabilidade de export job #16 ainda não existe e não entra como critério fingidamente satisfeito.
- [ ] Roundtrip `record → status/elapsed → stop → status` real por socket; descarte sem confirmação não executa; reconciliação UI sem falso idle.
- [ ] Pipeline offline com WAV/transcript sintéticos e respostas de LLM mockadas → nota+sidecars reais íntegros em diretório temporário. Tratar isso como integração hardware-free, não E2E de áudio/provider.
- [ ] Todos os testes novos estão sob `tests/` e rodam pelo comando já usado no CI; CI 3.11–3.13 precisa verde para o SHA integrado após push autorizado.
- [ ] Consolidator Gemini atualiza somente documentação dos contratos entregues: README, `.env.example` sintético e snippet. Sem apagar WIP; fazer em worktree limpa. #34/#35 continuam abertas fora do alcance parcial.
- [ ] Reviews independentes: correctness/security por AGY Claude Opus ou Sonnet em worktree review, simplicidade e coverage por rota diferente. Autor não é único revisor do próprio pacote. Findings com `path:line`, severidade, reprodução e teste faltante.
- [ ] Zero blockers, suíte rerodada pelo Core após última correção, diff completo inspecionado; todos os critérios vinculados a evidência no ledger.

**Comandos:** `$PY -m pytest tests/test_daemon_integration.py tests/test_desktop_integration.py -v`; `$PY -m pytest tests/ -v`; `$PY -m compileall -q src export_to_vault.py waybar-meetcap.py`; `bash -n meetcap.sh rofi-meetcap.sh`; `git diff --check` e `git diff --check "$BASE_SHA"..HEAD`.

## 7. Protocolo operacional para o Core em GLM 5.3 Flash

### Antes de qualquer execução

1. Ler este plano, `AGENTS.md`, ledger e skills; conferir que Lucca aprovou escopo e que a troca de modelo ocorreu. Não alterar provider/config do Hermes automaticamente.
2. `git status --short --branch`, `git fetch origin`, `git rev-parse origin/main`, `git worktree list`; relistar issues/PRs. Se main ou status das issues mudou, rebasear o **plano**, não executar sobre snapshot antigo cegamente.
3. Registrar `$BASE_SHA` e decisão WIP. Criar integration worktree e worktree por lane fora do checkout principal. Exemplo de nomes: `sprint5/integration`, `sprint5/a-runtime`, `sprint5/b-export`, `sprint5/c-privacy`; sempre validar ausência de branch/path prévio. Não usar stash/reset/clean nem alterar main.
4. `$PY` é o Python do venv verificado, com caminho absoluto resolvido pelo Core. Usá-lo em todas as lanes/testes. Se precisar instalar dependência, venv por worktree, nunca instalar/rebuildar no venv compartilhado. requirements atuais já cobrem o Python necessário.
5. Criar artifacts operacionais em `$HOME/.local/state/meetcap-sprint5/`: briefs, stdout/stderr, summaries e verdicts. São locais, fora do repo público. Não registrar tokens/env completo, transcripts reais, nome de cliente nem paths do vault.
6. Rodar `agy models`, `opencode models` e smoke mínimo com cwd/brief sintético para as rotas escolhidas. Pin não aceito ou modelo diferente no output = **BLOCKED**, sem fallback silencioso. Catálogo não equivale a autenticação/quota suficiente. Falha do helper ACP/Vertex deve ser isolada e corrigida via skill apropriada ou rota OC já suportada; não editar Hermes no meio da sprint sem autorização.

### Forma do brief obrigatório por pacote

O Core gera um arquivo antes de cada dispatch contendo:

- pacote/issues, base SHA exato, dependências GREEN e objetivo;
- tarefa(s) desta rodada e caixas de aceite copiadas do plano;
- allowlist de arquivos e arquivo proibido por ownership; declarar quais caminhos são novos;
- contratos RED congelados e hashes; proibição de enfraquecer assertions;
- `$PY`, cwd, comandos de teste e ambiente sintético;
- sem dados reais/rede/provider pago/serviço ativo, sem commit/push/merge pelo writer;
- saída em artifact local: arquivos alterados, testes/comandos reais com exit, pendências, modelo efetivo, riscos e trechos de documentação a consolidar;
- para review: primeira linha do verdict `APPROVE` ou `REQUEST_CHANGES`, findings com referências. Output vazio não equivale a aprovação.

Não inventar comandos ou imports para reduzir tokens do brief. Se um writer precisar mudar contrato/allowlist, devolver proposta ao Core antes de tocar arquivo alheio.

### Dispatch AGY (Gemini e Claude)

Template documentado; não executado durante planejamento. Substituir variáveis por valores validados, não deixar placeholders chegarem ao agente:

```bash
python3 "$HOME/.hermes/scripts/run_agy_headless_lane.py" \
  --cwd "$WT" --brief "$BRIEF" --model "$MODEL" \
  --mode accept-edits --print-timeout 25m --output-format stream-json \
  > "$OUT" 2> "$ERR"
```

Executar com `terminal(background=true, notify=true)`. Verificar `init.model` e `result.status`, processo real e artifacts. Gemini Flash é default de rotina; Pro ou Claude Opus para correção difícil/review somente com pin verificado e registro. Quotas de Gemini e Claude são buckets distintos; trocar Flash por Pro não foge de quota Gemini, nem Sonnet por Opus da quota Claude. Se quota esgotar, parar/aguardar ou pedir mudança de rota, não fingir que o modelo solicitado rodou.

### Dispatch OpenCode ACP

```bash
python3 "$HOME/.hermes/scripts/run_acp_lane.py" \
  --command /usr/bin/opencode --arg acp \
  --cwd "$WT" --brief "$BRIEF" \
  --model-hint omniroute/oc-executor-free \
  --approve-permissions --turn-timeout 1800 \
  > "$OUT" 2> "$ERR"
```

Mesma regra de background/notify. `--approve-permissions` é aceitação automática: só usar com worktree/brief de escopo limitado e consentimento de execução; não é sandbox de segurança. Se a sessão não aceitar o hint, interromper. O combo `oc-executor-free` não identifica necessariamente um único modelo upstream: reportar rota efetiva e upstream apenas se houver evidência, nunca chamar o combo de modelo Claude/Gemini por suposição.

**Monitoramento:** ler stderr forense e árvore real de PIDs. Notificação `exited`/exit null, output 0B ou summary ausente não provam que writer parou. Nunca rodar gates/escrever/commitar worktree com child vivo. Não matar lane jovem por falta de diff. Timeout exige preservar progressos e brief de correção específico, não reiniciar tudo. Limite de duas rodadas de correção por pacote antes de escalar decisão; não afrouxar gates para caber no tempo.

### Fan-in e commits

- Core não implementa; pede correções ao dono da lane. Pode escrever plano/brief/ledger e executar testes/inspeções/Git autorizados.
- Depois de writer e descendants terminarem, Core lê diff integral, confirma allowlist e executa testes. Summary de agente é alegação, não prova.
- Na execução aprovada, Core pode fazer commits locais focados/Conventional Commits após gates, com `git add` apenas da allowlist. Neste planejamento não há autorização implícita para commit.
- Integrar serialmente em worktree de integração na ordem do DAG. Cada pick: testes ativos + compile + diff-check. Não integrar todos e deixar regressão sem owner.
- Testes RED de feature futura não ficam na branch de integração até sua implementação; branches/artifacts de contrato preservam a prova RED. Nada de contar suíte parcialmente selecionada como suíte completa.
- Push/PR/merge exigem autorização explícita. Nenhuma alteração de issue/milestone/label remota foi feita pelo planejamento. Depois de write remoto autorizado, reler SHA/PR/issue exata.
- PRs podem agrupar o pacote; dependentes contra base correta. `Fixes #N` só quando TODOS os critérios de N estão satisfeitos. Para #12/#14/#42 antes do último hook usar `Refs`, não fechamento automático prematuro.
- SHA após correção invalida review/test evidence anterior: rerodar gates afetados e suite completa. CI verde precisa ser do head correto; não reutilizar o run da baseline como validação da sprint.

## 8. Definition of Done, aceite e rollback

### Gates obrigatórios

| Gate | Evidência | Bloqueia |
|---|---|---|
| G0 — autorização/base | aprovação, SHA, decisão WIP, models efetivos, sandbox | qualquer writer de produto |
| C0 — RED real | teste/assert, comando, exit, motivo correto da falha | fan-out inicial |
| G1/G2/G3 — integração de onda | commits, diff allowlist, suíte ativa completa, contratos GREEN | onda dependente |
| G4 — segurança/integridade | testes cross-process/ENOSPC/concurrency e review independente | marcar pacotes Done |
| G5 — CI | matriz verde 3.11–3.13 do SHA final, após push autorizado | claim CI/release-ready |
| G6 — QA desktop/áudio | instalação/alvo autorizados, gravação sintética/controlada real, indicator/stop/reminder/discard verificados | fechamento integral #42 e claim E2E/deploy |

G5/G6 não autorizados ou ambiente indisponível = **BLOCKED/PENDING**, não PASS. Pode reportar implementação local verificada sem dizer sprint concluída/instalada.

### QA real (somente depois de autorização)

- [ ] Confirmar que não há captura, transcrição ou export ativo; Lucca define janela. Nunca parar serviço para descobrir se estava em uso.
- [ ] Registrar runtime/unit/config atuais e destino de teste; preservar rollback sem copiar secrets para repo/log.
- [ ] Instalar unit/config dedicados apenas no alvo aprovado. Provisionar credential do Meetcap com permissão restrita sem imprimi-la; não herdar `.hermes/.env`.
- [ ] Áudio de teste autorizado, sem conversa real: mic + sistema com fontes controladas. Capturar, verificar indicador/elapsed, `Stop & Save`, WAV válido/duração e transcript local se parte do smoke autorizado.
- [ ] Testar reminder com thresholds temporários curtos e restaurar; testar unavailable sem idle falso; descarte de gravação **sintética recém-criada**, confirmação cancelada e confirmação aceita.
- [ ] Export real para vault real/OpenRouter não é necessário para #41; se pedido E2E externo, obter consentimento específico e usar apenas transcript sintético e destino temporário aprovado. Ler de volta artifacts do alvo antes de alegar sucesso.
- [ ] Não apagar recordings históricos nem alterar Waybar/Hyprland fora do snippet consentido. Capturar provas UI sem paths/clientes/secrets.

### Rollback

- Código: revert dos commits próprios na integração; nada de reset/clean sobre WIP. Não force-push por default.
- Antes de deploy, guardar referência da unit/config anteriores localmente sem expor conteúdo. Sem rollback automático que reative captura ou credenciais globais.
- Runtime novo não deve apagar paths globais legados; migração com serviço parado em janela autorizada. Cliente novo versus daemon antigo deve exibir incompatibilidade/unavailable claramente, não conectar silenciosamente no legado.
- Artifacts no-clobber dispensam restauração por overwrite: nota e sidecars anteriores ficam íntegros. Reversão de código não apaga revisões novas, `.partial` ou staging; recuperação manual documentada e por ID.
- Se QA falhar, manter evidência e status de falha; voltar à unit anterior só com autorização e sem operação ativa. Não anunciar que mitigação manual fechou a issue.

## 9. Ledger do handoff (atualizar durante a execução)

Estados permitidos: `PLANNED → RED → IMPLEMENTED → VERIFIED → REVIEWED → INTEGRATED → RELEASED`; `BLOCKED` em qualquer etapa. Issue só `DONE` quando todos os critérios estão comprovados; ledger nunca substitui estado real do GitHub.

| Pacote | Issues | Estado atual | Base/commit | RED/GREEN/log | Review | Pendência |
|---|---|---|---|---|---|---|
| C0 | contratos/sandbox | PLANNED | — | — | — | Aprovação/G0 |
| A | #13 #18 #19 #28 | PLANNED | — | — | — | C0 |
| B | #12(export) #17 | PLANNED | — | — | — | C0; hook E para #12 |
| C | #25 | PLANNED | — | — | — | C0 |
| D | #11 #33 #42(backend) | PLANNED | — | — | — | A |
| S | #14(service/env) | PLANNED | — | — | — | A; wiring E |
| E | #15 #37 #12(hook) #14(wiring) | PLANNED | — | — | — | D/S/B |
| U | #27 #42(UI) | PLANNED | — | — | — | A/D |
| Q | #41 e integração final | PLANNED | — | — | — | Q1 após A; Q2 após E/U |

**Por issue, antes de fechamento:** anexar lista de cada critério original com nome de teste/evidência, SHA, comando+exit, review e limitações. Se um critério depende de deploy/UI real e só tem mock, manter pendente.

### Próximo passo exato após aprovação e troca de modelo

Executar **G0**, não o fan-out inteiro. Resolver base/WIP, confirmar rotas/modelos e criar worktrees/brief C0. Entregar C0 RED revisado e só então iniciar A/B/C. Não reinterpretar 15 issues como autorização para “go total” no restante do backlog; 18 continuam fora.
