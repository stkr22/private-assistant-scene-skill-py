import jinja2
import pytest

from private_assistant_scene_skill.models import SceneDevice
from private_assistant_scene_skill.scene_skill import Parameters


@pytest.fixture(scope="module")
def jinja_env():
    return jinja2.Environment(
        loader=jinja2.PackageLoader("private_assistant_scene_skill", "templates"),
    )


@pytest.mark.parametrize(
    "template_name,parameters,device_count,expected_output",
    [
        (
            "apply.j2",
            Parameters(
                scene_names=["romantic"],
                targets=[
                    SceneDevice(
                        name="romantic",
                        device_actions=[{"topic": "light/1", "payload": "ON"}],
                    )
                ],
            ),
            1,
            "The scene romantic has been applied affecting 1 device.\n",
        ),
        (
            "apply.j2",
            Parameters(
                scene_names=["romantic", "morning"],
                targets=[
                    SceneDevice(
                        name="romantic",
                        device_actions=[{"topic": "light/1", "payload": "ON"}],
                    ),
                    SceneDevice(
                        name="morning",
                        device_actions=[{"topic": "light/2", "payload": "ON"}],
                    ),
                ],
            ),
            2,
            "The scenes romantic and morning have been applied affecting 2 devices.\n",
        ),
    ],
)
def test_templates(jinja_env, template_name, parameters, device_count, expected_output):
    template = jinja_env.get_template(template_name)
    result = template.render(parameters=parameters, device_count=device_count)
    assert result == expected_output


def test_help_template(jinja_env):
    """Test that help template renders correctly."""
    template = jinja_env.get_template("help.j2")
    result = template.render()
    assert "scenes" in result.lower()
    assert "activate" in result.lower() or "apply" in result.lower()
