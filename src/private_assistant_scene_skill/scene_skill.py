import asyncio
from enum import Enum

import aiomqtt
import jinja2
import private_assistant_commons as commons
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncEngine
from sqlalchemy.orm import selectinload
from sqlmodel import select
from sqlmodel.ext.asyncio.session import AsyncSession

from private_assistant_scene_skill.models import SceneSkillDevices, SceneSkillScenes


class Parameters(BaseModel):
    scene_names: list[str] = []
    devices: list[SceneSkillDevices] = []


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
        config_obj: commons.SkillConfig,
        mqtt_client: aiomqtt.Client,
        db_engine: AsyncEngine,
        template_env: jinja2.Environment,
        task_group: asyncio.TaskGroup,
        logger,
    ) -> None:
        super().__init__(config_obj, mqtt_client, task_group, logger=logger)
        self.db_engine = db_engine
        self.template_env: jinja2.Environment = template_env
        self.action_to_template: dict[Action, jinja2.Template] = {}

        self._scene_cache: dict[str, list[SceneSkillDevices]] = {}

    def _load_templates(self) -> None:
        try:
            for action in Action:
                self.action_to_template[action] = self.template_env.get_template(f"{action.name.lower()}.j2")
            self.logger.debug("Templates loaded successfully")
        except jinja2.TemplateNotFound as e:
            self.logger.error("Failed to load template: %s", e)

    async def load_scene_cache(self) -> None:
        """Asynchronously load devices into the cache."""
        if not self._scene_cache:
            self.logger.debug("Loading devices into cache asynchronously.")
            async with AsyncSession(self.db_engine) as session:
                result = (await session.exec(select(SceneSkillScenes).options(selectinload("*")))).all()
                self._scene_cache = {scene.name: list(scene.devices) for scene in result}

    async def skill_preparations(self) -> None:
        self._load_templates()
        await self.load_scene_cache()

    async def calculate_certainty(self, intent_analysis_result: commons.IntentAnalysisResult) -> float:
        keywords = ["scenery", "scene", "scenario"]
        if any(noun in keywords for noun in intent_analysis_result.nouns):
            self.logger.info("Keywords %s in nouns detected, certainty set to 1.0.", keywords)
            return 1.0
        self.logger.debug("No keyword in nouns detected, certainty set to 0.")
        return 0

    def find_parameter_scenes(self, nouns: list[str]) -> tuple[list[str], list[SceneSkillDevices]]:
        names = []
        devices: list[SceneSkillDevices] = []
        nouns_lower = [n.lower() for n in nouns]
        for scene_name, scene_devices in self._scene_cache.items():
            if scene_name in nouns_lower:
                names.append(scene_name)
                devices += scene_devices
        return names, devices

    def find_parameters(self, action: Action, intent_analysis_result: commons.IntentAnalysisResult) -> Parameters:
        parameters = Parameters()
        if action == Action.LIST:
            parameters.scene_names = list(self._scene_cache)
        elif action == Action.APPLY:
            parameters.scene_names, parameters.devices = self.find_parameter_scenes(intent_analysis_result.nouns)
        self.logger.debug("Parameters found for action %s: %s.", action, parameters)
        return parameters

    def get_answer(self, action: Action, parameters: Parameters) -> str:
        template = self.action_to_template.get(action)
        if template:
            answer = template.render(
                action=action,
                parameters=parameters,
            )
            self.logger.debug("Generated answer using template for action %s.", action)
            return answer
        self.logger.error("No template found for action %s.", action)
        return "Sorry, couldn't process your request."

    async def send_mqtt_command(self, parameters: Parameters) -> None:
        """Send the MQTT command asynchronously."""
        for device in parameters.devices:
            self.logger.info(
                "Sending payload %s to topic %s via MQTT.",
                device.topic,
                device.scene_payload,
            )
            await self.mqtt_client.publish(device.topic, device.scene_payload, qos=1)

    async def process_request(self, intent_analysis_result: commons.IntentAnalysisResult) -> None:
        action = Action.find_matching_action(intent_analysis_result.verbs)
        if action is None:
            self.logger.error("Unrecognized action in verbs: %s", intent_analysis_result.verbs)
            return

        parameters = self.find_parameters(action, intent_analysis_result=intent_analysis_result)
        if parameters.scene_names:
            answer = self.get_answer(action, parameters)
            self.add_task(self.send_response(answer, client_request=intent_analysis_result.client_request))
            if action not in [Action.HELP, Action.LIST]:
                self.add_task(self.send_mqtt_command(parameters))
        else:
            self.logger.error("No targets found for action %s.", action)
