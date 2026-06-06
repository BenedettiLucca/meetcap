# Recording Retention Policy

- **Date captured:** 2026-06-03
- **Source:** Synapse Diff cron
- **Status:** idea
- **Repo remote:** none (local-only fallback)
- **Project:** `/home/lucca/Projects/meetcap`

## Summary

Meetcap já fecha o loop principal bem: grava, transcreve, resume e exporta pro vault. O ponto fraco agora é o pós-processamento dos artefatos crus. O diretório `recordings/` virou backlog físico da automação — os outputs são duráveis no vault, mas os arquivos pesados continuam se acumulando localmente sem política explícita.

## Why now

Estado observado em 2026-06-03:
- `recordings/` com **22 arquivos**
- tamanho total: **1.2G**
- WAVs isolados de **665500KB**, **283864KB**, **111932KB**
- logs `.ffmpeg.log` e transcrições `.txt` também ficam acumulando

Isso é o tipo de dívida que parece pequena até começar a atrapalhar uso real, backup e manutenção. Como o export para Obsidian já existe, agora faz sentido transformar “nota durável, bruto descartável/arquivável” em comportamento oficial do projeto.

## Proposed feature

**Name:** Export-Then-Prune Recording Retention

### Core behavior

Depois de uma exportação bem-sucedida para o vault, o Meetcap deve registrar um manifest do artefato e aplicar uma política configurável de retenção para:
- `.wav`
- `.txt`
- `.ffmpeg.log`

### Policy knobs

- `keep_last_n_recordings`
- `retain_wav_days`
- `retain_logs_days`
- `retain_transcripts_days`
- `archive_instead_of_delete`
- `cleanup_dry_run`

### Safety model

Nunca apagar nada antes de confirmar:
1. transcrição existe
2. export para o vault foi concluído
3. note path final foi registrado no manifest
4. cleanup está fora da janela de retenção

## Suggested implementation

### 1. Emit artifact manifest
Criar um `recordings/<basename>.manifest.json` ou um índice único com:
- audio path
- transcript path
- ffmpeg log path
- exported note path
- exported_at
- file sizes
- cleanup status

### 2. Add cleanup command
Adicionar algo como:

```bash
python3 export_to_vault.py <transcript> --cleanup-policy default
python3 -m exporter.cleanup --dry-run
```

ou um comando dedicado:

```bash
python3 manage_recordings.py --dry-run
```

### 3. Archive lane
Se `archive_instead_of_delete=true`, mover artefatos antigos para:

```text
recordings/archive/YYYY-MM/
```

### 4. Human-readable summary
Imprimir no final do run algo como:
- bytes reclaimable
- files archived
- files deleted
- files skipped for safety

## Likely code touchpoints

- `export_to_vault.py`
- `src/exporter/vault_exporter.py`
- `src/exporter/config.py`
- novo módulo tipo `src/exporter/cleanup.py`

## Scope estimate

**Effort:** M

## Good first slice

1. gerar manifest por gravação exportada
2. criar `cleanup --dry-run`
3. suportar archive-only antes de delete
4. só depois adicionar deleção automática por idade

## Open questions

- manter `.txt` para reprocessamento futuro ou arquivar junto com o áudio?
- deletar `.ffmpeg.log` agressivamente depois do sucesso ou reter por alguns dias?
- a policy deve rodar no mesmo fluxo do export ou num cleanup job separado?
- vale compactar transcript/log antes de arquivar?

## Origin excerpt

> Meetcap já é bom em gerar artefato. Agora falta ele ser bom em encerrar artefato. Se a nota no vault já virou a versão durável, o projeto precisa parar de tratar WAV de 650MB como souvenir eterno.
