# SentinelSwarm — Work Report: Tactical Patrol Planning & Execution Upgrade
**Datum:** 2026-09-25  
**Komponenten:** Backend (`sentinelswarm.agents.base`, `sentinelswarm.api.app`), Frontend (`app.js`, `app.css`, `index.html`), Tests (`test_mission.py`, `test_e2e_simulation.py`)  
**Status:** Abgeschlossen & Verifiziert (63/63 Pytest Tests, Ruff Clean, Mypy Clean, CDP End-to-End Browser Tests)

---

## 1. Übersicht & Zielsetzung

Die Patrol- und Scatter-Mechanismen wurden von statischen Prototypen zu einem vollwertigen, missionskritischen taktischen Missionsplanungssystem für heterogene Drohnenflotten erweitert. Folgende Kernanforderungen wurden umgesetzt:

1. **Exklusive Scout-Drohnen-Validierung:** Nur Aufklärungs- und Scout-Drohnen (`sim-scout`, `esp32-s3-mini`, `esp32-c6-nano`, `px4-mini-racer`) dürfen für Patrouillenmissionen eingeplant werden. Schwere Transport- und Lastendrohnen sind blockiert.
2. **Interaktive Missionsplanung auf der Tactical Map:**
   - **Zone Loiter:** Interaktives Aufziehen eines flexiblen Kreisradius auf der taktischen Karte mit Live-Vorschau.
   - **Multi-Point Waypoint Route:** Sequenzielles Setzen beliebig vieler Wegpunkte ($A \to B \to C$).
3. **Rundflug-Modus (Closed Circuit Loop):**
   - Option zur Schließung des Rundflugs direkt durch Anklicken von Startpunkt A oder über den HUD-Button `[☍ CLOSE LOOP (RUNDFLUG)]`.
   - Visuelle Schließlinie, Markierungsring und `RUNDFLUG ☍`-Callout.
   - Kontinuierliches Abfliegen des Rundkurses während der gesamten Patrouillendauer.
4. **Aktive Vollflächen-Erkundung bei Zone Loiter:**
   - Drohnen bleiben nicht mehr statisch im Zentrum stehen, sondern tasten den gesamten Bereich über 8 Perimeter-Wegpunkte ($r = 0{,}72 R$) und 4 Innen-Kreuzungspunkte ($r = 0{,}35 R$) kontinuierlich ab.
5. **Safe-RTB Batterie-Budgetierung & Missionsdauer:**
   - Automatische Berechnung der Flugdistanzen (Hinweg + Pfad + Rückweg zur Basis), des Transit-Verbrauchs und der sicheren RTB-Reserve (15% kritisch + 10% Reserve).
   - Anzeige der maximal sicheren Stationszeit und Schnellauswahlbuttons (30s, 60s, 120s, MAX SAFE).
6. **Terrain-Reset & Flotten-Decommissioning:**
   - Direkte Zurücksetzung der Coverage-Datenbank via `RESET TERRAIN`.
   - Sicheres Entfernen von Drohnen aus der Flotte via `REMOVE`-Aktion mit Bestätigungsdialog.

---

## 2. Technische Implementierung

### 2.1 Backend & Agent Autonomous Flight (`src/sentinelswarm/agents/base.py`)
- **`generate_zone_exploration_waypoints(center, radius, altitude)`:**
  Erzeugt eine algorithmische Abdeckungsroute innerhalb des kreisförmigen Loiter-Bereichs:
  - 8 Perimeter-Punkte bei $r = 0{,}72 \cdot \text{Radius}$ in $45^\circ$-Schritten.
  - 4 innere Cross-Points bei $r = 0{,}35 \cdot \text{Radius}$ in $90^\circ$-Schritten.
- **`_ActiveMission` & `DroneAgent._advance()`:**
  - Erweitert um `exploration_waypoints` und `exploration_index`.
  - Beim Übergang von `TRANSIT` zu `PATROLLING` wird die Wegpunktliste initialisiert.
  - In `PATROLLING`: Bei Erreichen eines Wegpunktes schaltet der Agent zyklisch zum nächsten Punkt weiter (`(index + 1) % len(waypoints)`), bis die vom Operator vorgegebene Missionsdauer abgelaufen ist.
  - Erkennung von Rundflügen: Liegt der Endpunkt innerhalb von 2.0m zum Startpunkt ($A \to B \dots \to A$), wird die Route kontinuierlich im Kreis abgeflogen.

