from unittest.mock import AsyncMock, Mock

import jinja2
import pytest
from homeassistant_api import Entity, Group, State
from private_assistant_commons import messages

from private_assistant_scene_skill.scene_skill import Action, Parameters, SceneSkill


@pytest.fixture
def mock_mqtt_client():
    return AsyncMock()


@pytest.fixture
def mock_ha_api_client():
    return AsyncMock()


@pytest.fixture
def mock_template_env():
    return Mock(spec=jinja2.Environment)


@pytest.fixture
def mock_task_group():
    return AsyncMock()


@pytest.fixture
def mock_logger():
    return Mock()


@pytest.fixture
async def scene_skill(
    mock_mqtt_client,
    mock_ha_api_client,
    mock_template_env,
    mock_task_group,
    mock_logger,
):
    skill = SceneSkill(
        config_obj=Mock(),
        mqtt_client=mock_mqtt_client,
        ha_api_client=mock_ha_api_client,
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
async def test_get_targets(scene_skill, mock_ha_api_client):
    mock_state = Mock(spec=State, state="on", attributes={"friendly_name": "Romantic Evening"})
    mock_entity = Mock(spec=Entity, state=mock_state)
    mock_group = Mock(spec=Group, entities={"entity_id_1": mock_entity})

    mock_ha_api_client.async_get_entities.return_value = {"scene": mock_group}
    targets = await scene_skill.get_targets()

    assert "entity_id_1" in targets
    assert targets["entity_id_1"] == mock_state


@pytest.mark.asyncio
async def test_find_parameter_targets(scene_skill):
    scene_skill._target_alias_cache = {
        "romantic_evening": "romantic evening",
        "morning_routine": "morning routine",
        "night_mode": "night mode",
    }
    assert await scene_skill.find_parameter_targets(["romantic", "morning"]) == [
        "romantic_evening",
        "morning_routine",
    ]


@pytest.mark.asyncio
async def test_get_answer(scene_skill):
    mock_template = Mock()
    mock_template.render.return_value = "Setting the scene to Romantic Evening"
    scene_skill.action_to_template = {Action.LIST: mock_template, Action.APPLY: mock_template}

    mock_parameters = Parameters(targets=["romantic_evening"])
    answer = scene_skill.get_answer(Action.APPLY, mock_parameters)

    assert answer == "Setting the scene to Romantic Evening"
    mock_template.render.assert_called_once_with(
        action=Action.APPLY,
        parameters=mock_parameters,
        target_alias_cache=scene_skill._target_alias_cache,
    )


@pytest.mark.asyncio
async def test_call_action_api(scene_skill, mock_ha_api_client):
    mock_domain = AsyncMock()
    mock_ha_api_client.async_get_domain.return_value = mock_domain

    parameters = Parameters(targets=["romantic_evening"])
    await scene_skill.call_action_api(Action.APPLY, parameters)

    mock_ha_api_client.async_get_domain.assert_awaited_once_with("scene")
    mock_domain.turn_on.assert_awaited_once_with(entity_id="romantic_evening")


@pytest.mark.asyncio
async def test_process_request_with_valid_action(scene_skill, monkeypatch):
    mock_client_request = Mock(room="living room")
    mock_intent_result = Mock(
        spec=messages.IntentAnalysisResult,
        verbs=["apply"],
        nouns=["romantic"],
        client_request=mock_client_request,
    )

    monkeypatch.setattr(
        scene_skill,
        "get_answer",
        Mock(return_value="Setting the scene to Romantic Evening"),
    )
    monkeypatch.setattr(
        scene_skill,
        "call_action_api",
        AsyncMock(),
    )
    monkeypatch.setattr(
        scene_skill,
        "find_parameter_targets",
        AsyncMock(return_value=["romantic_evening"]),
    )
    monkeypatch.setattr(
        scene_skill,
        "send_response",
        AsyncMock(),
    )

    await scene_skill.process_request(mock_intent_result)

    mock_parameters = Parameters(targets=["romantic_evening"])
    scene_skill.get_answer.assert_called_once_with(Action.APPLY, mock_parameters)
    scene_skill.call_action_api.assert_called_once_with(Action.APPLY, mock_parameters)
    scene_skill.find_parameter_targets.assert_awaited_once_with(["romantic"])
    scene_skill.send_response.assert_called_once_with(
        "Setting the scene to Romantic Evening",
        client_request=mock_client_request,
    )
