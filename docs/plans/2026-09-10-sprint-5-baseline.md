# Sprint 5 — baseline do planejamento

## Proveniência

Inspeção e execução durante a sessão de planejamento de 2026-09-10, por Core em GPT 6 Astra. Fonte remota: <https://github.com/BenedettiLucca/meetcap>.

- `git fetch origin` executado; HEAD e origin/main em `124f9d1f2bc433c21ba2f7d3bd3eae8669e3d132`.
- Inventário REST paginado: 33 issues abertas, excluindo PRs. 3 P0, 22 P1, 7 P2 nos títulos; #9 sem prioridade. Nenhuma PR aberta.
- CI observado: <https://github.com/BenedettiLucca/meetcap/actions/runs/33989244045>, completed/success no mesmo SHA. O workflow usa Python 3.11–3.13.
- Nenhuma issue foi fechada/editada, nenhuma lane de implementação foi disparada, nenhum commit/push/deploy foi realizado nesta etapa.

## Testes efetivamente executados

Python do venv do projeto: **3.11.14**.

Foram criadas duas cópias temporárias de `src`, `tests` e `export_to_vault.py` a partir de `git archive origin/main`. A segunda recebeu somente os arquivos Python modificados do WIP (`src/meetcap.py` e `src/exporter/config.py`). O venv foi reutilizado apenas para executar, sem instalar dependências.

A execução usou `pytest.main(['tests/', '-q', '-p', 'no:cacheprovider'])`, com:

- HOME e vault sintéticos;
- ambiente reconstruído sem API keys/credenciais;
- autoload de plugins pytest externos desabilitado;
- conexões INET bloqueadas; sockets UNIX temporários continuaram reais;
- tentativa de conexão ao endpoint legado do host bloqueada;
- abertura de log global pelo teste antigo de manual spawn redirecionada ao sandbox.

Resultado devolvido pela ferramenta, preservado aqui como registro textual:

```text
remote-main
exit_code: 0
188 passed, 10 subtests passed in 0.59s

local-wip
exit_code: 0
188 passed, 10 subtests passed in 0.56s
```

Os logs originais foram temporários e não estavam mais disponíveis na retomada da sessão. Este documento registra o output recebido da execução, não apresenta um novo rerun nem um arquivo bruto de log ainda acessível. A execução futura deve produzir evidência durável de seus próprios gates fora do repo público.

## Limites da evidência

- Não houve teste real de áudio, Whisper/GPU, router de transcrição, OpenRouter, vault real, instalação de unit ou UI desktop.
- Baseline verde não reproduz nem refuta os bugs de race/crash/IPC reportados. Os contratos RED e a integração hardware-free estão planejados para a execução.
- WIP passar nos testes antigos não aprova suas features nem sua integração na sprint.
- Nesta etapa não foi executada localmente a matriz Python 3.12/3.13; o sinal dessa matriz vem do CI remoto da baseline, não de testes novos.

## Catálogos de executores conferidos

`agy models` retornou, entre outros, estes IDs literais:

```text
gemini-3.8-flash-high
gemini-3.1-pro-high
claude-sonnet-4-6
claude-opus-4-6-thinking
```

`opencode models` incluiu `omniroute/oc-executor-free` e `omniroute/oc-planner-free`. O plano usa a primeira rota para implementação. O catálogo não comprovou quota, autenticação de uma execução ou upstream efetivamente selecionado por um combo.

O helper `run_acp_lane.py --help` retornou exit 0 e também um warning de plugin Vertex com SyntaxError. O helper `run_agy_headless_lane.py --help` retornou exit 0. Ambos são descoberta de comandos, não smoke bem-sucedido de lane. G0 exige smoke e pin de modelo antes de qualquer implementação.

## Estado preservado

O WIP preexistente permaneceu fora do escopo de edição. O planejamento alterou somente documentação do plano: o índice canônico em `docs/sprint-plan.md`, o plano detalhado e este registro de baseline.
