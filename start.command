#!/bin/zsh
set -euo pipefail
cd -- "$(dirname -- "$0")"
export PATH="/opt/homebrew/opt/node@22/bin:/usr/local/opt/node@22/bin:/opt/homebrew/bin:/usr/local/bin:$HOME/.local/bin:$PATH"

for tool in uv npm ffmpeg ffprobe; do
  if ! command -v "$tool" >/dev/null 2>&1; then
    echo "缺少 $tool，请参考 README.md 完成首次安装。"
    exit 1
  fi
done

echo "检查本地运行环境…"
if ! uv sync --python 3.12 --locked --extra local --offline; then
  echo "首次准备运行依赖，需要联网下载…"
  uv sync --python 3.12 --locked --extra local
fi
if [[ ! -d frontend/node_modules ]]; then
  npm ci --prefix frontend --prefer-offline --no-audit --no-fund
fi
npm run build --prefix frontend
exec .venv/bin/python -m app.launch "$@"
