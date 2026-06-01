from __future__ import annotations

import os
from dataclasses import dataclass


class SettingsError(RuntimeError):
    """Raised when required runtime settings are invalid."""


_TRUE_VALUES = {"1", "true", "yes", "on"}


def _as_bool(value: str | None, default: bool = False) -> bool:
    if value is None:
        return default
    return value.strip().lower() in _TRUE_VALUES


@dataclass(frozen=True)
class Settings:
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

    @property
    def is_production(self) -> bool:
        return self.environment in {"production", "prod"}

    @classmethod
    def from_env(cls) -> "Settings":
        env = os.getenv("FLASK_ENV", os.getenv("ENVIRONMENT", "development")).strip().lower()
        debug = _as_bool(
            os.getenv("DEBUG"),
            default=env in {"development", "dev", "local", "test", "testing"},
        )

        secret_key = os.getenv("SECRET_KEY", "")
        jwt_secret_key = os.getenv("JWT_SECRET_KEY", os.getenv("JWT_SECRET", ""))
        admin_username = os.getenv("ADMIN_USERNAME", "admin")
        admin_password = os.getenv("ADMIN_PASSWORD", "")

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
        )
        settings.validate()
        return settings

    def validate(self) -> None:
        errors: list[str] = []

        if self.is_production:
            if not self.secret_key or len(self.secret_key) < 16:
                errors.append("SECRET_KEY must be set to a strong value in production")
            if not self.jwt_secret_key or len(self.jwt_secret_key) < 16:
                errors.append("JWT_SECRET_KEY must be set to a strong value in production")
            if not self.admin_password or self.admin_password == "changeme123":
                errors.append("ADMIN_PASSWORD must be set to a non-default value in production")

            if not self.stripe_secret_key and not (
                self.paypal_client_id and self.paypal_client_secret
            ):
                errors.append(
                    "At least one payment provider must be configured in production "
                    "(Stripe key or PayPal client id/secret)"
                )

        if errors:
            raise SettingsError("; ".join(errors))
