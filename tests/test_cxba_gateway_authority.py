from types import SimpleNamespace

import pytest

from tui_gateway.transport import (
    bind_transport,
    has_cxba_private_authority,
    reset_transport,
)
from tui_gateway.ws import _has_cxba_private_token


@pytest.mark.parametrize(
    ("configured", "supplied", "expected"),
    [
        (None, None, False),
        ("a" * 32, None, False),
        ("a" * 32, "b" * 32, False),
        ("short", "short", False),
        ("a" * 32, "a" * 32, True),
    ],
)
def test_private_websocket_token(monkeypatch, configured, supplied, expected):
    if configured is None:
        monkeypatch.delenv("CXBA_GATEWAY_PRIVATE_TOKEN", raising=False)
    else:
        monkeypatch.setenv("CXBA_GATEWAY_PRIVATE_TOKEN", configured)
    headers = {} if supplied is None else {"x-cxba-gateway-token": supplied}
    assert _has_cxba_private_token(SimpleNamespace(headers=headers)) is expected


def test_private_authority_is_bound_to_transport_not_prompt_data():
    token = bind_transport(SimpleNamespace(cxba_private_authority=False))
    try:
        assert has_cxba_private_authority() is False
    finally:
        reset_transport(token)

    token = bind_transport(SimpleNamespace(cxba_private_authority=True))
    try:
        assert has_cxba_private_authority() is True
    finally:
        reset_transport(token)
