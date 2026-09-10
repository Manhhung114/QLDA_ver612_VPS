from __future__ import annotations


PATCH_MARKER = "V6.22 BOQ CLAIM PRICE HEADER GUARD V2"


def install_boq_claim_price_header_guard() -> None:
    """Disambiguate real Claim headers: Khối lượng and Đơn giá -> Vật tư/Nhân công."""
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
                # Exact/short Khối lượng labels are stronger than carried group labels.
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

        # Legacy semantic parser asks for "Đơn giá lắp đặt". Real signed Claim
        # workbooks use a grouped parent "Đơn giá (VNĐ)" with child "Nhân công".
        if wanted == ("don gia lap dat",):
            strong = []
            weak = []
            for index, raw in enumerate(headers):
                text = str(raw or "")
                if any(word in text for word in ("gia tri nghiem thu", "khau tru", "giu lai")):
                    continue
                if "nhan cong" in text:
                    if "don gia" in text:
                        strong.append(index)
                    else:
                        weak.append(index)
                elif "lap dat" in text and "don gia" in text:
                    strong.append(index)
            if strong:
                return strong[0]
            if weak:
                return weak[0]

        # Material price is already a native field, but make the grouped
        # Vật tư/Vật liệu header explicit for the same real workbook family.
        if wanted == ("don gia vat tu",):
            strong = []
            weak = []
            for index, raw in enumerate(headers):
                text = str(raw or "")
                if any(word in text for word in ("gia tri nghiem thu", "khau tru", "giu lai")):
                    continue
                if "vat tu" in text or "vat lieu" in text:
                    if "don gia" in text:
                        strong.append(index)
                    else:
                        weak.append(index)
            if strong:
                return strong[0]
            if weak:
                return weak[0]

        return original_find(headers, include, exclude)

    adaptive._find_header_col = find_header_col_guarded
    adaptive._qlda_price_header_guard_installed = True
    adaptive._qlda_price_header_guard_marker = PATCH_MARKER
