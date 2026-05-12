"""Network status sensor.

Monitors wifi/cellular changes, signal strength changes,
and VPN status.
"""

from __future__ import annotations

import logging
import platform
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from cortex_protocol.schemas import NetworkStatusEvent, NetworkStatusPayload, NetworkType
from laptop_minion.config import SensorSettings
from laptop_minion.sensors.base import BaseSensor, SensorEvent

logger = logging.getLogger(__name__)


@dataclass
class NetworkState:
    """Network state snapshot."""

    connected: bool = False
    network_type: NetworkType = NetworkType.NONE
    ssid: str | None = None
    signal_strength: int | None = None  # 0-100
    ip_address: str | None = None
    vpn_active: bool = False


class NetworkSensor(BaseSensor):
    """Network status sensor using psutil or platform-specific APIs.

    Triggers on:
    - Connection/disconnection
    - Network type changes (wifi <-> cellular)
    - Signal strength changes > 10%
    - VPN activation/deactivation
    """

    SIGNAL_CHANGE_THRESHOLD = 10  # Trigger on > 10% signal change

    def __init__(
        self,
        settings: SensorSettings,
        on_event: SensorEvent,
    ):
        super().__init__("network_status", settings, on_event)
        self._platform = platform.system()

    def _get_current_state(self) -> dict[str, Any]:
        """Get current network state based on platform."""
        if self._platform == "Darwin":
            return self._get_state_macos()
        elif self._platform == "Windows":
            return self._get_state_windows()
        else:
            return self._get_state_linux()

    def _get_state_macos(self) -> dict[str, Any]:
        """Get network state on macOS using networksetup or system_profiler."""
        state: dict[str, Any] = {
            "connected": False,
            "network_type": "unknown",
            "ssid": None,
            "signal_strength": None,
            "ip_address": None,
            "vpn_active": False,
        }

        try:
            import subprocess

            # Get WiFi info
            result = subprocess.run(
                ["networksetup", "-getairportnetwork", "en0"],
                capture_output=True,
                text=True,
                timeout=2,
            )
            if result.returncode == 0 and result.stdout.strip():
                state["connected"] = True
                state["network_type"] = "wifi"
                state["ssid"] = result.stdout.strip()

            # Get IP address
            result = subprocess.run(
                ["ipconfig", "getifaddr", "en0"],
                capture_output=True,
                text=True,
                timeout=2,
            )
            if result.returncode == 0:
                ip = result.stdout.strip()
                if ip and ip != "null":
                    state["ip_address"] = ip

            # Check for VPN
            result = subprocess.run(
                ["networksetup", "-listallnetworkservices"],
                capture_output=True,
                text=True,
                timeout=2,
            )
            if result.returncode == 0:
                state["vpn_active"] = "VPN" in result.stdout

            # Get signal strength (if airport utility available)
            try:
                result = subprocess.run(
                    ["/System/Library/PrivateFrameworks/Apple80211.framework/Versions/Current/Resources/airport", "-I"],
                    capture_output=True,
                    text=True,
                    timeout=2,
                )
                if result.returncode == 0:
                    for line in result.stdout.split("\n"):
                        if "agrCtlRSSI" in line:
                            # RSSI to percentage (rough conversion)
                            try:
                                rssi = int(line.split(":")[1].strip())
                                # Convert RSSI (-100 to -30) to percentage (0 to 100)
                                rssi_pct = max(0, min(100, (rssi + 100) * 100 // 70))
                                state["signal_strength"] = rssi_pct
                            except (ValueError, IndexError):
                                pass
            except FileNotFoundError:
                pass

        except subprocess.TimeoutExpired:
            logger.warning("macOS networksetup timed out")
        except Exception as e:
            logger.debug(f"macOS network state error: {e}")

        # Fallback to psutil
        if not state["connected"]:
            try:
                import psutil

                net_if_stats = psutil.net_if_stats()
                for iface, stats in net_if_stats.items():
                    if stats.isup and "en" in iface:
                        state["connected"] = True
                        if "wi-fi" in iface.lower() or "wlan" in iface.lower():
                            state["network_type"] = "wifi"
                        break
            except ImportError:
                pass

        return state

    def _get_state_windows(self) -> dict[str, Any]:
        """Get network state on Windows using psutil or netsh."""
        state: dict[str, Any] = {
            "connected": False,
            "network_type": "unknown",
            "ssid": None,
            "signal_strength": None,
            "ip_address": None,
            "vpn_active": False,
        }

        try:
            import psutil

            # Get wifi info via subprocess
            import subprocess

            # Get WiFi SSID
            try:
                result = subprocess.run(
                    ["netsh", "wlan", "show", "interfaces"],
                    capture_output=True,
                    text=True,
                    timeout=5,
                    encoding="utf-8",
                    errors="ignore",
                )
                if result.returncode == 0:
                    output = result.stdout
                    if "State" in output and "connected" in output.lower():
                        state["connected"] = True
                        state["network_type"] = "wifi"

                        for line in output.split("\n"):
                            line = line.strip()
                            if line.startswith("SSID"):
                                state["ssid"] = line.split(":", 1)[1].strip()
                            elif "Signal" in line:
                                # Parse signal percentage
                                try:
                                    signal = line.split(":")[1].strip().rstrip("%")
                                    state["signal_strength"] = int(signal)
                                except (ValueError, IndexError):
                                    pass
            except FileNotFoundError:
                pass

            # Get IP addresses
            addrs = psutil.net_if_addrs()
            for iface, addr_list in addrs.items():
                for addr in addr_list:
                    if addr.family.name == "AF_INET":
                        # Prefer wifi interface
                        if "wi-fi" in iface.lower() or "wlan" in iface.lower():
                            state["ip_address"] = addr.address
                            break

            # Check for VPN adapters
            vpn_adapters = [i for i in addrs.keys() if "vpn" in i.lower() or "tap" in i.lower() or "tun" in i.lower()]
            state["vpn_active"] = len(vpn_adapters) > 0

        except ImportError:
            logger.warning("psutil not available for network")
        except Exception as e:
            logger.debug(f"Windows network state error: {e}")

        return state

    def _get_state_linux(self) -> dict[str, Any]:
        """Get network state on Linux using iw or /proc."""
        state: dict[str, Any] = {
            "connected": False,
            "network_type": "unknown",
            "ssid": None,
            "signal_strength": None,
            "ip_address": None,
            "vpn_active": False,
        }

        try:
            import subprocess

            # Try iwctl for WiFi
            try:
                result = subprocess.run(
                    ["iwctl", "station", "show"],
                    capture_output=True,
                    text=True,
                    timeout=5,
                )
                if result.returncode == 0:
                    output = result.stdout
                    if "connected" in output.lower():
                        state["connected"] = True
                        state["network_type"] = "wifi"

                        # Extract SSID
                        for line in output.split("\n"):
                            if "Connected network" in line:
                                parts = line.split(":", 1)
                                if len(parts) > 1:
                                    state["ssid"] = parts[1].strip()
            except FileNotFoundError:
                pass

            # Fallback: nmcli
            if not state["connected"]:
                try:
                    result = subprocess.run(
                        ["nmcli", "-t", "-f", "ACTIVE,SSID,TYPE", "dev", "wifi"],
                        capture_output=True,
                        text=True,
                        timeout=5,
                    )
                    if result.returncode == 0:
                        for line in result.stdout.split("\n"):
                            parts = line.split(":")
                            if len(parts) >= 3 and parts[0] == "yes":
                                state["connected"] = True
                                state["ssid"] = parts[1] or None
                                network_type = parts[2].lower()
                                if "wifi" in network_type or "wlan" in network_type:
                                    state["network_type"] = "wifi"
                                elif "ethernet" in network_type:
                                    state["network_type"] = "ethernet"
                except FileNotFoundError:
                    pass

            # Get signal strength
            try:
                result = subprocess.run(
                    ["iwctl", "station", "wlan0", "get-networks"],
                    capture_output=True,
                    text=True,
                    timeout=5,
                )
                if result.returncode == 0:
                    for line in result.stdout.split("\n"):
                        if "dBm" in line:
                            import re

                            match = re.search(r"(\-?\d+)\s*dBm", line)
                            if match:
                                rssi = int(match.group(1))
                                state["signal_strength"] = max(0, min(100, (rssi + 100) * 100 // 70))
            except Exception:
                pass

            # Check for VPN
            import os

            if os.path.exists("/proc/net/ip_vpn"):
                state["vpn_active"] = True
            else:
                # Check for tun interfaces
                result = subprocess.run(
                    ["ip", "link", "show"],
                    capture_output=True,
                    text=True,
                    timeout=2,
                )
                if result.returncode == 0:
                    state["vpn_active"] = "tun" in result.stdout or "tap" in result.stdout

        except FileNotFoundError:
            logger.debug("Linux network tools not available")
        except Exception as e:
            logger.debug(f"Linux network state error: {e}")

        return state

    def _create_event(self, state: dict[str, Any]) -> NetworkStatusEvent | None:
        """Create a network status event."""
        payload = NetworkStatusPayload(
            connected=state.get("connected", False),
            network_type=self._map_network_type(state.get("network_type", "unknown")),
            ssid=state.get("ssid"),
            signal_strength=state.get("signal_strength"),
            ip_address=state.get("ip_address"),
            vpn_active=state.get("vpn_active", False),
        )

        return NetworkStatusEvent(
            occurred_at=datetime.utcnow(),
            payload=payload,
        )

    def _should_emit(self, prev_state: dict[str, Any], new_state: dict[str, Any]) -> bool:
        """Emit on significant network changes."""
        # Connection state change
        if new_state.get("connected") != prev_state.get("connected"):
            return True

        # Network type change
        if new_state.get("network_type") != prev_state.get("network_type"):
            return True

        # Signal strength change
        prev_signal = prev_state.get("signal_strength")
        new_signal = new_state.get("signal_strength")
        if prev_signal is not None and new_signal is not None:
            if abs(new_signal - prev_signal) > self.SIGNAL_CHANGE_THRESHOLD:
                return True

        # VPN state change
        if new_state.get("vpn_active") != prev_state.get("vpn_active"):
            return True

        # SSID change (on same network type)
        if new_state.get("ssid") != prev_state.get("ssid"):
            return True

        return False

    def _map_network_type(self, network_type: str) -> NetworkType:
        """Map string network type to enum."""
        type_lower = network_type.lower()
        if "wifi" in type_lower or "wlan" in type_lower or "wi-fi" in type_lower:
            return NetworkType.WIFI
        elif "ethernet" in type_lower or "lan" in type_lower:
            return NetworkType.ETHERNET
        elif "cellular" in type_lower or "mobile" in type_lower or "lte" in type_lower:
            return NetworkType.CELLULAR
        elif "bluetooth" in type_lower:
            return NetworkType.BLUETOOTH
        elif "vpn" in type_lower:
            return NetworkType.VPN
        else:
            return NetworkType.NONE
