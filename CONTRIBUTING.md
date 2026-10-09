# Contribuindo com o SEI CLI

## 🎯 Como Contribuir

### Encontrou um Bug?

1. **Abra uma Issue** usando o template "Bug Report"
2. Inclua:
   - O que estava fazendo (comando, fluxo, operação)
   - O erro que apareceu (traceback completo)
   - Comportamento esperado vs. real
   - Se possível, o HTML relevante da página do SEI (sanitizado — sem dados pessoais)
3. **Não commite o fix diretamente** — abra a Issue primeiro

### Quer Sugerir uma Feature?

1. Abra uma Issue usando o template "Feature Request"
2. Descreva o caso de uso (o quê e por quê, não como)
3. Se for um workflow específico do seu órgão, mencione na Issue

### Usando com AI Agent?

Se seu agente de IA encontrou um bug enquanto usava o sei-cli:
1. O agente deve **abrir uma Issue** no GitHub (não commitar fix)
2. A Issue deve conter: contexto da operação, traceback, e HTML relevante
3. O maintainer revisa, valida, e implementa o fix

## 🏗️ Arquitetura

```
sei_cli/          ← Core genérico (funciona pra qualquer SEI)
  client.py       ← Toda lógica HTTP
  cli.py          ← Comandos Click
  parsers.py      ← Parsing de HTML
  models.py       ← Dataclasses
  config.py       ← Credenciais e sessão

workflows/        ← Fluxos específicos por órgão (futuro)
  cbmrn/          ← CBMRN
  ibama/          ← IBAMA
  ...

~/.config/sei/    ← Profile local do usuário (gitignored)
  credentials.json
  profile.yaml
  session.json
```

### Regra de Ouro

O core (`sei_cli/`) é **genérico**. Se algo é específico do seu órgão (IDs de unidades, fluxos de processo, marcadores padrão), vai no profile local ou no módulo de workflows.

## 🔒 Segurança

- **NUNCA** inclua credenciais em Issues, PRs, ou código
- **NUNCA** inclua dados pessoais de processos reais
- Sanitize HTML antes de colar em Issues (remova nomes, CPFs, números de processo reais)
- Session files são sempre chmod 600

## 🔧 Setup de Desenvolvimento

```bash
git clone git@github.com:zeolenon/sei-cli.git
cd sei-cli
python -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"
pytest
```

### Credenciais

Configure via variáveis de ambiente:
```bash
export SEI_USUARIO="seu_usuario"
export SEI_SENHA="sua_senha"
export SEI_ORGAO="CBM"  # ou o código do seu órgão
export SEI_LOGIN_URL="https://sei.example.test/sip/login.php"
```

Ou via arquivo `~/.config/sei/credentials.json`:
```json
{
  "usuario": "seu_usuario",
  "senha": "sua_senha",
  "orgao": "CBM",
  "login_url": "https://sei.example.test/sip/login.php"
}
```

## 📝 Padrões de Código

- Type annotations em todas as funções públicas
- Docstrings: português nos comandos CLI, inglês nos internals
- Erros: `rich.console` + return graceful (nunca crash)
- Testes: fixtures HTML mockado em `tests/`
- Branches: `feature/*` ou `fix/*`, merge via PR

## 🤝 Processo de Review

1. Fork ou branch a partir de `main`
2. Implemente com testes
3. Abra PR com descrição clara
4. Aguarde review do maintainer
5. Merge após aprovação
## 🤖 Agentes OpenClaw / ACP

O `sei-cli` mantém uma skill oficial no diretório `skills/openclaw/SKILL.md` atualizada junto com o código-fonte.

**Para instalar/atualizar na sua máquina local:**

```bash
# Link simbólico para manter a skill sempre sincronizada com o repositório
mkdir -p ~/.openclaw/workspace/skills/sei
ln -sf ~/Projects/sei-cli/skills/openclaw/SKILL.md ~/.openclaw/workspace/skills/sei/SKILL.md
```

Ao fazer modificações em métodos públicos da API ou adicionar funcionalidades, **sempre atualize o arquivo `skills/openclaw/SKILL.md`** correspondente.

