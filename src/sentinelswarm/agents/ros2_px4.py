"""Extension seam for real vehicles (ROS 2 / PX4).

This module is intentionally a stub. It documents *exactly* where a real drone
plugs in: implement :class:`DroneDriver` on top of MAVSDK/PX4 offboard control
and ROS 2 topics, register the resulting agent with the fleet, and the control
plane treats it identically to a simulated drone.

No heavy robotics dependencies are imported here so the core project stays
installable and testable on any machine and in CI.
"""

from __future__ import annotations

from ..domain.drone import DroneKind
from ..domain.geometry import Position


class Ros2Px4Driver:
    """Placeholder real-vehicle driver.

    A production implementation would:

    * subscribe to ROS 2 topics (``/fmu/out/vehicle_local_position``, battery
      status, etc.) to populate :attr:`position` / :attr:`battery_pct`;
    * publish PX4 offboard setpoints (via MAVSDK or ``px4_msgs``) in
      :meth:`set_setpoint`;
    * map PX4 failsafe / EKF health flags to :attr:`fault_code`.
    """

    kind = DroneKind.REAL

    def __init__(self, drone_id: str, connection_url: str) -> None:
        self.drone_id = drone_id
        self.connection_url = connection_url

    @property
    def position(self) -> Position:  # pragma: no cover - stub
        raise NotImplementedError("Real ROS2/PX4 integration is out of scope for the demo.")

    @property
    def battery_pct(self) -> float:  # pragma: no cover - stub
        raise NotImplementedError

    @property
    def fault_code(self) -> str | None:  # pragma: no cover - stub
        raise NotImplementedError

    async def connect(self) -> None:  # pragma: no cover - stub
        raise NotImplementedError("Connect to the vehicle via MAVSDK/PX4 here.")

    def set_setpoint(self, target: Position, speed: float) -> None:  # pragma: no cover - stub
        raise NotImplementedError

    def set_charging(self, on: bool) -> None:  # pragma: no cover - stub
        raise NotImplementedError

    async def step(self, dt: float) -> None:  # pragma: no cover - stub
        raise NotImplementedError

    def inject_fault(self, code: str) -> None:  # pragma: no cover - stub
        raise NotImplementedError

    def clear_fault(self) -> None:  # pragma: no cover - stub
        raise NotImplementedError
