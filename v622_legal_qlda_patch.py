from __future__ import annotations


PATCH_MARKER = "V6.22 LEGAL QLXD UI V3 PCCC OFFICIAL BACKFILL"


def _replace_once(source: str, old: str, new: str, label: str) -> str:
    count = source.count(old)
    if count != 1:
        raise RuntimeError(f"{PATCH_MARKER}: expected one {label}, found {count}")
    return source.replace(old, new, 1)


def patch_legal_qlda(source: str) -> str:
    if PATCH_MARKER in source:
        return source

    render_old = '''def render_legal_documents():
    from legal_documents import sync_source, sync_all, search_online_all, search_online_sites
    legal_repo = _legal_repo_for_view()
    st.subheader("📚 Văn bản QLDA Xây dựng")
    _ui_note("Luật • Nghị định • Thông tư • QCVN • TCVN • Quyết định • Dự thảo — TVPL là nguồn tra cứu chính/ưu tiên; luôn giữ link để mở văn bản trực tiếp.")
'''
    render_new = f'''def render_legal_documents():
    # {PATCH_MARKER}
    # Legal crawler stays lazy: install the expanded QLXD policy only when this
    # sheet is opened, preserving normal app cold-start performance.
    from legal_qlda_v622 import install_legal_qlda, purge_drafts
    from legal_standards_backfill_v622 import install_legal_standard_backfill
    from legal_pccc_backfill_v622 import install_legal_pccc_backfill, seed_official_legal_documents
    install_legal_qlda()
    install_legal_standard_backfill()
    install_legal_pccc_backfill()
    from legal_documents import sync_source, sync_all, search_online_all, search_online_sites
    legal_repo = _legal_repo_for_view()
    # Critical legal anchors are inserted from official URLs once/process. This
    # prevents old-but-current documents (e.g. 06/2021/TT-BXD) from disappearing
    # merely because an external search source ranks them below newer results.
    seed_official_legal_documents(legal_repo)
    purge_drafts(legal_repo)
    st.subheader("📚 Văn bản QLDA Xây dựng")
    _ui_note("Luật • Nghị định • Thông tư • Quyết định • QCVN • TCVN — có backfill chính thức cho TT-BXD và PCCC/CNCH; PCCC quét riêng Công báo/VBPL cùng toàn họ VSQI ICS 13.220.")
'''
    source = _replace_once(source, render_old, render_new, "legal render header")

    actions_old = '''    c1, c2, c3, c4, c5 = st.columns(5)
    actions = [
        (c1, "🔄 Cập nhật tất cả", "all"),
        (c2, "⚖️ VBPL / Chính phủ", "vbpl"),
        (c3, "📐 TCVN - VSQI", "vsqi"),
        (c4, "📝 Dự thảo BXD", "moc_drafts"),
        (c5, "📚 Cập nhật TVPL (ưu tiên)", "tvpl"),
    ]
'''
    actions_new = '''    c1, c2, c3, c4, c5 = st.columns(5)
    actions = [
        (c1, "🔄 Cập nhật QLXD", "all"),
        (c2, "⚖️ VBPL / Chính phủ", "vbpl"),
        (c3, "📐 QCVN / TCVN - VSQI", "vsqi"),
        (c4, "🔥 PCCC / CNCH", "pccc"),
        (c5, "📚 QLXD mở rộng - TVPL", "tvpl"),
    ]
'''
    source = _replace_once(source, actions_old, actions_new, "legal source buttons")

    filter_old = '''    f1, f2, f3, f4 = st.columns([3, 1.2, 1.5, 1.6])
    keyword = f1.text_input("Tìm số hiệu / tên / lĩnh vực", key="legal_keyword")
    category = f2.selectbox("Loại", cats, key="legal_category")
    status = f3.selectbox("Hiệu lực / trạng thái", statuses, key="legal_status")
    source = f4.selectbox("Nguồn", sources, key="legal_source")
    include_drafts = st.checkbox("Hiển thị cả dự thảo đang lấy ý kiến", value=True, key="legal_include_drafts")

    rows = legal_repo.list_documents(keyword, category, status, source, include_drafts)
    total = len(rows)
    active = sum(1 for r in rows if "còn hiệu lực" in (r["status"] or "").lower() and "hết hiệu lực" not in (r["status"] or "").lower())
    drafts = sum(1 for r in rows if r["is_draft"])
    standards = sum(1 for r in rows if r["category"] in ("TCVN", "QCVN", "Dự thảo TCVN", "Dự thảo QCVN"))
    m1, m2, m3, m4 = st.columns(4)
    m1.metric("Tổng văn bản", total)
    m2.metric("Còn hiệu lực", active)
    m3.metric("TCVN / QCVN", standards)
    m4.metric("Dự thảo", drafts)
'''
    filter_new = '''    f1, f2, f3, f4 = st.columns([3, 1.2, 1.5, 1.6])
    keyword = f1.text_input("Tìm số hiệu / tên / lĩnh vực", key="legal_keyword")
    category = f2.selectbox("Loại", cats, key="legal_category")
    status = f3.selectbox("Hiệu lực / trạng thái", statuses, key="legal_status")
    source = f4.selectbox("Nguồn", sources, key="legal_source")

    # Dự thảo đã được loại khỏi kho/luồng cập nhật; chỉ hiển thị văn bản chính thức
    # và nguồn tham khảo pháp luật phục vụ QLXD.
    rows = legal_repo.list_documents(keyword, category, status, source, False)
    total = len(rows)
    active = sum(1 for r in rows if "còn hiệu lực" in (r["status"] or "").lower() and "hết hiệu lực" not in (r["status"] or "").lower())
    standards = sum(1 for r in rows if r["category"] in ("TCVN", "QCVN"))
    pccc = sum(
        1 for r in rows
        if any(token in " ".join(str(r[k] or "") for k in ("number", "title", "field")).lower()
               for token in ("pccc", "phòng cháy", "chữa cháy", "an toàn cháy", "cứu nạn", "cnch"))
    )
    m1, m2, m3, m4 = st.columns(4)
    m1.metric("Tổng văn bản QLXD", total)
    m2.metric("Còn hiệu lực", active)
    m3.metric("QCVN / TCVN", standards)
    m4.metric("PCCC / CNCH", pccc)
'''
    source = _replace_once(source, filter_old, filter_new, "legal draft filter/metrics")
    return source
