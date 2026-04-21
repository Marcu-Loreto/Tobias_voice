"""
MCP Bridge — Connects to MCP servers and exposes tools via HTTP.
Supports both N8N SSE-based MCP servers and the Google Workspace MCP.
"""

import asyncio
import json
import logging
import os
import subprocess
import sys
from typing import Any

from dotenv import load_dotenv

load_dotenv()

logger = logging.getLogger("mcp_bridge")

# N8N MCP endpoints (SSE-based)
N8N_MCP_SERVERS = {
    "n8n_email_calendar": os.environ.get(
        "N8N_MCP_URL_1",
        "https://webhook.etechats.com.br/mcp/40c4e626-8646-49a1-9f52-04a722bae221",
    ),
    "n8n_extra": os.environ.get(
        "N8N_MCP_URL_2",
        "https://webhook.etechats.com.br/mcp/bebebab9-e4b9-4bb8-8fe2-7c9b852d30e7",
    ),
}


class GoogleWorkspaceMCP:
    """Manages a local Google Workspace MCP server process and communicates via stdio."""

    def __init__(self):
        self._process: subprocess.Popen | None = None
        self._request_id = 0
        self._lock = asyncio.Lock()
        self._tools: list[dict] = []

    async def start(self) -> bool:
        """Start the Google Workspace MCP server as a subprocess."""
        client_id = os.environ.get("GOOGLE_OAUTH_CLIENT_ID", "")
        client_secret = os.environ.get("GOOGLE_OAUTH_CLIENT_SECRET", "")

        if not client_id:
            logger.warning("GOOGLE_OAUTH_CLIENT_ID not set — Google MCP disabled")
            return False

        env = {
            **os.environ,
            "GOOGLE_OAUTH_CLIENT_ID": client_id,
            "GOOGLE_OAUTH_CLIENT_SECRET": client_secret,
            "OAUTHLIB_INSECURE_TRANSPORT": "1",
        }

        user_email = os.environ.get("USER_GOOGLE_EMAIL", "")
        if user_email:
            env["USER_GOOGLE_EMAIL"] = user_email

        try:
            self._process = subprocess.Popen(
                ["workspace-mcp", "--tools", "gmail", "calendar", "--single-user", "--tool-tier", "core"],
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                env=env,
            )
            logger.info("Google Workspace MCP server started (PID %d)", self._process.pid)

            # Initialize the MCP connection
            await self._send_request("initialize", {
                "protocolVersion": "2024-11-05",
                "capabilities": {},
                "clientInfo": {"name": "noturna-bridge", "version": "1.0.0"},
            })

            # Send initialized notification (required before tool calls)
            await asyncio.sleep(1)
            notif = json.dumps({"jsonrpc": "2.0", "method": "notifications/initialized", "params": {}}) + "\n"
            self._process.stdin.write(notif.encode())
            self._process.stdin.flush()
            await asyncio.sleep(1)

            # List available tools
            result = await self._send_request("tools/list", {})
            if result and "tools" in result:
                self._tools = result["tools"]
                tool_names = [t["name"] for t in self._tools]
                logger.info("Google MCP tools available: %s", tool_names)

            return True
        except Exception as e:
            logger.error("Failed to start Google MCP: %s", e)
            return False

    async def _send_request(self, method: str, params: dict) -> dict | None:
        """Send a JSON-RPC request to the MCP server via stdio."""
        if not self._process or not self._process.stdin or not self._process.stdout:
            return None

        async with self._lock:
            self._request_id += 1
            request = {
                "jsonrpc": "2.0",
                "id": self._request_id,
                "method": method,
                "params": params,
            }

            line = json.dumps(request) + "\n"
            try:
                self._process.stdin.write(line.encode())
                self._process.stdin.flush()

                # Read response (blocking, but MCP stdio is line-based)
                loop = asyncio.get_event_loop()
                response_line = await loop.run_in_executor(
                    None, self._process.stdout.readline
                )

                if response_line:
                    response = json.loads(response_line.decode().strip())
                    if "error" in response:
                        logger.error("MCP error: %s", response["error"])
                        return None
                    return response.get("result", {})
            except Exception as e:
                logger.error("MCP request failed: %s", e)
            return None

    async def call_tool(self, tool_name: str, arguments: dict) -> dict[str, Any]:
        """Call a tool on the Google Workspace MCP server."""
        result = await self._send_request("tools/call", {
            "name": tool_name,
            "arguments": arguments,
        })
        if result:
            # Extract text from MCP content format
            content = result.get("content", [])
            texts = [c.get("text", "") for c in content if c.get("type") == "text"]
            return {"success": True, "result": "\n".join(texts) if texts else str(result)}
        return {"success": False, "error": "Tool call failed or no response"}

    def get_tools(self) -> list[dict]:
        """Return the list of available tools."""
        return self._tools

    async def stop(self):
        """Stop the MCP server process."""
        if self._process:
            self._process.terminate()
            self._process.wait(timeout=5)
            self._process = None
            logger.info("Google Workspace MCP server stopped")


