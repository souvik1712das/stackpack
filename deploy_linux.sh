#!/usr/bin/env bash
# ==============================================================================
# Stackpack v4 - Linux Deployment Script
# Automated setup for Ubuntu / Debian / RHEL / CentOS
# ==============================================================================

set -e

echo "=== Starting Stackpack v4 Setup ==="

# 1. Check Python installation
if ! command -v python3 &> /dev/null; then
    echo "[ERROR] python3 is not installed. Please install Python 3.10 or higher."
    echo "  Ubuntu/Debian: sudo apt update && sudo apt install -y python3 python3-pip python3-venv python3-full git"
    echo "  RHEL/CentOS:   sudo dnf install -y python3 python3-pip git"
    exit 1
fi

PYTHON_VER=$(python3 -c 'import sys; print(f"{sys.version_info.major}.{sys.version_info.minor}")')
echo "[INFO] Detected Python version: $PYTHON_VER"

# 2. Handle invalid or incomplete .venv directory
if [ -d ".venv" ] && [ ! -f ".venv/bin/activate" ]; then
    echo "[WARNING] Incomplete or broken virtual environment detected in .venv. Removing it..."
    rm -rf .venv
fi

# 3. Create virtual environment if missing
if [ ! -f ".venv/bin/activate" ]; then
    echo "[INFO] Creating Python virtual environment in .venv..."
    if ! python3 -m venv .venv 2>/dev/null; then
        echo "[WARNING] Initial venv creation failed. Attempting with --without-pip fallback or system package check..."
        
        # Check if apt-get is available to offer actionable fix
        if command -v apt-get &> /dev/null; then
            echo ""
            echo "[ERROR] python3-venv is missing on this Ubuntu/Debian system."
            echo "Please run the following command to install the required system packages:"
            echo ""
            echo "    sudo apt update && sudo apt install -y python3-venv python3-full python3-pip"
            echo ""
            exit 1
        elif command -v dnf &> /dev/null; then
            echo ""
            echo "[ERROR] python3-devel / venv modules are missing on this RHEL/CentOS system."
            echo "Please run: sudo dnf install -y python3 python3-pip python3-devel"
            echo ""
            exit 1
        else
            echo "[ERROR] Failed to create virtual environment."
            exit 1
        fi
    fi
fi

# Double check that .venv/bin/activate exists
if [ ! -f ".venv/bin/activate" ]; then
    echo "[ERROR] Virtual environment activation script (.venv/bin/activate) was not found."
    echo "Please ensure python3-venv (or python3-full) is installed:"
    echo "  sudo apt update && sudo apt install -y python3-venv python3-full"
    exit 1
fi

# 4. Activate virtual environment
echo "[INFO] Activating virtual environment..."
source .venv/bin/activate

# Ensure pip is installed inside venv
if ! command -v pip &> /dev/null; then
    echo "[INFO] Installing pip inside virtual environment..."
    python3 -m ensurepip --upgrade || true
fi

# 5. Install requirements
echo "[INFO] Upgrading pip and installing required Python packages..."
pip install --upgrade pip
pip install -r requirements.txt

echo ""
echo "=============================================================================="
echo "=== Stackpack v4 Setup Complete! ==="
echo "=============================================================================="
echo ""
echo "To run Stackpack manually in foreground:"
echo "  source .venv/bin/activate"
echo "  streamlit run app.py --server.port 8501 --server.address 0.0.0.0"
echo ""
echo "To run Stackpack as a systemd background service:"
echo "  1. Copy stackpack.service to /etc/systemd/system/stackpack.service:"
echo "     sudo cp stackpack.service /etc/systemd/system/stackpack.service"
echo "  2. Reload systemd, enable, and start:"
echo "     sudo systemctl daemon-reload"
echo "     sudo systemctl enable --now stackpack"
echo "  3. Check status:"
echo "     sudo systemctl status stackpack"
echo "=============================================================================="
