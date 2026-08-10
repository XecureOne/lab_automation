from __future__ import annotations

import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Dict, List

from base.base_cleanup import BaseCleanupService
from config import EngineConfig
from models.verification_result import VerificationResult
from utils import write_json


class VerificationEngine:
    def __init__(self, plugins: List[BaseCleanupService], config: EngineConfig, logger):
        self.plugins = plugins
        self.config = config
        self.logger = logger

    def run(self) -> Dict[str, VerificationResult]:
        results: Dict[str, VerificationResult] = {}
        with ThreadPoolExecutor(max_workers=self.config.parallel_workers) as pool:
            futures = {pool.submit(plugin.verify): plugin for plugin in self.plugins}
            for future in as_completed(futures):
                plugin = futures[future]
                try:
                    results[plugin.service_name] = future.result()
                except Exception as exc:  # noqa: BLE001
                    self.logger.error(f"Verification for '{plugin.service_name}' raised: {exc}")
                    results[plugin.service_name] = VerificationResult(plugin.service_name, passed=False, error=str(exc))
        return results

    def run_until_pass(self, max_passes: int = 1, delay_seconds: int = 15) -> Dict[str, VerificationResult]:
        results: Dict[str, VerificationResult] = {}
        passes = max(1, max_passes)
        for attempt in range(1, passes + 1):
            results = self.run()
            if self.overall_passed(results):
                return results
            if attempt < passes:
                remaining = [name for name, r in results.items() if not r.passed]
                self.logger.verify(
                    f"Verification pass {attempt}/{passes} found remaining resources in {remaining}; "
                    f"retrying in {delay_seconds}s"
                )
                time.sleep(delay_seconds)
        return results

    def overall_passed(self, results: Dict[str, VerificationResult]) -> bool:
        if not results:
            return True
        return all(r.passed for r in results.values())

    def write(self, results: Dict[str, VerificationResult], path: str = None) -> str:
        path = path or f"{self.config.report_dir}/verification.json"
        payload = {
            "services": [results[name].to_dict() for name in sorted(results)],
            "overall": "PASS" if self.overall_passed(results) else "FAIL",
        }
        write_json(path, payload)
        self.logger.info(f"Verification written to {path}")
        return path
