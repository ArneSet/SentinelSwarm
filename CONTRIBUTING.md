# Contributing

Thanks for your interest in SentinelSwarm. It's a personal project about drone
**fleet management** — contributions should keep it clean, typed, tested and, above
all, **safe**.

## Ground rules (non-negotiable)

SentinelSwarm is and will remain **non-weaponized**. We will not accept contributions that
add weapons, targeting, autonomous engagement, facial recognition, identification/tracking
of specific persons, offensive/military functionality, or unbounded autonomous behaviour.
Computer vision is limited to environmental/infrastructure awareness.

## Development setup

```bash
python -m venv .venv
. .venv/bin/activate            # Windows: .\.venv\Scripts\Activate.ps1
pip install -e ".[dev,vision]"
```

## Common commands

```bash
pytest                       # run the test-suite
ruff check .                 # lint
ruff format .                # auto-format
ruff format --check .        # verify formatting (CI)
mypy                         # strict type-check
sentinelswarm-sim            # run the demo
uvicorn sentinelswarm.api.app:app --reload   # live API + dashboard
```

All four checks (`ruff check`, `ruff format --check`, `mypy`, `pytest`) must pass — CI runs
exactly these on Python 3.11 and 3.12.

## Coding conventions

- **Typed**: full type hints; `mypy --strict` must pass. Public domain types stay
  framework-free (`domain/` has no FastAPI/pydantic imports).
- **Explicit state machines** over ad-hoc flags; validate transitions.
- **Deterministic control**: depend on the `Clock` abstraction, never `time`/`asyncio.sleep`
  directly, so logic stays testable.
- **Small components, clear seams**: keep the `DroneDriver`, `MessageBus` and repository
  interfaces intact so simulated/real, in-memory/broker, and in-memory/Postgres remain
  swappable.
- **Comments** explain *why*, not *what*. Keep them to a line where possible.
- **Line length** 100; ruff enforces import order and formatting.

## Tests

- Put fast, pure tests next to the behaviour they cover (`tests/test_*.py`).
- Use the `ManualClock` + `orchestrator` fixtures for async/integration tests so they are
  deterministic.
- Add or update an end-to-end assertion when you change fleet behaviour.

## Pull requests

1. Branch from `main`.
2. Keep changes focused; update docs when behaviour changes.
3. Ensure lint, format, type-check and tests pass locally.
4. Describe the behaviour change and how you verified it.

## Extension points looking for contributors

See the [roadmap](README.md#roadmap): a PX4/Gazebo `Ros2Px4Driver`, a NATS/MQTT bus adapter,
PostgreSQL repositories, OpenTelemetry traces, Grafana dashboards, and OpenCV environmental
detectors — all behind seams that already exist.
