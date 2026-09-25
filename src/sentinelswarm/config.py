"""Central configuration for the SentinelSwarm control plane.

All tunables live here so behaviour is deterministic and testable. Values can be
overridden via environment variables prefixed with ``SENTINEL_`` or a ``.env``
file, which keeps the simulation-first defaults reproducible while allowing
real-deployment overrides.
"""

from __future__ import annotations

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class BatterySettings(BaseSettings):
    """Battery-aware scheduling thresholds (percent, 0-100)."""

    model_config = SettingsConfigDict(env_prefix="SENTINEL_BATTERY_")

    critical_pct: float = Field(
        default=15.0,
        description="At/below this the drone must return-to-base immediately.",
    )
    return_pct: float = Field(
        default=30.0,
        description="At/below this the drone should finish up and head home.",
    )
    min_dispatch_pct: float = Field(
        default=40.0,
        description="Minimum battery required for the scheduler to dispatch a new mission.",
    )
    full_pct: float = Field(default=100.0, description="Charge target before returning to service.")
    # Simple linear energy model used by the simulator and feasibility checks.
    drain_per_meter_pct: float = Field(
        default=0.02, description="Battery percent consumed per metre travelled."
    )
    drain_per_second_pct: float = Field(
        default=0.01, description="Idle/hover battery percent consumed per second."
    )
    charge_per_second_pct: float = Field(
        default=2.0, description="Battery percent recovered per second while charging."
    )


class HealthSettings(BaseSettings):
    """Heartbeat / liveness thresholds for health monitoring."""

    model_config = SettingsConfigDict(env_prefix="SENTINEL_HEALTH_")

    heartbeat_interval_s: float = Field(
        default=1.0, description="Expected interval between drone heartbeats."
    )
    heartbeat_timeout_s: float = Field(
        default=5.0,
        description="No heartbeat for this long => drone considered comms-lost/offline.",
    )
    fault_recovery_timeout_s: float = Field(
        default=15.0, description="Max time a drone may spend in RECOVERING before grounding."
    )


class MissionSettings(BaseSettings):
    """Mission lifecycle defaults."""

    model_config = SettingsConfigDict(env_prefix="SENTINEL_MISSION_")

    default_timeout_s: float = Field(
        default=120.0, description="Missions unfinished after this are timed out."
    )
    default_max_retries: int = Field(
        default=2, description="Retry/reassignment attempts before a mission is failed."
    )


class SchedulerSettings(BaseSettings):
    """Fleet scheduler cadence."""

    model_config = SettingsConfigDict(env_prefix="SENTINEL_SCHEDULER_")

    tick_interval_s: float = Field(
        default=0.5, description="How often the scheduler re-evaluates the mission queue."
    )
    battery_safety_margin_pct: float = Field(
        default=10.0,
        description="Extra battery required beyond the estimated round-trip cost.",
    )


class Settings(BaseSettings):
    """Root settings object composed of the sub-sections above."""

    model_config = SettingsConfigDict(
        env_prefix="SENTINEL_", env_file=".env", env_file_encoding="utf-8", extra="ignore"
    )

    app_name: str = "SentinelSwarm"
    environment: str = Field(default="simulation", description="simulation | staging | production")

    # Structured logging
    log_level: str = "INFO"
    log_json: bool = Field(default=True, description="Emit JSON logs (True) or console logs.")

    # Home / charging location shared by the demo scenario (metres, local ENU frame).
    base_x: float = 0.0
    base_y: float = 0.0
    base_z: float = 0.0

    battery: BatterySettings = Field(default_factory=BatterySettings)
    health: HealthSettings = Field(default_factory=HealthSettings)
    mission: MissionSettings = Field(default_factory=MissionSettings)
    scheduler: SchedulerSettings = Field(default_factory=SchedulerSettings)


def load_settings() -> Settings:
    """Return a fresh :class:`Settings` instance (reads env/.env each call)."""

    return Settings()
