from ..db import Database
from .requests import RequestsRepository
from .storages import StoragesRepository


class Repositories:
    """저장소 집합. API/컨트롤러는 이 객체 하나로 DB에 접근한다."""
    def __init__(self, db: Database):
        self.db = db
        self.requests = RequestsRepository(db)
        self.storages = StoragesRepository(db)