class N8NMCPClient:
    """Client for N8N-hosted MCP servers via SSE transport."""

    def __init__(self, name: str, base_url: str):
        self.name = name
        self.base_url = base_url
        self._session_url: str | None = None
        self._tools: list[dict] = []

    async def connect(self) -> bool:
        """Connect to the N8N MCP server and discover tools."""
        import requests as http_requests

        try:
            # Step 1: Get session via SSE
            resp = http_requests.get(
                self.base_url,
                headers={"Accept": "text/event-stream"},
                stream=True,
                timeout=10,
            )
            session_path = None
            for line in resp.iter_lines(decode_unicode=True):
                if line and line.startswith("data:"):
                    session_path = line.replace("data: ", "").strip()
                    break
            resp.close()

            if not session_path:
                logger.warning("N8N MCP %s: no session received", self.name)
                return False

            self._session_url = f"https://webhook.etechats.com.br{session_path}"
            logger.info("N8N MCP %s: session at %s", self.name, self._session_url)

            # Step 2: Send tools/list
            post_resp = http_requests.post(
                self._session_url,
                json={"jsonrpc": "2.0", "id": 1, "method": "tools/list", "params": {}},
                timeout=10,
            )

            if post_resp.status_code in (200, 202):
                try:
                    data = post_resp.json()
                    if "result" in data and "tools" in data["result"]:
                        self._tools = data["result"]["tools"]
                        logger.info(
                            "N8N MCP %s tools: %s",
                            self.name,
                            [t["name"] for t in self._tools],
                        )
                except Exception:
                    # N8N returns 202 Accepted — tools come via SSE (async)
                    logger.info("N8N MCP %s: connected (async response mode)", self.name)

            return True
        except Exception as e:
            logger.warning("N8N MCP %s connection failed: %s", self.name, e)
            return False

    async def call_tool(self, tool_name: str, arguments: dict) -> dict[str, Any]:
        """Call a tool on the N8N MCP server."""
        import requests as http_requests

        if not self._session_url:
            return {"success": False, "error": "Not connected"}

        try:
            resp = http_requests.post(
                self._session_url,
                json={
                    "jsonrpc": "2.0",
                    "id": 2,
                    "method": "tools/call",
                    "params": {"name": tool_name, "arguments": arguments},
                },
                timeout=30,
            )
            if resp.status_code == 200:
                data = resp.json()
                return {"success": True, "result": data.get("result", {})}
            return {"success": True, "status": "accepted (async)"}
        except Exception as e:
            return {"success": False, "error": str(e)}

    def get_tools(self) -> list[dict]:
        return self._tools


class MCPBridge:
    """Unified bridge that manages all MCP server connections."""

    def __init__(self):
        self.google_mcp = GoogleWorkspaceMCP()
        self.n8n_clients: dict[str, N8NMCPClient] = {}
        self._all_tools: dict[str, tuple[str, str]] = {}  # tool_name -> (source, tool_name)

    async def start(self):
        """Start all MCP connections."""
        # Try Google Workspace MCP
        google_ok = await self.google_mcp.start()
        if google_ok:
            for tool in self.google_mcp.get_tools():
                self._all_tools[tool["name"]] = ("google", tool["name"])

        # Try N8N MCP servers
        for name, url in N8N_MCP_SERVERS.items():
            client = N8NMCPClient(name, url)
            ok = await client.connect()
            if ok:
                self.n8n_clients[name] = client
                for tool in client.get_tools():
                    self._all_tools[tool["name"]] = ("n8n", name)

        logger.info("MCP Bridge ready. Total tools: %d", len(self._all_tools))

    async def call_tool(self, tool_name: str, arguments: dict) -> dict[str, Any]:
        """Route a tool call to the appropriate MCP server."""
        if tool_name not in self._all_tools:
            return {"success": False, "error": f"Unknown tool: {tool_name}"}

        source, ref = self._all_tools[tool_name]

        if source == "google":
            return await self.google_mcp.call_tool(tool_name, arguments)
        elif source == "n8n":
            client = self.n8n_clients.get(ref)
            if client:
                return await client.call_tool(tool_name, arguments)

        return {"success": False, "error": "No handler for tool"}

    def list_tools(self) -> list[dict]:
        """List all available tools across all MCP servers."""
        tools = []
        for tool in self.google_mcp.get_tools():
            tools.append({**tool, "source": "google"})
        for name, client in self.n8n_clients.items():
            for tool in client.get_tools():
                tools.append({**tool, "source": f"n8n:{name}"})
        return tools

    async def stop(self):
        """Stop all MCP connections."""
        await self.google_mcp.stop()
