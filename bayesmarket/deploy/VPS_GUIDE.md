# ════════════════════════════════════════════════════════
# BayesMarket VPS Management Cheatsheet
# ════════════════════════════════════════════════════════

# ── FIRST TIME SETUP ──────────────────────────────────

# 1. Upload project ke VPS
scp -r ./bayesmarket ubuntu@YOUR_VPS_IP:/opt/bayesmarket

# 2. SSH ke VPS
ssh ubuntu@YOUR_VPS_IP

# 3. Jalankan setup
cd /opt/bayesmarket
chmod +x deploy/setup.sh
./deploy/setup.sh

# 4. Edit .env
nano .env
# Isi: TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID

# ── SERVICE MANAGEMENT ────────────────────────────────

# Start bot
sudo systemctl start bayesmarket

# Stop bot
sudo systemctl stop bayesmarket

# Restart bot (setelah update code)
sudo systemctl restart bayesmarket

# Lihat status
sudo systemctl status bayesmarket

# Enable auto-start saat VPS reboot
sudo systemctl enable bayesmarket

# ── LOGS ──────────────────────────────────────────────

# Live logs
sudo journalctl -u bayesmarket -f

# Logs 100 baris terakhir
sudo journalctl -u bayesmarket -n 100

# Logs sejak jam tertentu
sudo journalctl -u bayesmarket --since "2024-01-01 10:00:00"

# ── UPDATE CODE ───────────────────────────────────────

# Upload file baru
scp bayesmarket/config.py ubuntu@YOUR_VPS_IP:/opt/bayesmarket/bayesmarket/

# Restart setelah update
sudo systemctl restart bayesmarket

# ── DATABASE ──────────────────────────────────────────

# Download DB untuk analysis lokal
scp ubuntu@YOUR_VPS_IP:/opt/bayesmarket/bayesmarket.db ./bayesmarket_backup.db

# Jalankan report dari VPS
cd /opt/bayesmarket
source venv/bin/activate
python -m bayesmarket.report --period 7d --detail

# ── MONITORING ────────────────────────────────────────

# Cek resource usage
htop
# atau
top -p $(pgrep -f bayesmarket)

# Cek disk usage
df -h

# Cek memory
free -h

# ── RECOMMENDED VPS SPECS ─────────────────────────────
#
# Minimum:  1 vCPU, 1GB RAM, 10GB SSD
# Recommended: 2 vCPU, 2GB RAM, 20GB SSD
#
# Provider yang recommended:
#   - Contabo (sudah familiar)  → https://contabo.com
#   - DigitalOcean Droplet      → $6/mo untuk 1GB RAM
#   - Vultr                     → $6/mo
#   - Hetzner (Europe)          → €4/mo, sangat murah
#
# OS: Ubuntu 22.04 LTS atau 24.04 LTS
# Region: pilih yang latency rendah ke Hyperliquid
#   → Hyperliquid server ada di AWS us-east
#   → Gunakan region US East (Virginia/Ohio)
#
# ── KEEPALIVE / NETWORK ───────────────────────────────

# Bot sudah punya reconnect logic (exponential backoff)
# Tapi kalau VPS sering disconnect, tambahkan:
# /etc/sysctl.conf:
#   net.ipv4.tcp_keepalive_time = 60
#   net.ipv4.tcp_keepalive_intvl = 10
#   net.ipv4.tcp_keepalive_probes = 6
sudo sysctl -p

# ── TIMEZONE ──────────────────────────────────────────
# Set ke UTC untuk konsistensi dengan exchange
sudo timedatectl set-timezone UTC
timedatectl status
