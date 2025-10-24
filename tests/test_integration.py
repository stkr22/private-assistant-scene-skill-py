"""End-to-end integration tests for the scene skill.

These tests validate the complete skill workflow with real external services:
- PostgreSQL database (device registry)
- MQTT broker (message bus)
- Scene skill running in background

Test flow:
1. Setup database with test scene devices
2. Start skill in background
3. Publish IntentRequest to MQTT
4. Assert skill publishes correct device commands and responses

Run these tests with:
    pytest tests/test_integration.py -v -m integration -n 0

Requirements:
- Compose services (PostgreSQL, Mosquitto) must be running
- If mosquitto.conf was just updated, restart services with:
  docker compose -f .devcontainer/compose.yml restart mosquitto
"""

import asyncio
import contextlib
import logging
import os
import pathlib
import tempfile
import uuid
from collections.abc import AsyncGenerator
from datetime import datetime
from typing import cast

import aiomqtt
import pytest
import yaml
from private_assistant_commons import ClassifiedIntent, ClientRequest, Entity, EntityType, IntentRequest, IntentType
from private_assistant_commons.database import PostgresConfig
from private_assistant_commons.database.models import DeviceType, GlobalDevice, Room, Skill
from sqlalchemy.ext.asyncio import create_async_engine
from sqlmodel import SQLModel, select
from sqlmodel.ext.asyncio.session import AsyncSession

from private_assistant_scene_skill.main import start_skill

# Mark all tests in this module as integration tests
pytestmark = pytest.mark.integration

# Logger for test debugging
logger = logging.getLogger(__name__)


@pytest.fixture(scope="function")
async def db_engine():
    """Create a database engine for integration tests."""
    db_config = PostgresConfig()
    engine = create_async_engine(db_config.connection_string_async, echo=False)

    # Ensure tables exist
    async with engine.begin() as conn:
        await conn.run_sync(SQLModel.metadata.create_all)

    yield engine

    await engine.dispose()


@pytest.fixture
async def db_session(db_engine):
    """Create a database session for each test."""
    async with AsyncSession(db_engine) as session:
        yield session


@pytest.fixture
def mqtt_config():
    """Get MQTT configuration from environment variables."""
    return {
        "host": os.getenv("MQTT_HOST", "mosquitto"),
        "port": int(os.getenv("MQTT_PORT", "1883")),
    }


@pytest.fixture
async def mqtt_test_client(mqtt_config):
    """Create an MQTT test client."""
    async with aiomqtt.Client(hostname=mqtt_config["host"], port=mqtt_config["port"]) as client:
        yield client


@pytest.fixture
async def test_skill_entity(db_session) -> Skill:
    """Create a test skill entity in the database."""
    result = await db_session.exec(select(Skill).where(Skill.name == "scene-skill-integration-test"))
    skill = result.first()

    if skill is None:
        skill = Skill(name="scene-skill-integration-test")
        db_session.add(skill)
        await db_session.flush()
        await db_session.refresh(skill)

    assert skill is not None
    return cast("Skill", skill)


@pytest.fixture
async def test_device_type(db_session) -> DeviceType:
    """Create a test device type in the database."""
    result = await db_session.exec(select(DeviceType).where(DeviceType.name == "scene"))
    device_type = result.first()

    if device_type is None:
        device_type = DeviceType(name="scene")
        db_session.add(device_type)
        await db_session.flush()
        await db_session.refresh(device_type)

    assert device_type is not None
    return cast("DeviceType", device_type)


@pytest.fixture
async def test_room(db_session) -> Room:
    """Create a test room in the database."""
    room_name = f"test_room_{uuid.uuid4().hex[:8]}"
    room = Room(name=room_name)
    db_session.add(room)
    await db_session.flush()
    await db_session.refresh(room)
    return room


