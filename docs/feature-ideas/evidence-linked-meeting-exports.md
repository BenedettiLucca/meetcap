# Evidence-Linked Meeting Exports

- **Date captured:** 2026-06-05
- **Source:** Synapse Diff cron
- **Status:** idea
- **Repo remote:** none (local-only fallback)
- **Project:** `/home/lucca/Projects/meetcap`

## Summary

Meetcap já resolve bem a parte de **gravar → transcrever → resumir → sugerir tasks → exportar pro vault**.

O gargalo agora é outro: os artefatos saem úteis para leitura humana, mas ainda saem fracos para **reuso editorial**. Quando uma reunião vira base para workshop, nota de cliente, página de wiki ou post, falta um bloco explícito de **claims com evidência e timestamp**.

Hoje o resumo fica bom, mas ainda exige reler a transcrição inteira para provar de onde saiu cada insight.

## Why now

Nas últimas 48h o padrão apareceu de novo em três frentes:
- workshop de IA virando proposta executiva
- Synapse / Content Coach puxando ângulos de artefatos internos
- wiki ganhando páginas-hub que precisam de tese com lastro

Isso mostra que o próximo salto do Meetcap não é “resumir melhor”. É **exportar melhor para downstream use**.

Se a nota exportada já sair com evidência navegável, ela deixa de ser só memória de reunião e vira matéria-prima confiável para conteúdo, strategy docs e wiki synthesis.

## Proposed feature

**Name:** Evidence-Linked Export Blocks

### Core behavior

Além do resumo e das sugestões de tasks, o export deve gerar um bloco tipo:

```markdown
## 🔎 Claims & Evidence
- Claim: <insight curto>
  - Why it matters: <1 linha>
  - Evidence: 00:12:41, 00:18:09
  - Speakers: <nomes se disponíveis>
- Claim: <insight curto>
  - Evidence: 00:34:22
```

### Optional artifact outputs

Gerar também artefatos auxiliares no mesmo export:
- `evidence.json` — machine-readable claims, timestamps, speakers, confidence
- `evidence.md` — versão humana para colar em docs/wiki
- `quotes.md` — top quotes com timestamp

## Suggested implementation

### 1. Reuse parsed transcript segments
Usar a estrutura já extraída por `parse_meetcap_transcript()` para manter timestamps e blocos de fala acessíveis ao pipeline, em vez de colapsar tudo cedo demais em `transcript_text`.

### 2. Add a claim extractor pass
Criar um módulo novo, algo como:
- `src/exporter/claim_extractor.py`

Ele receberia:
- transcript segmentado
- summary atual
- talvez pending tasks do dia

E devolveria uma estrutura JSON com:
- `claim`
- `why_it_matters`
- `timestamps`
- `speakers`
- `confidence`
- `quote_excerpt`

### 3. Extend the note renderer
Em `src/exporter/vault_exporter.py`, inserir a nova seção `## 🔎 Claims & Evidence` antes da transcrição completa.

### 4. Keep it advisory-first
Primeira versão não precisa tentar “provar tudo”. Melhor 5 claims boas com timestamps reais do que 20 bullets fofas sem utilidade.

## Likely code touchpoints

- `src/exporter/vault_exporter.py`
- `src/exporter/transcript_parser.py`
- `src/exporter/summarizer.py`
- `src/exporter/prompts.py`
- `export_to_vault.py`
- novo módulo: `src/exporter/claim_extractor.py`

## Scope estimate

**Effort:** M

## Good first slice

1. preservar segmentos com timestamp no pipeline inteiro
2. extrair 3-5 claims com timestamps
3. renderizar `## 🔎 Claims & Evidence` no note final
4. exportar `evidence.json`
5. deixar quotes/speakers enrichment para a segunda passada

## Open questions / risks

- o transcript parser já preserva speaker attribution de forma confiável ou isso precisa melhorar primeiro?
- vale suportar deep-link futuro para áudio original ou timestamp textual já resolve 80% do problema?
- como evitar claim inflation / hallucination na camada de extração?
- a seção deve priorizar decisões, insights de conteúdo, ou qualquer afirmação material?

## 2026-06-08 addendum — Evidence block transfer

O update mais útil aqui é parar de pensar isso só como feature de transcript e começar a tratar como **port do padrão de evidence blocks** que já apareceu em outro contexto do stack.

O `MOIC-MCP` acabou de shippar um bloco enxuto mas muito útil para busca semântica operacional:
- `authority` por fonte
- `freshness` por janela de tempo
- `evidenceMix` no topo do resultado

Esse trio é praticamente o missing layer do Meetcap export. A reunião já sai com resumo e task suggestion; o que falta é o agente downstream saber:
- o que é **decisão canônica** da sala vs interpretação do resumo
- o que está **fresh** vs já envelheceu
- se o export está apoiado em 1-2 trechos fortes ou em um monte de contexto fraco

### Upgrade proposto na feature

Adicionar ao `## 🔎 Claims & Evidence` um mini bloco agregador no topo, algo como:

```markdown
## Evidence Summary
- Authority mix: decision-heavy / discussion-heavy / mixed
- Freshness: same-day / aging / stale follow-up
- Conflict flag: yes/no
```

Isso deixa o export mais útil não só para leitura humana, mas para:
- [[Hermes]]/wiki promotion futura
- reaproveitamento em proposta/workshop
- recall de projeto sem reler a transcrição inteira

Em resumo: o ganho não é “resumo mais bonito”. É **hierarquia de evidência portátil**.

## Why this is better than “just improve the summary”

Porque summary bom ainda é opinativo. Evidence-linked export cria um artefato que outros agentes e o próprio Lucca conseguem reutilizar sem voltar ao raw toda hora.

É a diferença entre:
- “essa reunião foi resumida”
- “essa reunião virou fonte confiável para estratégia”

## Origin excerpt

> O próximo passo do Meetcap não é escrever um resumo mais bonito. É sair da reunião já com os claims importantes amarrados em timestamp, pra virar workshop, post, nota de cliente ou página de wiki sem ter que cavar a transcrição inteira de novo.
