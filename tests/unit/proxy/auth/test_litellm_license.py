import logging
import json
from datetime import date

import httpx
import pytest
from cryptography.hazmat.primitives.asymmetric.rsa import RSAPublicKey

from litellm._logging import verbose_proxy_logger
from litellm.proxy.auth.litellm_license import LicenseCheck


class _RecordingLicenseHTTPClient:
    def __init__(self, verified: bool) -> None:
        self.verified = verified
        self.requested_urls: tuple[str, ...] = ()

    def get(self, url: str) -> httpx.Response:
        self.requested_urls = (*self.requested_urls, url)
        return httpx.Response(200, json={"verify": self.verified}, request=httpx.Request("GET", url))


def test_read_public_key_loads_successfully():
    """Ensure public_key.pem is valid PEM with no leading whitespace."""
    license_check = LicenseCheck()
    assert license_check.public_key is not None, (
        "public_key.pem could not be loaded — check for leading whitespace or malformed PEM header"
    )


def test_is_over_limit():
    license_check = LicenseCheck()
    license_check.airgapped_license_data = {"max_users": 100}
    assert license_check.is_over_limit(101) is True
    assert license_check.is_over_limit(100) is False
    assert license_check.is_over_limit(99) is False

    license_check.airgapped_license_data = {}
    assert license_check.is_over_limit(101) is False
    assert license_check.is_over_limit(100) is False
    assert license_check.is_over_limit(99) is False

    license_check.airgapped_license_data = None
    assert license_check.is_over_limit(101) is False
    assert license_check.is_over_limit(100) is False
    assert license_check.is_over_limit(99) is False


@pytest.mark.parametrize(
    ("current_users", "users_to_add", "expected"),
    ((1, 1, False), (2, 1, True), (0, 1, True)),
)
def test_would_exceed_user_limit_checks_the_post_create_count(
    current_users: int, users_to_add: int, expected: bool
) -> None:
    license_check = LicenseCheck()
    license_check.airgapped_license_data = {"max_users": 2 if current_users else 0}

    assert license_check.would_exceed_user_limit(current_users, users_to_add) is expected


@pytest.mark.parametrize(
    ("current_teams", "teams_to_add", "expected"),
    ((1, 1, False), (2, 1, True), (0, 1, True)),
)
def test_would_exceed_team_limit_checks_the_post_create_count(
    current_teams: int, teams_to_add: int, expected: bool
) -> None:
    license_check = LicenseCheck()
    license_check.airgapped_license_data = {"max_teams": 2 if current_teams else 0}

    assert license_check.would_exceed_team_limit(current_teams, teams_to_add) is expected


def test_auto_router_capability_limit() -> None:
    """The signed license's auto_router feature or its "*" wildcard lifts the one-router limit; an
    API-verified license (no airgapped data) and an airgapped license without either keep it."""
    license_check = LicenseCheck()
    license_check.airgapped_license_data = {"expiration_date": "2999-01-01", "allowed_features": ["auto_router"]}
    assert license_check.auto_router_capability_limit() is None

    license_check.airgapped_license_data = {
        "expiration_date": "2999-01-01",
        "allowed_features": ["sso", "auto_router", "audit_logs"],
    }
    assert license_check.auto_router_capability_limit() is None

    license_check.airgapped_license_data = {"expiration_date": "2999-01-01", "allowed_features": ["*"]}
    assert license_check.auto_router_capability_limit() is None

    license_check.airgapped_license_data = {"expiration_date": "2999-01-01", "allowed_features": ["sso", "*"]}
    assert license_check.auto_router_capability_limit() is None

    license_check.airgapped_license_data = {"expiration_date": "2999-01-01", "allowed_features": ["sso"]}
    assert license_check.auto_router_capability_limit() == 1

    license_check.airgapped_license_data = {"expiration_date": "2999-01-01", "allowed_features": "*"}
    assert license_check.auto_router_capability_limit() is None

    license_check.airgapped_license_data = {"expiration_date": "2999-01-01"}
    assert license_check.auto_router_capability_limit() == 1

    license_check.airgapped_license_data = None
    assert license_check.auto_router_capability_limit() == 1