@pytest.fixture
async def test_scene_device(
    db_session, test_skill_entity, test_device_type, test_room
) -> AsyncGenerator[GlobalDevice, None]:
    """Create a single test scene device in the database.

    Note: This fixture must be created BEFORE the running_skill fixture
    so the device is loaded during skill initialization.
    """
    await db_session.refresh(test_room)
    await db_session.refresh(test_skill_entity)
    await db_session.refresh(test_device_type)

    logger.debug("Creating scene device with skill_id=%s, skill_name=%s", test_skill_entity.id, test_skill_entity.name)

    device = GlobalDevice(
        device_type_id=test_device_type.id,
        name="romantic",
        pattern=["romantic", "romantic scene", f"{test_room.name} romantic"],
        device_attributes={
            "device_actions": [
                {"topic": "test/integration/scene/light1", "payload": "ON"},
                {"topic": "test/integration/scene/light2", "payload": "50"},
                {"topic": "test/integration/scene/light3", "payload": "OFF"},
            ]
        },
        room_id=test_room.id,
        skill_id=test_skill_entity.id,
    )
    db_session.add(device)
    await db_session.commit()
    await db_session.refresh(device, ["room"])

    logger.debug("Scene device created with ID=%s, skill_id=%s", device.id, device.skill_id)

    yield device

    # Cleanup: Delete test device
    logger.debug("Cleaning up scene device %s", device.id)
    await db_session.delete(device)
    await db_session.commit()


@pytest.fixture
async def test_scene_devices_multiple(
    db_session, test_skill_entity, test_device_type, test_room
) -> AsyncGenerator[list[GlobalDevice], None]:
    """Create multiple test scene devices in the same room.

    Note: This fixture must be created BEFORE the running_skill fixture
    so the devices are loaded during skill initialization.
    """
    await db_session.refresh(test_room)
    await db_session.refresh(test_skill_entity)
    await db_session.refresh(test_device_type)

    room_id = test_room.id
    skill_id = test_skill_entity.id
    device_type_id = test_device_type.id
    room_name = test_room.name

    devices = [
        GlobalDevice(
            device_type_id=device_type_id,
            name="romantic",
            pattern=["romantic", "romantic scene", f"{room_name} romantic"],
            device_attributes={
                "device_actions": [
                    {"topic": "test/integration/multi/light1", "payload": "ON"},
                    {"topic": "test/integration/multi/light2", "payload": "50"},
                ]
            },
            room_id=room_id,
            skill_id=skill_id,
        ),
        GlobalDevice(
            device_type_id=device_type_id,
            name="morning",
            pattern=["morning", "morning scene", f"{room_name} morning"],
            device_attributes={
                "device_actions": [
                    {"topic": "test/integration/multi/light1", "payload": "100"},
                    {"topic": "test/integration/multi/curtain", "payload": "OPEN"},
                ]
            },
            room_id=room_id,
            skill_id=skill_id,
        ),
    ]

    for device in devices:
        db_session.add(device)

    await db_session.commit()

    for device in devices:
        await db_session.refresh(device, ["room"])

    yield devices

    # Cleanup: Delete all test devices
    for device in devices:
        await db_session.delete(device)
    await db_session.commit()


@pytest.fixture
async def skill_config_file(mqtt_config):
    """Create a temporary config file for the skill."""
    config = {
        "client_id": "scene-skill-integration-test",
        "mqtt_server_host": mqtt_config["host"],
        "mqtt_server_port": mqtt_config["port"],
        "base_topic": "assistant",
    }

    with tempfile.NamedTemporaryFile(mode="w", suffix=".yaml", delete=False) as f:
        yaml.dump(config, f)
        config_path = pathlib.Path(f.name)

    yield config_path

    # Cleanup: Remove temp file
    config_path.unlink(missing_ok=True)


@pytest.fixture
async def running_skill_single_scene(skill_config_file, test_scene_device, db_engine):  # noqa: ARG001
    """Start the skill in background with a single scene device ready.

    Args:
        skill_config_file: Path to skill config
        test_scene_device: Test scene device that must be created before skill starts
        db_engine: Database engine to verify device visibility
    """
    # Device is already created by test_scene_device fixture
    # Give database time to fully persist the commit
    await asyncio.sleep(0.5)

    # Start skill as background task
    skill_task = asyncio.create_task(start_skill(skill_config_file))

    # Wait for skill to initialize and subscribe to all topics
    # This includes the device update topic listener
    await asyncio.sleep(3)

    # Trigger device load by publishing device update notification
    # The skill's device cache is only populated when it receives this notification
    mqtt_host = os.getenv("MQTT_HOST", "mosquitto")
    mqtt_port = int(os.getenv("MQTT_PORT", "1883"))
    async with aiomqtt.Client(hostname=mqtt_host, port=mqtt_port) as trigger_client:
        await trigger_client.publish("assistant/global_device_update", "", qos=1)

    # Wait for skill to process the device update and load devices
    await asyncio.sleep(2)

    yield

    # Cleanup: Cancel skill task
    skill_task.cancel()
    with contextlib.suppress(asyncio.CancelledError):
        await skill_task


