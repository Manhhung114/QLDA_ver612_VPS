from __future__ import annotations

import unittest
from unittest.mock import patch

from qlda.modules.contractor_data.worker import run_project


class _FakeService:
    last_client = "unset"

    def __init__(self, db, client=None):
        del db
        type(self).last_client = client

    def sync_due_spaces(self, master_project_id: int):
        return {
            "due": 1,
            "success": 1,
            "errors": 0,
            "records": 12,
            "master_project_id": int(master_project_id),
        }


class ContractorDataWorkerPublicLinkTests(unittest.TestCase):
    def test_project_without_oauth_is_not_skipped(self):
        with patch(
            "qlda.application.contractor_data_hub.ContractorDataHubService",
            _FakeService,
        ), patch(
            "qlda.runtime_core.google_connection_store.load_project_connection",
            return_value={},
        ), patch(
            "qlda.runtime_core.google_oauth_settings.apply_to_environment",
            return_value=None,
        ):
            result = run_project(object(), 77)

        self.assertIsNone(_FakeService.last_client)
        self.assertFalse(result.get("skipped", False))
        self.assertFalse(result["oauth_connected"])
        self.assertEqual(result["success"], 1)
        self.assertEqual(result["records"], 12)


if __name__ == "__main__":
    unittest.main()
