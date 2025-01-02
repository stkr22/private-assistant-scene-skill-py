from unittest.mock import AsyncMock, Mock

import jinja2
import pytest
from private_assistant_commons import messages
from sqlalchemy.ext.asyncio import create_async_engine
from sqlmodel import SQLModel

from private_assistant_scene_skill.models import SceneSkillDevices
from private_assistant_scene_skill.scene_skill import Action, Parameters, SceneSkill


@pytest.fixture
async def db_engine():
    engine = create_async_engine(
        "sqlite+aiosqlite:///:memory:",
        echo=False,
        future=True,
    )
    async with engine.begin() as conn:
        await conn.run_sync(SQLModel.metadata.create_all)

    return engine


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
    return AsyncMock()


@pytest.fixture
def mock_logger():
    return Mock()


@pytest.fixture
async def scene_skill(
    mock_mqtt_client,
    mock_template_env,
    mock_task_group,
    mock_logger,
    db_engine,
):
    skill = SceneSkill(
        config_obj=Mock(),
        mqtt_client=mock_mqtt_client,
        db_engine=db_engine,
        template_env=mock_template_env,
        task_group=mock_task_group,
        logger=mock_logger,
    )
    await skill.skill_preparations()
    return skill


@pytest.mark.asyncio
async def test_calculate_certainty_with_scenery(scene_skill):
    mock_intent_result = Mock(spec=messages.IntentAnalysisResult, nouns=["scenery"])
    assert await scene_skill.calculate_certainty(mock_intent_result) == 1.0


@pytest.mark.asyncio
async def test_calculate_certainty_without_scenery(scene_skill):
    mock_intent_result = Mock(spec=messages.IntentAnalysisResult, nouns=["lights"])
    assert await scene_skill.calculate_certainty(mock_intent_result) == 0


@pytest.mark.asyncio
async def test_find_parameter_scenes(scene_skill):
    scene_skill._scene_cache = {
        "romantic": [SceneSkillDevices(topic="light/1", scene_payload="ON")],
        "morning": [SceneSkillDevices(topic="light/2", scene_payload="ON")],
    }
    names, devices = scene_skill.find_parameter_scenes(["romantic", "morning"])
    assert names == ["romantic", "morning"]
    assert len(devices) == 2
    assert all(isinstance(d, SceneSkillDevices) for d in devices)


@pytest.mark.asyncio
async def test_get_answer(scene_skill):
    mock_template = Mock()
    mock_template.render.return_value = "Setting scene romantic"
    scene_skill.action_to_template = {Action.LIST: mock_template, Action.APPLY: mock_template}

    parameters = Parameters(scene_names=["romantic"])
    answer = scene_skill.get_answer(Action.APPLY, parameters)

    assert answer == "Setting scene romantic"
    mock_template.render.assert_called_once_with(action=Action.APPLY, parameters=parameters)


@pytest.mark.asyncio
async def test_send_mqtt_command(scene_skill, mock_mqtt_client):
    devices = [
        SceneSkillDevices(topic="light/1", scene_payload="ON"),
        SceneSkillDevices(topic="light/2", scene_payload="OFF"),
    ]
    parameters = Parameters(scene_names=["test"], devices=devices)

    await scene_skill.send_mqtt_command(parameters)

    assert mock_mqtt_client.publish.await_count == 2
    mock_mqtt_client.publish.assert_any_await("light/1", "ON", qos=1)
    mock_mqtt_client.publish.assert_any_await("light/2", "OFF", qos=1)


@pytest.mark.asyncio
async def test_process_request_with_valid_action(scene_skill, monkeypatch):
    mock_client_request = Mock(room="living")
    mock_intent_result = Mock(
        spec=messages.IntentAnalysisResult,
        verbs=["apply"],
        nouns=["romantic"],
        client_request=mock_client_request,
    )

    scene_skill._scene_cache = {"romantic": [SceneSkillDevices(topic="light/1", scene_payload="ON")]}

    monkeypatch.setattr(scene_skill, "send_response", AsyncMock())
    monkeypatch.setattr(scene_skill, "send_mqtt_command", AsyncMock())

    await scene_skill.process_request(mock_intent_result)

    scene_skill.send_response.assert_called_once()
    scene_skill.send_mqtt_command.assert_called_once()
