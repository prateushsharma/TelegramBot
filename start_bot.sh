#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "$0")"

if [[ ! -d "venv" ]]; then
  echo "Creating Python virtual environment..."
  python3 -m venv venv
fi

source venv/bin/activate

if [[ ! -f ".env" ]]; then
  echo "Missing .env file."
  echo "Run: cp .env.example .env"
  echo "Then put your Telegram bot token inside .env."
  exit 1
fi

pip install -q -r requirements.txt

exec python bot.py
