# Canonical Entity Resolution for Transcripts

- **Date captured:** 2026-06-14
- **Source:** Synapse Diff cron
- **Status:** idea
- **Repo remote:** none (local-only fallback)
- **Project:** `/path/to/meetcap`

## Summary

Meetcap já resolveu o loop principal de **gravar → transcrever → resumir → sugerir tasks → exportar pro vault**.

O próximo gargalo não é só qualidade de resumo. É **qualidade de proper noun**.

Quando uma transcrição automática erra um nome importante, o erro pode subir de camada muito rápido: resumo, task suggestions, meeting notes, wiki update, conteúdo de cliente. O caso mais claro apareceu no alinhamento da INTMAX: nomes como **Zcash** e **Project Tachyon** precisaram ser corrigidos manualmente depois que o texto automático saiu manglado.

## Why now

Duas mudanças recentes aumentaram o custo desse erro:

- Meetcap agora exporta notas pro vault como artefato de trabalho, não só memória crua
- essas notas já estão virando insumo de wiki, sessões futuras e client work

Ou seja: transcript noise deixou de ser um detalhe feio no raw e virou **risco de propagação semântica**.

## Proposed feature

**Name:** Canonical Entity Resolver

### Core behavior

Antes de renderizar a nota final, o export roda uma passada leve de resolução de entidades para:

- detectar nomes próprios suspeitos no resumo e nas sugestões de tasks
- comparar contra um vocabulário canônico local
- sugerir correções quando houver match forte
- opcionalmente auto-aplicar apenas correções de alta confiança nas camadas derivadas

Importante: **não reescrever o raw transcript**. O raw continua bruto. A correção vale para summary/task/export surfaces, onde legibilidade e downstream reuse importam mais.

### Vocabulary sources

Primeira versão pode usar fontes locais e baratas:

- filenames/slugs de `vault/wiki/entities/` e `vault/wiki/concepts/`
- glossário opcional por projeto (`docs/glossary.txt` ou `.json`)
- nomes de participantes explícitos quando existirem no contexto de export

### Output behavior

O export poderia adicionar um bloco tipo:

```markdown
## Name Corrections
- `TechKeyon` → `Project Tachyon` (high confidence)
- `Jiracash` → `Zcash` (high confidence)
```

Ou, numa primeira fatia ainda mais segura:

- corrigir automaticamente no summary/task section
- registrar as correções em `corrections.json`
- manter o transcript bruto intacto

## Suggested implementation

### 1. Extract candidate entities

Adicionar um passo depois da transcrição / antes do render final para coletar:

- sequências capitalizadas
- tokens repetidos próximos de timestamps relevantes
- termos fora de dicionário com distância pequena de entidades conhecidas

### 2. Build local resolver

Criar um módulo como:

- `src/exporter/entity_resolver.py`

Responsabilidades:

- carregar vocabulário canônico local
- fazer fuzzy match / normalization
- devolver lista de `candidate -> canonical` com confidence score

### 3. Apply only on derived artifacts

Em `src/exporter/vault_exporter.py`:

- aplicar correções high-confidence em `summary` e `task_suggestions`
- opcionalmente renderizar `## Name Corrections`
- exportar `corrections.json` para auditoria

### 4. Keep uncertainty visible

Se confidence for média, não corrigir silenciosamente. Só flaggar.

Exemplo:

- `Rie` vs `Rhea`
- nomes de protocolos pouco conhecidos
- nomes japoneses / siglas curtas

## Likely code touchpoints

- `src/exporter/transcript_parser.py`
- `src/exporter/summarizer.py`
- `src/exporter/task_extractor.py`
- `src/exporter/vault_exporter.py`
- new module: `src/exporter/entity_resolver.py`

## Scope estimate

**Effort:** M

## Good first slice

1. carregar slugs da wiki como vocabulário canônico
2. extrair candidatos do summary e task suggestions
3. sugerir correções high-confidence
4. aplicar só nas camadas derivadas
5. salvar `corrections.json`

## Open questions / risks

- qual threshold evita falso positivo irritante?
- vale corrigir também participantes ou só protocolos/produtos?
- o vocabulário deve ser somente wiki-driven ou permitir glossário por projeto?
- como lidar com nomes realmente novos que ainda não existem na wiki?

## Why this beats “just improve the model”

Porque esse problema não é só geração. É **grounding local**.

Modelo melhor ainda pode errar proper noun raro. Resolver contra um vocabulário canônico do próprio stack cria uma camada mais confiável e barata do que ficar torcendo para o LLM acertar toda vez.

## Origin excerpt

> Agora que o Meetcap exporta pro vault e vira matéria-prima de wiki e client work, proper noun errado não é mais detalhe de transcript. É erro de infraestrutura semântica.
