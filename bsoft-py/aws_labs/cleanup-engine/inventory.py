from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Dict, List

from base.base_cleanup import BaseCleanupService
from config import EngineConfig
from models.inventory_result import InventoryResult
from utils import write_json


class InventoryEngine:
    def __init__(self, plugins: List[BaseCleanupService], config: EngineConfig, logger):
        self.plugins = plugins
        self.config = config
        self.logger = logger

    def run(self) -> Dict[str, InventoryResult]:
        results: Dict[str, InventoryResult] = {}
        with ThreadPoolExecutor(max_workers=self.config.parallel_workers) as pool:
            futures = {pool.submit(plugin.discover): plugin for plugin in self.plugins}
            for future in as_completed(futures):
                plugin = futures[future]
                try:
                    results[plugin.service_name] = future.result()
                except Exception as exc:  # noqa: BLE001
                    self.logger.error(f"Inventory scan for '{plugin.service_name}' raised: {exc}")
                    results[plugin.service_name] = InventoryResult(service_name=plugin.service_name, error=str(exc))
        return results

    def write(self, results: Dict[str, InventoryResult], path: str = None) -> str:
        path = path or f"{self.config.report_dir}/inventory.json"
        payload = {
            "services": [results[name].to_dict() for name in sorted(results)],
            "total_resources": sum(r.resource_count for r in results.values()),
        }
        write_json(path, payload)
        self.logger.info(f"Inventory written to {path}")
        return path