### 2.2 API & Validierung (`src/sentinelswarm/api/app.py`)
- **Modell-Typisierung beim Hinzufügen:** `add_drone` übernimmt das gewählte Modell (`sim-scout`, `sim-heavy`, etc.) und hinterlegt es im Driver und FleetManager.
- **Scout-Gatekeeper:** Bei `create_mission` mit Typ `PATROL_ZONE` oder `WAYPOINT_ROUTE` wird geprüft, ob die zugewiesene Drohne eine Scout-Drohne ist. Bei Verletzung wird HTTP 422 (`Only scout-class drones are permitted for patrol missions`) zurückgegeben.
- **Terrain-Reset Endpoint:** `POST /api/{world}/terrain/reset` setzt das Grid und die Flächenstatistik zurück.
- **Drone-Decommissioning:** `DELETE /api/{world}/drones/{drone_id}` entfernt die Drohne sicher aus Manager und Simulation.

### 2.3 Frontend & Interaktion (`src/sentinelswarm/api/static/app.js` & `app.css`)
- **Taktische Strategie-Karten:** Große, barrierefreie Klickkarten (`.strategy-btn`) für *Zone Loiter* und *Multi-Point*.
- **Scout-Filterung im Dropdown:** Nur qualifizierte Scouts werden im Missions-Launcher aufgeführt; automatische Zuweisung des besten Scouts.
- **Rundflug-Steuerung:**
  - Click-to-Close: Klick auf Wegpunkt A (Radius 24px) aktiviert/deaktiviert den Rundflug.
  - HUD-Toggle: `[☍ CLOSE LOOP (RUNDFLUG)]` schaltet um auf `[✓ RUNDFLUG (CLOSED)]`.
  - HUD-Banner: Zeigt Punktanzahl, Gesamtdistanz inkl. Schließung und Rundflug-Status in einer einzeiligen Leiste (`white-space: nowrap`).
- **Canvas-Rendering:**
  - Gestrichelte Schließlinie bei aktivem Rundflug.
  - Zielkreis und `RUNDFLUG ☍`-Schriftzug am Startpunkt A.
- **Duration & Safe-RTB Dialog:**
  - Detailkarte mit Missionsprofil (`Zone Exploration (R = Xm)` bzw. `Rundflug / Circuit Loop (N pts)`), Taktischem Muster (`Continuous Closed Circuit Patrol` bzw. `Active Perimeter & Sector Coverage`), Transit-Distanz, Drain und maximal sichere Zeit.
  - Voreingestellte Dauer-Buttons und Max-Safe-Limitierung.

---

## 3. Qualitätssicherung & Verifikation

| Test / Gate | Ausführung | Ergebnis |
|---|---|---|
| **Pytest Test-Suite** | `pytest -q` (63 Tests) | **100% Passed** |
| **Ruff Linter** | `ruff check .` | **All checks passed (0 Fehler)** |
| **Ruff Formatter** | `ruff format --check .` | **43 files compliant** |
| **Mypy Static Typing** | `mypy` | **Success (34 files, 0 Fehler)** |
| **E2E Browser CDP Test (Patrol Modal)** | `test_full_patrol.py` | **7/7 Schritte bestanden** |
| **E2E Browser CDP Test (Rundflug & Zone)**| `test_rundflug_and_zone.py` | **Alle Schritte bestanden** |

### Screenshot-Referenzen:
- `patrol_rundflug_map.png`: Taktische Karte mit geschlossenem Dreiecks-Rundflug $A \to B \to C \to A$, Schließungsring und HUD-Leiste.
- `patrol_rundflug_dialog.png`: Safe-RTB Budgetdialog mit Profil `Rundflug / Circuit Loop (4 pts)` und `Continuous Closed Circuit Patrol`.
- `patrol_zone_dialog.png`: Safe-RTB Budgetdialog für Zone Loiter mit Profil `Zone Exploration (R = 50m)` und `Active Perimeter & Sector Coverage`.

