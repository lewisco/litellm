# What is this?
## If litellm license in env, checks if it's valid
import base64
import os
from collections.abc import Callable
from datetime import date
from enum import Enum
from typing import TYPE_CHECKING, Final, Protocol, cast

import httpx
from cryptography.hazmat.primitives.asymmetric.rsa import RSAPublicKey
from pydantic import BaseModel, ConfigDict, Field, StrictBool, StrictStr, TypeAdapter

from litellm._logging import verbose_proxy_logger
from litellm.constants import NON_LLM_CONNECTION_TIMEOUT
from litellm.llms.custom_httpx.http_handler import HTTPHandler

if TYPE_CHECKING:
    from litellm.proxy._types import EnterpriseLicenseData


AUTO_ROUTER_LICENSE_FEATURE: Final = "auto_router"
LICENSE_ALL_FEATURES: Final = "*"
AUTO_ROUTER_LICENSE_REMEDY: Final = "A LiteLLM license with the 'auto_router' feature lifts the limit."


class _LicenseHTTPClient(Protocol):
    def get(self, url: str) -> httpx.Response: ...


class _LocalLicenseStatus(Enum):
    VALID = "valid"
    EXPIRED = "expired"
    INVALID = "invalid"


class _EnterpriseLicensePayload(BaseModel):
    model_config = ConfigDict(extra="ignore", frozen=True)

    expiration_date: date
    user_id: StrictStr
    allowed_features: tuple[StrictStr, ...] = ()
    max_users: int | None = Field(default=None, ge=0, strict=True)
    max_teams: int | None = Field(default=None, ge=0, strict=True)


class _RemoteLicensePayload(BaseModel):
    model_config = ConfigDict(extra="ignore", frozen=True)

    verify: StrictBool


