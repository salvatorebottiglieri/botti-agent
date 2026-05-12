"""Sensors package for laptop-minion.

Each sensor monitors specific system data and emits events when
significant changes are detected.
"""

from laptop_minion.sensors.base import BaseSensor, SensorEvent
from laptop_minion.sensors.screen import ScreenSensor, ApplicationFocusSensor
from laptop_minion.sensors.keyboard import KeyboardSensor
from laptop_minion.sensors.battery import BatterySensor
from laptop_minion.sensors.network import NetworkSensor

__all__ = [
    "BaseSensor",
    "SensorEvent",
    "ScreenSensor",
    "ApplicationFocusSensor",
    "KeyboardSensor",
    "BatterySensor",
    "NetworkSensor",
]

# Sensor registry
SENSOR_REGISTRY: dict[str, type[BaseSensor]] = {
    "screen_activity": ScreenSensor,
    "application_focus": ApplicationFocusSensor,
    "keyboard_activity": KeyboardSensor,
    "battery": BatterySensor,
    "network_status": NetworkSensor,
}
