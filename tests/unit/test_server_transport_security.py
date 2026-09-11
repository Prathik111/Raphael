import pytest

from ai_ecosystem.interface.server import LocalHttpServer


class _Api:
    pass


def test_remote_bind_requires_tls_and_authentication():
    with pytest.raises(Exception):
        LocalHttpServer(_Api(), host="0.0.0.0", allow_remote=True, auth_token="strong-token")
