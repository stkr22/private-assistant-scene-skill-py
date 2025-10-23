from unittest.mock import AsyncMock, Mock
from uuid import uuid4

import jinja2
import pytest
from private_assistant_commons import IntentRequest, IntentType

from private_assistant_scene_skill.models import SceneDevice
from private_assistant_scene_skill.scene_skill import Parameters, SceneSkill


@pytest.fixture
def mock_db_engine():
    """Mock database engine for unit tests."""
    return Mock()


@pytest.fixture
def mock_mqtt_client():
    return AsyncMock()


@pytest.fixture
def mock_template_env():
    env = Mock(spec=jinja2.Environment)
    template = Mock()
    template.render.return_value = "Test response"
    env.get_template.return_value = template
    return env


@pytest.fixture
def mock_task_group():
    """Mock task group that returns proper Task-like objects."""
    task_group = AsyncMock()
    # Mock create_task to return a Mock with add_done_callback
    def create_mock_task(coro, name=None):
        mock_task = Mock()
        mock_task.add_done_callback = Mock()
        return mock_task
    task_group.create_task = Mock(side_effect=create_mock_task)
    return task_group


@pytest.fixture
def mock_logger():
    return Mock()


@pytest.fixture
async def scene_skill(
    mock_mqtt_client,
    mock_template_env,
    mock_task_group,
    mock_logger,
    mock_db_engine,
):
    mock_config = Mock()
    mock_config.client_id = "test-scene-skill"

    skill = SceneSkill(
        config_obj=mock_config,
        mqtt_client=mock_mqtt_client,
        db_engine=mock_db_engine,
        template_env=mock_template_env,
        task_group=mock_task_group,
        logger=mock_logger,
    )
    # Mock skill_preparations to avoid database calls
    skill.global_devices = []
    skill._global_skill_id = None
    return skill


@pytest.mark.asyncio
async def test_scene_device_from_global_device():
    """Test creating SceneDevice from global device registry entry."""
    mock_room = Mock()
    mock_room.name = "living room"

    mock_global_device = Mock()
    mock_global_device.name = "romantic"
    mock_global_device.room = mock_room
    mock_global_device.device_attributes = {
        "device_actions": [
            {"topic": "light/1", "payload": "ON"},
            {"topic": "light/2", "payload": "50"},
        ]
    }

    scene_device = SceneDevice.from_global_device(mock_global_device)

    assert scene_device.name == "romantic"
    assert scene_device.room == "living room"
    assert len(scene_device.device_actions) == 2  # noqa: PLR2004
    assert scene_device.device_actions[0]["topic"] == "light/1"
    assert scene_device.device_actions[0]["payload"] == "ON"


@pytest.mark.asyncio
async def test_get_scenes_filters_by_name(scene_skill):
    """Test that get_scenes properly filters by scene name."""
    # Mock global devices
    mock_room = Mock()
    mock_room.name = "living room"

    mock_device1 = Mock()
    mock_device1.name = "romantic"
    mock_device1.room = mock_room
    mock_device1.device_attributes = {"device_actions": [{"topic": "light/1", "payload": "ON"}]}

    mock_device2 = Mock()
    mock_device2.name = "morning"
    mock_device2.room = mock_room
    mock_device2.device_attributes = {"device_actions": [{"topic": "light/2", "payload": "ON"}]}

    scene_skill.global_devices = [mock_device1, mock_device2]

    # Test filtering by name
    scenes = await scene_skill.get_scenes(scene_names=["romantic"])

    assert len(scenes) == 1
    assert scenes[0].name == "romantic"


@pytest.mark.asyncio
async def test_get_scenes_filters_by_room(scene_skill):
    """Test that get_scenes properly filters by room."""
    mock_room1 = Mock()
    mock_room1.name = "living room"

    mock_room2 = Mock()
    mock_room2.name = "bedroom"

    mock_device1 = Mock()
    mock_device1.name = "romantic"
    mock_device1.room = mock_room1
    mock_device1.device_attributes = {"device_actions": [{"topic": "light/1", "payload": "ON"}]}

    mock_device2 = Mock()
    mock_device2.name = "sleep"
    mock_device2.room = mock_room2
    mock_device2.device_attributes = {"device_actions": [{"topic": "light/2", "payload": "OFF"}]}

    scene_skill.global_devices = [mock_device1, mock_device2]

    # Test filtering by room
    scenes = await scene_skill.get_scenes(rooms=["bedroom"])

    assert len(scenes) == 1
    assert scenes[0].name == "sleep"
    assert scenes[0].room == "bedroom"


@pytest.mark.asyncio
async def test_render_response(scene_skill):
    """Test response rendering with template."""
    mock_template = Mock()
    mock_template.render.return_value = "The scene romantic has been applied affecting 2 devices."
    scene_skill.intent_to_template = {IntentType.SCENE_APPLY: mock_template}

    scene_device = SceneDevice(
        name="romantic",
        room="living room",
        device_actions=[
            {"topic": "light/1", "payload": "ON"},
            {"topic": "light/2", "payload": "50"},
        ],
    )

    parameters = Parameters(scene_names=["romantic"], targets=[scene_device])
    answer = scene_skill._render_response(IntentType.SCENE_APPLY, parameters)

    assert answer == "The scene romantic has been applied affecting 2 devices."
    mock_template.render.assert_called_once()


@pytest.mark.asyncio
async def test_send_mqtt_commands(scene_skill, mock_mqtt_client):
    """Test sending MQTT commands for scene activation."""
    scene_device = SceneDevice(
        name="romantic",
        room="living room",
        device_actions=[
            {"topic": "light/1", "payload": "ON"},
            {"topic": "light/2", "payload": "50"},
        ],
    )

    parameters = Parameters(scene_names=["romantic"], targets=[scene_device])

    await scene_skill._send_mqtt_commands(parameters)

    assert mock_mqtt_client.publish.await_count == 2  # noqa: PLR2004
    mock_mqtt_client.publish.assert_any_await("light/1", "ON", qos=1)
    mock_mqtt_client.publish.assert_any_await("light/2", "50", qos=1)


