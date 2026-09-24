from qlda.application.google_sheets.service import normalize_production_sheet, parse_spreadsheet_id


def test_parse_google_sheet_url():
    url = "https://docs.google.com/spreadsheets/d/1Wl1JFfdbult5V4OaySCVu3Gz7GCzedMjt2905-bdJKk/edit?gid=128177642"
    assert parse_spreadsheet_id(url) == "1Wl1JFfdbult5V4OaySCVu3Gz7GCzedMjt2905-bdJKk"


def test_normalize_zone_progress_table():
    values = [
        ["BẢNG KHỐI LƯỢNG MEP"],
        ["Công tác", "B2", "", ""],
        ["Zone", "Zone 1", "Zone 2", "Zone 3"],
        ["CTN Tầng Hầm", "", "", ""],
        ["Thi công lắp đặt phần thô", "", "", ""],
        ["Thi công tuyến ống cấp nước rửa sàn", "100%", 1, 0.75],
        ["Thi công tuyến ống cấp nước vào bể nước sạch", 0.5, "25%", ""],
    ]
    rows = normalize_production_sheet("Hầm", values)
    assert len(rows) == 5
    assert rows[0].work_item == "Thi công tuyến ống cấp nước rửa sàn"
    assert rows[0].zone == "Zone 1"
    assert rows[0].progress_percent == 100.0
    assert rows[2].progress_percent == 75.0
    assert rows[3].progress_percent == 50.0
    assert rows[4].progress_percent == 25.0
