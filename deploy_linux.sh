#!/usr/bin/env bash
# ==============================================================================
# Stackpack v4 - Linux Deployment Script
# Automated setup for Ubuntu / Debian / RHEL / CentOS
# ==============================================================================

set -e

echo "=== Starting Stackpack v4 Setup ==="

# Check Python version
if ! command -v python3 &> /dev/null; then
    echo "[ERROR] python3 is not installed. Please install Python 3.10 or higher."
    exit 1
fi

PYTHON_VER=$(python3 -c 'import sys; print(f"{sys.version_info.major}.{sys.version_info.minor}")')
echo "[INFO] Detected Python version: $PYTHON_VER"

# Ensure venv module is available
if ! python3 -m venv --help &> /dev/null; then
    echo "[WARNING] python3-venv package might be required."
    echo "If script fails, install python3-venv (e.g., sudo apt install python3-venv)"
fi

# Create virtual environment if it doesn't exist
if [ ! -d ".venv" ]; then
    echo "[INFO] Creating Python virtual environment in .venv..."
    python3 -m venv .venv
else
    echo "[INFO] Using existing virtual environment .venv"
fi

# Activate virtual environment
source .venv/bin/activate

# Upgrade pip and install requirements
echo "[INFO] Upgrading pip and installing required packages..."
pip install --upgrade pip
pip install -r requirements.txt

echo ""
echo "=== Stackpack v4 Setup Complete! ==="
echo ""
echo "To run Stackpack manually in foreground:"
echo "  source .venv/bin/activate"
echo "  streamlit run app.py --server.port 8501 --server.address 0.0.0.0"
echo ""
echo "To run Stackpack as a systemd background service:"
echo "  1. Copy stackpack.service to /etc/systemd/system/stackpack.service"
echo "  2. Update User and ExecStart paths inside /etc/systemd/system/stackpack.service"
echo "  3. Run:"
echo "     sudo systemctl daemon-reload"
echo "     sudo systemctl enable stackpack"
echo "     sudo systemctl start stackpack"
echo "     sudo systemctl status stackpack"
echo "=============================================================================="