@pytest.mark.asyncio
async def test_handle_scene_apply_success(scene_skill, monkeypatch):
    """Test successful SCENE_APPLY intent handling."""
    # Mock classified intent and client request
    mock_entity = Mock()
    mock_entity.normalized_value = "romantic"

    mock_classified_intent = Mock()
    mock_classified_intent.intent_type = IntentType.SCENE_APPLY
    mock_classified_intent.confidence = 0.9
    mock_classified_intent.entities = {"scenes": [mock_entity], "rooms": []}

    mock_client_request = Mock()
    mock_client_request.room = "living room"

    mock_intent_request = Mock(spec=IntentRequest)
    mock_intent_request.id = uuid4()
    mock_intent_request.classified_intent = mock_classified_intent
    mock_intent_request.client_request = mock_client_request

    # Mock global device
    mock_room = Mock()
    mock_room.name = "living room"

    mock_device = Mock()
    mock_device.name = "romantic"
    mock_device.room = mock_room
    mock_device.device_attributes = {"device_actions": [{"topic": "light/1", "payload": "ON"}]}

    scene_skill.global_devices = [mock_device]

    # Mock methods
    monkeypatch.setattr(scene_skill, "send_response", AsyncMock())
    monkeypatch.setattr(scene_skill, "_send_mqtt_commands", AsyncMock())
    monkeypatch.setattr(scene_skill, "_render_response", Mock(return_value="Scene applied"))

    await scene_skill._handle_scene_apply(mock_intent_request)

    scene_skill.send_response.assert_called_once()
    scene_skill._send_mqtt_commands.assert_called_once()


@pytest.mark.asyncio
async def test_handle_scene_apply_not_found(scene_skill, monkeypatch):
    """Test SCENE_APPLY intent when scene is not found."""
    # Mock classified intent with non-existent scene
    mock_entity = Mock()
    mock_entity.normalized_value = "nonexistent"

    mock_classified_intent = Mock()
    mock_classified_intent.intent_type = IntentType.SCENE_APPLY
    mock_classified_intent.confidence = 0.9
    mock_classified_intent.entities = {"scenes": [mock_entity], "rooms": []}

    mock_client_request = Mock()
    mock_client_request.room = "living room"

    mock_intent_request = Mock(spec=IntentRequest)
    mock_intent_request.id = uuid4()
    mock_intent_request.classified_intent = mock_classified_intent
    mock_intent_request.client_request = mock_client_request

    scene_skill.global_devices = []

    # Mock send_response
    monkeypatch.setattr(scene_skill, "send_response", AsyncMock())

    await scene_skill._handle_scene_apply(mock_intent_request)

    # Should send error message
    scene_skill.send_response.assert_called_once()
    call_args = scene_skill.send_response.call_args
    assert "couldn't find" in call_args[0][0].lower()


@pytest.mark.asyncio
async def test_handle_system_help(scene_skill, monkeypatch):
    """Test SYSTEM_HELP intent handling."""
    mock_classified_intent = Mock()
    mock_classified_intent.intent_type = IntentType.SYSTEM_HELP
    mock_classified_intent.confidence = 0.9

    mock_client_request = Mock()
    mock_client_request.room = "living room"

    mock_intent_request = Mock(spec=IntentRequest)
    mock_intent_request.id = uuid4()
    mock_intent_request.classified_intent = mock_classified_intent
    mock_intent_request.client_request = mock_client_request

    # Mock methods
    monkeypatch.setattr(scene_skill, "send_response", AsyncMock())
    monkeypatch.setattr(scene_skill, "_render_response", Mock(return_value="Help text"))

    await scene_skill._handle_system_help(mock_intent_request)

    scene_skill.send_response.assert_called_once()


@pytest.mark.asyncio
async def test_process_request_routes_to_scene_apply(scene_skill, monkeypatch):
    """Test that process_request routes SCENE_APPLY to correct handler."""
    mock_classified_intent = Mock()
    mock_classified_intent.intent_type = IntentType.SCENE_APPLY
    mock_classified_intent.confidence = 0.9

    mock_client_request = Mock()
    mock_client_request.room = "living room"

    mock_intent_request = Mock(spec=IntentRequest)
    mock_intent_request.id = uuid4()
    mock_intent_request.classified_intent = mock_classified_intent
    mock_intent_request.client_request = mock_client_request

    # Mock handler
    monkeypatch.setattr(scene_skill, "_handle_scene_apply", AsyncMock())

    await scene_skill.process_request(mock_intent_request)

    scene_skill._handle_scene_apply.assert_called_once_with(mock_intent_request)


@pytest.mark.asyncio
async def test_process_request_routes_to_help(scene_skill, monkeypatch):
    """Test that process_request routes SYSTEM_HELP to correct handler."""
    mock_classified_intent = Mock()
    mock_classified_intent.intent_type = IntentType.SYSTEM_HELP
    mock_classified_intent.confidence = 0.9

    mock_client_request = Mock()
    mock_client_request.room = "living room"

    mock_intent_request = Mock(spec=IntentRequest)
    mock_intent_request.id = uuid4()
    mock_intent_request.classified_intent = mock_classified_intent
    mock_intent_request.client_request = mock_client_request

    # Mock handler
    monkeypatch.setattr(scene_skill, "_handle_system_help", AsyncMock())

    await scene_skill.process_request(mock_intent_request)

    scene_skill._handle_system_help.assert_called_once_with(mock_intent_request)
