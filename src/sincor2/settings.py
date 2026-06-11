from __future__ import annotations

import logging
import os
import secrets
from dataclasses import dataclass


class SettingsError(RuntimeError):
    """Raised when required runtime settings are invalid."""


_TRUE_VALUES = {"1", "true", "yes", "on"}
_NON_PROD_ENVIRONMENTS = {"development", "dev", "local", "test", "testing"}
_MIN_SECRET_LENGTH = 16
logger = logging.getLogger(__name__)


def _as_bool(value: str | None, default: bool = False) -> bool:
    if value is None:
        return default
    return value.strip().lower() in _TRUE_VALUES


def _normalize_environment(value: str | None) -> str:
    normalized = (value or "development").strip().lower()
    return normalized or "development"


def _is_strong_enough(value: str | None, minimum_length: int = _MIN_SECRET_LENGTH) -> bool:
    return bool(value and len(value) >= minimum_length)


def _development_fallback(name: str, current_value: str, *, minimum_length: int = 32) -> str:
    if _is_strong_enough(current_value, minimum_length):
        return current_value

    generated = secrets.token_urlsafe(max(minimum_length, 32))
    logger.warning(
        (
            "%s is missing or weak outside production; "
            "generated an in-memory fallback for this process."
        ),
        name,
    )
    return generated


@dataclass(frozen=True)
class Settings:
    """Typed runtime settings loaded from environment variables with validation."""

    environment: str
    debug: bool
    secret_key: str
    jwt_secret_key: str
    admin_username: str
    admin_password: str
    stripe_secret_key: str | None
    paypal_client_id: str | None
    paypal_client_secret: str | None
    anthropic_api_key: str | None
    base_rpc_url: str | None
    # SINC token integration
    sinc_contract_address: str
    sinc_platform_access_address: str
    platform_signer_key: str | None

    @property
    def is_production(self) -> bool:
        return self.environment in {"production", "prod"}

    @classmethod
    def from_env(cls) -> "Settings":
        env = _normalize_environment(
            os.getenv("FLASK_ENV", os.getenv("ENVIRONMENT", "development"))
        )
        debug = _as_bool(
            os.getenv("DEBUG"),
            default=env in _NON_PROD_ENVIRONMENTS,
        )

        secret_key = os.getenv("SECRET_KEY", "")
        jwt_secret_key = os.getenv("JWT_SECRET_KEY", os.getenv("JWT_SECRET", ""))
        admin_username = os.getenv("ADMIN_USERNAME", "admin")
        admin_password = os.getenv("ADMIN_PASSWORD", "")
        is_production = env in {"production", "prod"}

        if not is_production:
            secret_key = _development_fallback("SECRET_KEY", secret_key)
            jwt_secret_key = _development_fallback("JWT_SECRET_KEY", jwt_secret_key)
            if not admin_password or admin_password == "changeme123":
                admin_password = _development_fallback("ADMIN_PASSWORD", admin_password)

        settings = cls(
            environment=env,
            debug=debug,
            secret_key=secret_key,
            jwt_secret_key=jwt_secret_key,
            admin_username=admin_username,
            admin_password=admin_password,
            stripe_secret_key=(
                os.getenv("STRIPE_SECRET_KEY")
                or os.getenv("STRIPE_API_KEY")
                or os.getenv("STRIPE_API_SECRET")
            ),
            paypal_client_id=os.getenv("PAYPAL_REST_API_ID"),
            paypal_client_secret=os.getenv("PAYPAL_REST_API_SECRET"),
            anthropic_api_key=os.getenv("ANTHROPIC_API_KEY"),
            base_rpc_url=os.getenv("BASE_RPC_URL"),
            sinc_contract_address=os.getenv(
                "SINC_CONTRACT_ADDRESS",
                "0x9C8cd8d3961F445D653713dE65C6578bE11668e7",
            ),
            sinc_platform_access_address=os.getenv("SINC_PLATFORM_ACCESS_ADDRESS", ""),
            platform_signer_key=os.getenv("PLATFORM_SIGNER_KEY"),
        )
        settings.validate()
        return settings

    def validate(self) -> None:
        errors: list[str] = []

        if self.is_production:
            if not _is_strong_enough(self.secret_key):
                current_length = len(self.secret_key) if self.secret_key else 0
                errors.append(
                    "SECRET_KEY must be set to a strong value in production "
                    f"(minimum {_MIN_SECRET_LENGTH} characters, got {current_length})"
                )
            if not _is_strong_enough(self.jwt_secret_key):
                current_length = len(self.jwt_secret_key) if self.jwt_secret_key else 0
                errors.append(
                    "JWT_SECRET_KEY must be set to a strong value in production "
                    f"(minimum {_MIN_SECRET_LENGTH} characters, got {current_length})"
                )
            if not self.admin_password or self.admin_password == "changeme123":
                errors.append("ADMIN_PASSWORD must be set to a non-default value in production")


        if errors:
            raise SettingsError(f"Invalid production configuration: {'; '.join(errors)}")
