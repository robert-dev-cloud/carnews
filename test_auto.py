"""python3 -m unittest test_auto -v   （自動摘要與清除舊資料；用本機假伺服器，不會真的連外）"""
import json
import tempfile
import threading
import unittest
from datetime import date
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from unittest import mock

import export_announcements
import purge
import summarize

SRC = ("一、汽車駕駛人駕駛汽車經測試檢定有吸食毒品者，處新臺幣三萬元以上十二萬元以下罰鍰，並吊扣駕駛執照二年。（修正條文第35條）\n"
       "二、本次修正自115年10月31日施行，罰鍰最高12萬元。配合修正裁罰基準表，另增訂沒入車輛之規定，共修正 3 條條文，以維護道路交通安全。" * 2)
GOOD = {"headline": "毒駕罰鍰提高", "points": ["最高罰 12 萬元，吊扣駕照。", "依第35條處理。"], "effective": "115年10月31日施行", "impact": ""}


class FakeServer:
    """記錄收到的請求，照設定好的內容回應。"""

    def __init__(self, responder):
        self.calls = []
        outer = self

        class H(BaseHTTPRequestHandler):
            def _do(self):
                n = int(self.headers.get("content-length") or 0)
                body = json.loads(self.rfile.read(n) or b"null")
                outer.calls.append({"method": self.command, "path": self.path, "headers": dict(self.headers), "body": body})
                status, payload = responder(self.command, self.path, body)
                raw = json.dumps(payload).encode()
                self.send_response(status)
                self.send_header("content-type", "application/json")
                self.send_header("content-length", str(len(raw)))
                self.end_headers()
                self.wfile.write(raw)

            do_POST = _do
            do_DELETE = _do

            def log_message(self, *a):
                pass

        self.httpd = HTTPServer(("127.0.0.1", 0), H)
        self.url = f"http://127.0.0.1:{self.httpd.server_port}"
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()

    def close(self):
        self.httpd.shutdown()
        self.httpd.server_close()


class CheckSummary(unittest.TestCase):
    def test_good_summary_passes_and_is_trimmed(self):
        out = summarize.check_summary({**GOOD, "headline": "  毒駕罰鍰提高 "}, SRC)
        self.assertEqual(out["headline"], "毒駕罰鍰提高")
        self.assertEqual(len(out["points"]), 2)

    def test_invented_number_is_rejected(self):
        bad = {**GOOD, "points": ["最高罰 36 萬元。"]}
        self.assertIsNone(summarize.check_summary(bad, SRC))

    def test_fullwidth_and_comma_numbers_match(self):
        ok = {**GOOD, "points": ["罰鍰 120,000 元"]}
        self.assertIsNotNone(summarize.check_summary(ok, SRC + " 罰鍰１２００００元"))

    def test_links_markup_and_bad_shapes_are_rejected(self):
        for bad in [
            {**GOOD, "points": ["詳情見 https://evil.example"]},
            {**GOOD, "impact": "請上 www.example.com"},
            {**GOOD, "headline": "<b>毒駕</b>"},
            {**GOOD, "headline": ""},
            {**GOOD, "headline": "長" * 41},
            {**GOOD, "points": []},
            {**GOOD, "points": ["a", "b", "c", "d", "e"]},
            {**GOOD, "points": ["長" * 91]},
            {**GOOD, "points": [{"x": 1}]},
            {**GOOD, "points": "不是清單"},
            {**GOOD, "effective": 115},
            ["不是物件"], None, "字串",
        ]:
            self.assertIsNone(summarize.check_summary(bad, SRC), bad)

    def test_parse_reply_handles_code_fence_and_garbage(self):
        self.assertEqual(summarize.parse_reply('```json\n{"a": 1}\n```'), {"a": 1})
        self.assertIsNone(summarize.parse_reply("抱歉，我無法"))
        self.assertIsNone(summarize.parse_reply(""))


