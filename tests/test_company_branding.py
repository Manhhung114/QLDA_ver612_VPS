from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from qlda.presentation.streamlit import company_branding as branding


_PNG_1X1 = (
    b"\x89PNG\r\n\x1a\n"
    b"\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01\x08\x06\x00\x00\x00\x1f\x15\xc4\x89"
)


class CompanyBrandingTests(unittest.TestCase):
    def test_save_and_delete_company_logo(self):
        with tempfile.TemporaryDirectory() as temp_dir, patch.dict(
            os.environ, {"QLDA_BRANDING_DIR": temp_dir}, clear=False
        ):
            saved = branding.save_company_logo(_PNG_1X1)
            self.assertEqual(saved, Path(temp_dir) / "company_logo.png")
            self.assertEqual(branding.current_logo_path(), saved)
            self.assertTrue(saved.exists())

            self.assertTrue(branding.delete_company_logo())
            self.assertIsNone(branding.current_logo_path())
            self.assertFalse(saved.exists())

    def test_watermark_is_centered_background_and_non_interactive(self):
        css = branding._watermark_css(
            "data:image/png;base64,AAAA",
            opacity=0.055,
            width_vw=34,
        )
        self.assertIn("background-position:center 58%", css)
        self.assertIn("pointer-events:none", css)
        self.assertIn("position:fixed", css)
        self.assertIn("background-repeat:no-repeat", css)
        self.assertNotIn("bottom:6px", css)
        self.assertNotIn("right:8px", css)

    def test_invalid_logo_type_is_rejected(self):
        with tempfile.TemporaryDirectory() as temp_dir, patch.dict(
            os.environ, {"QLDA_BRANDING_DIR": temp_dir}, clear=False
        ):
            with self.assertRaises(ValueError):
                branding.save_company_logo(b"not-an-image")


if __name__ == "__main__":
    unittest.main()
