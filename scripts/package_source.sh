#!/usr/bin/env bash
set -euo pipefail

archive="${1:-futures-signal-src.tar.gz}"

export COPYFILE_DISABLE=1

tar \
  --exclude='.git' \
  --exclude='.venv' \
  --exclude='venv' \
  --exclude='.env' \
  --exclude='data/*.db' \
  --exclude='data/*.db-*' \
  --exclude='logs/*.log' \
  --exclude='__pycache__' \
  --exclude='.pytest_cache' \
  --exclude='.mypy_cache' \
  --exclude='.ruff_cache' \
  --exclude='dist' \
  --exclude='build' \
  --exclude='*.egg-info' \
  --exclude='.DS_Store' \
  --exclude='._*' \
  -czf "$archive" .

echo "created $archive"
