# Path: backend/modules/tunnel_manager.py
# Use: Automatic Cloudflare WSS Tunnel Manager for global remote access.

import os
import sys
import re
import asyncio
import logging
import subprocess
from typing import Optional

logger = logging.getLogger("MAX.TUNNEL")

class CloudflareTunnelManager:
    """
    Manages zero-login, 100% free encrypted Cloudflare WSS Tunnels for MAX Assistant.
    Exposes local port 8000 to HTTPS/WSS globally without opening router ports.
    """
    _instance = None

    def __init__(self, port: int = 8000):
        self.port = port
        self.public_url: Optional[str] = None
        self.process: Optional[subprocess.Popen] = None

    @classmethod
    def get_instance(cls, port: int = 8000):
        if cls._instance is None:
            cls._instance = CloudflareTunnelManager(port)
        return cls._instance

    def start_tunnel(self) -> Optional[str]:
        """
        Launch cloudflared tunnel in background and extract public HTTPS/WSS URL.
        """
        try:
            # Check if cloudflared binary is available
            cmd = ["cloudflared", "tunnel", "--url", f"http://localhost:{self.port}"]
            logger.info(f"🌐 Launching Cloudflare Tunnel for port {self.port}...")
            
            self.process = subprocess.Popen(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                bufsize=1
            )

            # Read output to capture generated trycloudflare.com URL
            for line in iter(self.process.stdout.readline, ''):
                logger.debug(f"Cloudflared: {line.strip()}")
                match = re.search(r'https://[a-zA-Z0-9-]+\.trycloudflare\.com', line)
                if match:
                    self.public_url = match.group(0)
                    logger.info(f"🎉 CLOUDFLARE SECURE TUNNEL ONLINE: {self.public_url}")
                    wss_url = self.public_url.replace("https://", "wss://") + "/ws"
                    logger.info(f"📱 MOBILE WSS ENDPOINT: {wss_url}")
                    return self.public_url

        except FileNotFoundError:
            logger.warning("Cloudflared binary not installed. Install via: winget install Cloudflare.cloudflared")
        except Exception as e:
            logger.error(f"Cloudflare Tunnel start failed: {e}")

        return None

    def stop_tunnel(self):
        """Terminate active Cloudflare tunnel."""
        if self.process:
            try:
                self.process.terminate()
                self.process.wait(timeout=2)
                logger.info("🛑 Cloudflare Tunnel stopped.")
            except Exception:
                pass
            self.process = None

# Singleton accessor
_tunnel_manager: Optional[CloudflareTunnelManager] = None

def get_tunnel_manager(port: int = 8000) -> CloudflareTunnelManager:
    global _tunnel_manager
    if _tunnel_manager is None:
        _tunnel_manager = CloudflareTunnelManager(port)
    return _tunnel_manager
