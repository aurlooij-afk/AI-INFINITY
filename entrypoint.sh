#!/bin/sh
set -eu

# Blitz can persist an old Start Command separately from the Dockerfile.
# A previously saved malformed value may arrive as one literal bracketed
# argument such as "[sh,-c,exec uvicorn ...]". Ignore only that malformed
# legacy shape; preserve legitimate custom commands when supplied.
if [ "$#" -gt 0 ]; then
    case "$1" in
        \[*sh,-c,*\]) set -- ;;
        *) exec "$@" ;;
    esac
fi

exec uvicorn main:app --host 0.0.0.0 --port "${PORT:-10000}"
