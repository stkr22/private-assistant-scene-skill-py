"""Data models for the scene skill.

This module contains skill-specific device representations that convert
from the global device registry format to scene skill-specific format.
"""

import json
import re
from typing import Any

from pydantic import BaseModel, Field, field_validator

# Define a regex pattern for a valid MQTT topic
MQTT_TOPIC_REGEX = re.compile(r"[\$#\+\s\0-\31]+")  # Disallow '+', '#', whitespace, and control characters
MAX_TOPIC_LENGTH = 128  # Maximum allowed MQTT topic length


class SceneDevice(BaseModel):
    """Skill-specific device representation for scene devices.

    Converts from global device registry to skill-specific format with validation.
    A scene contains a list of device actions (topic + payload pairs) to execute.
    """

    name: str
    room: str | None = None
    device_actions: list[dict[str, str]] = Field(default_factory=list)

    @field_validator("device_actions")
    @classmethod
    def validate_device_actions(cls, value: list[dict[str, str]]) -> list[dict[str, str]]:
        """Validate that each device action has a valid topic.

        Args:
            value: List of device actions to validate

        Returns:
            Validated list of device actions

        Raises:
            ValueError: If any action is missing a topic or has an invalid topic

        """
        for idx, action in enumerate(value):
            if "topic" not in action:
                raise ValueError(f"Device action at index {idx} missing required 'topic' field")

            topic = action["topic"]

            # Validate topic format
            if MQTT_TOPIC_REGEX.findall(topic):
                raise ValueError(
                    f"Device action at index {idx}: Topic must not contain '+', '#', whitespace, or control characters."
                )

            if len(topic) > MAX_TOPIC_LENGTH:
                raise ValueError(
                    f"Device action at index {idx}: Topic length exceeds maximum "
                    f"allowed limit ({MAX_TOPIC_LENGTH} characters)."
                )

        return value

    @classmethod
    def from_global_device(cls, global_device: Any) -> "SceneDevice":
        """Transform GlobalDevice to SceneDevice with type safety.

        Args:
            global_device: Device from global registry

        Returns:
            SceneDevice with skill-specific fields

        Raises:
            ValueError: If required device_attributes are missing or invalid

        """
        attrs = global_device.device_attributes or {}

        if not attrs.get("device_actions"):
            raise ValueError(f"Device {global_device.name} missing required 'device_actions' in device_attributes")

        # Parse device_actions from device_attributes
        device_actions = attrs["device_actions"]

        # Handle both list and JSON string formats
        if isinstance(device_actions, str):
            try:
                device_actions = json.loads(device_actions)
            except json.JSONDecodeError as e:
                raise ValueError(f"Device {global_device.name} has invalid JSON in 'device_actions': {e}") from e

        if not isinstance(device_actions, list):
            raise ValueError(
                f"Device {global_device.name} 'device_actions' must be a list, got {type(device_actions).__name__}"
            )

        if not device_actions:
            raise ValueError(f"Device {global_device.name} 'device_actions' list is empty")

        return cls(
            name=global_device.name,
            room=global_device.room.name if global_device.room else None,
            device_actions=device_actions,
        )
