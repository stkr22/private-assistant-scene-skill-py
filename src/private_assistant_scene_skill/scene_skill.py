import asyncio
from enum import Enum

import aiomqtt
import homeassistant_api as ha_api
import jinja2
import private_assistant_commons as commons
from pydantic import BaseModel

from private_assistant_scene_skill import config


class Parameters(BaseModel):
    targets: list[str] = []


class Action(Enum):
    HELP = "help"
    LIST = "list"
    APPLY = "apply"

    @classmethod
    def find_matching_action(cls, verbs: list):
        for action in cls:
            if action.value in verbs:
                return action
        return None


class SceneSkill(commons.BaseSkill):
    def __init__(
        self,
        config_obj: config.SkillConfig,
        mqtt_client: aiomqtt.Client,
        ha_api_client: ha_api.Client,
        template_env: jinja2.Environment,
        task_group: asyncio.TaskGroup,
        logger,
    ) -> None:
        super().__init__(config_obj, mqtt_client, task_group, logger=logger)
        self.ha_api_client: ha_api.Client = ha_api_client
        self.template_env: jinja2.Environment = template_env
        self.action_to_answer: dict[Action, jinja2.Template] = {}

        # Preload templates
        try:
            self.action_to_answer[Action.HELP] = self.template_env.get_template("help.j2")
            self.action_to_answer[Action.LIST] = self.template_env.get_template("list.j2")
            self.action_to_answer[Action.APPLY] = self.template_env.get_template("apply.j2")
            self.logger.debug("Templates successfully loaded during initialization.")
        except jinja2.TemplateNotFound as e:
            self.logger.error("Failed to load template: %s", e)

        self._target_cache: dict[str, ha_api.State] = {}
        self._target_alias_cache: dict[str, str] = {}

    async def load_target_cache(self) -> None:
        """Asynchronously load targets from the Home Assistant API."""
        if len(self._target_cache) < 1:
            self.logger.debug("Fetching targets from Home Assistant API.")
            entity_groups = await self.ha_api_client.async_get_entities()
            self._target_cache = {
                entity_name: entity.state for entity_name, entity in entity_groups["scene"].entities.items()
            }
            self.logger.debug("Retrieved %d scene entities from Home Assistant.", len(self._target_cache))

    async def get_targets(self) -> dict[str, ha_api.State]:
        if len(self._target_cache) < 1:
            await self.load_target_cache()
        return self._target_cache

    async def load_target_alias_cache(self) -> None:
        """Asynchronously build alias cache for targets."""
        if len(self._target_alias_cache) < 1:
            self.logger.debug("Building target alias cache.")
            await self.load_target_cache()
            for target in self._target_cache.values():
                alias = target.attributes.get("friendly_name", "no name").lower()
                self._target_alias_cache[target.entity_id] = alias

    async def get_target_alias_cache(self) -> dict[str, str]:
        if len(self._target_alias_cache) < 1:
            await self.load_target_alias_cache()
        return self._target_alias_cache

    async def calculate_certainty(self, intent_analysis_result: commons.IntentAnalysisResult) -> float:
        if "scenery" in intent_analysis_result.nouns:
            self.logger.debug("Scenery noun detected, certainty set to 1.0.")
            return 1.0
        self.logger.debug("No scenery noun detected, certainty set to 0.")
        return 0

    async def find_parameter_targets(self, nouns: list[str]) -> list[str]:
        await self.load_target_alias_cache()
        targets = [target for target, alias in self._target_alias_cache.items() if any(noun in alias for noun in nouns)]
        self.logger.debug("Found %d targets matching nouns '%s'.", len(targets), nouns)
        return targets

    async def find_parameters(self, action: Action, intent_analysis_result: commons.IntentAnalysisResult) -> Parameters:
        parameters = Parameters()
        if action == Action.LIST:
            parameters.targets = list((await self.get_target_alias_cache()).keys())
        elif action == Action.APPLY:
            found_scenes = await self.find_parameter_targets(intent_analysis_result.nouns)
            if found_scenes:
                parameters.targets = found_scenes
        self.logger.debug("Parameters found for action %s: %s.", action, parameters)
        return parameters

    def get_answer(self, action: Action, parameters: Parameters) -> str:
        template = self.action_to_answer.get(action)
        if template:
            answer = template.render(
                action=action,
                parameters=parameters,
                target_alias_cache=self._target_alias_cache,
            )
            self.logger.debug("Generated answer using template for action %s.", action)
            return answer
        else:
            self.logger.error("No template found for action %s.", action)
            return "Sorry, I couldn't process your request."

    async def call_action_api(self, action: Action, parameters: Parameters) -> None:
        """Call the appropriate action in Home Assistant API asynchronously."""
        service = await self.ha_api_client.async_get_domain("scene")
        if service is None:
            self.logger.error("Failed to retrieve the scene service from Home Assistant API.")
        else:
            for target in parameters.targets:
                if action == Action.APPLY:
                    self.logger.debug("Applying scene for target '%s'.", target)
                    await service.async_turn_on(entity_id=target)

    async def process_request(self, intent_analysis_result: commons.IntentAnalysisResult) -> None:
        action = Action.find_matching_action(intent_analysis_result.verbs)
        if action is None:
            self.logger.error("Unrecognized action in verbs: %s", intent_analysis_result.verbs)
            return

        parameters = await self.find_parameters(action, intent_analysis_result=intent_analysis_result)
        if parameters.targets:
            answer = self.get_answer(action, parameters)
            self.add_task(self.add_text_to_output_topic(answer, client_request=intent_analysis_result.client_request))
            if action not in [Action.HELP, Action.LIST]:
                self.add_task(self.call_action_api(action, parameters))
        else:
            self.logger.error("No targets found for action.")
