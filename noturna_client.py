"""
Noturna Voice Agent Client — Web Interface
Serves a responsive web UI that connects to the Noturna assistant
hosted on Vocal Bridge AI. Works on desktop and mobile browsers.

Usage:
    uv run python noturna_client.py
    # Opens https://localhost:8443 (self-signed cert for mic access)
    # For mobile on same network: https://<your-ip>:8443
"""

import logging
import logging.handlers
import os
import ssl
import subprocess
import sys
from contextlib import asynccontextmanager
from datetime import datetime
from pathlib import Path

from dotenv import load_dotenv
from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, JSONResponse
import requests as http_requests
import uvicorn

from mcp_bridge import MCPBridge
from noturna_agent import NoturnaLocalAgent
from whatsapp_bridge import WhatsAppBridge

load_dotenv(override=True)

# ── Logging setup ──
LOG_DIR = Path(__file__).parent / "logs"
LOG_DIR.mkdir(exist_ok=True)
LOG_FILE = LOG_DIR / "noturna.log"

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
    handlers=[
        logging.StreamHandler(sys.stdout),
        logging.handlers.RotatingFileHandler(
            LOG_FILE, maxBytes=5 * 1024 * 1024, backupCount=3, encoding="utf-8"
        ),
    ],
)
logger = logging.getLogger("noturna")

VOCAL_BRIDGE_API_KEY = os.environ.get("VOCAL_BRIDGE_API_KEY", "")
VOCAL_BRIDGE_URL = "https://vocalbridgeai.com"
CERT_DIR = Path(__file__).parent / ".certs"
CERT_FILE = CERT_DIR / "cert.pem"
KEY_FILE = CERT_DIR / "key.pem"

mcp = MCPBridge()
whatsapp = WhatsAppBridge()
agent = NoturnaLocalAgent()


async def _weather_tool(city: str) -> dict:
    """Weather tool for the local agent."""
    try:
        resp = http_requests.get(
            "https://api.openweathermap.org/data/2.5/weather",
            params={"q": city, "appid": OPENWEATHER_API_KEY, "units": "metric", "lang": "pt_br"},
            timeout=10,
        )
        resp.raise_for_status()
        d = resp.json()
        return {
            "city": d.get("name", city),
            "temp": d["main"]["temp"],
            "feels_like": d["main"]["feels_like"],
            "humidity": d["main"]["humidity"],
            "description": d["weather"][0]["description"],
            "wind_speed": d["wind"]["speed"],
        }
    except Exception as e:
        return {"error": str(e)}


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Start MCP bridge and local agent on startup."""
    logger.info("Starting Noturna Voice Client...")
    await mcp.start()
    agent.mcp = mcp
    agent.weather_fn = _weather_tool
    agent.whatsapp = whatsapp
    logger.info("MCP Bridge started. Tools: %d", len(mcp.list_tools()))
    yield
    logger.info("Shutting down...")
    await mcp.stop()


app = FastAPI(title="Noturna Voice Client", lifespan=lifespan)


def ensure_ssl_certs() -> bool:
    """Generate self-signed SSL certs if they don't exist (needed for mic access over network)."""
    if CERT_FILE.exists() and KEY_FILE.exists():
        return True
    try:
        CERT_DIR.mkdir(exist_ok=True)
        subprocess.run(
            [
                "openssl", "req", "-x509", "-newkey", "rsa:2048",
                "-keyout", str(KEY_FILE), "-out", str(CERT_FILE),
                "-days", "365", "-nodes",
                "-subj", "/CN=noturna-local",
                "-addext", "subjectAltName=DNS:localhost,IP:0.0.0.0",
            ],
            check=True,
            capture_output=True,
        )
        print(f"[SSL] Certificados gerados em {CERT_DIR}")
        return True
    except (subprocess.CalledProcessError, FileNotFoundError):
        print("[SSL] openssl não encontrado — rodando sem HTTPS (mic pode não funcionar via rede)")
        return False


