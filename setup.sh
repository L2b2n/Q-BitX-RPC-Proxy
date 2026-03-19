#!/bin/bash
# ============================================
# Q-BitX RPC Proxy — Server Setup Script
# Run on your Ubuntu/Debian server as root
# ============================================
set -e

echo "=== Q-BitX RPC Proxy Setup ==="

# 1. Create user (if not exists)
if ! id "qbitx" &>/dev/null; then
    useradd -r -s /bin/false qbitx
    echo "[+] Created user 'qbitx'"
fi

# 2. Create directories
mkdir -p /opt/qbitx-rpc-proxy
mkdir -p /var/log/qbitx-proxy
chown qbitx:qbitx /var/log/qbitx-proxy

# 3. Copy files
cp server.py /opt/qbitx-rpc-proxy/
cp requirements.txt /opt/qbitx-rpc-proxy/
echo "[+] Copied server files"

# 4. Python venv + deps
apt-get update -qq && apt-get install -y -qq python3 python3-venv
python3 -m venv /opt/qbitx-rpc-proxy/venv
/opt/qbitx-rpc-proxy/venv/bin/pip install -q -r /opt/qbitx-rpc-proxy/requirements.txt
echo "[+] Python dependencies installed"

# 5. Install systemd service
cp qbitx-proxy.service /etc/systemd/system/
systemctl daemon-reload
systemctl enable qbitx-proxy
systemctl start qbitx-proxy
echo "[+] Service started"

# 6. Firewall (allow port 8080)
if command -v ufw &>/dev/null; then
    ufw allow 8080/tcp
    echo "[+] Firewall: port 8080 opened"
fi

echo ""
echo "=== DONE ==="
echo "Proxy running on http://$(hostname -I | awk '{print $1}'):8080"
echo ""
echo "Test:  curl http://localhost:8080/"
echo "Logs:  journalctl -u qbitx-proxy -f"
echo ""
echo "IMPORTANT: Edit /opt/qbitx-rpc-proxy/server.py to set:"
echo "  NODE_USER / NODE_PASS  (your Q-BitX RPC credentials)"
echo "  NODE_WALLET            (your wallet name)"
echo "Then: systemctl restart qbitx-proxy"
