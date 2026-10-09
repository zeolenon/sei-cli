# SEI CLI Constitution

## Core Principles

### I. Core-First, Org-Agnostic
The CLI core (`sei_cli/`) is generic SEI automation. It works for **any** organization that uses SEI (CBMRN, IBAMA, TRF, universidades). No org-specific logic, credentials, or unit IDs in the core. Organization-specific workflows live in separate, loadable modules.

### II. Three-Layer Architecture
1. **Core** (commitado, genérico) — HTTP client, HTML parsing, CLI commands. Works out of the box for any SEI instance.
2. **User Profile** (local, gitignored) — `~/.config/sei/profile.yaml`. User's units, preferred marcadores, monitored processes, personal preferences.
3. **Org Workflows** (módulos por órgão) — `workflows/<org>/`. Process flows mapped as declarative steps (solicitante→cmt→secretaria→BG). Loadable by org key.

### III. HTTP Session Integrity (NON-NEGOTIABLE)
- `_ensure_session()` validates before every operation
- `_harvest_hashes()` extracts `infra_hash` from every HTML response — SEI uses hashes in ALL URLs
- Never cache hashes across sessions
- Reuse the existing session; stop on session or transport errors without automatic login. Authentication is an explicit operation.
- Rate limit: max ~5 req/s or session drops
- `batch_mode()` context manager for bulk operations

### IV. Credentials Security (NON-NEGOTIABLE)
- Credentials come from environment variables (`SEI_*`) or encrypted config (`~/.config/sei/credentials.json`)
- NEVER hardcode credentials in source
- Session files are chmod 600
- No credentials in logs, error messages, or issue reports

### V. Parse Defensively
SEI HTML changes between versions and sometimes between requests. Every parser must:
- Handle missing elements gracefully (return None, not crash)
- Log warnings with `rich.console` on unexpected HTML
- Use `_navigate_with_retry()` for transient failures
- Test against mockado HTML fixtures

### VI. CLI-First Interface
- Every feature exposed as a `click` command with `--json` output
- Human-readable (rich tables) by default, JSON for automation
- `sei goto <numero>` is the universal navigator — never traverse lists manually
- Commands are verbs: `sei goto`, `sei listar`, `sei assinar`, `sei concluir`

### VII. Contributor Discipline
- **Collaborators do NOT commit fixes directly to main**
- Bugs found → GitHub Issue with: reproduction steps, traceback, HTML context, expected behavior
- Fixes go through branches → PR → review → merge
- AI agents assisting collaborators should open Issues, not push code
- Issue templates enforce structured reporting

## Architecture Layers Detail

### Layer 1: Core (`sei_cli/`)
- `client.py` — All HTTP logic centralized. Single `SEIClient` class.
- `cli.py` — Click commands, output formatting
- `parsers.py` — HTML parsing helpers
- `models.py` — Dataclasses for all domain objects
- `config.py` — Credential loading, session persistence
- `auth.py` — Login flow

### Layer 2: User Profile (`~/.config/sei/profile.yaml`, gitignored)
```yaml
# Example profile
orgao: CBM
login_url: https://sei.example.test/sip/login.php
unidades:
  - id: "910000015"
    sigla: UNIDADE TESTE A
    default: true
  - id: "910000014"
    sigla: BETA DESTINO
marcadores_preferidos:
  - nome: Pessoal
    cor: verde
  - nome: Operacional
    cor: azul
processos_monitorados:
  - numero: "81000108"
    descricao: "JPMS ativo"
    alerta: true
```

### Layer 3: Org Workflows (`workflows/<org>/`)
```yaml
# workflows/cbmrn/reaprazamento.yaml
nome: Reaprazamento de Férias
orgao: cbmrn
etapas:
  - ator: solicitante
    acao: criar_processo
    tipo: "Requerimento"
    destino: comandante
  - ator: comandante
    acao: despachar
    decisao: [aprovar, indeferir]
    destino_aprovado: secretaria
  - ator: secretaria
    acao: acostar_autos
    destino: bg
  - ator: bg
    acao: publicar
    notificar: [comandante, solicitante]
```

## Development Workflow

### Branching
- `main` — stable, tested
- `feature/*` — new features (via spec-kit)
- `fix/*` — bug fixes
- All merges via PR with at least 1 review

### Testing
- `pytest` with mocked HTML fixtures
- Every new parser method needs a fixture test
- Integration tests against real SEI are manual and documented

### Code Standards
- Type annotations on all public functions
- Docstrings: português for CLI commands, English for internals
- Errors: log with `rich.console`, return gracefully (never crash)
- New commands: add as click groups

## Governance

This constitution supersedes ad-hoc practices. Amendments require:
1. Issue or discussion documenting the change
2. PR updating this file
3. Approval from project owner (@zeolenon)

**Version**: 1.0.0 | **Ratified**: 2026-03-26 | **Last Amended**: 2026-03-26