class LicenseCheck:
    """
    - Check if license in env
    - Returns if license is valid
    """

    base_url = "https://license.litellm.ai"

    def __init__(
        self,
        *,
        license_str: str | None = None,
        http_handler: _LicenseHTTPClient | None = None,
        public_key: RSAPublicKey | None = None,
        today: Callable[[], date] = date.today,
    ) -> None:
        self.license_str = license_str if license_str is not None else os.getenv("LITELLM_LICENSE", None)
        verbose_proxy_logger.debug("LiteLLM license configured: %s", self.license_str is not None)
        self.http_handler: _LicenseHTTPClient = (
            http_handler
            if http_handler is not None
            else cast(_LicenseHTTPClient, HTTPHandler(timeout=NON_LLM_CONNECTION_TIMEOUT))
        )
        self._premium_check_logged = False
        self.public_key = public_key if public_key is not None else self.read_public_key()
        self._today = today
        self.airgapped_license_data: EnterpriseLicenseData | None = None

    def read_public_key(self) -> RSAPublicKey | None:
        try:
            from cryptography.hazmat.primitives import serialization

            current_dir: Final = os.path.dirname(os.path.realpath(__file__))
            _path_to_public_key: Final = os.path.join(current_dir, "public_key.pem")
            if not os.path.exists(_path_to_public_key):
                return None
            with open(_path_to_public_key, "rb") as key_file:
                public_key: Final = serialization.load_pem_public_key(key_file.read())
            return public_key if isinstance(public_key, RSAPublicKey) else None
        except Exception as e:
            verbose_proxy_logger.error("Error reading public key: %s", e)
            return None

    def _verify(self, license_str: str) -> bool:
        verbose_proxy_logger.debug(
            "litellm.proxy.auth.litellm_license.py::_verify - Checking license against %s/verify_license",
            self.base_url,
        )
        url: Final = f"{self.base_url}/verify_license/{license_str}"

        try:
            response: Final = self._get_remote_response(url=url, attempts_remaining=3)
            payload: Final = _RemoteLicensePayload.model_validate_json(response.content)
            verbose_proxy_logger.debug("Remote LiteLLM license verification result: %s", payload.verify)
            return payload.verify
        except Exception as e:
            verbose_proxy_logger.exception(
                "litellm.proxy.auth.litellm_license.py::_verify - Unable to verify license via api. - %s",
                e,
            )
            return False

    def _get_remote_response(self, url: str, attempts_remaining: int) -> httpx.Response:
        try:
            response: Final = self.http_handler.get(url=url)
            response.raise_for_status()
            return response
        except httpx.HTTPStatusError:
            if attempts_remaining <= 1:
                raise
            return self._get_remote_response(url=url, attempts_remaining=attempts_remaining - 1)

    def is_premium(self) -> bool:
        """
        1. verify_license_without_api_request: checks if license was generate using private / public key pair
        2. _verify: checks if license is valid calling litellm API. This is the old way we were generating/validating license
        """
        try:
            if not self._premium_check_logged:
                verbose_proxy_logger.debug(
                    "litellm.proxy.auth.litellm_license.py::is_premium() - ENTERING 'IS_PREMIUM' - license configured=%s",
                    self.license_str is not None,
                )

            if self.license_str is None:
                self.license_str = os.getenv("LITELLM_LICENSE", None)

            if not self._premium_check_logged:
                verbose_proxy_logger.debug(
                    "litellm.proxy.auth.litellm_license.py::is_premium() - Updated license configured=%s",
                    self.license_str is not None,
                )
                self._premium_check_logged = True

            if self.license_str is None:
                return False
            local_status: Final = self._verify_signed_license(public_key=self.public_key, license_key=self.license_str)
            if local_status is _LocalLicenseStatus.VALID:
                return True
            if local_status is _LocalLicenseStatus.EXPIRED:
                return False
            return self._verify(license_str=self.license_str)
        except Exception:
            return False

    def is_over_limit(self, total_users: int) -> bool:
        """
        Check if the license is over the limit
        """
        if self.airgapped_license_data is None:
            return False
        max_users: Final = self.airgapped_license_data.get("max_users")
        if max_users is None:
            return False
        return total_users > max_users

    def would_exceed_user_limit(self, current_users: int, users_to_add: int = 1) -> bool:
        return self.is_over_limit(total_users=current_users + users_to_add)

    def is_team_count_over_limit(self, team_count: int) -> bool:
        """
        Check if the license is over the limit
        """
        if self.airgapped_license_data is None:
            return False

        _max_teams_in_license: Final[int | None] = self.airgapped_license_data.get("max_teams")
        if "max_teams" not in self.airgapped_license_data or not isinstance(_max_teams_in_license, int):
            return False
        return team_count > _max_teams_in_license

    def would_exceed_team_limit(self, current_teams: int, teams_to_add: int = 1) -> bool:
        return self.is_team_count_over_limit(team_count=current_teams + teams_to_add)

    def grants_feature(self, feature: str) -> bool:
        if self.airgapped_license_data is None:
            return False
        allowed_features: Final = self.airgapped_license_data.get("allowed_features")
        granted: Final = allowed_features if isinstance(allowed_features, list) else (allowed_features,)
        return feature in granted or LICENSE_ALL_FEATURES in granted

    def auto_router_capability_limit(self) -> int | None:
        """
        How many auto-routers may claim each gated classifier or customization capability:
        unlimited (None) only when the signed license lists the auto_router feature or the
        "*" wildcard that grants every feature, otherwise one per capability. A license verified
        through the API carries no feature list, so it does not lift the limit either.
        """
        if self.grants_feature(AUTO_ROUTER_LICENSE_FEATURE):
            return None
        return 1

    def verify_license_without_api_request(self, public_key: RSAPublicKey | None, license_key: str) -> bool:
        return self._verify_signed_license(public_key=public_key, license_key=license_key) is _LocalLicenseStatus.VALID

    def _verify_signed_license(self, public_key: RSAPublicKey | None, license_key: str) -> _LocalLicenseStatus:
        try:
            from cryptography.hazmat.primitives import hashes
            from cryptography.hazmat.primitives.asymmetric import padding

            from litellm.proxy._types import EnterpriseLicenseData

            if public_key is None:
                return _LocalLicenseStatus.INVALID

            padding_needed: Final = len(license_key) % 4
            padded_license_key: Final = f"{license_key}{'=' * (4 - padding_needed)}" if padding_needed else license_key
            decoded: Final = base64.b64decode(padded_license_key, validate=True)
            signature_length: Final = (public_key.key_size + 7) // 8
            separator_index: Final = len(decoded) - signature_length - 1
            if separator_index < 0 or decoded[separator_index : separator_index + 1] != b".":
                return _LocalLicenseStatus.INVALID
            message: Final = decoded[:separator_index]
            signature: Final = decoded[separator_index + 1 :]

            public_key.verify(
                signature,
                message,
                padding.PSS(
                    mgf=padding.MGF1(hashes.SHA256()),
                    salt_length=padding.PSS.MAX_LENGTH,
                ),
                hashes.SHA256(),
            )

            payload: Final = _EnterpriseLicensePayload.model_validate_json(message)
            if payload.expiration_date < self._today():
                self.airgapped_license_data = None
                return _LocalLicenseStatus.EXPIRED

            adapter: Final = TypeAdapter(EnterpriseLicenseData)
            self.airgapped_license_data = adapter.validate_python(payload.model_dump(mode="json", exclude_none=True))

            return _LocalLicenseStatus.VALID

        except Exception as e:
            self.airgapped_license_data = None
            verbose_proxy_logger.debug(
                "litellm.proxy.auth.litellm_license.py::verify_license_without_api_request - Unable to verify License locally. - %s",
                e,
            )
            return _LocalLicenseStatus.INVALID
