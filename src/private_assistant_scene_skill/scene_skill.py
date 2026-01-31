"""Scene control skill for activating predefined scenes via MQTT."""

import asyncio
import logging

import aiomqtt
import jinja2
import private_assistant_commons as commons
from private_assistant_commons import (
    ClassifiedIntent,
    IntentRequest,
    IntentType,
)
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncEngine

from private_assistant_scene_skill.models import SceneDevice


class Parameters(BaseModel):
    """Parameters extracted from intent for scene operations."""

    scene_names: list[str] = []
    targets: list[SceneDevice] = []
    rooms: list[str] = []


class SceneSkill(commons.BaseSkill):
    """Scene control skill for activating predefined scenes via MQTT.

    Processes voice commands to activate scenes that trigger multiple devices.
    Integrates with global device registry for scene discovery and management.
    """

    help_text = "This skill can help you activate scenes. "

    def __init__(  # noqa: PLR0913
        self,
        config_obj: commons.SkillConfig,
        mqtt_client: aiomqtt.Client,
        db_engine: AsyncEngine,
        template_env: jinja2.Environment,
        task_group: asyncio.TaskGroup,
        logger: logging.Logger,
    ) -> None:
        """Initialize the scene skill with dependencies.

        Args:
            config_obj: Skill configuration from commons
            mqtt_client: MQTT client for device communication
            db_engine: Database engine for global device registry
            template_env: Jinja2 environment for response templates
            task_group: Async task group for concurrent operations
            logger: Logger instance for debugging and monitoring

        """
        # Pass engine to BaseSkill (NEW REQUIRED PARAMETER)
        super().__init__(
            config_obj=config_obj,
            mqtt_client=mqtt_client,
            task_group=task_group,
            engine=db_engine,
            logger=logger,
        )
        self.db_engine = db_engine
        self.template_env = template_env
        self.intent_to_template: dict[IntentType, jinja2.Template] = {}

        # AIDEV-NOTE: Intent-based configuration replaces calculate_certainty method
        self.supported_intents = {
            IntentType.SCENE_APPLY: 0.8,  # "activate romantic scene"
        }

        # AIDEV-NOTE: Device types this skill can control
        self.supported_device_types = ["scene"]

        # AIDEV-NOTE: Template preloading at init prevents runtime template lookup failures
        self._load_templates()

    def _load_templates(self) -> None:
        """Load and validate all required templates with fallback handling.

        Raises:
            RuntimeError: If critical templates cannot be loaded

        """
        template_mappings = {
            IntentType.SCENE_APPLY: "apply.j2",
        }

        failed_templates = []
        for intent_type, template_name in template_mappings.items():
            try:
                self.intent_to_template[intent_type] = self.template_env.get_template(template_name)
            except jinja2.TemplateNotFound as e:
                self.logger.error("Failed to load template %s: %s", template_name, e)
                failed_templates.append(template_name)

        if failed_templates:
            raise RuntimeError(f"Critical templates failed to load: {', '.join(failed_templates)}")

        self.logger.debug("All templates successfully loaded during initialization.")

    async def get_scenes(
        self, rooms: list[str] | None = None, scene_names: list[str] | None = None
    ) -> list[SceneDevice]:
        """Return scene devices from global device registry.

        Args:
            rooms: Optional list of room names to filter scenes
            scene_names: Optional list of scene names to filter

        Returns:
            List of SceneDevice objects matching the filters

        """
        self.logger.info("Fetching scenes with filters - rooms: %s, names: %s", rooms, scene_names)

        scenes = []
        for global_device in self.global_devices:
            # Filter by room if specified
            if rooms and global_device.room and global_device.room.name not in rooms:
                continue

            # Filter by name if specified
            if scene_names is not None:
                # Normalize names for comparison (lowercase)
                normalized_scene_names = [name.lower() for name in scene_names]
                if global_device.name.lower() not in normalized_scene_names:
                    continue

            try:
                scene_device = SceneDevice.from_global_device(global_device)
                scenes.append(scene_device)
            except ValueError as e:
                self.logger.warning("Skipping scene device %s: %s", global_device.name, e)

        self.logger.debug("Found %d scenes matching filters", len(scenes))
        return scenes

    async def find_parameters(
        self, intent_type: IntentType, classified_intent: ClassifiedIntent, current_room: str
    ) -> Parameters:
        """Extract parameters from classified intent entities.

        Args:
            intent_type: The type of intent being processed
            classified_intent: The classified intent with extracted entities
            current_room: The room where the command originated

        Returns:
            Parameters object with scenes, rooms, and targets

        """
        parameters = Parameters()

        # AIDEV-NOTE: Intent classifier uses singular entity keys ("device", "room")
        # Extract rooms from entities, fallback to current room
        room_entities = classified_intent.entities.get("room", [])
        parameters.rooms = [room.normalized_value for room in room_entities] if room_entities else [current_room]

        # Extract scene names from device entities, filtering for device_type="scene"
        device_entities = classified_intent.entities.get("device", [])
        if device_entities:
            # Filter for scene-type devices only
            scene_names = []
            for entity in device_entities:
                metadata = getattr(entity, "metadata", {}) or {}
                if metadata.get("device_type") == "scene":
                    scene_names.append(entity.normalized_value)
            parameters.scene_names = scene_names

        if intent_type == IntentType.SCENE_APPLY:
            # Get scenes matching the requested names and/or rooms
            scenes = await self.get_scenes(
                rooms=parameters.rooms if room_entities else None,
                scene_names=parameters.scene_names if device_entities else None,
            )
            parameters.targets = scenes

        self.logger.debug("Extracted parameters: %s", parameters.model_dump())
        return parameters

    def _render_response(self, intent_type: IntentType, parameters: Parameters) -> str:
        """Render response template for the given intent and parameters.

        Args:
            intent_type: The type of intent being processed
            parameters: Parameters extracted from the intent

        Returns:
            Rendered response text

        """
        template = self.intent_to_template.get(intent_type)
        if not template:
            self.logger.error("No template found for intent type %s", intent_type)
            return "Sorry, I couldn't process your request."

        # Count total devices affected
        device_count = sum(len(scene.device_actions) for scene in parameters.targets)

        answer = template.render(
            parameters=parameters,
            device_count=device_count,
        )
        self.logger.debug("Generated answer using template for intent %s", intent_type)
        return answer

    async def _send_mqtt_commands(self, parameters: Parameters) -> None:
        """Send MQTT commands to activate scenes.

        Args:
            parameters: Parameters containing target scenes to activate

        """
        for scene in parameters.targets:
            self.logger.info("Activating scene: %s with %d device actions", scene.name, len(scene.device_actions))

            for device_action in scene.device_actions:
                topic = device_action.get("topic")
                payload = device_action.get("payload", "ON")

                if topic:
                    self.logger.info("Sending payload '%s' to topic '%s' via MQTT", payload, topic)
                    await self.mqtt_client.publish(topic, payload, qos=1)
                else:
                    self.logger.warning("Device action missing topic in scene %s", scene.name)

    async def _handle_scene_apply(self, intent_request: IntentRequest) -> None:
        """Handle SCENE_APPLY intent - activate one or more scenes.

        Args:
            intent_request: The intent request with classified intent and client request

        """
        classified_intent = intent_request.classified_intent
        client_request = intent_request.client_request
        current_room = client_request.room

        # Extract parameters from entities
        parameters = await self.find_parameters(IntentType.SCENE_APPLY, classified_intent, current_room)

        if not parameters.targets:
            await self.send_response("I couldn't find any scenes to activate.", client_request)
            return

        # Send response and MQTT commands
        answer = self._render_response(IntentType.SCENE_APPLY, parameters)
        self.add_task(self.send_response(answer, client_request=client_request))
        self.add_task(self._send_mqtt_commands(parameters))

    async def process_request(self, intent_request: IntentRequest) -> None:
        """Route intent request to the appropriate handler.

        Orchestrates the full command processing pipeline:
        1. Extract intent type from classified intent
        2. Route to appropriate intent handler
        3. Handler extracts entities, controls devices, and sends response

        Args:
            intent_request: The intent request with classified intent and client request

        """
        classified_intent = intent_request.classified_intent
        intent_type = classified_intent.intent_type

        self.logger.debug(
            "Processing intent %s with confidence %.2f",
            intent_type,
            classified_intent.confidence,
        )

        # Route to appropriate handler
        if intent_type == IntentType.SCENE_APPLY:
            await self._handle_scene_apply(intent_request)
        else:
            self.logger.warning("Unsupported intent type: %s", intent_type)
            await self.send_response(
                "I'm not sure how to handle that request.",
                client_request=intent_request.client_request,
            )
