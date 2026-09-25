"""Kinematic + battery simulation of a drone, behind the :class:`DroneDriver` seam.

Deliberately simple and dependency-free: constant-speed motion toward a setpoint
with a linear energy model. That is enough to exercise the entire fleet-management
control plane (scheduling, health, failure, reassignment, RTB) without Gazebo/PX4,
so the demo and CI run anywhere. Swapping in a real ``Ros2Px4Driver`` does not
change any control-plane code.
"""

from __future__ import annotations

from dataclasses import dataclass

from ..config import Settings
from ..domain.drone import DroneKind
from ..domain.geometry import Position


@dataclass(slots=True)
class SimParams:
    """Physical parameters for the simulated vehicle."""

    default_speed_mps: float = 12.0
    drain_per_meter_pct: float = 0.02
    drain_per_second_pct: float = 0.01
    charge_per_second_pct: float = 2.0

    @classmethod
    def from_settings(cls, settings: Settings) -> SimParams:
        return cls(
            drain_per_meter_pct=settings.battery.drain_per_meter_pct,
            drain_per_second_pct=settings.battery.drain_per_second_pct,
            charge_per_second_pct=settings.battery.charge_per_second_pct,
        )


class SimulatedDriver:
    """A software-only drone that integrates a kinematic + battery model."""

    kind = DroneKind.SIMULATED

    def __init__(
        self,
        drone_id: str,
        start: Position,
        battery_pct: float = 100.0,
        params: SimParams | None = None,
    ) -> None:
        self.drone_id = drone_id
        self._pos = start
        self._setpoint = start
        self._speed = (params or SimParams()).default_speed_mps
        self._battery = battery_pct
        self._charging = False
        self._fault: str | None = None
        self.params = params or SimParams()

    # -- reads -----------------------------------------------------------
    @property
    def position(self) -> Position:
        return self._pos

    @property
    def battery_pct(self) -> float:
        return self._battery

    @property
    def fault_code(self) -> str | None:
        return self._fault

    # -- commands --------------------------------------------------------
    async def connect(self) -> None:
        return None

    def set_setpoint(self, target: Position, speed: float) -> None:
        self._setpoint = target
        self._speed = max(0.1, speed)

    def set_charging(self, on: bool) -> None:
        self._charging = on

    def inject_fault(self, code: str) -> None:
        self._fault = code

    def clear_fault(self) -> None:
        self._fault = None

    # -- physics ---------------------------------------------------------
    async def step(self, dt: float) -> None:
        if dt <= 0:
            return
        if self._charging:
            self._battery = min(100.0, self._battery + self.params.charge_per_second_pct * dt)
            return
        if self._fault is not None:
            # A faulted vehicle holds position but still consumes idle power.
            self._drain(0.0, dt)
            return

        travel = self._speed * dt
        new_pos = self._pos.moved_towards(self._setpoint, travel)
        distance = self._pos.distance_to(new_pos)
        self._pos = new_pos
        self._drain(distance, dt)

    def _drain(self, distance_m: float, dt: float) -> None:
        used = distance_m * self.params.drain_per_meter_pct + dt * self.params.drain_per_second_pct
        self._battery = max(0.0, self._battery - used)
