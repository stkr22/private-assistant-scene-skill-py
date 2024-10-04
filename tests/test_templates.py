import jinja2
import pytest

from private_assistant_scene_skill.scene_skill import Parameters


# Fixture to set up the Jinja2 environment
@pytest.fixture(scope="module")
def jinja_env():
    return jinja2.Environment(
        loader=jinja2.PackageLoader(
            "private_assistant_scene_skill",
            "templates",
        ),
    )


def render_template(template_name, parameters, env, target_alias_cache=None):
    template = env.get_template(template_name)
    return template.render(parameters=parameters, target_alias_cache=target_alias_cache)


# Test for apply.j2 (applying scenes)
@pytest.mark.parametrize(
    "targets, target_alias_cache, expected_output",
    [
        # Single scene
        (
            ["romantic_evening"],
            {"romantic_evening": "Romantic Evening"},
            "The scene Romantic Evening has been applied.\n",
        ),
        # Multiple scenes
        (
            ["romantic_evening", "morning_routine"],
            {"romantic_evening": "Romantic Evening", "morning_routine": "Morning Routine"},
            "The scenes Romantic Evening and Morning Routine have been applied.\n",
        ),
    ],
)
def test_apply_template(jinja_env, targets, target_alias_cache, expected_output):
    parameters = Parameters(targets=targets)
    result = render_template("apply.j2", parameters, jinja_env, target_alias_cache=target_alias_cache)
    assert result == expected_output
