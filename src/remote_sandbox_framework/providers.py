from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class ProviderProfile:
    name: str
    display_name: str
    description: str
    default_hourly_rate: float = 0.0
    default_shutdown_command: str = "shutdown -h now"


_PROFILES: dict[str, ProviderProfile] = {
    "generic": ProviderProfile(
        name="generic",
        display_name="Generic Remote Host",
        description="Reusable preset for any Linux remote workspace.",
        default_hourly_rate=0.0,
        default_shutdown_command="shutdown -h now",
    ),
    "autodl": ProviderProfile(
        name="autodl",
        display_name="AutoDL",
        description="Preset for AutoDL GPU instances controlled from a local machine.",
        default_hourly_rate=6.0,
        default_shutdown_command="shutdown -h now",
    ),
}


def resolve_provider(name: str | None) -> ProviderProfile:
    key = (name or "generic").strip().lower()
    try:
        return _PROFILES[key]
    except KeyError as exc:
        available = ", ".join(sorted(_PROFILES))
        raise ValueError(f"Unknown provider '{key}'. Available: {available}") from exc


def list_provider_profiles() -> list[ProviderProfile]:
    return [profile for _, profile in sorted(_PROFILES.items())]
