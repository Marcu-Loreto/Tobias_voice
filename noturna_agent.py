"""
Noturna Local Agent — Text-based assistant that works independently of Vocal Bridge.
Uses the same system prompt as the voice agent, with access to MCP tools and weather.
Conversation memory is persisted to SQLite.
"""

import json
import logging
import os
import sqlite3
from datetime import datetime
from pathlib import Path

from dotenv import load_dotenv
from openai import AsyncOpenAI

load_dotenv(override=True)

logger = logging.getLogger("noturna.agent")

PROMPT_FILE = Path(__file__).parent / "prompts" / "prompt_noturna.md"
DB_PATH = Path(__file__).parent / "data" / "noturna_memory.db"


def _load_prompt() -> str:
    """Load system prompt from external file."""
    try:
        return PROMPT_FILE.read_text(encoding="utf-8")
    except FileNotFoundError:
        logger.warning("Prompt file not found: %s", PROMPT_FILE)
        return "Você é a Noturna, assistente pessoal. Responda em Português do Brasil."


class MemoryStore:
    """SQLite-backed conversation memory."""

    def __init__(self, db_path: Path = DB_PATH):
        db_path.parent.mkdir(parents=True, exist_ok=True)
        self.db_path = db_path
        self._init_db()

    def _init_db(self):
        with sqlite3.connect(self.db_path) as conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS messages (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    session_id TEXT NOT NULL,
                    role TEXT NOT NULL,
                    content TEXT,
                    tool_call_id TEXT,
                    tool_calls TEXT,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            """)
            conn.execute("""
                CREATE INDEX IF NOT EXISTS idx_session
                ON messages(session_id, created_at)
            """)
        logger.info("Memory DB ready: %s", self.db_path)

    def save_message(self, session_id: str, msg):
        """Save a single message to the database. Accepts dict or OpenAI message object."""
        tool_calls = None

        if hasattr(msg, "role"):
            # OpenAI ChatCompletionMessage object
            role = msg.role
            content = msg.content
            tool_call_id = getattr(msg, "tool_call_id", None)
            if hasattr(msg, "tool_calls") and msg.tool_calls:
                tool_calls = json.dumps([
                    {"id": tc.id, "type": "function", "function": {"name": tc.function.name, "arguments": tc.function.arguments}}
                    for tc in msg.tool_calls
                ], ensure_ascii=False)
        else:
            # Plain dict
            role = msg.get("role", "user")
            content = msg.get("content")
            tool_call_id = msg.get("tool_call_id")

        with sqlite3.connect(self.db_path) as conn:
            conn.execute(
                "INSERT INTO messages (session_id, role, content, tool_call_id, tool_calls) VALUES (?, ?, ?, ?, ?)",
                (session_id, role, content, tool_call_id, tool_calls),
            )

    def load_messages(self, session_id: str, limit: int = 50) -> list[dict]:
        """Load recent messages for a session."""
        with sqlite3.connect(self.db_path) as conn:
            rows = conn.execute(
                "SELECT role, content, tool_call_id, tool_calls FROM messages WHERE session_id = ? ORDER BY created_at DESC LIMIT ?",
                (session_id, limit),
            ).fetchall()

        messages = []
        for role, content, tool_call_id, tool_calls_json in reversed(rows):
            if tool_calls_json:
                # Reconstruct assistant message with tool_calls (ensure 'type' field)
                tool_calls = json.loads(tool_calls_json)
                for tc in tool_calls:
                    tc.setdefault("type", "function")
                msg = {"role": role, "content": content, "tool_calls": tool_calls}
                messages.append(msg)
            elif tool_call_id:
                messages.append({"role": role, "tool_call_id": tool_call_id, "content": content})
            else:
                messages.append({"role": role, "content": content})
        return messages

    def clear_session(self, session_id: str):
        with sqlite3.connect(self.db_path) as conn:
            conn.execute("DELETE FROM messages WHERE session_id = ?", (session_id,))

    def list_sessions(self) -> list[dict]:
        with sqlite3.connect(self.db_path) as conn:
            rows = conn.execute(
                "SELECT session_id, COUNT(*) as msg_count, MAX(created_at) as last_at FROM messages GROUP BY session_id ORDER BY last_at DESC"
            ).fetchall()
        return [{"session_id": r[0], "messages": r[1], "last_active": r[2]} for r in rows]


TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "get_weather",
            "description": "Busca previsão do tempo para uma cidade",
            "parameters": {
                "type": "object",
                "properties": {
                    "city": {"type": "string", "description": "Nome da cidade, ex: Salvador,BR"}
                },
                "required": ["city"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "search_gmail",
            "description": "Busca emails no Gmail do usuário",
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "Query de busca Gmail, ex: is:unread, is:inbox"},
                    "page_size": {"type": "integer", "description": "Máximo de resultados", "default": 5},
                },
                "required": ["query"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_email_content",
            "description": "Lê o conteúdo completo de um email pelo message_id",
            "parameters": {
                "type": "object",
                "properties": {
                    "message_id": {"type": "string", "description": "ID da mensagem Gmail"},
                },
                "required": ["message_id"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "send_email",
            "description": "Envia um email via Gmail",
            "parameters": {
                "type": "object",
                "properties": {
                    "to": {"type": "string", "description": "Email do destinatário"},
                    "subject": {"type": "string", "description": "Assunto"},
                    "body": {"type": "string", "description": "Corpo do email"},
                },
                "required": ["to", "subject", "body"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "list_calendars",
            "description": "Lista os calendários do Google Calendar",
            "parameters": {"type": "object", "properties": {}},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_events",
            "description": "Busca eventos do Google Calendar",
            "parameters": {
                "type": "object",
                "properties": {
                    "time_min": {"type": "string", "description": "Data início ISO, ex: 2025-01-01T00:00:00Z"},
                    "time_max": {"type": "string", "description": "Data fim ISO"},
                    "max_results": {"type": "integer", "default": 10},
                },
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "manage_event",
            "description": "Cria, atualiza ou deleta evento no Google Calendar",
            "parameters": {
                "type": "object",
                "properties": {
                    "action": {"type": "string", "enum": ["create", "update", "delete"]},
                    "summary": {"type": "string", "description": "Título do evento"},
                    "start": {"type": "string", "description": "Início ISO"},
                    "end": {"type": "string", "description": "Fim ISO"},
                    "event_id": {"type": "string", "description": "ID do evento (para update/delete)"},
                },
                "required": ["action"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "send_whatsapp",
            "description": "Envia mensagem de texto pelo WhatsApp",
            "parameters": {
                "type": "object",
                "properties": {
                    "number": {"type": "string", "description": "Número com DDD e código do país, ex: 5519999998888"},
                    "text": {"type": "string", "description": "Texto da mensagem"},
                },
                "required": ["number", "text"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_whatsapp_chats",
            "description": "Lista as conversas recentes do WhatsApp",
            "parameters": {"type": "object", "properties": {}},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_whatsapp_messages",
            "description": "Lê as últimas mensagens de uma conversa do WhatsApp",
            "parameters": {
                "type": "object",
                "properties": {
                    "number": {"type": "string", "description": "Número do contato, ex: 5519999998888"},
                    "count": {"type": "integer", "description": "Quantidade de mensagens", "default": 5},
                },
                "required": ["number"],
            },
        },
    },
]

MCP_TOOL_MAP = {
    "search_gmail": "search_gmail_messages",
    "get_email_content": "get_gmail_message_content",
    "send_email": "send_gmail_message",
    "list_calendars": "list_calendars",
    "get_events": "get_events",
    "manage_event": "manage_event",
}


class NoturnaLocalAgent:
    """Local text-based Noturna agent with persistent memory and tool calling."""

    def __init__(self, mcp_bridge=None, weather_fn=None, whatsapp=None):
        api_key = os.environ.get("OPENAI_API_KEY", "")
        self.client = AsyncOpenAI(api_key=api_key) if api_key else None
        self.model = os.environ.get("NOTURNA_MODEL", "gpt-4o-mini")
        self.mcp = mcp_bridge
        self.weather_fn = weather_fn
        self.whatsapp = whatsapp
        self.memory = MemoryStore()

    def _get_system_prompt(self) -> str:
        today = datetime.now().strftime("%A, %d/%m/%Y %H:%M")
        return _load_prompt().format(today=today)

    def _build_messages(self, session_id: str) -> list[dict]:
        """Build message list: system prompt + persisted history."""
        system = {"role": "system", "content": self._get_system_prompt()}
        history = self.memory.load_messages(session_id, limit=50)
        return [system] + history

    async def chat(self, message: str, session_id: str = "default") -> str:
        """Send a text message and get a response."""
        if not self.client:
            return "Erro: OPENAI_API_KEY não configurada. Adicione ao .env."

        # Save user message
        self.memory.save_message(session_id, {"role": "user", "content": message})
        logger.info("Chat [%s]: %s", session_id, message[:100])

        try:
            messages = self._build_messages(session_id)

            # Loop to handle multiple rounds of tool calls
            for _round in range(5):  # max 5 rounds of tool calls
                response = await self.client.chat.completions.create(
                    model=self.model,
                    messages=messages,
                    tools=TOOLS,
                    tool_choice="auto",
                )

                assistant_msg = response.choices[0].message

                if not assistant_msg.tool_calls:
                    break

                # Process tool calls
                self.memory.save_message(session_id, assistant_msg)

                for tool_call in assistant_msg.tool_calls:
                    fn_name = tool_call.function.name
                    fn_args = json.loads(tool_call.function.arguments)
                    logger.info("Tool call [round %d]: %s(%s)", _round + 1, fn_name, fn_args)

                    result = await self._execute_tool(fn_name, fn_args)

                    tool_msg = {
                        "role": "tool",
                        "tool_call_id": tool_call.id,
                        "content": json.dumps(result, ensure_ascii=False),
                    }
                    self.memory.save_message(session_id, tool_msg)

                # Rebuild messages with tool results for next round
                messages = self._build_messages(session_id)

            reply = assistant_msg.content or ""
            self.memory.save_message(session_id, {"role": "assistant", "content": reply})

            logger.info("Reply [%s]: %s", session_id, reply[:100])
            return reply

        except Exception as e:
            logger.error("Chat error: %s", e)
            return f"Erro ao processar: {e}"

    async def _execute_tool(self, name: str, args: dict) -> dict:
        """Execute a tool call — weather, WhatsApp, or MCP."""
        if name == "get_weather" and self.weather_fn:
            return await self.weather_fn(args.get("city", "São Paulo"))

        # WhatsApp tools
        if name == "send_whatsapp" and self.whatsapp:
            return await self.whatsapp.send_message(args["number"], args["text"])
        if name == "get_whatsapp_chats" and self.whatsapp:
            return await self.whatsapp.get_chats()
        if name == "get_whatsapp_messages" and self.whatsapp:
            return await self.whatsapp.get_messages(args["number"], args.get("count", 5))

        # Google Workspace via MCP
        mcp_name = MCP_TOOL_MAP.get(name, name)
        if self.mcp:
            email = os.environ.get("USER_GOOGLE_EMAIL", "")
            if email:
                args["user_google_email"] = email
            return await self.mcp.call_tool(mcp_name, args)

        return {"error": f"Tool {name} not available"}

    def clear_session(self, session_id: str = "default"):
        self.memory.clear_session(session_id)

    def list_sessions(self) -> list[dict]:
        return self.memory.list_sessions()
