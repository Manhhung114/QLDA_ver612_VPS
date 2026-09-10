from __future__ import annotations


PATCH_MARKER = "V6.22 BOQ CLAIM PRICE HEADER GUARD V1"


def install_boq_claim_price_header_guard() -> None:
    """Prefer the standalone Khối lượng column over description text containing 'khối lượng'."""
    import ipc_adaptive_parser_v622 as adaptive

    if getattr(adaptive, "_qlda_price_header_guard_installed", False):
        return

    original_find = adaptive._find_header_col

    def find_header_col_guarded(headers, include, exclude=()):
        wanted = tuple(include or ())
        if wanted == ("hop dong", "khoi luong"):
            blocked = (
                "nghiem thu", "vat tu", "vat lieu", "lap dat", "nhan cong",
                "ten cong tac", "dien giai", "noi dung", "hang muc cong viec",
                "thanh tien", "gia tri",
            )
            candidates = []
            for index, raw in enumerate(headers):
                text = str(raw or "")
                if "khoi luong" not in text or any(word in text for word in blocked):
                    continue
                # Exact/short Khối lượng labels are stronger than a carried group label.
                score = 100 if text.strip() == "khoi luong" else 50
                if "hop dong" in text:
                    score += 20
                candidates.append((score, index))
            if candidates:
                candidates.sort(key=lambda item: (-item[0], item[1]))
                return candidates[0][1]

            found = original_find(headers, include, exclude)
            if found is not None:
                text = str(headers[found] or "")
                if not any(word in text for word in ("ten cong tac", "dien giai", "noi dung", "hang muc cong viec")):
                    return found
            return None

        return original_find(headers, include, exclude)

    adaptive._find_header_col = find_header_col_guarded
    adaptive._qlda_price_header_guard_installed = True
    adaptive._qlda_price_header_guard_marker = PATCH_MARKER
