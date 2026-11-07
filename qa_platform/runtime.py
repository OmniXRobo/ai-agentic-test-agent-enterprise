"""Runtime container: one place that wires services together."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from . import branding
from .agents.orchestration import OrchestrationAgent
from .ai.gateway import AIGateway
from .ai.managed import ManagedLLM
from .automation.framework.studio import ScriptStudio
from .automation.service import AutomationService
from .config import Paths, Settings
from .credentials import SecretBackend, SecretStore
from .defects.service import DefectService
from .design.data_agent import TestDataService
from .design.service import DesignService
from .design.suite_cases import SuiteCaseService
from .events import names
from .events.bus import EventBus
from .events.state import AgentStateManager
from .first_run import FirstRunSetup
from .health import HealthService
from .logging_setup import get_logger
from .knowledge.service import KnowledgeService
from .notifications import NotificationService
from .performance.service import PerformanceService
from .reports.analytics import AnalyticsService
from .reports.service import ReportService
from .runs import RunStore
from .storage.db import Database
from .voice.service import VoiceService
from .tfs.hierarchy import HierarchyService
from .tfs.service import TfsService

LIFECYCLE = (names.AGENT_STARTED, names.AGENT_COMPLETED, names.AGENT_FAILED)


log = get_logger("runtime")


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
    hierarchy: HierarchyService
    knowledge: KnowledgeService
    health: HealthService
    llm: ManagedLLM
    setup: FirstRunSetup
    mailer: Any = None  # 23.3.21: Outlook email
    scheduler: Any = None  # 23.3.21: Scheduled Runs
    perf_scheduler: Any = None  # 23.3.29: performance schedules
    ai_reports: Any = None  # 23.3.31: AI analysis reports with PDF and email

    def close(self) -> None:
        if self.scheduler is not None:
            self.scheduler.close()
        if self.perf_scheduler is not None:
            self.perf_scheduler.close()  # logs what closing means for the schedules
        try:
            self.studio.runner.close()  # 23.3.32: test runs (pytest and their browsers) this application started
        except Exception:  # closing goes on whatever one part does
            pass
        self.performance.close()  # 23.3.22: a running load test is stopped and recorded as INTERRUPTED
        self.reports.close()  # 23.3.28: a run report being made is finished before the database closes
        self.llm.close()  # only the llama-server this application started
        self.voice.fw.close()  # only the Faster-Whisper server this application started
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
    branding.configure(settings)
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
    design.cases = SuiteCaseService(design)  # suite-aware, acceptance-criterion traceable test cases
    design.datasets = TestDataService(design, design.cases)  # suite-aware, versioned test data rows
    automation = AutomationService(paths, settings, bus, db, runs, secrets, tfs, ai)
    performance = PerformanceService(paths, settings, bus, db, runs, ai, automation.discovery)
    performance.secrets = secrets  # 23.3.24: secrets of recorded scripts
    defects = DefectService(settings, bus, db, tfs, automation, performance)
    voice = VoiceService(settings, bus, db, runs, state, tfs, ai, design, automation, performance, defects,
                         transport=stt_transport)
    analytics = AnalyticsService(db, tfs, automation)
    reports = ReportService(paths, bus, db, tfs, automation, performance, defects, design, analytics)
    studio = ScriptStudio(paths, settings, bus, db, runs, secrets, tfs, ai, automation, design, defects)
    hierarchy = HierarchyService(tfs)
    knowledge = KnowledgeService(db, bus, hierarchy, automation, design)
    studio.knowledge = knowledge  # Unified QA Knowledge for the Automation Engineering Agent
    knowledge.studio = studio  # requirement -> discovery -> script generation chain
    health = HealthService(tfs, ai, orchestration, voice)
    voice.attach(studio=studio, reports=reports, notifications=notifications, health=health)
    # 23.3.7: Faster-Whisper speech recognition starts with the application when it is the chosen speech server.
    voice.fw.logs = paths.logs
    voice.fw.log_file = paths.logs / "faster-whisper.log"
    if stt_transport is None and voice.fw.selected() and voice.fw.config()["auto_start"]:
        voice.fw.start_in_background()
    llm = ManagedLLM(settings, paths)
    # 23.3.33: while the model loads the status light says "Starting", and it is checked again once loading ends,
    # instead of showing a cached "Offline" from the first seconds after start.
    health.llm = llm
    llm.on_state.append(lambda state: health.forget("ai"))
    if llm.config().get("auto_start") and ai_transport is None:
        try:
            llm.start(wait=False)  # once, when the application starts; never on page navigation
        except Exception as exc:  # the application still starts; the AI page shows why
            log.warning(f"Managed LLM did not start: {exc}")
    rt = Runtime(paths, settings, db, bus, state, notifications, runs, orchestration, secrets, tfs, ai, design,
                 automation, performance, defects, voice, analytics, reports, studio, hierarchy, knowledge, health, llm,
                 FirstRunSetup(settings, paths, tfs, llm, voice, health))
    # 23.3.21: Outlook email and Scheduled Runs (a background thread checks the schedules every 15 seconds)
    from .automation.scheduler import Scheduler
    from .mailer import Mailer

    rt.mailer = Mailer(settings, secrets)
    rt.scheduler = Scheduler(automation, paths.root / "schedules.json", tfs=tfs, mailer=rt.mailer,
                             reports_dir=paths.reports / "scheduled")
    rt.scheduler.studio = studio
    # 23.3.31: AI analysis reports (functional run, performance run, dashboard) with a PDF and an Outlook email
    from .reports.ai_reports import AiReportService

    rt.ai_reports = AiReportService(paths, ai, automation, performance, analytics, rt.mailer, agent=reports.agent)
    rt.scheduler.start()
    # 23.3.29: performance schedules. Queued scheduled runs left from before a shutdown follow their missed-run
    # policy before the run queue starts; then the schedules are checked every 15 seconds.
    from .performance.schedules import PerfScheduler

    rt.perf_scheduler = PerfScheduler(performance, reports=reports, notifications=notifications, mailer=rt.mailer,
                                      defects=defects)
    rt.perf_scheduler.recover()
    # 23.3.22: performance runs left active by an earlier shutdown become INTERRUPTED; the run queue starts
    performance.start()
    rt.perf_scheduler.start()
    return rt
