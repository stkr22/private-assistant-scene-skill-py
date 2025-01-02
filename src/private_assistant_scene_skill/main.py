import asyncio
import pathlib
from typing import Annotated

import jinja2
import typer
from homeassistant_api import Client
from private_assistant_commons import mqtt_connection_handler, skill_config, skill_logger

from private_assistant_scene_skill import config, scene_skill

app = typer.Typer()


@app.command()
def main(config_path: Annotated[pathlib.Path, typer.Argument(envvar="PRIVATE_ASSISTANT_CONFIG_PATH")]) -> None:
    asyncio.run(start_skill(config_path))


async def start_skill(
    config_path: pathlib.Path,
):
    # Set up logger early on
    logger = skill_logger.SkillLogger.get_logger("Private Assistant SceneSkill")

    # Load configuration
    config_obj = skill_config.load_config(config_path, config.SkillConfig)

    # Set up Jinja2 template environment
    template_env = jinja2.Environment(
        loader=jinja2.PackageLoader(
            "private_assistant_scene_skill",
            "templates",
        )
    )

    # Set up Home Assistant API client
    ha_api_client = Client(
        config_obj.home_assistant_api_url,
        config_obj.home_assistant_token,
        async_cache_session=False,
        use_async=True,
    )

    # Start the skill using the async MQTT connection handler
    await mqtt_connection_handler.mqtt_connection_handler(
        scene_skill.SceneSkill,
        config_obj,
        retry_interval=5,
        logger=logger,
        template_env=template_env,
        ha_api_client=ha_api_client,
    )


if __name__ == "__main__":
    app()
