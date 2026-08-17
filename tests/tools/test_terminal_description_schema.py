from tools.terminal_tool import TERMINAL_SCHEMA


def test_terminal_requires_model_authored_description():
    parameters = TERMINAL_SCHEMA["parameters"]

    assert parameters["required"] == ["command", "description"]
    assert parameters["properties"]["description"]["minLength"] == 1
    assert "user-facing explanation" in (
        parameters["properties"]["description"]["description"]
    )