@app.post("/api/voice-token")
async def voice_token(request: Request):
    """Proxy token request to Vocal Bridge API."""
    body = await request.json()
    participant_name = body.get("participant_name", "Loreto")
    logger.info("Voice token requested for: %s", participant_name)

    try:
        resp = http_requests.post(
            f"{VOCAL_BRIDGE_URL}/api/v1/token",
            headers={
                "X-API-Key": VOCAL_BRIDGE_API_KEY,
                "Content-Type": "application/json",
            },
            json={"participant_name": participant_name},
            timeout=15,
        )
        resp.raise_for_status()
        data = resp.json()
        logger.info("Voice token OK. Room: %s", data.get("room_name", "?"))
        return JSONResponse(content=data)
    except Exception as e:
        logger.error("Voice token failed: %s", e)
        return JSONResponse(content={"error": str(e)}, status_code=500)


OPENWEATHER_API_KEY = os.environ.get("OPENWEATHER_API_KEY", "")


@app.post("/api/weather")
async def get_weather(request: Request):
    """Get weather forecast from OpenWeather API."""
    body = await request.json()
    city = body.get("city", "São Paulo")
    lang = body.get("lang", "pt_br")
    logger.info("Weather request for: %s", city)

    if not OPENWEATHER_API_KEY:
        logger.error("OPENWEATHER_API_KEY not configured")
        return JSONResponse(content={"error": "OPENWEATHER_API_KEY not configured"}, status_code=500)

    try:
        current = http_requests.get(
            "https://api.openweathermap.org/data/2.5/weather",
            params={"q": city, "appid": OPENWEATHER_API_KEY, "units": "metric", "lang": lang},
            timeout=10,
        )
        current.raise_for_status()
        current_data = current.json()

        forecast = http_requests.get(
            "https://api.openweathermap.org/data/2.5/forecast",
            params={"q": city, "appid": OPENWEATHER_API_KEY, "units": "metric", "lang": lang, "cnt": 8},
            timeout=10,
        )
        forecast.raise_for_status()
        forecast_data = forecast.json()

        result = {
            "city": current_data.get("name", city),
            "country": current_data.get("sys", {}).get("country", ""),
            "current": {
                "temp": current_data["main"]["temp"],
                "feels_like": current_data["main"]["feels_like"],
                "humidity": current_data["main"]["humidity"],
                "description": current_data["weather"][0]["description"],
                "wind_speed": current_data["wind"]["speed"],
            },
            "forecast": [
                {
                    "dt_txt": item["dt_txt"],
                    "temp": item["main"]["temp"],
                    "description": item["weather"][0]["description"],
                }
                for item in forecast_data.get("list", [])
            ],
        }
        logger.info("Weather OK: %s — %.1f°C %s", result["city"], result["current"]["temp"], result["current"]["description"])
        return JSONResponse(content=result)
    except Exception as e:
        logger.error("Weather failed for %s: %s", city, e)
        return JSONResponse(content={"error": str(e)}, status_code=500)


@app.get("/", response_class=HTMLResponse)
async def index():
    """Serve the main UI page."""
    return HTML_PAGE


@app.get("/api/mcp/tools")
async def list_mcp_tools():
    """List all available MCP tools."""
    tools = mcp.list_tools()
    logger.info("MCP tools listed: %d tools", len(tools))
    return JSONResponse(content={"tools": tools})


@app.post("/api/mcp/call")
async def call_mcp_tool(request: Request):
    """Call an MCP tool by name with arguments."""
    body = await request.json()
    tool_name = body.get("tool", "")
    arguments = body.get("arguments", {})
    logger.info("MCP call: %s args=%s", tool_name, arguments)
    result = await mcp.call_tool(tool_name, arguments)
    logger.info("MCP result: %s → %s", tool_name, str(result)[:200])
    return JSONResponse(content=result)


