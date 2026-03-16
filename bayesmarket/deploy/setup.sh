#!/bin/bash
# ════════════════════════════════════════════════════════
# BayesMarket VPS Setup Script
# Tested on: Ubuntu 22.04 LTS / 24.04 LTS
# Minimum specs: 1 vCPU, 1GB RAM, 10GB disk
# ════════════════════════════════════════════════════════

set -e  # Exit on error

echo ""
echo "╔══════════════════════════════════════════╗"
echo "║     BayesMarket VPS Setup                ║"
echo "╚══════════════════════════════════════════╝"
echo ""

# ── 1. System update ─────────────────────────────────────────────
echo "[1/7] Updating system packages..."
sudo apt-get update -qq
sudo apt-get upgrade -y -qq

# ── 2. Install Python 3.11+ ──────────────────────────────────────
echo "[2/7] Installing Python 3.11..."
sudo apt-get install -y python3.11 python3.11-venv python3.11-dev python3-pip -qq
sudo apt-get install -y git curl wget screen tmux -qq

# Set python3.11 as default if not already
if ! python3 --version | grep -q "3.1[1-9]"; then
    sudo update-alternatives --install /usr/bin/python3 python3 /usr/bin/python3.11 1
fi

echo "Python version: $(python3 --version)"

# ── 3. Create app user (optional, more secure) ───────────────────
echo "[3/7] Setting up app directory..."
APP_DIR="/opt/bayesmarket"
sudo mkdir -p $APP_DIR
sudo chown $USER:$USER $APP_DIR

# ── 4. Clone / copy app ──────────────────────────────────────────
echo "[4/7] Setting up virtual environment..."
cd $APP_DIR

# Create venv
python3 -m venv venv
source venv/bin/activate

# Install dependencies
pip install --upgrade pip -q
pip install -r requirements.txt -q

echo "Dependencies installed."

# ── 5. Setup .env ────────────────────────────────────────────────
echo "[5/7] Setting up environment..."
if [ ! -f "$APP_DIR/.env" ]; then
    cp $APP_DIR/.env.example $APP_DIR/.env
    echo ""
    echo "⚠️  File .env dibuat dari template."
    echo "    Edit file berikut sebelum menjalankan bot:"
    echo "    nano $APP_DIR/.env"
    echo ""
    echo "    Yang WAJIB diisi:"
    echo "    - TELEGRAM_BOT_TOKEN"
    echo "    - TELEGRAM_CHAT_ID"
    echo "    Opsional untuk live mode:"
    echo "    - HL_PRIVATE_KEY"
    echo "    - HL_ACCOUNT_ADDRESS"
else
    echo ".env sudah ada, skip."
fi

# ── 6. Install systemd service ───────────────────────────────────
echo "[6/7] Installing systemd service..."
sudo cp $APP_DIR/deploy/bayesmarket.service /etc/systemd/system/bayesmarket.service

# Update path di service file sesuai user dan direktori aktual
sudo sed -i "s|/opt/bayesmarket|$APP_DIR|g" /etc/systemd/system/bayesmarket.service
sudo sed -i "s|User=ubuntu|User=$USER|g" /etc/systemd/system/bayesmarket.service

sudo systemctl daemon-reload
sudo systemctl enable bayesmarket

echo "Systemd service installed."

# ── 7. Setup log rotation ────────────────────────────────────────
echo "[7/7] Setting up log rotation..."
sudo tee /etc/logrotate.d/bayesmarket > /dev/null << 'EOF'
/opt/bayesmarket/logs/*.log {
    daily
    rotate 7
    compress
    missingok
    notifempty
    copytruncate
}
EOF

mkdir -p $APP_DIR/logs

# ── Done ─────────────────────────────────────────────────────────
echo ""
echo "╔══════════════════════════════════════════╗"
echo "║     Setup Complete!                      ║"
echo "╚══════════════════════════════════════════╝"
echo ""
echo "Next steps:"
echo ""
echo "  1. Edit konfigurasi:"
echo "     nano $APP_DIR/.env"
echo ""
echo "  2. Test jalankan manual dulu:"
echo "     cd $APP_DIR"
echo "     source venv/bin/activate"
echo "     python -m bayesmarket"
echo ""
echo "  3. Kalau sudah OK, jalankan sebagai service:"
echo "     sudo systemctl start bayesmarket"
echo "     sudo systemctl status bayesmarket"
echo ""
echo "  4. Lihat logs:"
echo "     sudo journalctl -u bayesmarket -f"
echo "     # atau"
echo "     tail -f $APP_DIR/logs/bayesmarket.log"
echo ""
echo "  5. Control via Telegram:"
echo "     /start — main menu"
echo "     /status — status bot"
echo "     /live — switch ke live mode"
echo ""
