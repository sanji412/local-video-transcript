#!/bin/zsh
set -euo pipefail
cd -- "$(dirname -- "$0")/.."
.venv/bin/python -m pytest backend/tests -q
.venv/bin/ruff check backend
npm test --prefix frontend -- --run
npm run build --prefix frontend
git diff --check
