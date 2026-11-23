from ..db import Database
from .accounts import AccountsRepository
from .agents import AgentsRepository
from .requests import RequestsRepository
from .storages import StoragesRepository
from .control import ControlRepository
from .data_jobs import DataJobsRepository
from .batches import BatchesRepository
from .scan_paths import UserScanPathsRepository
from .builds import BuildsRepository
from .observability import ObservabilityRepository
from .releases import ReleasesRepository
from .metrics import MetricsRepository
from .sync_pairs import SyncPairsRepository
from .mail_settings import MailSettingsRepository
from .scan_digests import ScanDigestsRepository
from .node_exclusions import NodeExclusionsRepository
from .request_purges import RequestPurgesRepository
from .k8s import K8sRepository
from .batch_schedules import BatchSchedulesRepository
from .api_base_checks import ApiBaseChecksRepository


class Repositories:
    """저장소 집합. API/컨트롤러는 이 객체 하나로 DB에 접근한다."""
    def __init__(self, db: Database):
        self.db = db
        self.accounts = AccountsRepository(db)
        self.agents = AgentsRepository(db)
        self.requests = RequestsRepository(db)
        self.storages = StoragesRepository(db)
        self.control = ControlRepository(db)
        self.data_jobs = DataJobsRepository(db)
        self.batches = BatchesRepository(db)
        self.scan_paths = UserScanPathsRepository(db)
        self.builds = BuildsRepository(db)
        self.observability = ObservabilityRepository(db)
        self.releases = ReleasesRepository(db)
        self.metrics = MetricsRepository(db)
        self.sync_pairs = SyncPairsRepository(db)
        self.mail_settings = MailSettingsRepository(db)
        self.scan_digests = ScanDigestsRepository(db)
        self.node_exclusions = NodeExclusionsRepository(db)
        self.request_purges = RequestPurgesRepository(db)
        self.k8s = K8sRepository(db)
        # 배치 스케줄러(2026-11-16) -- 규칙·판정은 dms.batch_scheduler, 회차는 컨트롤러 batch-scheduler 루프.
        self.batch_schedules = BatchSchedulesRepository(db)
        self.api_base_checks = ApiBaseChecksRepository(db)
