from ..db import Database
from .accounts import AccountsRepository
from .agents import AgentsRepository
from .requests import RequestsRepository
from .storages import StoragesRepository
from .control import ControlRepository
from .data_jobs import DataJobsRepository
from .batches import BatchesRepository
from .scan_paths import UserScanPathsRepository


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
