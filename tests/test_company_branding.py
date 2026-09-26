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

    def test_sidebar_logo_is_first_block_above_logout(self):
        css = branding._sidebar_logo_css(
            "data:image/png;base64,AAAA",
            opacity_pct=73,
            height_px=96,
            gap_px=11,
        )
        self.assertIn('[data-testid="stSidebarContent"]::before', css)
        self.assertIn("height:96px", css)
        self.assertIn("margin:0 0 11px 0", css)
        self.assertIn("background-position:center center", css)
        self.assertIn("opacity:0.730", css)
        self.assertIn("pointer-events:none", css)
        self.assertIn('[data-testid="stSidebar"] h3::before', css)
        self.assertIn("display:none!important", css)
        self.assertNotIn("stAppViewContainer", css)
        self.assertNotIn("position:fixed", css)

    def test_negative_gap_pulls_logout_closer_to_logo(self):
        css = branding._sidebar_logo_css(
            "data:image/png;base64,AAAA",
            opacity_pct=100,
            height_px=100,
            gap_px=-45,
        )
        self.assertIn("margin:0 0 -45px 0", css)
        self.assertIn("margin-bottom:-45px", css)

    def test_opacity_accepts_full_zero_to_one_hundred_percent_range(self):
        hidden = branding._sidebar_logo_css(
            "data:image/png;base64,AAAA",
            opacity_pct=0,
            height_px=90,
            gap_px=10,
        )
        self.assertIn('[data-testid="stSidebarContent"]::before', hidden)
        self.assertIn("display:none!important", hidden)

        full = branding._sidebar_logo_css(
            "data:image/png;base64,AAAA",
            opacity_pct=100,
            height_px=90,
            gap_px=10,
        )
        self.assertIn("opacity:1.000", full)

    def test_invalid_logo_type_is_rejected(self):
        with tempfile.TemporaryDirectory() as temp_dir, patch.dict(
            os.environ, {"QLDA_BRANDING_DIR": temp_dir}, clear=False
        ):
            with self.assertRaises(ValueError):
                branding.save_company_logo(b"not-an-image")


if __name__ == "__main__":
    unittest.main()
