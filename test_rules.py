"""python3 -m unittest test_rules -v"""
import unittest

from fetch import any_date, gazette_zip_links, parse_ntpc_transport, parse_rss_date, parse_taipei_police, roc_to_iso, strip_tags, tidy_explain
from rules import classify
import json
import tempfile
from datetime import date
from pathlib import Path

from export_announcements import merge_previous, official_points, write_feed


class Rules(unittest.TestCase):
    def test_traffic_rule_is_relevant(self):
        c = classify("交通部、內政部令：修正「道路交通安全規則」第50條", "", "汽車駕駛執照為駕駛汽車之許可憑證")
        self.assertTrue(c["relevant"])
        self.assertIn("罰則與交通規則", c["topics"])
        self.assertEqual(c["audience"], "一般車主")

    def test_noise_needs_vehicle_context(self):
        self.assertFalse(classify("環境部公告：修正營建工程噪音管制標準", "", "工地施工噪音管制")["relevant"])
        self.assertTrue(classify("環境部公告：修正機動車輛噪音管制標準", "", "使用中機車排氣管噪音")["relevant"])

    def test_factory_emission_excluded(self):
        c = classify("環境部預告「鋼鐵業集塵灰高溫冶煉設施戴奧辛管制及排放標準」修正草案", "", "固定污染源排放標準")
        self.assertFalse(c["relevant"])

    def test_railway_news_excluded(self):
        c = classify("臺鐵公司因應演唱會加開區間車疏運", "", "加開列車疏運旅客")
        self.assertFalse(c["relevant"])

    def test_unrelated(self):
        self.assertFalse(classify("中央選舉委員會令：修正「選舉工作費支給數額基準」", "", "選務人員")["relevant"])

    def test_audience_tags(self):
        self.assertEqual(classify("交通部令：修正「計程車客運服務業申請核准經營辦法」", "", "道路交通管理處罰條例 車輛檢驗 罰鍰 汽車")["audience"], "營業車")
        self.assertEqual(classify("交通部公告：預告「車輛型式安全審驗管理辦法」修正草案", "", "電動汽車 車輛")["audience"], "車廠與業者")

    def test_vehicle_tags(self):
        c = classify("預告「老舊車輛汰舊換新空氣污染物減量補助辦法」修正草案", "", "燃油機車、燃油小客車 汰舊換新 電動車")
        self.assertEqual(c["vehicles"], ["汽車", "機車"])


