import jinja2
import pytest

from private_assistant_scene_skill.models import SceneSkillDevices
from private_assistant_scene_skill.scene_skill import Parameters


@pytest.fixture(scope="module")
def jinja_env():
    return jinja2.Environment(
        loader=jinja2.PackageLoader("private_assistant_scene_skill", "templates"),
    )


@pytest.mark.parametrize(
    "template_name,parameters,expected_output",
    [
        (
            "apply.j2",
            Parameters(
                scene_names=["romantic"],
                devices=[SceneSkillDevices(topic="light/1", scene_payload="ON")],
            ),
            "The scene romantic has been applied affecting 1 device.\n",
        ),
        (
            "apply.j2",
            Parameters(
                scene_names=["romantic", "morning"],
                devices=[
                    SceneSkillDevices(topic="light/1", scene_payload="ON"),
                    SceneSkillDevices(topic="light/2", scene_payload="ON"),
                ],
            ),
            "The scenes romantic and morning have been applied affecting 2 devices.\n",
        ),
        (
            "list.j2",
            Parameters(scene_names=["romantic"]),
            "Scenes romantic can be used.",
        ),
        (
            "list.j2",
            Parameters(scene_names=["romantic", "morning"]),
            "Scenes romantic and morning can be used.",
        ),
    ],
)
def test_templates(jinja_env, template_name, parameters, expected_output):
    template = jinja_env.get_template(template_name)
    result = template.render(parameters=parameters)
    assert result == expected_output