@pytest.fixture
async def running_skill_multiple_scenes(skill_config_file, test_scene_devices_multiple):  # noqa: ARG001
    """Start the skill in background with multiple scene devices ready.

    Args:
        skill_config_file: Path to skill config
        test_scene_devices_multiple: Test scene devices that must be created before skill starts
    """
    # Devices are already created by test_scene_devices_multiple fixture
    # Give database time to fully persist the commit
    await asyncio.sleep(0.5)

    # Start skill as background task
    skill_task = asyncio.create_task(start_skill(skill_config_file))

    # Wait for skill to initialize and subscribe to all topics
    # This includes the device update topic listener
    await asyncio.sleep(3)

    # Trigger device load by publishing device update notification
    mqtt_host = os.getenv("MQTT_HOST", "mosquitto")
    mqtt_port = int(os.getenv("MQTT_PORT", "1883"))
    async with aiomqtt.Client(hostname=mqtt_host, port=mqtt_port) as trigger_client:
        await trigger_client.publish("assistant/global_device_update", "", qos=1)

    # Wait for skill to process the device update and load devices
    await asyncio.sleep(2)

    yield

    # Cleanup: Cancel skill task
    skill_task.cancel()
    with contextlib.suppress(asyncio.CancelledError):
        await skill_task


@pytest.fixture
async def running_skill(skill_config_file):
    """Start the skill in background without any test devices.

    Used for tests that don't need devices (e.g., error handling tests).
    """
    # Start skill as background task
    skill_task = asyncio.create_task(start_skill(skill_config_file))

    # Wait for skill to initialize and subscribe to topics
    await asyncio.sleep(3)

    yield

    # Cleanup: Cancel skill task
    skill_task.cancel()
    with contextlib.suppress(asyncio.CancelledError):
        await skill_task


class TestSceneApplyCommand:
    """Test scene activation commands (SCENE_APPLY)."""

    async def test_scene_apply_command(
        self,
        test_scene_device,
        test_room,
        running_skill_single_scene,  # noqa: ARG002
        mqtt_test_client,
    ):
        """Test that SCENE_APPLY intent triggers correct MQTT commands and response.

        Flow:
        1. Publish IntentRequest with SCENE_APPLY intent and scene name
        2. Assert all device commands published to correct topics with correct payloads
        3. Assert response published to output topic

        Note: Uses running_skill_single_scene fixture which ensures test_scene_device
        is created before the skill starts.
        """
        output_topic = f"test/output/{uuid.uuid4().hex}"
        device_actions = test_scene_device.device_attributes["device_actions"]
        expected_topics = [action["topic"] for action in device_actions]

        # Prepare IntentRequest
        scene_entity = Entity(
            id=uuid.uuid4(),
            type=EntityType.SCENE,
            raw_text="romantic",
            normalized_value="romantic",
            confidence=0.9,
            metadata={"device_type": "scene"},
            linked_to=[],
        )

        classified_intent = ClassifiedIntent(
            id=uuid.uuid4(),
            intent_type=IntentType.SCENE_APPLY,
            confidence=0.9,
            entities={"device": [scene_entity]},
            alternative_intents=[],
            raw_text="activate romantic scene",
            timestamp=datetime.now(),
        )

        client_request = ClientRequest(
            id=uuid.uuid4(),
            text="activate romantic scene",
            room=test_room.name,
            output_topic=output_topic,
        )

        intent_request = IntentRequest(
            id=uuid.uuid4(),
            classified_intent=classified_intent,
            client_request=client_request,
        )

        # Subscribe to all device topics and response topic
        for topic in expected_topics:
            await mqtt_test_client.subscribe(topic)
        await mqtt_test_client.subscribe(output_topic)

        # Publish IntentRequest
        await mqtt_test_client.publish(
            "assistant/intent_engine/result",
            intent_request.model_dump_json(),
            qos=1,
        )

        # Collect messages
        device_commands_received = {}
        response_received = False

        async with asyncio.timeout(10):
            async for message in mqtt_test_client.messages:
                topic = str(message.topic)
                payload = message.payload.decode()

                if topic in expected_topics:
                    device_commands_received[topic] = payload

                if topic == output_topic:
                    # Response should mention the scene
                    assert "romantic" in payload.lower()
                    response_received = True

                # Exit when all messages received
                if len(device_commands_received) == len(expected_topics) and response_received:
                    break

        # Verify all device commands were received with correct payloads
        assert len(device_commands_received) == len(expected_topics), (
            f"Expected {len(expected_topics)} device commands, got {len(device_commands_received)}"
        )

        for action in device_actions:
            assert action["topic"] in device_commands_received
            assert device_commands_received[action["topic"]] == action["payload"]

        assert response_received, "Response was not published"