@app.post("/api/chat")
async def chat_text(request: Request):
    """Local text chat with Noturna — works without Vocal Bridge."""
    body = await request.json()
    message = body.get("message", "")
    session_id = body.get("session_id", "default")

    if not message:
        return JSONResponse(content={"error": "message is required"}, status_code=400)

    logger.info("Text chat: %s", message[:100])
    reply = await agent.chat(message, session_id)
    logger.info("Text reply: %s", reply[:100])
    return JSONResponse(content={"reply": reply, "session_id": session_id})


@app.get("/api/chat/history")
async def chat_history(session_id: str = "default"):
    """Get conversation history for a session."""
    messages = agent.memory.load_messages(session_id, limit=100)
    # Filter to only user/assistant messages for display
    history = [m for m in messages if m.get("role") in ("user", "assistant")]
    return JSONResponse(content={"session_id": session_id, "messages": history})


@app.get("/api/chat/sessions")
async def chat_sessions():
    """List all chat sessions."""
    return JSONResponse(content={"sessions": agent.list_sessions()})


@app.delete("/api/chat/history")
async def clear_history(request: Request):
    """Clear conversation history for a session."""
    body = await request.json()
    session_id = body.get("session_id", "default")
    agent.clear_session(session_id)
    logger.info("Cleared session: %s", session_id)
    return JSONResponse(content={"ok": True})


@app.post("/api/chat/save")
async def save_voice_message(request: Request):
    """Save a voice transcript message to persistent memory."""
    body = await request.json()
    role = body.get("role", "user")
    content = body.get("content", "")
    session_id = body.get("session_id", "default")

    if content:
        agent.memory.save_message(session_id, {"role": role, "content": content})
        logger.info("Voice msg saved [%s]: %s — %s", session_id, role, content[:80])

    return JSONResponse(content={"ok": True})