class Helpers(unittest.TestCase):
    def test_roc_dates(self):
        self.assertEqual(roc_to_iso("中華民國115年11月18日"), "2026-11-18")
        self.assertEqual(roc_to_iso("115-11-03"), "2026-11-03")
        self.assertIsNone(roc_to_iso("115年2月30日"))
        self.assertIsNone(roc_to_iso(""))

    def test_rss_dates(self):
        self.assertEqual(parse_rss_date("2026-11-18"), "2026-11-18")
        self.assertEqual(parse_rss_date("Wed, 18 Nov 2026 10:00:00 +0800"), "2026-11-18")
        self.assertIsNone(parse_rss_date("沒有日期"))

    def test_strip_tags(self):
        self.assertEqual(strip_tags("<p>甲&amp;乙</p><script>x()</script><div>丙</div>"), "甲&乙\n丙")

    def test_zip_links(self):
        page = '<a href="fileView.do?fileType=zipPath&amp;fileName=abc&amp;zipfile=115-11-18.zip">x</a>'
        links = gazette_zip_links(page)
        self.assertEqual(list(links), ["115-11-18"])
        self.assertIn("&fileName=abc&", links["115-11-18"])

    def test_taipei_police_rows(self):
        page = ('<table><tbody><tr> <td data-title="編號"><span>1</span></td><td data-title="標題"><span>'
                '<a href="News_Content.aspx?n=B1&amp;sms=72&amp;s=89" title="x">馬拉松週日登場 相關交通管制措施</a></span></td>'
                '<td class="c" align="center" data-title="發布日期" headers="h"><span>115-11-19</span></td>'
                '<td data-title="發布機關"><span>交通警察大隊</span></td> </tr>'
                '<tr><td>沒有連結的列</td></tr></tbody></table>')
        rows = parse_taipei_police(page)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["date"], "2026-11-19")
        self.assertEqual(rows[0]["title"], "馬拉松週日登場 相關交通管制措施")
        self.assertEqual(rows[0]["url"], "https://td.police.gov.taipei/News_Content.aspx?n=B1&sms=72&s=89")
        self.assertEqual(rows[0]["region"], "臺北市")

    def test_ntpc_transport_rows(self):
        page = ('<div class="list"> <a href="home.jsp?id=54fa46e9e522dde4&act=be4f48068b2b0031&dataserno=aa8a8ef112ad4622d2d951452f5c3ce8" '
                'title="&#x4e94;&#x80a1;&#x4ea4;&#x6d41;&#x9053;北出匝道改道"> <span class="stitle col-lg-7">五股…</span>'
                '<span class="issued col-lg-3">交通局</span> <span class="date col-lg-2 col-xs-0">115-11-20</span> </a>'
                '<a href="home.jsp?id=3338eea68717f3fe&act=x&dataserno=ff" title="別的單元"><span class="date">115-11-20</span></a></div>')
        rows = parse_ntpc_transport(page)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["title"], "五股交流道北出匝道改道")
        self.assertEqual(rows[0]["date"], "2026-11-20")
        self.assertEqual(rows[0]["region"], "新北市")
        self.assertTrue(rows[0]["url"].startswith("https://www.traffic.ntpc.gov.tw/home.jsp?id=54fa46e9e522dde4&act="))

    def test_official_points(self):
        exp = "某規則為配合某事，爰修正本規則，其修正要點如下：\n一、延長補助期間至一百十九年底。（修正條文第五條）\n二、放寬申請文件。（修正條文第六條、第七條）\n三、" + "長" * 200
        pts = official_points(exp)
        self.assertEqual(pts[0], "延長補助期間至一百十九年底。")
        self.assertEqual(pts[1], "放寬申請文件。")
        self.assertEqual(len(pts[2]), 90)
        self.assertTrue(pts[2].endswith("…"))
        self.assertEqual(official_points(""), [])
        self.assertEqual(official_points("沒有條列的說明"), [])

    def test_merge_previous_keeps_unexpired_old_items(self):
        with tempfile.TemporaryDirectory() as d:
            out = Path(d) / "sub" / "feed.json"
            write_feed([{"id": "old-keep", "published": "2026-10-20"}, {"id": "old-expired", "published": "2026-08-01"},
                        {"id": "same", "published": "2026-11-01", "headline": "舊版"}], out)
            self.assertIn("generatedAt", json.loads(out.read_text(encoding="utf-8")))
            new = [{"id": "new", "published": "2026-11-22"}, {"id": "same", "published": "2026-11-01", "headline": "新版"}]
            merged = merge_previous(new, out, today=date(2026, 11, 23))
            self.assertEqual([m["id"] for m in merged], ["new", "same", "old-keep"])
            self.assertEqual(merged[1]["headline"], "新版")
            self.assertEqual(merge_previous(new, Path(d) / "missing.json"), new)
            (Path(d) / "bad.json").write_text("{{{", encoding="utf-8")
            self.assertEqual(merge_previous(new, Path(d) / "bad.json"), new)

    def test_any_date(self):
        self.assertEqual(any_date("2026/11/20"), "2026-11-20")
        self.assertEqual(any_date("115-11-20"), "2026-11-20")
        self.assertIsNone(any_date("2026-13-40"))
        self.assertIsNone(any_date(""))

    def test_tidy_explain(self):
        raw = "某辦法修正總說\n 明\n 本辦法為某目的，其修正要點如下：\n 一、延長期間。（修正條文第五\n 條）\n 二、調整文件。（修正條文第六條）\n 某辦法修正條文對照表\n 修正條文 現行條文"
        t = tidy_explain(raw)
        self.assertTrue(t.startswith("本辦法為某目的"))
        self.assertIn("一、延長期間。（修正條文第五條）\n二、調整文件。（修正條文第六條）", t)
        self.assertNotIn("對照表", t)
        self.assertEqual(tidy_explain("沒有相關段落"), "")


if __name__ == "__main__":
    unittest.main()
