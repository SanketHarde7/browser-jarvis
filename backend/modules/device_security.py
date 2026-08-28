# Path: backend/modules/device_security.py
# Use: Zero-Trust Device Pairing, Master/Guest Role Governance, and Emergency Kill-Switch Manager.

import os
import sys
import json
import secrets
import logging
import asyncio
from pathlib import Path
from datetime import datetime
from typing import Dict, Any, Tuple, Optional

logger = logging.getLogger("MAX.SECURITY")

class DeviceSecurityManager:
    """
    Zero-Trust Security & Role Governance Manager for MAX Assistant.
    Enforces device authorization, pairing approval flows, role hierarchy,
    and Master Emergency Kill-Switch.
    """
    _instance = None

    def __init__(self, config=None):
        self.config = config
        self.data_dir = Path(getattr(config, "DATA_DIR", "backend/data"))
        self.devices_file = self.data_dir / "approved_devices.json"
        self._lock = asyncio.Lock()
        self._load_devices()

    @classmethod
    def get_instance(cls, config=None):
        if cls._instance is None:
            cls._instance = DeviceSecurityManager(config)
        return cls._instance

    def _load_devices(self):
        """Load approved devices registry from disk."""
        if not self.devices_file.exists():
            self.data_dir.mkdir(parents=True, exist_ok=True)
            default_data = {
                "master_device_id": "sanket_vivo_t2_master",
                "devices": {
                    "sanket_vivo_t2_master": {
                        "role": "MASTER",
                        "device_name": "Sanket's Vivo T2 (Primary Master)",
                        "approved_at": datetime.now().isoformat(),
                        "last_seen": datetime.now().isoformat()
                    },
                    "laptop_local_host": {
                        "role": "MASTER",
                        "device_name": "Local Desktop Host",
                        "approved_at": datetime.now().isoformat(),
                        "last_seen": datetime.now().isoformat()
                    }
                },
                "pending_pairings": {}
            }
            with open(self.devices_file, "w", encoding="utf-8") as f:
                json.dump(default_data, f, indent=2)
            self.data_store = default_data
        else:
            try:
                with open(self.devices_file, "r", encoding="utf-8") as f:
                    self.data_store = json.load(f)
            except Exception as e:
                logger.error(f"Failed to parse approved_devices.json: {e}")
                self.data_store = {"master_device_id": "sanket_s24_primary_master", "devices": {}, "pending_pairings": {}}

    async def _save_devices(self):
        """Persist device registry to disk atomically and safely."""
        async with self._lock:
            try:
                def _write():
                    temp_file = self.devices_file.with_suffix(".tmp")
                    with open(temp_file, "w", encoding="utf-8") as f:
                        json.dump(self.data_store, f, indent=2, ensure_ascii=False)
                    try:
                        temp_file.replace(self.devices_file)
                    except OSError:
                        with open(self.devices_file, "w", encoding="utf-8") as f:
                            json.dump(self.data_store, f, indent=2, ensure_ascii=False)
                        if temp_file.exists():
                            try: temp_file.unlink()
                            except Exception: pass
                
                await asyncio.to_thread(_write)
            except Exception as e:
                logger.error(f"Failed to save approved_devices.json: {e}")

    async def validate_device(self, device_id: str) -> Tuple[bool, str]:
        """
        Validate whether a device ID is approved and return its role.
        Returns: (is_approved: bool, role: str) -> ('MASTER', 'GUEST', or 'UNAUTHORIZED')
        """
        if not device_id:
            return False, "UNAUTHORIZED"

        device_id_clean = str(device_id).strip()
        devices = self.data_store.get("devices", {})

        # Allow laptop local host connection by default
        if device_id_clean in ["laptop", "laptop_local_host", "localhost", "127.0.0.1"]:
            return True, "MASTER"

        if device_id_clean in devices:
            dev_info = devices[device_id_clean]
            dev_info["last_seen"] = datetime.now().isoformat()
            await self._save_devices()
            role = dev_info.get("role", "GUEST").upper()
            return True, role

        return False, "UNAUTHORIZED"

    async def is_master_device(self, device_id: str) -> bool:
        """Check if device has MASTER administrative privileges."""
        is_approved, role = await self.validate_device(device_id)
        return is_approved and role == "MASTER"

    async def create_pairing_request(self, device_id: str, device_name: str = "") -> str:
        """
        Generate a 4-digit temporary PIN for a new unapproved device.
        """
        device_id_clean = str(device_id).strip()
        pending = self.data_store.setdefault("pending_pairings", {})

        # Check if already has a pending PIN
        for pin, info in pending.items():
            if info.get("device_id") == device_id_clean:
                return pin

        # Generate unique 4-digit PIN
        while True:
            pin = f"{secrets.randbelow(9000) + 1000}"
            if pin not in pending:
                break

        pending[pin] = {
            "device_id": device_id_clean,
            "device_name": device_name or f"Device {device_id_clean[:6]}",
            "requested_at": datetime.now().isoformat()
        }
        await self._save_devices()
        logger.info(f"🔑 Created pairing PIN {pin} for device '{device_id_clean}'")
        return pin

    async def approve_pairing_pin(self, pin: str, assigned_role: str = "GUEST") -> Tuple[bool, str]:
        """
        Approve a pending device pairing PIN and add it to approved_devices.json.
        """
        pin_clean = str(pin).strip()
        pending = self.data_store.get("pending_pairings", {})

        if pin_clean not in pending:
            return False, f"Invalid or expired pairing PIN '{pin_clean}'"

        info = pending.pop(pin_clean)
        device_id = info["device_id"]
        device_name = info.get("device_name", f"Paired Device {pin_clean}")

        role = assigned_role.upper() if assigned_role.upper() in ["MASTER", "GUEST"] else "GUEST"

        self.data_store.setdefault("devices", {})[device_id] = {
            "role": role,
            "device_name": device_name,
            "approved_at": datetime.now().isoformat(),
            "last_seen": datetime.now().isoformat()
        }
        await self._save_devices()
        logger.info(f"✅ Device '{device_name}' ({device_id}) approved as {role} via PIN {pin_clean}")
        return True, f"Device '{device_name}' approved successfully as {role}!"

    async def revoke_device(self, device_id: str) -> Tuple[bool, str]:
        """Revoke device access immediately."""
        device_id_clean = str(device_id).strip()
        devices = self.data_store.get("devices", {})

        if device_id_clean not in devices:
            return False, f"Device '{device_id_clean}' is not registered."

        if devices[device_id_clean].get("role") == "MASTER" and device_id_clean == self.data_store.get("master_device_id"):
            return False, "Cannot revoke the Primary Master Device."

        dev_name = devices.pop(device_id_clean).get("device_name", device_id_clean)
        await self._save_devices()
        logger.info(f"🚫 Revoked access for device '{dev_name}' ({device_id_clean})")
        return True, f"Access revoked for '{dev_name}'."

    async def elevate_device_to_master(self, device_id: str) -> Tuple[bool, str]:
        """Elevate a paired device role from GUEST to MASTER via secret passphrase."""
        device_id_clean = str(device_id).strip()
        devices = self.data_store.get("devices", {})
        if device_id_clean not in devices:
            devices[device_id_clean] = {
                "role": "MASTER",
                "device_name": f"Elevated Master ({device_id_clean[:6]})",
                "approved_at": datetime.now().isoformat(),
                "last_seen": datetime.now().isoformat()
            }
        else:
            devices[device_id_clean]["role"] = "MASTER"
            devices[device_id_clean]["last_seen"] = datetime.now().isoformat()

        await self._save_devices()
        logger.info(f"👑 Device '{device_id_clean}' elevated to MASTER role via Secret Passphrase!")
        return True, "Secret passphrase verified! Admin privileges granted to this device."

    def list_devices(self) -> Dict[str, Any]:
        """Return formatted summary of all paired and pending devices."""
        return {
            "master_device_id": self.data_store.get("master_device_id"),
            "approved_devices": self.data_store.get("devices", {}),
            "pending_pairings": self.data_store.get("pending_pairings", {})
        }

    async def execute_emergency_killswitch(self, sender_device_id: str) -> Tuple[bool, str]:
        """
        Execute Emergency Process Termination on the PC Backend.
        Requires MASTER role.
        """
        is_master = await self.is_master_device(sender_device_id)
        if not is_master:
            logger.warning(f"🚨 UNAUTHORIZED KILL-SWITCH ATTEMPT blocked from device '{sender_device_id}'!")
            return False, "Permission Denied: Emergency Shutdown can only be triggered by the Master Device (Sanket's Phone)."

        logger.critical(f"🚨 EMERGENCY SHUTDOWN TRIGGERED BY MASTER DEVICE '{sender_device_id}'! Terminating Backend Process.")
        
        # Schedule process exit in 0.5s so HTTP/WS response can flush
        asyncio.get_event_loop().call_later(0.5, lambda: os._exit(0))
        return True, "Emergency Shutdown sequence initiated. MAX Backend is stopping now."


# Singleton accessor
_security_manager: Optional[DeviceSecurityManager] = None

def get_security_manager(config=None) -> DeviceSecurityManager:
    global _security_manager
    if _security_manager is None:
        _security_manager = DeviceSecurityManager(config)
    return _security_manager