class Pending(unittest.TestCase):
    def test_only_recent_items_with_source_and_no_summary(self):
        items = [
            {"id": "a", "title": "t", "date": "2026-11-20", "explanation": SRC},
            {"id": "b", "title": "t", "date": "2026-11-21", "explanation": "太短"},
            {"id": "c", "title": "t", "date": "2026-01-01", "explanation": SRC},      # 太舊
            {"id": "d", "title": "t", "date": "2026-11-22", "explanation": SRC},      # 已有手寫
            {"id": "e", "title": "t", "date": "2026-11-23", "explanation": SRC},      # 已自動處理過
            {"id": "f", "title": "t", "date": "2026-11-24", "explanation": SRC},
        ]
        got = summarize.pending(items, {"d": {"points": ["x"]}}, {"e": {"rejected": True}}, date(2026, 11, 24))
        self.assertEqual([i["id"] for i in got], ["f", "a"])


class RunSummarize(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        (root / "data").mkdir()
        self.root = root
        self.items = [{"id": f"i{n}", "title": "修正  道路交通管理處罰條例", "date": "2026-11-2%d" % n, "explanation": SRC} for n in range(3)]
        (root / "data/items.json").write_text(json.dumps({"items": self.items}), encoding="utf-8")
        self.patches = [mock.patch.object(summarize, "ROOT", root), mock.patch.object(summarize, "AUTO_PATH", root / "data/plain_auto.json")]
        for p in self.patches:
            p.start()
        self.logs = []

    def tearDown(self):
        for p in self.patches:
            p.stop()
        self.tmp.cleanup()

    def auto(self):
        return json.loads((self.root / "data/plain_auto.json").read_text(encoding="utf-8"))

    def test_no_key_does_nothing(self):
        self.assertEqual(summarize.run(env={}, log=self.logs.append), 0)
        self.assertFalse((self.root / "data/plain_auto.json").exists())

    def test_saves_good_marks_bad_and_sends_key_only_in_header(self):
        replies = iter([json.dumps(GOOD, ensure_ascii=False), "我不知道", json.dumps({**GOOD, "points": ["罰 99 萬"]}, ensure_ascii=False)])
        srv = FakeServer(lambda m, p, b: (200, {"content": [{"type": "text", "text": next(replies)}]}))
        try:
            made = summarize.run(env={"ANTHROPIC_API_KEY": "sk-test", "ANTHROPIC_BASE_URL": srv.url, "CARNEWS_MODEL": "m1"},
                                 today=date(2026, 11, 24), log=self.logs.append)
        finally:
            srv.close()
        self.assertEqual(made, 1)
        auto = self.auto()
        self.assertEqual(auto["i2"]["headline"], "毒駕罰鍰提高")          # 最新的先處理
        self.assertEqual(auto["i1"], {"rejected": True})
        self.assertEqual(auto["i0"], {"rejected": True})
        call = srv.calls[0]
        self.assertEqual(call["path"], "/v1/messages")
        self.assertEqual(call["headers"].get("x-api-key") or call["headers"].get("X-Api-Key"), "sk-test")
        self.assertEqual(call["body"]["model"], "m1")
        self.assertNotIn("sk-test", json.dumps(call["body"]))
        self.assertNotIn("sk-test", " ".join(self.logs))
        self.assertIn("修正 道路交通管理處罰條例", call["body"]["messages"][0]["content"])

    def test_second_run_does_not_ask_again(self):
        srv = FakeServer(lambda m, p, b: (200, {"content": [{"type": "text", "text": json.dumps(GOOD, ensure_ascii=False)}]}))
        env = {"ANTHROPIC_API_KEY": "k", "ANTHROPIC_BASE_URL": srv.url}
        try:
            summarize.run(env=env, today=date(2026, 11, 24), log=self.logs.append)
            first = len(srv.calls)
            summarize.run(env=env, today=date(2026, 11, 24), log=self.logs.append)
        finally:
            srv.close()
        self.assertEqual(first, 3)
        self.assertEqual(len(srv.calls), 3)

    def test_api_error_stops_without_crashing_or_marking(self):
        srv = FakeServer(lambda m, p, b: (401, {"error": "bad key"}))
        try:
            made = summarize.run(env={"ANTHROPIC_API_KEY": "k", "ANTHROPIC_BASE_URL": srv.url}, today=date(2026, 11, 24), log=self.logs.append)
        finally:
            srv.close()
        self.assertEqual(made, 0)
        self.assertEqual(len(srv.calls), 1)       # 第一則失敗就停，不繼續花錢
        self.assertEqual(self.auto(), {})

    def test_unreachable_service_does_not_crash(self):
        made = summarize.run(env={"ANTHROPIC_API_KEY": "k", "ANTHROPIC_BASE_URL": "http://127.0.0.1:9"}, today=date(2026, 11, 24), log=self.logs.append)
        self.assertEqual(made, 0)


class ExportUsesAuto(unittest.TestCase):
    def test_manual_beats_auto_and_rejected_falls_back_to_official(self):
        with tempfile.TemporaryDirectory() as t:
            root = Path(t)
            (root / "data").mkdir()
            items = [{"id": k, "title": "標題", "date": "2026-11-20", "explanation": "一、官方重點甲。\n二、官方重點乙。"} for k in ("m", "a", "r", "n")]
            (root / "data/items.json").write_text(json.dumps({"items": items}), encoding="utf-8")
            (root / "data/plain.json").write_text(json.dumps({"m": {"headline": "手寫", "points": ["手寫重點"]}}), encoding="utf-8")
            (root / "data/plain_auto.json").write_text(json.dumps({
                "m": {"headline": "自動", "points": ["自動重點"]},
                "a": {"headline": "自動", "points": ["自動重點"]},
                "r": {"rejected": True},
            }), encoding="utf-8")
            with mock.patch.object(export_announcements, "ROOT", root):
                rows = {r["id"]: r for r in export_announcements.build(date(2026, 11, 24))}
        self.assertEqual((rows["m"]["headline"], rows["m"]["pointsFrom"]), ("手寫", "ai"))
        self.assertEqual((rows["a"]["headline"], rows["a"]["pointsFrom"]), ("自動", "ai"))
        self.assertEqual((rows["r"]["points"], rows["r"]["pointsFrom"]), (["官方重點甲。", "官方重點乙。"], "official"))
        self.assertEqual(rows["n"]["pointsFrom"], "official")


P1, P2 = "11111111-1111-4111-8111-111111111111.jpg", "22222222-2222-4222-8222-222222222222.jpg"


class Purge(unittest.TestCase):
    def test_missing_settings_do_nothing(self):
        logs = []
        self.assertEqual(purge.run(env={}, log=logs.append), 0)
        self.assertEqual(purge.run(env={"SUPABASE_URL": "https://x.supabase.co"}, log=logs.append), 0)

    def test_dedicated_schedule_fails_loudly_when_settings_are_missing(self):
        # 專門清資料的排程：忘了設定要顯示失敗，並且說缺哪一個
        logs = []
        self.assertEqual(purge.run(env={"PURGE_REQUIRED": "1"}, log=logs.append), 1)
        self.assertIn("SUPABASE_URL、SUPABASE_SERVICE_ROLE_KEY", logs[-1])
        logs = []
        self.assertEqual(purge.run(env={"PURGE_REQUIRED": "1", "SUPABASE_URL": "https://x.supabase.co", "SUPABASE_SERVICE_ROLE_KEY": "  "}, log=logs.append), 1)
        self.assertIn("還沒設定 SUPABASE_SERVICE_ROLE_KEY。", logs[-1])
        self.assertEqual(purge.run(env={"PURGE_REQUIRED": "0"}, log=lambda *_: None), 0)

    def test_failure_says_why_without_ever_showing_the_key(self):
        # 假的金鑰。故意拆開寫：整串寫在一起的話，GitHub 的金鑰掃描可能會誤以為有人把真的金鑰傳上來
        key = "sb_" + "secret_" + "ABCdef123456"
        token = "eyJ" + "hbGciOi." + "eyJ" + "yb2xlIjoi.c2lnbmF0dXJl"
        # 伺服器把金鑰原樣寫在錯誤訊息裡（不應該，但不能賭）：印出來的一定要遮掉
        srv = FakeServer(lambda m, p, b: (401, {"message": "Invalid API key " + key, "hint": "check " + token}))
        logs = []
        try:
            self.assertEqual(purge.run(env={"SUPABASE_URL": srv.url, "SUPABASE_SERVICE_ROLE_KEY": key}, log=logs.append), 1)
        finally:
            srv.close()
        text = " ".join(logs)
        self.assertIn("資料庫回應 401", text)
        self.assertIn("Invalid API key ***", text)
        self.assertIn("sb_secret_ 開頭", text)       # 提示該貼哪一把
        self.assertNotIn(key, text)
        self.assertNotIn(token, text)
        self.assertNotIn("hbGciOi", text)
        # 檔案刪不掉時也說得出原因
        srv = FakeServer(lambda m, p, b: (200, [P1]) if p.endswith("purge_old_reports") else (200, []) if "rpc" in p else (400, {"error": "Bucket not found"}))
        logs = []
        try:
            self.assertEqual(purge.run(env={"SUPABASE_URL": srv.url, "SUPABASE_SERVICE_ROLE_KEY": key}, log=logs.append), 1)
        finally:
            srv.close()
        self.assertIn("儲存空間回應 400  Bucket not found", " ".join(logs))

    def test_success_ends_with_a_clear_line(self):
        srv = FakeServer(lambda m, p, b: (200, []))
        logs = []
        try:
            self.assertEqual(purge.run(env={"SUPABASE_URL": srv.url, "SUPABASE_SERVICE_ROLE_KEY": "k", "PURGE_REQUIRED": "1"}, log=logs.append), 0)
        finally:
            srv.close()
        self.assertEqual(logs[-1], "完成。")

    def test_url_must_be_a_supabase_project(self):
        self.assertEqual(purge.clean_url("https://abc123.supabase.co/"), "https://abc123.supabase.co")
        for bad in ["http://abc.supabase.co", "https://abc.supabase.co.evil.com", "https://evil.com/?x=.supabase.co", "ftp://x", ""]:
            self.assertIsNone(purge.clean_url(bad), bad)
        logs = []
        self.assertEqual(purge.run(env={"SUPABASE_URL": "https://evil.com", "SUPABASE_SERVICE_ROLE_KEY": "secret"}, log=logs.append), 1)
        self.assertNotIn("secret", " ".join(logs))

    def test_purges_then_deletes_only_well_formed_photo_names(self):
        def respond(method, path, body):
            if path == "/rest/v1/rpc/purge_old_reports":
                return 200, [P1, P2, P1, "../../etc/passwd", "other-bucket/x.jpg", None, 5]
            return 200, []
        srv = FakeServer(respond)
        logs = []
        try:
            code = purge.run(env={"SUPABASE_URL": srv.url, "SUPABASE_SERVICE_ROLE_KEY": "secret-key"}, log=logs.append)
        finally:
            srv.close()
        self.assertEqual(code, 0)
        self.assertEqual([(c["method"], c["path"]) for c in srv.calls],
                         [("POST", "/rest/v1/rpc/purge_old_reports"), ("DELETE", "/storage/v1/object/report-photos"),
                          ("POST", "/rest/v1/rpc/orphan_files")])
        self.assertEqual(srv.calls[0]["body"], {"p_days": 7})
        self.assertEqual(srv.calls[1]["body"], {"prefixes": [P1, P2]})
        self.assertNotIn("secret-key", " ".join(logs))

    def test_unused_files_are_deleted_from_their_own_bucket_only(self):
        def respond(method, path, body):
            if path == "/rest/v1/rpc/orphan_files":
                return 200, [
                    {"bucket": "report-photos", "name": P1},
                    {"bucket": "avatars", "name": P2},
                    {"bucket": "avatars", "name": P2},              # 重複的只刪一次
                    {"bucket": "avatars", "name": "../report-photos/" + P1},  # 奇怪的檔名不理
                    {"bucket": "secrets", "name": P1},               # 不認得的儲存空間不理
                    {"bucket": "avatars"}, P1, None, 7,
                ]
            return 200, []
        srv = FakeServer(respond)
        logs = []
        try:
            code = purge.run(env={"SUPABASE_URL": srv.url, "SUPABASE_SERVICE_ROLE_KEY": "secret-key"}, log=logs.append)
        finally:
            srv.close()
        self.assertEqual(code, 0)
        self.assertEqual([(c["method"], c["path"], c["body"]) for c in srv.calls], [
            ("POST", "/rest/v1/rpc/purge_old_reports", {"p_days": 7}),
            ("POST", "/rest/v1/rpc/orphan_files", {"p_limit": 1000}),
            ("DELETE", "/storage/v1/object/report-photos", {"prefixes": [P1]}),
            ("DELETE", "/storage/v1/object/avatars", {"prefixes": [P2]}),
        ])
        self.assertNotIn("secret-key", " ".join(logs))

    def test_database_without_the_new_function_is_not_a_failure(self):
        # 資料庫還沒重跑新的 schema.sql：舊回報照樣清，只是提醒一下
        srv = FakeServer(lambda m, p, b: (404, {"code": "PGRST202"}) if p.endswith("orphan_files") else (200, []))
        logs = []
        try:
            self.assertEqual(purge.run(env={"SUPABASE_URL": srv.url, "SUPABASE_SERVICE_ROLE_KEY": "k"}, log=logs.append), 0)
        finally:
            srv.close()
        self.assertIn("schema.sql", " ".join(logs))
        # 其他錯誤（例如權限不對）要讓排程顯示失敗
        srv = FakeServer(lambda m, p, b: (403, {}) if p.endswith("orphan_files") else (200, []))
        try:
            self.assertEqual(purge.run(env={"SUPABASE_URL": srv.url, "SUPABASE_SERVICE_ROLE_KEY": "k"}, log=lambda *_: None), 1)
        finally:
            srv.close()
        # 檔案刪不掉也算失敗
        srv = FakeServer(lambda m, p, b: (200, [{"bucket": "avatars", "name": P1}]) if p.endswith("orphan_files") else (200, []) if "rpc" in p else (500, {}))
        try:
            self.assertEqual(purge.run(env={"SUPABASE_URL": srv.url, "SUPABASE_SERVICE_ROLE_KEY": "k"}, log=lambda *_: None), 1)
        finally:
            srv.close()

    def test_no_photos_means_no_storage_call(self):
        srv = FakeServer(lambda m, p, b: (200, []))
        try:
            self.assertEqual(purge.run(env={"SUPABASE_URL": srv.url, "SUPABASE_SERVICE_ROLE_KEY": "k"}, log=lambda *_: None), 0)
        finally:
            srv.close()
        self.assertEqual([c["path"] for c in srv.calls], ["/rest/v1/rpc/purge_old_reports", "/rest/v1/rpc/orphan_files"])

    def test_database_or_storage_failure_reports_error(self):
        srv = FakeServer(lambda m, p, b: (403, {"message": "no"}))
        try:
            self.assertEqual(purge.run(env={"SUPABASE_URL": srv.url, "SUPABASE_SERVICE_ROLE_KEY": "k"}, log=lambda *_: None), 1)
        finally:
            srv.close()
        srv = FakeServer(lambda m, p, b: (200, [P1]) if "rpc" in p else (500, {}))
        try:
            self.assertEqual(purge.run(env={"SUPABASE_URL": srv.url, "SUPABASE_SERVICE_ROLE_KEY": "k"}, log=lambda *_: None), 1)
        finally:
            srv.close()


if __name__ == "__main__":
    unittest.main()
