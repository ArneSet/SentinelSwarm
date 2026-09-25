"""In-memory fleet state store.

Holds the authoritative control-plane view of drones, missions and incidents.
The methods form a small repository interface; a production build would back the
same interface with PostgreSQL without changing the fleet manager.
"""

from __future__ import annotations

from ..domain.drone import Drone
from ..domain.mission import Mission
from ..domain.states import DroneState, MissionStatus
from .incidents import Incident, IncidentLog


class FleetState:
    """Authoritative, in-memory snapshot of the whole fleet."""

    def __init__(self) -> None:
        self._drones: dict[str, Drone] = {}
        self._missions: dict[str, Mission] = {}
        self.incidents = IncidentLog()

    # -- drones ----------------------------------------------------------
    def add_drone(self, drone: Drone) -> Drone:
        self._drones[drone.drone_id] = drone
        return drone

    def get_drone(self, drone_id: str) -> Drone | None:
        return self._drones.get(drone_id)

    def remove_drone(self, drone_id: str) -> Drone | None:
        return self._drones.pop(drone_id, None)

    def list_drones(self) -> list[Drone]:
        return list(self._drones.values())

    def counts_by_state(self) -> dict[DroneState, int]:
        counts: dict[DroneState, int] = dict.fromkeys(DroneState, 0)
        for drone in self._drones.values():
            counts[drone.state] += 1
        return counts

    # -- missions --------------------------------------------------------
    def add_mission(self, mission: Mission) -> Mission:
        self._missions[mission.mission_id] = mission
        return mission

    def get_mission(self, mission_id: str) -> Mission | None:
        return self._missions.get(mission_id)

    def list_missions(self) -> list[Mission]:
        return list(self._missions.values())

    def pending_missions(self) -> list[Mission]:
        return [m for m in self._missions.values() if m.status is MissionStatus.PENDING]

    def active_missions(self) -> list[Mission]:
        return [m for m in self._missions.values() if m.is_active]

    def missions_for_drone(self, drone_id: str) -> list[Mission]:
        return [m for m in self._missions.values() if m.assigned_drone == drone_id]

    # -- incidents -------------------------------------------------------
    def add_incident(self, incident: Incident) -> Incident:
        return self.incidents.add(incident)
