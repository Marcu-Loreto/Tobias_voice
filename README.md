# 🌙 Noturna — Assistente Pessoal com Voz, Texto e WhatsApp

Noturna é uma assistente pessoal multicanal que combina inteligência artificial (GPT-4o-mini), voz em tempo real (Vocal Bridge + LiveKit), e integração com serviços do Google Workspace e WhatsApp.

## O que faz

- **Chat por texto** — interface web responsiva (desktop e mobile)
- **Chat por voz** — STT/TTS via Vocal Bridge com microfone do browser
- **WhatsApp** — recebe e responde mensagens (texto e áudio) via Evolution API
- **Gmail** — lê, busca e envia emails
- **Google Calendar** — lista, cria, edita e deleta eventos
- **Previsão do tempo** — consulta OpenWeather API
- **Memória persistente** — conversas salvas em SQLite, sobrevivem a reinícios
- **Prompt externo** — personalidade e instruções em `prompts/prompt_noturna.md`

## Arquitetura

```
┌─────────────┐     ┌──────────────┐     ┌─────────────────┐
│  Browser     │────▶│  FastAPI      │────▶│  GPT-4o-mini    │
│  (voz/texto) │◀────│  (backend)    │◀────│  + Tool Calling │
└─────────────┘     └──────┬───────┘     └─────────────────┘
                           │
┌─────────────┐     ┌──────┴───────┐     ┌─────────────────┐
│  WhatsApp    │────▶│  Webhook     │     │  Google MCP     │
│  (Evolution) │◀────│  + Groq STT  │     │  (Gmail+Calendar)│
└─────────────┘     └──────────────┘     └─────────────────┘
                                          ┌─────────────────┐
                                          │  OpenWeather API │
                                          └─────────────────┘
```

## Estrutura do Projeto

```
├── noturna_client.py      # Servidor FastAPI + interface web (HTML/JS inline)
├── noturna_agent.py       # Agente local (OpenAI + tool calling + memória SQLite)
├── mcp_bridge.py          # Bridge para Google Workspace MCP (Gmail, Calendar)
├── whatsapp_bridge.py     # Bridge para Evolution API (WhatsApp) + Groq STT
├── prompts/
│   └── prompt_noturna.md  # System prompt da Noturna (editável)
├── setup_google_auth.py   # Script de autorização OAuth do Google (rodar 1x)
├── noturna.sh             # Script de início rápido
├── Dockerfile             # Container para deploy
├── docker-compose.yml     # Orquestração
├── pyproject.toml         # Dependências Python
└── .env                   # Variáveis de ambiente (não versionado)
```

## Dependências

### Python (gerenciadas via uv)

| Pacote            | Uso                                                  |
| ----------------- | ---------------------------------------------------- |
| fastapi + uvicorn | Servidor web e API                                   |
| openai            | Agente local (GPT-4o-mini)                           |
| requests          | Chamadas HTTP (Vocal Bridge, OpenWeather, Evolution) |
| python-dotenv     | Carregamento de variáveis de ambiente                |
| livekit           | SDK de áudio em tempo real                           |
| workspace-mcp     | Google Workspace MCP (Gmail, Calendar)               |

### Serviços Externos

| Serviço       | Função                        | Variável                                                       |
| ------------- | ----------------------------- | -------------------------------------------------------------- |
| OpenAI API    | LLM do agente local           | `OPENAI_API_KEY`                                               |
| Vocal Bridge  | Voz (STT/TTS via LiveKit)     | `VOCAL_BRIDGE_API_KEY`                                         |
| OpenWeather   | Previsão do tempo             | `OPENWEATHER_API_KEY`                                          |
| Google OAuth  | Gmail + Calendar              | `GOOGLE_OAUTH_CLIENT_ID`, `GOOGLE_OAUTH_CLIENT_SECRET`         |
| Evolution API | WhatsApp                      | `EVOLUTION_API_URL`, `EVOLUTION_API_KEY`, `EVOLUTION_INSTANCE` |
| Groq          | Transcrição de áudio WhatsApp | `GROQ_API_KEY`                                                 |

## Setup

### 1. Clone e instale

```bash
git clone https://github.com/Marcu-Loreto/Tobias_voice.git
cd Tobias_voice
cp .env_exemplo .env
# Edite o .env com suas credenciais
```

### 2. Instale dependências

```bash
# Requer Python 3.13+ e uv
uv sync
```

### 3. Autorize o Google (primeira vez)

```bash
uv run python setup_google_auth.py
# Abra a URL no browser e autorize
```

### 4. Rode

```bash
./noturna.sh
# Acesse https://localhost:8443
```

### Deploy com Docker

```bash
docker compose up -d --build
# Logs: docker compose logs -f noturna
```

## Variáveis de Ambiente

Veja `.env_exemplo` para a lista completa. As obrigatórias são:

```env
OPENAI_API_KEY=sk-...
VOCAL_BRIDGE_API_KEY=vb_...
OPENWEATHER_API_KEY=...
GOOGLE_OAUTH_CLIENT_ID=...
GOOGLE_OAUTH_CLIENT_SECRET=...
USER_GOOGLE_EMAIL=...
```

## Canais de Acesso

| Canal             | URL/Método                                        |
| ----------------- | ------------------------------------------------- |
| Web (texto + voz) | `https://localhost:8443`                          |
| WhatsApp          | Mensagem para o número conectado na Evolution API |
| API direta        | `POST /api/chat` com `{"message": "..."}`         |

## Licença

MIT
