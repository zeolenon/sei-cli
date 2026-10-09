# sei-cli — Plano de Desenvolvimento

## Visão
CLI Python para operações de leitura no SEI (sei.rn.gov.br), sem depender de browser.
Assinatura continua via browser automation (JS obrigatório).

## Escopo v0.1 (read-only)

### Comandos
```bash
sei login                           # Login HTTP, salva sessão
sei status                          # Mostra unidade ativa, sessão válida
sei processes [--unit SIGLA]        # Lista processos recebidos + gerados
sei process <numero>                # Abre processo, lista documentos
sei doc <numero>                    # Lê conteúdo de documento
sei blocks                          # Lista blocos de assinatura
sei block <id>                      # Lista documentos de um bloco
sei search <query>                  # Pesquisa processos
sei units                           # Lista unidades disponíveis
sei switch <sigla>                  # Troca unidade ativa
```

### Arquitetura
```
sei-cli/
├── sei_cli/
│   ├── __init__.py
│   ├── cli.py              # Click CLI entrypoint
│   ├── client.py            # SEIClient — HTTP session manager
│   ├── auth.py              # Login flow (SIP → SEI)
│   ├── parsers.py           # HTML parsers (BeautifulSoup)
│   ├── models.py            # Dataclasses: Process, Document, Block
│   └── config.py            # Config/credentials management
├── tests/
│   ├── test_auth.py
│   ├── test_parsers.py
│   └── fixtures/            # HTML fixtures para testes offline
├── pyproject.toml
└── README.md
```

### Detalhes Técnicos

#### Login Flow (auth.py) — TESTADO E CONFIRMADO 2026-03-05
```python
# 1. GET https://sei.rn.gov.br/sei/controlador.php?acao=usuario_login
#    → follow redirects → lands on /sip/login.php?sigla_orgao_sistema=SEAD&sigla_sistema=SEI&infra_url=<b64>
#    → Captura PHPSESSID cookie automaticamente
#
# 2. Parse form to get the action URL (relative to current page)
#    form action = "login.php?sigla_orgao_sistema=SEAD&sigla_sistema=SEI&infra_url=..."
#    Resolve with urljoin(current_url, form_action)
#
# 3. POST to resolved form action URL
#    body: txtUsuario=<cpf>&pwdSenha=<senha>&selOrgao=28&hdnAcao=2
#    ⚠️ CRITICAL: hdnAcao MUST be "2" (not "1")! 
#    The JS function acaoLogin(2) sets this on form submit.
#    hdnAcao=1 silently fails (returns to login page without error).
#
# 4. Response: follow_redirects=True → lands on Controle de Processos page
#    Verify: "Controle de Processos" in response text
#
# 5. Save PHPSESSID cookie + infra_hash from page for subsequent requests
#    Session file: ~/.config/sei-cli/session.json
```

#### SSL Note
Nota histórica do plano v0.1: o cliente usava bypass de certificado.
A política atual em `auth.create_http_client()` exige validação completa via
`tls.create_verified_context()`: hostname, validade e caminho até raiz já
confiável. A cadeia pública complementar está em `sei_cli/certificates/`;
`PARTIAL_CHAIN` fica desativado. Não usar `verify=False` nem ignorar avisos TLS.
Esse ajuste de TLS é independente da política atual de sessão: consulta não faz login automático.

#### WAF Bypass
- COTIC-RN WAF bloqueia requests sem User-Agent de browser
- Sempre enviar UA real: `Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36`

#### Session Management (client.py)

O cliente carrega a sessão existente e valida o contexto por inicialização
canônica. `status()` e listagens reutilizam o controle validado; leituras seriais
não reinicializam a sessão a cada documento. Os redirects são seguidos
manualmente, preservando cookies e interrompendo antes da página de login.

Falha de sessão ou transporte interrompe o cliente sem apagar cookies nem
consultar credenciais. Login é uma operação explícita (`sei login`), sujeita à
autorização do usuário. Rotas de processo/documento devem vir dos links nativos
do controle, da pesquisa ou da árvore, sem copiar hashes de outra ação.

#### HTML Parsing (parsers.py)
```python
# Processos recebidos: parsear #tblProcessosRecebidos tbody tr
# Cada tr tem: checkbox, ícones, link do processo (número + tipo), anotações
# Processos gerados: #tblProcessosGerados tbody tr

# Blocos: parsear tabela de blocos (nº, estado, geradora, descrição)

# Documentos de um processo: parsear árvore (#ifrArvore content)
# Nota: árvore é carregada via iframe separado, precisa GET da URL do iframe
```

#### Desafio: infra_hash
- URLs internas do SEI incluem `infra_hash` que muda a cada sessão
- Solução: parsear links do HTML (que já contêm o hash correto)
- Pesquisa rápida via POST não precisa de hash

#### Credenciais (config.py)
- Ler de `~/.config/sei/credentials.json` (já existe, usado pela skill atual)
- Formato: `{"usuario": "seu_usuario", "senha": "...", "orgao": "CBM", "login_url": "..."}`
- Campo é `usuario` (não `cpf`)
- Orgao CBM = selOrgao value "28"
- Consultar credenciais somente pelo fluxo canônico de autenticação explícita; nunca registrar valores.

### Dependências
```toml
[project]
dependencies = [
    "httpx>=0.27",
    "beautifulsoup4>=4.12",
    "lxml>=5.0",
    "click>=8.1",
    "rich>=13.0",  # Output formatado
]
```

### Output Format
- Default: rich tables (humano)
- `--json` flag: JSON estruturado (para scripts/cron)

### Testes
- Fixtures HTML salvas em `tests/fixtures/` (capturadas do SEI real)
- Testes de parser offline (sem rede)
- Teste de integração: login real + listar processos (manual, com creds)

### Uso em Cron/Heartbeat
```python
# Exemplo: monitorar processos novos
result = subprocess.run(["sei", "processes", "--json"], capture_output=True)
data = json.loads(result.stdout)
novos = [p for p in data["recebidos"] if p["novo"]]
if novos:
    notify(f"SEI: {len(novos)} processo(s) novo(s)")
```

## Fora do Escopo v0.1
- Assinatura de documentos (requer JS/browser)
- Autenticação de PDFs (requer JS/browser)
- Criação de documentos
- Tramitação/encaminhamento
- Upload de arquivos

## Estimativa
- Desenvolvimento: 2-3 dias
- Testes e ajustes: 1 dia
- Integração com skill SEI: 0.5 dia
