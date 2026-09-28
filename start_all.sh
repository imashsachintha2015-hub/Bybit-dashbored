#!/bin/bash
set -e

echo "========================================================================"
echo "  LAUNCHING CME-X5 MODEL B UNIFIED PLATFORM"
echo "  PORT: ${PORT:-8070}"
echo "========================================================================"

exec python launcher.py
