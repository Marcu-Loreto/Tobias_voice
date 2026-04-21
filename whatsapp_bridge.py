"""
WhatsApp Bridge — Integration with Evolution API for sending/reading WhatsApp messages.
"""

import logging
import os
from typing import Any

from dotenv import load_dotenv
import requests as http_requests

load_dotenv(override=True)

logger = logging.getLogger("noturna.whatsapp")

EVOLUTION_URL = os.environ.get("EVOLUTION_API_URL", "").rstrip("/")
EVOLUTION_KEY = os.environ.get("EVOLUTION_API_KEY", "")
EVOLUTION_INSTANCE = os.environ.get("EVOLUTION_INSTANCE", "")


def _headers() -> dict:
    return {"apikey": EVOLUTION_KEY, "Content-Type": "application/json"}


def _url(path: str) -> str:
    return f"{EVOLUTION_URL}/{path}"


class WhatsAppBridge:
    """Interact with WhatsApp via Evolution API."""

    def __init__(self):
        self.enabled = bool(EVOLUTION_URL and EVOLUTION_KEY and EVOLUTION_INSTANCE)
        if self.enabled:
            logger.info("WhatsApp bridge enabled: %s @ %s", EVOLUTION_INSTANCE, EVOLUTION_URL)
        else:
            logger.warning("WhatsApp bridge disabled — missing EVOLUTION_* env vars")

    async def send_message(self, number: str, text: str) -> dict[str, Any]:
        """Send a text message to a WhatsApp number."""
        if not self.enabled:
            return {"error": "WhatsApp not configured"}

        # Normalize number (remove spaces, dashes, +)
        number = number.replace(" ", "").replace("-", "").replace("+", "")
        if not number.endswith("@s.whatsapp.net"):
            number = f"{number}@s.whatsapp.net"

        try:
            resp = http_requests.post(
                _url(f"message/sendText/{EVOLUTION_INSTANCE}"),
                headers=_headers(),
                json={"number": number, "text": text},
                timeout=15,
            )
            resp.raise_for_status()
            data = resp.json()
            logger.info("WhatsApp sent to %s: %s", number, text[:50])
            return {"success": True, "message_id": data.get("key", {}).get("id", "sent")}
        except Exception as e:
            logger.error("WhatsApp send failed: %s", e)
            return {"error": str(e)}

    async def check_number(self, number: str) -> dict[str, Any]:
        """Check if a number is registered on WhatsApp."""
        if not self.enabled:
            return {"error": "WhatsApp not configured"}

        number = number.replace(" ", "").replace("-", "").replace("+", "")

        try:
            resp = http_requests.post(
                _url(f"chat/whatsappNumbers/{EVOLUTION_INSTANCE}"),
                headers=_headers(),
                json={"numbers": [number]},
                timeout=10,
            )
            resp.raise_for_status()
            data = resp.json()
            return {"success": True, "result": data}
        except Exception as e:
            return {"error": str(e)}

    async def get_chats(self) -> dict[str, Any]:
        """List recent chats."""
        if not self.enabled:
            return {"error": "WhatsApp not configured"}

        try:
            resp = http_requests.post(
                _url(f"chat/findChats/{EVOLUTION_INSTANCE}"),
                headers=_headers(),
                json={},
                timeout=15,
            )
            resp.raise_for_status()
            chats = resp.json()
            # Return last 10 chats with basic info
            result = []
            for chat in chats[:10]:
                result.append({
                    "id": chat.get("id", ""),
                    "name": chat.get("name", chat.get("id", "")),
                    "last_message": chat.get("lastMessage", {}).get("body", ""),
                })
            return {"success": True, "chats": result}
        except Exception as e:
            logger.error("WhatsApp get_chats failed: %s", e)
            return {"error": str(e)}

    async def get_messages(self, number: str, count: int = 5) -> dict[str, Any]:
        """Get recent messages from a chat."""
        if not self.enabled:
            return {"error": "WhatsApp not configured"}

        number = number.replace(" ", "").replace("-", "").replace("+", "")
        if not number.endswith("@s.whatsapp.net"):
            number = f"{number}@s.whatsapp.net"

        try:
            resp = http_requests.post(
                _url(f"chat/findMessages/{EVOLUTION_INSTANCE}"),
                headers=_headers(),
                json={"where": {"key": {"remoteJid": number}}, "limit": count},
                timeout=15,
            )
            resp.raise_for_status()
            messages = resp.json()
            result = []
            for msg in messages.get("messages", messages) if isinstance(messages, dict) else messages:
                result.append({
                    "from": msg.get("key", {}).get("remoteJid", ""),
                    "fromMe": msg.get("key", {}).get("fromMe", False),
                    "text": msg.get("message", {}).get("conversation", "")
                        or msg.get("message", {}).get("extendedTextMessage", {}).get("text", ""),
                    "timestamp": msg.get("messageTimestamp", ""),
                })
            return {"success": True, "messages": result[-count:]}
        except Exception as e:
            logger.error("WhatsApp get_messages failed: %s", e)
            return {"error": str(e)}
