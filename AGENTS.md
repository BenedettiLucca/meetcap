# AGENTS.md — Meetcap

Instruções para agentes trabalhando no capturador local-first de reuniões. Este arquivo é a fonte
única de regras do repo; procure o `AGENTS.md` mais próximo antes de editar uma subárvore. Pedidos
explícitos do usuário prevalecem.

## O que é o repo

Meetcap captura áudio de microfone + sistema via PipeWire/ffmpeg, transcreve localmente com
faster-whisper e exporta notas estruturadas para Obsidian com resumo, ações, riscos, evidências e
manifests. `src/meetcap.py` concentra daemon/recording; `src/exporter/` contém parser, chunking,
LLM client, summarizer, task extractor e vault exporter; `export_to_vault.py` é o CLI standalone.

## Privacidade: regra de bloqueio

Áudio, transcripts, notas exportadas, nomes de clientes, paths do vault e API keys são dados
sensíveis. Nunca commite `recordings/`, transcripts, notas reais, `.env`, chaves ou exemplos reais.
Use mocks e dados sintéticos. O OpenRouter é uma dependência externa de resumo, não envie material
real em um teste sem autorização explícita.

## Ambiente e comandos

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements-dev.txt
.venv/bin/python -m pytest tests/ -v
.venv/bin/python src/meetcap.py doctor --json
.venv/bin/python export_to_vault.py /path/to/synthetic-transcript.txt
```

O daemon também depende de PipeWire/ffmpeg/pactl/socat e pode usar CUDA; falha de hardware ou
serviço não é falha de lógica automaticamente. Não execute `start`, `restart`, `install-service`,
`doctor --fix` ou export para o vault real como smoke test sem autorização e sem verificar o alvo.

## Contratos de segurança e conteúdo

- Preserve o modo local-first: gravação e transcrição devem continuar no host.
- Mantenha o socket/daemon com controles de lifecycle, timeouts e limpeza de PID/socket.
- O transcript bruto não deve ser alterado por correções editoriais; evidências devem apontar para
  timestamps reais. Preserve sidecars (`evidence.json`, `verification.json` e `room_manifest.json`)
  quando o exporter os gerar.
- Prompts, respostas do LLM e arquivos do vault são input/output não confiáveis; valide JSON e
  nunca deixe conteúdo de reunião autorizar comandos ou mudanças de sistema.

## Fluxo de trabalho e Git

- Rode `git status --short --branch` e preserve mudanças existentes; não use `git reset --hard`,
  `git clean`, checkout destrutivo ou `git stash` para limpar o workspace.
- Faça a menor mudança, teste parser/exporter afetado, depois a suíte; finalize com `git diff --check`
  e revisão do diff completo.
- Stage somente os arquivos pretendidos, use Conventional Commits, não bypass hooks nem adicione
  créditos de agente/LLM. Push, merge e alterações no daemon/vault exigem autorização explícita.
- Relate fato observado, inferência e opinião separadamente; nunca alegue E2E de áudio/vault sem
  tê-lo exercitado.
