import unittest
from unittest.mock import Mock, patch

import jinja2
from homeassistant_api import Entity, Group, State
from private_assistant_commons import messages
from private_assistant_scene_skill.scene_skill import Action, Parameters, SceneSkill


class TestSceneSkill(unittest.TestCase):
    def setUp(self):
        self.mock_mqtt_client = Mock()
        self.mock_config = Mock()
        self.mock_ha_api_client = Mock()
        self.mock_template_env = Mock(spec=jinja2.Environment)

        self.skill = SceneSkill(
            config_obj=self.mock_config,
            mqtt_client=self.mock_mqtt_client,
            ha_api_client=self.mock_ha_api_client,
            template_env=self.mock_template_env,
        )

    def test_calculate_certainty_with_scenery(self):
        mock_intent_result = Mock(spec=messages.IntentAnalysisResult)
        mock_intent_result.nouns = ["scenery"]
        certainty = self.skill.calculate_certainty(mock_intent_result)
        self.assertEqual(certainty, 1.0)

    def test_calculate_certainty_without_scenery(self):
        mock_intent_result = Mock(spec=messages.IntentAnalysisResult)
        mock_intent_result.nouns = ["lights"]
        certainty = self.skill.calculate_certainty(mock_intent_result)
        self.assertEqual(certainty, 0)

    def test_get_targets(self):
        mock_state = Mock(spec=State)
        mock_state.state = "on"
        mock_state.attributes = {"friendly_name": "Romantic Evening"}

        mock_entity = Mock(spec=Entity)
        mock_entity.state = mock_state

        mock_group = Mock(spec=Group)
        mock_group.entities = {"entity_id_1": mock_entity}

        self.skill.ha_api_client.get_entities.return_value = {"scene": mock_group}

        targets = self.skill.get_targets()

        self.assertIn("entity_id_1", targets)
        self.assertEqual(targets["entity_id_1"], mock_state)

    def test_find_parameter_targets(self):
        self.skill._target_alias_cache = {
            "romantic_evening": "romantic evening",
            "morning_routine": "morning routine",
            "night_mode": "night mode",
        }
        targets = self.skill.find_parameter_targets(["romantic", "morning"])
        self.assertEqual(targets, ["romantic_evening", "morning_routine"])

    def test_get_answer(self):
        mock_template = Mock()
        mock_template.render.return_value = "Setting the scene to Romantic Evening"
        self.skill.action_to_answer = {Action.LIST: mock_template, Action.APPLY: mock_template}

        mock_state = Mock(spec=State)
        mock_state.entity_id = "romantic_evening"
        mock_state.state = "on"
        mock_state.attributes = {"friendly_name": "Romantic Evening"}

        mock_entity = Mock(spec=Entity)
        mock_entity.state = mock_state

        mock_group = Mock(spec=Group)
        mock_group.entities = {"entity_id_1": mock_entity}

        self.skill.ha_api_client.get_entities.return_value = {"scene": mock_group}

        _ = self.skill.target_alias_cache

        mock_parameters = Parameters(targets=["romantic_evening"])
        answer = self.skill.get_answer(Action.APPLY, mock_parameters)
        self.assertEqual(answer, "Setting the scene to Romantic Evening")
        mock_template.render.assert_called_once_with(
            action=Action.APPLY, parameters=mock_parameters, target_alias_cache=self.skill.target_alias_cache
        )

    @patch("private_assistant_scene_skill.scene_skill.logger")
    def test_call_action_api(self, mock_logger):
        mock_service = Mock()
        self.skill.ha_api_client.get_domain.return_value = mock_service

        parameters = Parameters(targets=["romantic_evening"])
        self.skill.call_action_api(Action.APPLY, parameters)

        mock_service.turn_on.assert_called_once_with(entity_id="romantic_evening")
        mock_logger.error.assert_not_called()

    def test_process_request_with_valid_action(self):
        mock_client_request = Mock()
        mock_client_request.room = "living room"

        mock_intent_result = Mock(spec=messages.IntentAnalysisResult)
        mock_intent_result.verbs = ["apply"]
        mock_intent_result.nouns = ["romantic"]
        mock_intent_result.client_request = mock_client_request

        mock_parameters = Parameters(targets=["romantic_evening"])

        with (
            patch.object(
                self.skill, "get_answer", return_value="Setting the scene to Romantic Evening"
            ) as mock_get_answer,
            patch.object(self.skill, "call_action_api") as mock_call_action_api,
            patch.object(self.skill, "find_parameter_targets", return_value=["romantic_evening"]),
            patch.object(self.skill, "add_text_to_output_topic") as mock_add_text_to_output_topic,
        ):
            self.skill.process_request(mock_intent_result)

            mock_get_answer.assert_called_once_with(Action.APPLY, mock_parameters)
            mock_call_action_api.assert_called_once_with(Action.APPLY, mock_parameters)
            mock_add_text_to_output_topic.assert_called_once_with(
                "Setting the scene to Romantic Evening", client_request=mock_intent_result.client_request
            )
