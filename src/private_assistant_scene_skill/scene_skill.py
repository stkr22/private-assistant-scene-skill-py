import logging
from enum import Enum

import homeassistant_api as ha_api
import jinja2
import paho.mqtt.client as mqtt
import private_assistant_commons as commons
import spacy
from pydantic import BaseModel

from private_assistant_scene_skill import config

logger = logging.getLogger(__name__)


class Parameters(BaseModel):
    targets: list[str] = []


class Action(Enum):
    HELP = "help"
    LIST = "list"
    APPLY = "apply"

    @classmethod
    def find_matching_action(cls, text):
        for action in cls:
            if action.value in text.lower():
                return action
        return None


class SceneSkill(commons.BaseSkill):
    def __init__(
        self,
        config_obj: config.SkillConfig,
        mqtt_client: mqtt.Client,
        nlp_model: spacy.Language,
        ha_api_client: ha_api.Client,
        template_env: jinja2.Environment,
    ) -> None:
        super().__init__(config_obj, mqtt_client, nlp_model)
        self.ha_api_client: ha_api.Client = ha_api_client
        self.template_env: jinja2.Environment = template_env
        self.action_to_answer: dict[Action, str] = {
            Action.HELP: "help.j2",
            Action.LIST: "list.j2",
            Action.APPLY: "apply.j2",
        }
        self._target_cache: dict[str, ha_api.State] = {}
        self._target_alias_cache: dict[str, str] = {}

    @property
    def target_cache(self) -> dict[str, ha_api.State]:
        if len(self._target_cache) < 1:
            self._target_cache = self.get_targets()
        return self._target_cache

    @property
    def target_alias_cache(self) -> dict[str, str]:
        if len(self._target_alias_cache) < 1:
            for target in self.target_cache.values():
                alias = target.attributes.get("friendly_name", "no name").lower()
                self._target_alias_cache[target.entity_id] = alias
        return self._target_alias_cache

    def calculate_certainty(self, doc: spacy.language.Doc) -> float:
        for token in doc:
            if token.lemma_.lower() in ["scene", "scenery"]:
                return 1.0
        return 0

    def get_targets(self) -> dict[str, ha_api.State]:
        entity_groups = self.ha_api_client.get_entities()
        room_entities = {
            entity_name: entity.state
            for entity_name, entity in entity_groups["scene"].entities.items()
        }
        return room_entities

    def find_parameters(self, action: Action, text: str) -> Parameters:
        parameters = Parameters()
        if action == Action.LIST:
            parameters.targets = list(self.target_alias_cache.keys())
        if action == Action.APPLY:
            found_scenes = self.find_parameter_targets(text=text)
            if len(found_scenes) > 0:
                parameters.targets = found_scenes
        return parameters

    def find_parameter_targets(self, text: str) -> list[str]:
        targets = []
        for target, alias in self.target_alias_cache.items():
            if alias in text.lower():
                targets.append(target)
        return targets

    def get_answer(self, action: Action, parameters: Parameters) -> str:
        template = self.template_env.get_template(self.action_to_answer[action])
        answer = template.render(
            action=action,
            parameters=parameters,
            target_alias_cache=self.target_alias_cache,
        )
        return answer

    def call_action_api(self, action: Action, parameters: Parameters) -> None:
        service = self.ha_api_client.get_domain("scene")
        if service is None:
            logger.error("Service is None.")
        else:
            for target in parameters.targets:
                if action == Action.APPLY:
                    service.turn_on(entity_id=target)

    def process_request(self, client_request: commons.ClientRequest) -> None:
        action = Action.find_matching_action(client_request.text)
        parameters = None
        if action is not None:
            parameters = self.find_parameters(action, text=client_request.text)
        if parameters is not None and action is not None:
            answer = self.get_answer(action, parameters)
            self.add_text_to_output_topic(answer, client_request=client_request)
            if action not in [Action.HELP, Action.LIST]:
                self.call_action_api(action, parameters)
