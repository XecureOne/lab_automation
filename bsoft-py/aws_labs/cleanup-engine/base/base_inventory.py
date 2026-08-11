from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Optional

from base.aws_client import AWSClientFactory
from checkpoint import CheckpointStore
from config import EngineConfig
from logger import StageLogger
from models.inventory_result import InventoryResult


class BaseInventoryService(ABC):
    service_name: str = "unknown"

    def __init__(
        self,
        clients: AWSClientFactory,
        config: EngineConfig,
        logger: StageLogger,
        checkpoint: Optional[CheckpointStore] = None,
    ):
        self.clients = clients
        self.config = config
        self.logger = logger
        self.checkpoint = checkpoint

    @abstractmethod
    def discover(self) -> InventoryResult:
        raise NotImplementedError