class TestMultipleScenes:
    """Test activating multiple scenes."""

    async def test_multiple_scenes_in_room(
        self,
        test_scene_devices_multiple,
        test_room,
        running_skill_multiple_scenes,  # noqa: ARG002
        mqtt_test_client,
    ):
        """Test activating one specific scene when multiple scenes exist.

        Flow:
        1. Publish IntentRequest with SCENE_APPLY for a specific scene
        2. Assert only commands for that scene are published
        3. Assert response indicates the correct scene

        Note: Uses running_skill_multiple_scenes fixture which ensures test_scene_devices_multiple
        is created before the skill starts.
        """
        output_topic = f"test/output/{uuid.uuid4().hex}"

        # Target the "romantic" scene specifically
        target_scene = next(d for d in test_scene_devices_multiple if d.name == "romantic")
        device_actions = target_scene.device_attributes["device_actions"]
        expected_topics = [action["topic"] for action in device_actions]

        # Prepare IntentRequest
        scene_entity = Entity(
            id=uuid.uuid4(),
            type=EntityType.SCENE,
            raw_text="romantic",
            normalized_value="romantic",
            confidence=0.9,
            metadata={"device_type": "scene"},
            linked_to=[],
        )

        classified_intent = ClassifiedIntent(
            id=uuid.uuid4(),
            intent_type=IntentType.SCENE_APPLY,
            confidence=0.9,
            entities={"device": [scene_entity]},
            alternative_intents=[],
            raw_text="activate romantic scene",
            timestamp=datetime.now(),
        )

        client_request = ClientRequest(
            id=uuid.uuid4(),
            text="activate romantic scene",
            room=test_room.name,
            output_topic=output_topic,
        )

        intent_request = IntentRequest(
            id=uuid.uuid4(),
            classified_intent=classified_intent,
            client_request=client_request,
        )

        # Subscribe to all possible topics to ensure we only get the right ones
        await mqtt_test_client.subscribe("test/integration/multi/#")
        await mqtt_test_client.subscribe(output_topic)

        # Publish IntentRequest
        await mqtt_test_client.publish(
            "assistant/intent_engine/result",
            intent_request.model_dump_json(),
            qos=1,
        )

        # Collect messages
        device_commands_received = {}
        response_received = False

        async with asyncio.timeout(10):
            async for message in mqtt_test_client.messages:
                topic = str(message.topic)
                payload = message.payload.decode()

                if topic.startswith("test/integration/multi/") and topic != output_topic:
                    device_commands_received[topic] = payload

                if topic == output_topic:
                    # Response should mention romantic scene
                    assert "romantic" in payload.lower()
                    response_received = True

                # Exit when response received and we've gotten commands
                if response_received and len(device_commands_received) >= len(expected_topics):
                    await asyncio.sleep(0.5)  # Wait a bit more to ensure no extra commands
                    break

        # Verify only the romantic scene's commands were sent
        assert len(device_commands_received) == len(expected_topics), (
            f"Expected {len(expected_topics)} device commands for romantic scene, got {len(device_commands_received)}"
        )

        for action in device_actions:
            assert action["topic"] in device_commands_received
            assert device_commands_received[action["topic"]] == action["payload"]

        assert response_received, "Response was not published"


