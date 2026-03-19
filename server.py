#!/usr/bin/env python3
"""
Q-BitX Public RPC Proxy — Stateless
====================================
Public-facing JSON-RPC proxy for Q-BitX wallets.
No authentication required. Rate-limited per IP.

Stateless design (like Infura/Alchemy for Ethereum):
  - Only blockchain queries + tx building + broadcasting
  - NO wallet operations — keys never touch the server
  - Clients sign transactions locally

Usage:
    python3 server.py
    # or via systemd: systemctl start qbitx-proxy
"""

import os
import time
import json
import logging
from collections import defaultdict
from threading import Lock

import requests
from flask import Flask, request, jsonify

# --------------- CONFIG ---------------

# Q-BitX Core node (local, with RPC auth)
# Set these via environment variables on the server!
NODE_HOST = os.environ.get("QBITX_NODE_HOST", "127.0.0.1")
NODE_PORT = int(os.environ.get("QBITX_NODE_PORT", "8332"))
NODE_USER = os.environ.get("QBITX_NODE_USER", "qbitx")
NODE_PASS = os.environ.get("QBITX_NODE_PASS", "changeme")

# Proxy settings
PROXY_HOST = "0.0.0.0"
PROXY_PORT = 8080

# Stateless RPC methods ONLY — no wallet operations
METHOD_LIMITS = {
    # Blockchain queries
    "getblockchaininfo":     30,
    "getblockcount":         30,
    "getbestblockhash":      30,
    "getblock":              20,
    "getblockhash":          20,
    "getrawtransaction":     20,
    "gettxout":              20,
    "getmempoolinfo":        20,
    "getrawmempool":         10,

    # UTXO queries (balance check without wallet)
    "scantxoutset":          10,

    # Fee estimation
    "estimatesmartfee":      15,

    # Transaction building (stateless — no keys stored)
    "createrawtransaction":  20,
    "decoderawtransaction":  20,

    # Broadcasting (signed tx from client)
    "sendrawtransaction":    10,
}

# Global rate limit: max requests per minute per IP across all methods
GLOBAL_LIMIT_PER_MIN = 60

# --------------- RATE LIMITER ---------------

class RateLimiter:
    """Simple sliding-window rate limiter per IP + method."""

    def __init__(self):
        self._lock = Lock()
        # ip -> method -> [timestamps]
        self._method_hits = defaultdict(lambda: defaultdict(list))
        # ip -> [timestamps]
        self._global_hits = defaultdict(list)

    def _cleanup(self, timestamps, window=60):
        """Remove timestamps older than the window."""
        cutoff = time.time() - window
        while timestamps and timestamps[0] < cutoff:
            timestamps.pop(0)

    def is_allowed(self, ip, method):
        """Check if the request is allowed. Returns (allowed, retry_after_seconds)."""
        now = time.time()
        with self._lock:
            # Global check
            ghits = self._global_hits[ip]
            self._cleanup(ghits)
            if len(ghits) >= GLOBAL_LIMIT_PER_MIN:
                wait = 60 - (now - ghits[0])
                return False, max(1, int(wait))

            # Per-method check
            limit = METHOD_LIMITS.get(method, 0)
            if limit == 0:
                return False, 0  # method not whitelisted

            mhits = self._method_hits[ip][method]
            self._cleanup(mhits)
            if len(mhits) >= limit:
                wait = 60 - (now - mhits[0])
                return False, max(1, int(wait))

            # Record hit
            ghits.append(now)
            mhits.append(now)
            return True, 0

rate_limiter = RateLimiter()

# --------------- NODE RPC ---------------

def node_url():
    return f"http://{NODE_HOST}:{NODE_PORT}"

def forward_rpc(method, params):
    """Forward a JSON-RPC call to the Q-BitX Core node."""
    payload = {
        "jsonrpc": "2.0",
        "id": 1,
        "method": method,
        "params": params
    }
    resp = requests.post(
        node_url(),
        json=payload,
        auth=(NODE_USER, NODE_PASS),
        timeout=30
    )
    return resp.json()

# --------------- FLASK APP ---------------

app = Flask(__name__)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s"
)
log = logging.getLogger("qbitx-proxy")


@app.route("/", methods=["POST"])
def rpc_proxy():
    """Main JSON-RPC proxy endpoint."""
    ip = request.headers.get("X-Forwarded-For", request.remote_addr)
    # Take first IP if multiple (behind reverse proxy)
    if ip and "," in ip:
        ip = ip.split(",")[0].strip()

    # Parse JSON-RPC request
    try:
        body = request.get_json(force=True)
    except Exception:
        return jsonify({"jsonrpc": "2.0", "id": None, "error": {"code": -32700, "message": "Parse error"}}), 400

    method = body.get("method", "")
    params = body.get("params", [])
    rpc_id = body.get("id", 1)

    # Validate method is whitelisted
    if method not in METHOD_LIMITS:
        log.warning(f"Blocked method '{method}' from {ip}")
        return jsonify({
            "jsonrpc": "2.0",
            "id": rpc_id,
            "error": {"code": -32601, "message": f"Method '{method}' not allowed"}
        }), 403

    # Rate limit check
    allowed, retry_after = rate_limiter.is_allowed(ip, method)
    if not allowed:
        log.warning(f"Rate limited {ip} on '{method}' (retry in {retry_after}s)")
        resp = jsonify({
            "jsonrpc": "2.0",
            "id": rpc_id,
            "error": {"code": -32000, "message": f"Rate limit exceeded. Retry in {retry_after}s"}
        })
        resp.headers["Retry-After"] = str(retry_after)
        return resp, 429

    # Forward to node
    log.info(f"{ip} -> {method}")
    try:
        result = forward_rpc(method, params)
        # Preserve original RPC id
        result["id"] = rpc_id
        return jsonify(result)
    except requests.exceptions.Timeout:
        return jsonify({
            "jsonrpc": "2.0",
            "id": rpc_id,
            "error": {"code": -32000, "message": "Node timeout"}
        }), 504
    except requests.exceptions.ConnectionError:
        return jsonify({
            "jsonrpc": "2.0",
            "id": rpc_id,
            "error": {"code": -32000, "message": "Node unreachable"}
        }), 502
    except Exception as e:
        log.error(f"Error forwarding '{method}': {e}")
        return jsonify({
            "jsonrpc": "2.0",
            "id": rpc_id,
            "error": {"code": -32603, "message": "Internal proxy error"}
        }), 500


@app.route("/", methods=["GET"])
def index():
    """Health check / info page."""
    return jsonify({
        "service": "Q-BitX Public RPC Proxy",
        "version": "2.0.0",
        "type": "stateless",
        "methods": sorted(METHOD_LIMITS.keys()),
        "note": "No wallet operations — sign transactions locally"
    })


@app.route("/limits", methods=["GET"])
def limits():
    """Show rate limits for all methods."""
    return jsonify({
        method: f"{limit}/min"
        for method, limit in sorted(METHOD_LIMITS.items())
    })


if __name__ == "__main__":
    log.info(f"Q-BitX Stateless RPC Proxy starting on {PROXY_HOST}:{PROXY_PORT}")
    log.info(f"Forwarding to node at {NODE_HOST}:{NODE_PORT}")
    log.info(f"Whitelisted methods: {len(METHOD_LIMITS)}")
    app.run(host=PROXY_HOST, port=PROXY_PORT, threaded=True)
