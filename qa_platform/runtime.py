"""Runtime container: one place that wires services together."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from .agents.orchestration import OrchestrationAgent
from .ai.gateway import AIGateway
from .automation.framework.studio import ScriptStudio
from .automation.service import AutomationService
from .config import Paths, Settings
from .credentials import SecretBackend, SecretStore
from .defects.service import DefectService
from .design.service import DesignService
from .events import names
from .events.bus import EventBus
from .events.state import AgentStateManager
from .notifications import NotificationService
from .performance.service import PerformanceService
from .reports.analytics import AnalyticsService
from .reports.service import ReportService
from .runs import RunStore
from .storage.db import Database
from .voice.service import VoiceService
from .tfs.service import TfsService

LIFECYCLE = (names.AGENT_STARTED, names.AGENT_COMPLETED, names.AGENT_FAILED)


@dataclass
class Runtime:
    paths: Paths
    settings: Settings
    db: Database
    bus: EventBus
    state: AgentStateManager
    notifications: NotificationService
    runs: RunStore
    orchestration: OrchestrationAgent
    secrets: SecretStore
    tfs: TfsService
    ai: AIGateway
    design: DesignService
    automation: AutomationService
    performance: PerformanceService
    defects: DefectService
    voice: VoiceService
    analytics: AnalyticsService
    reports: ReportService
    studio: ScriptStudio

    def close(self) -> None:
        self.db.close()


def build_runtime(
    paths: Paths | None = None,
    *,
    secret_backend: SecretBackend | None = None,
    tfs_transport: Any = None,
    ai_transport: Any = None,
    stt_transport: Any = None,
) -> Runtime:
    paths = (paths or Paths()).ensure()
    settings = Settings(paths.settings_file)
    db = Database(paths.db_file)
    bus = EventBus(db)
    state = AgentStateManager()
    placeholders = ",".join("?" for _ in LIFECYCLE)
    last = db.query(
        f"SELECT e.* FROM events e JOIN (SELECT agent_id, MAX(id) AS id FROM events WHERE name IN ({placeholders})"
        " GROUP BY agent_id) m ON e.id = m.id",
        LIFECYCLE,
    )
    state.restore(last)
    bus.add_listener(state.apply)
    notifications = NotificationService(db, bus)
    runs = RunStore(db)
    runs.mark_interrupted()
    orchestration = OrchestrationAgent(bus, db, settings, paths, runs)
    secrets = SecretStore(secret_backend)
    tfs = TfsService(settings, secrets, bus, transport=tfs_transport)
    ai = AIGateway(settings, bus, transport=ai_transport, secrets=secrets)
    design = DesignService(bus, db, runs, tfs, ai)
    automation = AutomationService(paths, settings, bus, db, runs, secrets, tfs, ai)
    performance = PerformanceService(paths, settings, bus, db, runs, ai, automation.discovery)
    defects = DefectService(settings, bus, db, tfs, automation, performance)
    voice = VoiceService(settings, bus, db, runs, state, tfs, ai, design, automation, performance, defects,
                         transport=stt_transport)
    analytics = AnalyticsService(db, tfs, automation)
    reports = ReportService(paths, bus, db, tfs, automation, performance, defects, design, analytics)
    studio = ScriptStudio(paths, settings, bus, db, runs, secrets, tfs, ai, automation, design, defects)
    return Runtime(paths, settings, db, bus, state, notifications, runs, orchestration, secrets, tfs, ai, design,
                   automation, performance, defects, voice, analytics, reports, studio)
