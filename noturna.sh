#!/bin/bash
# Noturna Voice Client — Start script
kill $(lsof -t -i :8443) 2>/dev/null
uv run python noturna_client.py
