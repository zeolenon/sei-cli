# sei-cli — CLI HTTP para SEI (Sistema Eletrônico de Informações)

## O que é
CLI Python para interagir com o SEI via HTTP puro, sem browser.
Reutiliza a sessão existente para consultas; autenticação e mutações são operações explícitas.

## Plano completo
Leia `docs/plan.md` para o plano detalhado com arquitetura, flows, e detalhes técnicos.

## Regras
- Python 3.12+
- Dependências: httpx, beautifulsoup4, lxml, click, rich
- Use `pyproject.toml` (não setup.py)
- Testes com pytest
- Type hints em todo lugar
- Output padrão: rich tables. Flag `--json` para JSON estruturado
- Credenciais: lidas de `~/.config/sei/credentials.json` ou env vars
- Sessão HTTP: salva cookies em `~/.config/sei-cli/session.json`
- User-Agent OBRIGATÓRIO em todo request (WAF bloqueia sem UA browser)
- Código limpo, sem over-engineering

## Estrutura
```
sei_cli/
├── __init__.py
├── cli.py          # Click CLI
├── client.py       # SEIClient (HTTP session)
├── auth.py         # Login flow
├── parsers.py      # HTML → dataclasses
├── models.py       # Dataclasses
└── config.py       # Config management
tests/
├── test_auth.py
├── test_parsers.py
└── fixtures/       # HTML sintético para testes offline
```

## Testes
- Fixtures HTML em `tests/fixtures/` para testar parsers offline
- Rodar: `pytest tests/ -v`
- NÃO fazer requests reais nos testes (usar fixtures)
