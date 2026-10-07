#!/bin/sh
set -eu

# The application owns the ASGI command. Ignore any stale/malformed platform
# Start Command arguments so an old host setting cannot replace the production
# entrypoint or invoke an unintended executable.
exec uvicorn main:app --host 0.0.0.0 --port "${PORT:-10000}"
