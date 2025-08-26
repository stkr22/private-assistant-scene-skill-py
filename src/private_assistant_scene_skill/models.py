import re

from pydantic import field_validator
from sqlmodel import Field, Relationship, SQLModel
from sqlmodel._compat import SQLModelConfig

# Define a regex pattern for a valid MQTT topic
MQTT_TOPIC_REGEX = re.compile(r"[\$#\+\s\0-\31]+")  # Disallow '+', '#', whitespace, and control characters
MAX_TOPIC_LENGTH = 128  # Maximum allowed MQTT topic length


class SQLModelValidation(SQLModel):
    """
    Helper class to allow for validation in SQLModel classes with table=True
    """

    model_config = SQLModelConfig(from_attributes=True, validate_assignment=True)


class SceneSkillScenes(SQLModelValidation, table=True):
    id: int | None = Field(default=None, primary_key=True)
    name: str

    devices: list["SceneSkillDevices"] = Relationship(back_populates="scene")


class SceneSkillDevices(SQLModelValidation, table=True):
    id: int | None = Field(default=None, primary_key=True)
    topic: str
    scene_payload: str = "ON"

    scene_id: int = Field(foreign_key="sceneskillscenes.id")
    scene: SceneSkillScenes = Relationship(back_populates="devices")

    # Validate the topic field to ensure it conforms to MQTT standards
    @field_validator("topic")
    @classmethod
    def validate_topic(cls, value: str):
        # Check for any invalid characters in the topic
        if MQTT_TOPIC_REGEX.findall(value):
            raise ValueError("must not contain '+', '#', whitespace, or control characters.")
        if len(value) > MAX_TOPIC_LENGTH:
            raise ValueError(f"Topic length exceeds maximum allowed limit ({MAX_TOPIC_LENGTH} characters).")

        # Trim any leading or trailing whitespace just in case
        return value.strip()
