from __future__ import annotations

import unittest

import ipc_claim_v622 as ipc
from ipc_claim_number_fix_v622 import _apply_filename_identity, claim_no_from_filename


class IPCClaimNumberTests(unittest.TestCase):
    def test_extracts_number_from_user_filename(self):
        self.assertEqual(claim_no_from_filename("(SME213) IPC#6 (11102025).xlsx"), "6")
        self.assertEqual(claim_no_from_filename("IPC 03 Final.xlsx"), "3")
        self.assertEqual(claim_no_from_filename("Claim-12 Rev1.xlsm"), "12")

    def test_filename_prevents_overwriting_previous_claim(self):
        result = {
            "claim_no": "5",
            "claim_code": "IPC-05",
            "metadata": {"claim_no": "5"},
            "warnings": [],
        }
        fixed = _apply_filename_identity(result, "(SME213) IPC#6 (11102025).xlsx")
        self.assertEqual(fixed["claim_no"], "6")
        self.assertEqual(fixed["claim_code"], "IPC-06")
        self.assertEqual(fixed["metadata"]["claim_no"], "6")
        self.assertTrue(any("CẢNH BÁO SỐ CLAIM" in x for x in fixed["warnings"]))

    def test_revised_old_claim_keeps_old_claim_number(self):
        result = {
            "claim_no": "3",
            "claim_code": "IPC-03",
            "metadata": {"claim_no": "3"},
            "warnings": [],
        }
        fixed = _apply_filename_identity(result, "(SME213) IPC#3 Rev2.xlsx")
        self.assertEqual(fixed["claim_no"], "3")
        self.assertEqual(fixed["claim_code"], "IPC-03")
        self.assertEqual(fixed["warnings"], [])


if __name__ == "__main__":
    unittest.main()