def _signed_license(
    expiration_date: str,
    allowed_features: tuple[str, ...] = ("auto_router",),
    user_id: str = "u",
) -> tuple[RSAPublicKey, str]:
    import base64

    from cryptography.hazmat.primitives import hashes
    from cryptography.hazmat.primitives.asymmetric import padding, rsa

    private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    message = json.dumps(
        {"expiration_date": expiration_date, "user_id": user_id, "allowed_features": list(allowed_features)}
    ).encode()
    signature = private_key.sign(
        message,
        padding.PSS(mgf=padding.MGF1(hashes.SHA256()), salt_length=padding.PSS.MAX_LENGTH),
        hashes.SHA256(),
    )
    return private_key.public_key(), base64.b64encode(message + b"." + signature).decode()


def test_expired_or_unreadable_license_grants_no_features() -> None:
    """The verifier stores the signed payload only after the expiry check passes and clears it when a
    later verify rejects the license, so a stale payload cannot keep lifting the heuristic_v2 limit."""
    license_check = LicenseCheck()
    public_key, valid_key = _signed_license("2999-01-01")
    assert license_check.verify_license_without_api_request(public_key=public_key, license_key=valid_key) is True
    assert license_check.auto_router_capability_limit() is None

    _, expired_key = _signed_license("2000-01-01")
    assert license_check.verify_license_without_api_request(public_key=public_key, license_key=expired_key) is False
    assert license_check.airgapped_license_data is None
    assert license_check.auto_router_capability_limit() == 1

    assert license_check.verify_license_without_api_request(public_key=public_key, license_key=valid_key) is True
    assert license_check.verify_license_without_api_request(public_key=public_key, license_key="not-a-license") is False
    assert license_check.airgapped_license_data is None


def test_valid_signed_license_with_auto_router_lifts_the_limit() -> None:
    license_check = LicenseCheck()
    public_key, license_key = _signed_license("2999-01-01")

    assert license_check.verify_license_without_api_request(public_key=public_key, license_key=license_key) is True
    assert license_check.auto_router_capability_limit() is None


def test_signed_license_payload_supports_periods() -> None:
    license_check = LicenseCheck()
    public_key, license_key = _signed_license("2999-01-01", user_id="person@example.com")

    assert license_check.verify_license_without_api_request(public_key=public_key, license_key=license_key) is True
    assert license_check.airgapped_license_data is not None
    assert license_check.airgapped_license_data["user_id"] == "person@example.com"


def test_signed_license_is_valid_through_its_expiration_date() -> None:
    public_key, license_key = _signed_license("2030-01-01")
    http_client = _RecordingLicenseHTTPClient(verified=False)
    license_check = LicenseCheck(
        license_str=license_key,
        http_handler=http_client,
        public_key=public_key,
        today=lambda: date(2030, 1, 1),
    )

    assert license_check.is_premium() is True
    assert http_client.requested_urls == ()


def test_expired_signed_license_does_not_leave_the_air_gapped_environment() -> None:
    public_key, license_key = _signed_license("2030-01-01")
    http_client = _RecordingLicenseHTTPClient(verified=True)
    license_check = LicenseCheck(
        license_str=license_key,
        http_handler=http_client,
        public_key=public_key,
        today=lambda: date(2030, 1, 2),
    )

    assert license_check.is_premium() is False
    assert http_client.requested_urls == ()


def test_legacy_remote_verification_does_not_log_the_license(caplog: pytest.LogCaptureFixture) -> None:
    license_key = "legacy-license-value-that-must-remain-secret"
    license_check = LicenseCheck(
        license_str=license_key,
        http_handler=_RecordingLicenseHTTPClient(verified=True),
    )

    with caplog.at_level(logging.DEBUG, logger=verbose_proxy_logger.name):
        assert license_check.is_premium() is True

    assert license_key not in caplog.text


def test_valid_signed_wildcard_license_lifts_the_limit() -> None:
    """The license generator defaults allowed_features to ["*"], meaning every feature, so a wildcard
    license grants auto_router the same way a license that names it does."""
    license_check = LicenseCheck()
    public_key, license_key = _signed_license("2999-01-01", allowed_features=("*",))

    assert license_check.verify_license_without_api_request(public_key=public_key, license_key=license_key) is True
    assert license_check.grants_feature("auto_router") is True
    assert license_check.auto_router_capability_limit() is None

    named_public_key, named_key = _signed_license("2999-01-01", allowed_features=("sso", "audit_logs"))
    assert license_check.verify_license_without_api_request(public_key=named_public_key, license_key=named_key) is True
    assert license_check.grants_feature("auto_router") is False
    assert license_check.auto_router_capability_limit() == 1
