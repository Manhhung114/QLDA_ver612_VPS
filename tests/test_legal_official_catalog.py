from qlda.application.legal_sources import (
    CONSTRUCTION, MEPF, PRIORITY_NUMBERS, collect_live,
    normalize_official, official_url, reconcile,
)


def test_official_urls_reject_spoofed_and_insecure():
    assert official_url('https://congbao.chinhphu.vn/van-ban/a')
    assert official_url('https://tieuchuan.vsqi.gov.vn/tieuchuan/view?sohieu=x')
    assert not official_url('https://congbao.chinhphu.vn.evil.test/a')
    assert not official_url('http://vbpl.vn/abc')
    assert not official_url('https://thuvienphapluat.vn/a')


def test_construction_and_mepf_seed_dedup_and_drafts():
    row = {'number': 'TCVN 2737:2023', 'title': 'Tải trọng và tác động',
           'source_url': 'https://tieuchuan.vsqi.gov.vn/tieuchuan/view?sohieu=x'}
    payload = reconcile([(row, CONSTRUCTION), (row, CONSTRUCTION), (row, MEPF)])
    assert len(payload['vanBan']) == 2
    assert payload['vanBan'][0]['linhVuc'] == CONSTRUCTION
    assert normalize_official({**row, 'title': 'Dự thảo TCVN'}, CONSTRUCTION) is None


def test_tcvn_not_accepted_from_non_vsqi_domain():
    row = {'number': 'TCVN 2737:2023', 'title': 'Tải trọng',
           'source_url': 'https://congbao.chinhphu.vn/a'}
    assert normalize_official(row, CONSTRUCTION) is None


def test_live_sync_searches_both_branches_and_keeps_only_official_results():
    calls = []
    def search(q):
        calls.append(q)
        return [{'number': 'TCVN 5574:2018', 'title': 'Kết cấu bê tông',
                 'source_url': 'https://tieuchuan.vsqi.gov.vn/tieuchuan/view?sohieu=TCVN+5574%3A2018'},
                {'number': 'TCVN 9999:2026', 'title': 'Dự thảo',
                 'source_url': 'https://tieuchuan.vsqi.gov.vn/x', 'is_draft': 1}]
    discovered = collect_live(search, search)
    clean = reconcile(discovered)['vanBan']
    assert any(r['linhVuc'] == CONSTRUCTION for r in clean)
    assert any(r['linhVuc'] == MEPF for r in clean)
    assert all(r['soHieu'] != 'TCVN 9999:2026' for r in clean)
    assert '206/2026/NĐ-CP' in calls
    assert 'TCVN 5687:2024' in calls
    assert 'TCVN 2737:2023' in PRIORITY_NUMBERS[CONSTRUCTION]
    assert all(r['tinhTrang'] == 'Cần kiểm tra hiệu lực tại nguồn' for r in clean)