HTML_PAGE = """<!DOCTYPE html>
<html lang="pt-BR">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0, user-scalable=no">
<title>Noturna — Assistente de Voz</title>
<style>
*{margin:0;padding:0;box-sizing:border-box}
:root{
  --bg:#0f0e17;--bg2:#1a1a2e;--bg3:#16213e;
  --accent:#e94560;--green:#2ecc71;--blue:#5dade2;
  --agent:#00d4aa;--text:#e0e0e0;--dim:#6b7280;
  --radius:12px;
}
body{font-family:'Segoe UI',system-ui,sans-serif;background:var(--bg);color:var(--text);
  height:100dvh;display:flex;flex-direction:column;overflow:hidden}
header{background:var(--bg2);padding:14px 20px;display:flex;align-items:center;
  justify-content:space-between;border-bottom:1px solid #ffffff10}
header h1{font-size:1.3rem;display:flex;align-items:center;gap:8px}
header h1 span{font-size:1.5rem}
#status-bar{display:flex;align-items:center;gap:8px;font-size:.85rem;color:var(--dim)}
#status-dot{width:10px;height:10px;border-radius:50%;background:#ef4444;transition:background .3s}
#status-dot.connected{background:var(--green)}
#status-dot.connecting{background:#f59e0b;animation:pulse 1s infinite}
@keyframes pulse{0%,100%{opacity:1}50%{opacity:.4}}

#transcript{flex:1;overflow-y:auto;padding:16px 20px;display:flex;flex-direction:column;gap:8px;
  scroll-behavior:smooth}
#transcript::-webkit-scrollbar{width:6px}
#transcript::-webkit-scrollbar-thumb{background:#ffffff20;border-radius:3px}
.msg{max-width:85%;padding:10px 14px;border-radius:var(--radius);font-size:.95rem;line-height:1.5;
  word-wrap:break-word;animation:fadeIn .3s ease}
@keyframes fadeIn{from{opacity:0;transform:translateY(8px)}to{opacity:1;transform:none}}
.msg.agent{background:var(--bg3);align-self:flex-start;border-bottom-left-radius:4px}
.msg.agent .role{color:var(--agent);font-weight:600;font-size:.8rem;margin-bottom:2px}
.msg.user{background:#1e3a5f;align-self:flex-end;border-bottom-right-radius:4px}
.msg.user .role{color:var(--blue);font-weight:600;font-size:.8rem;margin-bottom:2px}
.msg.system{background:transparent;align-self:center;color:var(--dim);font-size:.8rem;
  font-style:italic;padding:4px 0}
.msg .time{color:var(--dim);font-size:.7rem;margin-top:4px}

#controls{background:var(--bg2);padding:16px 20px;display:flex;gap:12px;align-items:center;
  border-top:1px solid #ffffff10;flex-shrink:0}
button{border:none;cursor:pointer;font-family:inherit;font-size:.95rem;border-radius:var(--radius);
  padding:12px 24px;font-weight:600;transition:all .2s;touch-action:manipulation}
#btn-connect{background:var(--accent);color:#fff;flex:1;max-width:200px}
#btn-connect:hover{filter:brightness(1.15)}
#btn-connect:active{transform:scale(.97)}
#btn-connect.connected{background:var(--green)}
#btn-mic{background:var(--bg3);color:var(--text);width:56px;height:56px;border-radius:50%;
  display:flex;align-items:center;justify-content:center;font-size:1.5rem;padding:0;
  border:2px solid transparent;position:relative}
#btn-mic.active{border-color:var(--green);box-shadow:0 0 20px #2ecc7140}
#btn-mic.muted{border-color:#ef4444;opacity:.7}
#btn-mic:disabled{opacity:.3;cursor:not-allowed}
#btn-mic .ripple{position:absolute;inset:-4px;border-radius:50%;border:2px solid var(--green);
  animation:ripple 1.5s infinite;pointer-events:none;display:none}
#btn-mic.active .ripple{display:block}
@keyframes ripple{0%{transform:scale(1);opacity:.6}100%{transform:scale(1.4);opacity:0}}
#btn-clear{background:transparent;color:var(--dim);padding:12px;font-size:.85rem}
#btn-clear:hover{color:var(--text)}

#text-input-bar{background:var(--bg2);padding:10px 20px;display:flex;gap:10px;align-items:center;
  border-top:1px solid #ffffff10;flex-shrink:0}
#text-input{flex:1;background:var(--bg3);color:var(--text);border:1px solid #ffffff15;
  border-radius:var(--radius);padding:12px 16px;font-family:inherit;font-size:.95rem;outline:none}
#text-input:focus{border-color:var(--accent)}
#text-input:disabled{opacity:.4}
#btn-send{background:var(--accent);color:#fff;border:none;border-radius:var(--radius);
  padding:12px 20px;font-weight:600;font-family:inherit;font-size:.95rem;cursor:pointer;
  transition:all .2s}
#btn-send:hover{filter:brightness(1.15)}
#btn-send:disabled{opacity:.3;cursor:not-allowed}

#device-select{background:var(--bg3);color:var(--text);border:1px solid #ffffff15;
  border-radius:8px;padding:8px 12px;font-size:.85rem;max-width:200px;
  font-family:inherit;outline:none}
#device-select:focus{border-color:var(--accent)}

.device-bar{background:var(--bg2);padding:8px 20px;display:flex;align-items:center;gap:10px;
  border-bottom:1px solid #ffffff10;font-size:.85rem;color:var(--dim);flex-shrink:0}
.device-bar label{white-space:nowrap}

@media(max-width:600px){
  header{padding:10px 14px}
  header h1{font-size:1.1rem}
  #transcript{padding:12px 14px}
  #controls{padding:12px 14px}
  #btn-connect{max-width:none;flex:1}
  .device-bar{padding:8px 14px;flex-wrap:wrap}
  #device-select{max-width:none;flex:1}
}
</style>
</head>
<body>

<header>
  <h1><span>🌙</span> Noturna</h1>
  <div id="status-bar">
    <div id="status-dot"></div>
    <span id="status-text">Desconectado</span>
  </div>
</header>

<div class="device-bar">
  <label>🎤 Microfone:</label>
  <select id="device-select"><option value="">Carregando...</option></select>
</div>

<div id="transcript">
  <div class="msg system">Digite uma mensagem ou conecte para usar voz</div>
</div>

<div id="text-input-bar">
  <input type="text" id="text-input" placeholder="Digite uma mensagem..." />
  <button id="btn-send">Enviar</button>
</div>

<div id="controls">
  <button id="btn-connect">Conectar</button>
  <button id="btn-mic" disabled title="Microfone">
    🎤
    <div class="ripple"></div>
  </button>
  <button id="btn-clear">Limpar</button>
</div>

<script type="module">
import { VocalBridge } from 'https://esm.sh/@vocalbridgeai/sdk@0.1.1';

const $ = s => document.querySelector(s);
const transcript = $('#transcript');
const btnConnect = $('#btn-connect');
const btnMic = $('#btn-mic');
const btnClear = $('#btn-clear');
const statusDot = $('#status-dot');
const statusText = $('#status-text');
const deviceSelect = $('#device-select');
const textInput = $('#text-input');
const btnSend = $('#btn-send');

let vb = null;
let selectedDeviceId = '';

// ── Device enumeration ──
const hasMediaDevices = !!(navigator.mediaDevices && navigator.mediaDevices.getUserMedia);

async function loadDevices() {
  if (!hasMediaDevices) {
    deviceSelect.innerHTML = '<option value="">⚠ Requer HTTPS para microfone</option>';
    addMsg('system', 'Microfone indisponível: acesse via HTTPS ou localhost.');
    addMsg('system', 'A Noturna ainda pode falar com você (somente escuta).');
    return;
  }

  try {
    // Request permission first so labels are visible
    const tempStream = await navigator.mediaDevices.getUserMedia({ audio: true });
    tempStream.getTracks().forEach(t => t.stop());

    const devices = await navigator.mediaDevices.enumerateDevices();
    const audioInputs = devices.filter(d => d.kind === 'audioinput');

    deviceSelect.innerHTML = '';
    if (audioInputs.length === 0) {
      deviceSelect.innerHTML = '<option value="">Nenhum microfone encontrado</option>';
      return;
    }

    audioInputs.forEach((d, i) => {
      const opt = document.createElement('option');
      opt.value = d.deviceId;
      opt.textContent = d.label || `Microfone ${i + 1}`;
      deviceSelect.appendChild(opt);
    });

    selectedDeviceId = audioInputs[0].deviceId;
    addMsg('system', `Microfone detectado: ${audioInputs[0].label || 'Microfone 1'}`);
  } catch (err) {
    const msg = err.name === 'NotAllowedError'
      ? 'Permissão de microfone negada. Clique no ícone de cadeado do browser para permitir.'
      : err.name === 'NotFoundError'
        ? 'Nenhum microfone encontrado no dispositivo.'
        : `Erro ao acessar microfone: ${err.message}`;
    deviceSelect.innerHTML = '<option value="">Permissão negada</option>';
    addMsg('system', msg);
  }
}

deviceSelect.addEventListener('change', (e) => {
  selectedDeviceId = e.target.value;
  addMsg('system', `Microfone alterado: ${deviceSelect.options[deviceSelect.selectedIndex].text}`);
});

// Listen for device changes (plug/unplug)
if (hasMediaDevices) {
  navigator.mediaDevices.addEventListener('devicechange', loadDevices);
}

// ── UI helpers ──
function addMsg(type, text) {
  const now = new Date().toLocaleTimeString('pt-BR', { hour: '2-digit', minute: '2-digit', second: '2-digit' });
  const div = document.createElement('div');
  div.className = `msg ${type}`;

  if (type === 'system') {
    div.textContent = text;
  } else {
    const role = type === 'agent' ? 'Noturna' : 'Você';
    div.innerHTML = `<div class="role">${role}</div>${text}<div class="time">${now}</div>`;
  }

  transcript.appendChild(div);
  transcript.scrollTop = transcript.scrollHeight;
}

function setStatus(state, text) {
  statusDot.className = state;
  statusText.textContent = text;
}

// ── Connection ──
async function connect() {
  if (vb) { await disconnect(); return; }

  setStatus('connecting', 'Conectando...');
  btnConnect.disabled = true;

  try {
    vb = new VocalBridge({
      auth: { tokenUrl: '/api/voice-token', body: { participant_name: 'Loreto' } },
      participantName: 'Loreto',
      autoPlayAudio: true,
      debug: false,
    });

    vb.on('connectionStateChanged', (state) => {
      if (state === 'connected') {
        setStatus('connected', 'Conectado');
        btnConnect.textContent = 'Desconectar';
        btnConnect.classList.add('connected');
        btnMic.disabled = false;
        btnMic.classList.add('active');
        addMsg('system', 'Conectado à Noturna. Fale ou digite!');
      } else if (state === 'connecting' || state === 'waiting_for_agent') {
        setStatus('connecting', state === 'waiting_for_agent' ? 'Aguardando agente...' : 'Conectando...');
      } else if (state === 'reconnecting') {
        setStatus('connecting', 'Reconectando...');
      } else if (state === 'disconnected') {
        handleDisconnect();
      }
    });

    vb.on('transcript', ({ role, text }) => {
      // Only show user transcripts — agent responses come from our backend via aiAgentQuery
      if (role === 'user') {
        addMsg('user', text);
      }
    });

    // Handle agent actions — including MCP tool calls
    vb.on('agentAction', async ({ action, payload }) => {
      if (action === 'heartbeat' || action === 'send_transcript') return;
      addMsg('system', `Ação VB: ${action}`);
    });

    // AI Agent mode: VB does STT → sends here → we process → VB does TTS
    vb.on('aiAgentQuery', async ({ query, turnId }) => {
      try {
        const res = await fetch('/api/chat', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ message: query }),
        });
        const data = await res.json();
        const reply = data.reply || 'Desculpe, não consegui processar.';
        addMsg('agent', reply);
        if (vb.sendAIAgentResponse) vb.sendAIAgentResponse(turnId, reply);
      } catch (err) {
        addMsg('system', `Erro: ${err.message}`);
        if (vb.sendAIAgentResponse) vb.sendAIAgentResponse(turnId, 'Desculpe, houve um erro.');
      }
    });

    vb.on('error', (err) => {
      addMsg('system', `Erro: ${err.message || err}`);
    });

    // Apply selected device constraint before connecting
    if (hasMediaDevices && selectedDeviceId) {
      const origGetUserMedia = navigator.mediaDevices.getUserMedia.bind(navigator.mediaDevices);
      navigator.mediaDevices.getUserMedia = async (constraints) => {
        if (constraints?.audio) {
          constraints.audio = typeof constraints.audio === 'object' ? constraints.audio : {};
          constraints.audio.deviceId = { exact: selectedDeviceId };
        }
        const stream = await origGetUserMedia(constraints);
        // Restore original after first call
        navigator.mediaDevices.getUserMedia = origGetUserMedia;
        return stream;
      };
    }

    await vb.connect();
  } catch (err) {
    addMsg('system', `Falha ao conectar: ${err.message || err}`);
    setStatus('', 'Erro');
    vb = null;
  }
  btnConnect.disabled = false;
}

async function disconnect() {
  if (vb) {
    try { await vb.disconnect(); } catch {}
    vb = null;
  }
  handleDisconnect();
}

function handleDisconnect() {
  setStatus('', 'Desconectado');
  btnConnect.textContent = 'Conectar';
  btnConnect.classList.remove('connected');
  btnMic.disabled = true;
  btnMic.classList.remove('active');
  btnMic.classList.remove('muted');
}

// ── Mic toggle ──
async function toggleMic() {
  if (!vb) return;
  try {
    await vb.toggleMicrophone();
    const enabled = vb.isMicrophoneEnabled;
    btnMic.classList.toggle('active', enabled);
    btnMic.classList.toggle('muted', !enabled);
    addMsg('system', enabled ? 'Microfone ativado' : 'Microfone mutado');
  } catch (err) {
    addMsg('system', `Erro no microfone: ${err.message}`);
  }
}

// ── Send text message ──
async function sendText() {
  const text = textInput.value.trim();
  if (!text) return;

  addMsg('user', text);
  textInput.value = '';
  textInput.disabled = true;
  btnSend.disabled = true;

  if (vb && vb.state === 'connected') {
    // Voice mode: send via LiveKit data channel
    try {
      const msg = JSON.stringify({
        type: 'client_action',
        action: 'user_text_message',
        payload: { text }
      });
      await vb.room?.localParticipant?.publishData(
        new TextEncoder().encode(msg),
        { reliable: true, topic: 'client_actions' }
      );
    } catch (err) {
      addMsg('system', `Erro via voz, tentando local...`);
      await sendToLocalAgent(text);
    }
  } else {
    // Text mode: use local backend agent
    await sendToLocalAgent(text);
  }

  textInput.disabled = false;
  btnSend.disabled = false;
  textInput.focus();
}

async function sendToLocalAgent(text) {
  try {
    const res = await fetch('/api/chat', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ message: text }),
    });
    const data = await res.json();
    if (data.reply) {
      addMsg('agent', data.reply);
    } else if (data.error) {
      addMsg('system', `Erro: ${data.error}`);
    }
  } catch (err) {
    addMsg('system', `Erro no chat local: ${err.message}`);
  }
}

// ── Events ──
btnConnect.addEventListener('click', connect);
btnMic.addEventListener('click', toggleMic);
btnSend.addEventListener('click', sendText);
textInput.addEventListener('keydown', (e) => {
  if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); sendText(); }
});
btnClear.addEventListener('click', () => {
  transcript.innerHTML = '<div class="msg system">Histórico limpo</div>';
});

// ── Load history on startup ──
async function loadHistory() {
  try {
    const res = await fetch('/api/chat/history?session_id=default');
    const data = await res.json();
    if (data.messages && data.messages.length > 0) {
      data.messages.forEach(m => {
        addMsg(m.role === 'assistant' ? 'agent' : 'user', m.content);
      });
      addMsg('system', `${data.messages.length} mensagens restauradas`);
    }
  } catch {}
}

// ── Init ──
loadDevices();
loadHistory();
</script>
</body>
</html>"""


def get_local_ip() -> str:
    """Get the local network IP for mobile access."""
    import socket

    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        ip = s.getsockname()[0]
        s.close()
        return ip
    except Exception:
        return "127.0.0.1"


if __name__ == "__main__":
    if not VOCAL_BRIDGE_API_KEY:
        print("Erro: VOCAL_BRIDGE_API_KEY não encontrada no .env")
        sys.exit(1)

    has_ssl = ensure_ssl_certs()
    local_ip = get_local_ip()

    if has_ssl:
        port = 8443
        print(f"\n🌙 Noturna Voice Client (HTTPS)")
        print(f"   Local:   https://localhost:{port}")
        print(f"   Rede:    https://{local_ip}:{port}")
        print(f"   (Aceite o certificado self-signed no browser)\n")
        uvicorn.run(
            app,
            host="0.0.0.0",
            port=port,
            ssl_certfile=str(CERT_FILE),
            ssl_keyfile=str(KEY_FILE),
        )
    else:
        port = 8000
        print(f"\n🌙 Noturna Voice Client (HTTP)")
        print(f"   Local:   http://localhost:{port}")
        print(f"   ⚠ Microfone só funciona em localhost (sem HTTPS)\n")
        uvicorn.run(app, host="0.0.0.0", port=port)
