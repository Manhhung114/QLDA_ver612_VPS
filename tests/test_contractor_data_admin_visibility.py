from __future__ import annotations

import unittest

from qlda.runtime_core.contractor_data_admin_visibility import (
    _RestrictedRepo,
    _RestrictedService,
    _RestrictedStreamlit,
    visible_tab_labels,
)


class _Repo:
    def project_metrics(self, *args, **kwargs):
        return [{"contractor_name": "SME", "last_error": "Google timeout"}]

    def records(self):
        return [1, 2, 3]


class _Service:
    def __init__(self):
        self.repo = _Repo()

    def project_alerts(self, *args, **kwargs):
        return [{"level": "warning", "message": "missing source"}]


class _Streamlit:
    def __init__(self):
        self.warning_calls = 0
        self.info_calls = 0

    def warning(self, *args, **kwargs):
        self.warning_calls += 1

    def info(self, *args, **kwargs):
        self.info_calls += 1
        return "info"


class ContractorDataAdminVisibilityTests(unittest.TestCase):
    def test_admin_sees_management_tabs(self):
        self.assertEqual(
            visible_tab_labels(True),
            ["📈 Tổng quan", "🏢 Kho nhà thầu", "🔗 Nguồn Google", "🕘 Lịch sử"],
        )

    def test_non_admin_does_not_see_management_tabs(self):
        labels = visible_tab_labels(False)
        self.assertEqual(labels, ["📈 Tổng quan", "🕘 Lịch sử"])
        self.assertNotIn("🏢 Kho nhà thầu", labels)
        self.assertNotIn("🔗 Nguồn Google", labels)

    def test_non_admin_alert_feed_and_error_text_are_hidden(self):
        restricted = _RestrictedService(_Service())
        self.assertEqual(restricted.project_alerts(1), [])
        metrics = restricted.repo.project_metrics(1)
        self.assertEqual(metrics[0]["last_error"], "")
        self.assertEqual(restricted.repo.records(), [1, 2, 3])

    def test_non_admin_warning_banner_is_suppressed(self):
        base = _Streamlit()
        restricted = _RestrictedStreamlit(base)
        restricted.warning("secret warning")
        self.assertEqual(base.warning_calls, 0)
        self.assertEqual(restricted.info("normal info"), "info")
        self.assertEqual(base.info_calls, 1)


if __name__ == "__main__":
    unittest.main()
