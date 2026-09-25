from __future__ import annotations

import unittest

from tools.architecture_guard import (
    check_layer_boundaries,
    check_no_dynamic_source_execution,
    check_no_new_patch_debt,
    check_runtime_init_side_effect_free,
    check_runtime_ui_feature_budget,
    check_streamlit_shell_frozen,
)


class ArchitectureBoundaryTests(unittest.TestCase):
    def test_runtime_core_import_is_side_effect_free(self):
        self.assertEqual(check_runtime_init_side_effect_free(), [])

    def test_domain_and_application_do_not_depend_on_runtime_core(self):
        self.assertEqual(check_layer_boundaries(), [])

    def test_materialized_streamlit_shell_cannot_grow(self):
        self.assertEqual(check_streamlit_shell_frozen(), [])

    def test_no_new_fix_patch_recovery_guard_modules(self):
        self.assertEqual(check_no_new_patch_debt(), [])

    def test_new_ui_features_must_live_in_presentation(self):
        self.assertEqual(check_runtime_ui_feature_budget(), [])

    def test_packaged_source_cannot_use_exec_or_eval(self):
        self.assertEqual(check_no_dynamic_source_execution(), [])


if __name__ == "__main__":
    unittest.main()