class TestSceneNotFound:
    """Test error handling when scene is not found."""

    async def test_scene_not_found(self, running_skill, mqtt_test_client, test_room):  # noqa: ARG002
        """Test that request for non-existent scene sends error response.

        Flow:
        1. Publish IntentRequest for SCENE_APPLY with non-existent scene
        2. Assert no device commands published
        3. Assert error response published
        """
        output_topic = f"test/output/{uuid.uuid4().hex}"

        # Prepare IntentRequest for non-existent scene
        scene_entity = Entity(
            id=uuid.uuid4(),
            type=EntityType.SCENE,
            raw_text="nonexistent",
            normalized_value="nonexistent",
            confidence=0.9,
            metadata={"device_type": "scene"},
            linked_to=[],
        )

        classified_intent = ClassifiedIntent(
            id=uuid.uuid4(),
            intent_type=IntentType.SCENE_APPLY,
            confidence=0.9,
            entities={"device": [scene_entity]},
            alternative_intents=[],
            raw_text="activate nonexistent scene",
            timestamp=datetime.now(),
        )

        client_request = ClientRequest(
            id=uuid.uuid4(),
            text="activate nonexistent scene",
            room=test_room.name,
            output_topic=output_topic,
        )

        intent_request = IntentRequest(
            id=uuid.uuid4(),
            classified_intent=classified_intent,
            client_request=client_request,
        )

        # Subscribe to response topic and a wildcard for any device commands
        await mqtt_test_client.subscribe(output_topic)
        await mqtt_test_client.subscribe("test/integration/#")

        # Publish IntentRequest
        await mqtt_test_client.publish(
            "assistant/intent_engine/result",
            intent_request.model_dump_json(),
            qos=1,
        )

        # Collect messages
        device_command_received = False
        response_received = False
        response_payload = None

        async with asyncio.timeout(10):
            async for message in mqtt_test_client.messages:
                topic = str(message.topic)
                payload = message.payload.decode()

                # Check if any device command was sent
                if topic.startswith("test/integration/") and topic != output_topic:
                    device_command_received = True

                if topic == output_topic:
                    response_payload = payload
                    response_received = True
                    break  # Got response, can exit

        assert not device_command_received, "Device command should not be published for non-existent scene"
        assert response_received, "Error response should be published"
        # Response should indicate scene not found or similar error
        assert response_payload is not None
        assert "couldn't find" in response_payload.lower() or "not found" in response_payload.lower()


class TestSystemHelp:
    """Test SYSTEM_HELP intent."""

    async def test_system_help(self, running_skill, mqtt_test_client, test_room):  # noqa: ARG002
        """Test that SYSTEM_HELP intent sends help response.

        Flow:
        1. Publish IntentRequest with SYSTEM_HELP intent
        2. Assert help response published to output topic
        3. Assert no device commands published
        """
        output_topic = f"test/output/{uuid.uuid4().hex}"

        # Prepare IntentRequest
        classified_intent = ClassifiedIntent(
            id=uuid.uuid4(),
            intent_type=IntentType.SYSTEM_HELP,
            confidence=0.9,
            entities={},
            alternative_intents=[],
            raw_text="help with scenes",
            timestamp=datetime.now(),
        )

        client_request = ClientRequest(
            id=uuid.uuid4(),
            text="help with scenes",
            room=test_room.name,
            output_topic=output_topic,
        )

        intent_request = IntentRequest(
            id=uuid.uuid4(),
            classified_intent=classified_intent,
            client_request=client_request,
        )

        # Subscribe to response topic
        await mqtt_test_client.subscribe(output_topic)

        # Publish IntentRequest
        await mqtt_test_client.publish(
            "assistant/intent_engine/result",
            intent_request.model_dump_json(),
            qos=1,
        )

        # Collect messages
        response_received = False
        response_payload = None

        async with asyncio.timeout(10):
            async for message in mqtt_test_client.messages:
                topic = str(message.topic)
                payload = message.payload.decode()

                if topic == output_topic:
                    response_payload = payload
                    response_received = True
                    break  # Got response, can exit

        assert response_received, "Help response should be published"
        assert response_payload is not None
        # Response should contain help information about scenes
        assert len(response_payload) > 0
        assert "scene" in response_payload.lower()
