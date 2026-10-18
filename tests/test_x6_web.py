"""HTTP bridge boundaries, payload integrity and automatic ADFU dispatch."""

import dataclasses
import hashlib
import http.client
import json
import socket
import sys
import tempfile
import threading
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))
import x6_toolkit as toolkit
import x6_web as web


class WebBridgeTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.runtime = Path(self.temporary.name)
        hashes = {}
        for name in web.PAYLOAD_NAMES:
            data = name.encode("ascii")
            (self.runtime / name).write_bytes(data)
            hashes[name] = hashlib.sha256(data).hexdigest()
        profile = dataclasses.replace(toolkit.Profile.load(), runtime_sha256=hashes)
        self.server = web.WebServer(0, profile, self.runtime)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()

    def tearDown(self) -> None:
        self.server.shutdown()
        self.server.server_close()
        self.thread.join()
        self.temporary.cleanup()

    def request(self, path, method="GET", **headers):
        connection = http.client.HTTPConnection("127.0.0.1", self.server.server_port)
        connection.request(method, path, headers={"X-X6-Session": self.server.token, **headers})
        response = connection.getresponse()
        result = response.status, response.read()
        connection.close()
        return result

    def test_profile_and_whitelisted_payload(self):
        status, data = self.request("/api/profile")
        self.assertEqual(status, 200)
        self.assertEqual(json.loads(data)["nand_id"], "2c68044aa9")
        self.assertNotIn("actions_dump.exe", json.loads(data)["runtime_sha256"])
        self.assertEqual(self.request("/api/payloads/adfus.bin"),
                         (200, (self.runtime / "adfus.bin").read_bytes()))
        self.assertEqual(self.request("/api/payloads/../original/lfi.bin")[0], 409)

    def test_cross_origin_and_wrong_token_cannot_switch(self):
        for headers in ({"Origin": "https://example.com"}, {"X-X6-Session": "wrong"},
                        {"Host": "evil.example"}, {"Sec-Fetch-Site": "cross-site"}):
            with mock.patch.object(toolkit, "enter_adfu") as enter:
                self.assertEqual(self.request("/api/enter-adfu", "POST", **headers)[0], 403)
                enter.assert_not_called()

    def test_only_existing_adfu_switch_is_exposed(self):
        with mock.patch.object(toolkit, "enter_adfu", return_value=toolkit.Device("adfu")) as enter:
            status, data = self.request("/api/enter-adfu", "POST")
            self.assertEqual((status, json.loads(data)["mode"]), (200, "adfu"))
            enter.assert_called_once_with(self.server.profile)
        self.assertEqual(self.request("/api/flash", "POST")[0], 404)
        self.assertEqual(self.request("/../private_dumps/toolkit/original/lfi.bin")[0], 404)

    def test_corrupt_payload_is_never_served(self):
        with mock.patch.object(Path, "read_bytes", return_value=b"wrong"):
            status, data = self.request("/api/payloads/fwsc.bin")
            self.assertEqual(status, 409)
            self.assertIn("Prüfsumme", json.loads(data)["error"])

    def test_opening_page_and_profile_does_not_probe_any_device(self):
        """Opening the UI is passive; device discovery needs the Connect action."""
        with mock.patch.object(toolkit, "probe_device") as probe:
            self.assertEqual(self.request("/")[0], 200)
            self.assertEqual(self.request("/api/profile")[0], 200)
            probe.assert_not_called()
        with mock.patch.object(toolkit, "probe_device", return_value=toolkit.Device("missing")) as probe:
            status, data = self.request("/api/status")
            self.assertEqual((status, json.loads(data)["mode"]), (200, "missing"))
            probe.assert_called_once_with(self.server.profile)

    def test_pages_cors_is_opt_in_and_still_requires_a_token(self):
        origin = "https://netfreak25.github.io"
        self.assertEqual(self.request("/api/profile", Origin=origin)[0], 403)
        self.server.allowed_origins.add(origin)
        self.assertEqual(self.request("/api/profile", Origin=origin, **{"Sec-Fetch-Site": "cross-site"})[0], 200)
        self.assertEqual(self.request("/api/profile", Origin=origin, **{"X-X6-Session": "wrong"})[0], 403)
        preflight = {"Origin": origin, "Access-Control-Request-Method": "POST", "Access-Control-Request-Headers": "x-x6-session"}
        self.assertEqual(self.request("/api/enter-adfu", "OPTIONS", **preflight)[0], 204)
        preflight["Origin"] = "https://evil.example"
        self.assertEqual(self.request("/api/enter-adfu", "OPTIONS", **preflight)[0], 403)

    def test_launcher_keeps_secret_out_of_public_request_url(self):
        from urllib.parse import parse_qs, urlsplit

        url = urlsplit(self.server.launch_url(True))
        self.assertEqual(url.query, "")
        self.assertEqual(url.hostname, "netfreak25.github.io")
        self.assertEqual(parse_qs(url.fragment)["session"], [self.server.token])

    def test_direct_pages_pairing_requires_exact_origin_host_and_custom_header(self):
        origin = "https://netfreak25.github.io"
        headers = {"Origin": origin, "X-X6-Pair": "1", "X-X6-Session": ""}
        self.assertEqual(self.request("/api/pair", "POST", **headers)[0], 403)
        self.server.allowed_origins.add(origin)
        with mock.patch.object(toolkit, "probe_device") as probe, mock.patch.object(toolkit, "enter_adfu") as enter:
            status, data = self.request("/api/pair", "POST", **headers)
            self.assertEqual(status, 200)
            self.assertEqual(json.loads(data), {"origin": self.server.origin, "token": self.server.token})
            for invalid in ({"Origin": "https://evil.example"}, {"Origin": ""},
                            {"Host": "evil.example"}, {"X-X6-Pair": ""}):
                self.assertEqual(self.request("/api/pair", "POST", **{**headers, **invalid})[0], 403)
            self.assertEqual(self.request("/api/enter-adfu", "POST", **headers)[0], 403)
            probe.assert_not_called()
            enter.assert_not_called()

    def test_pairing_preflight_only_accepts_post_and_pair_header(self):
        origin = "https://netfreak25.github.io"
        self.server.allowed_origins.add(origin)
        headers = {"Origin": origin, "Access-Control-Request-Method": "POST",
                   "Access-Control-Request-Headers": "x-x6-pair"}
        self.assertEqual(self.request("/api/pair", "OPTIONS", **headers)[0], 204)
        self.assertEqual(self.request("/api/status", "OPTIONS", **headers)[0], 403)
        headers["Access-Control-Request-Method"] = "GET"
        self.assertEqual(self.request("/api/pair", "OPTIONS", **headers)[0], 403)

    def test_launcher_prefers_chrome_when_both_browsers_are_installed(self):
        import winreg

        with mock.patch.object(winreg, "OpenKey") as key, mock.patch.object(winreg, "QueryValue", return_value="chrome.exe"), \
                mock.patch.object(Path, "is_file", return_value=True):
            self.assertEqual(web.browser_path(), Path("chrome.exe"))
            self.assertTrue(key.call_args.args[1].endswith("chrome.exe"))

    def test_second_helper_cannot_share_the_listening_port(self):
        with self.assertRaisesRegex(toolkit.ToolkitError, "Port"), \
                web.WebServer(self.server.server_port, self.server.profile, self.runtime):
            pass

    @unittest.skipUnless(hasattr(socket, "SO_EXCLUSIVEADDRUSE"), "Windows socket semantics")
    def test_legacy_reuseaddr_listener_cannot_share_a_new_helper_port(self):
        with socket.socket() as legacy:
            legacy.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            with self.assertRaises(OSError):
                legacy.bind(self.server.server_address)

    @unittest.skipUnless(hasattr(socket, "SO_EXCLUSIVEADDRUSE"), "Windows socket semantics")
    def test_helper_rejects_port_already_owned_by_legacy_reuseaddr_server(self):
        with socket.socket() as legacy:
            legacy.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            legacy.bind(("127.0.0.1", 0))
            legacy.listen()
            with self.assertRaisesRegex(toolkit.ToolkitError, "Port"), \
                    web.WebServer(legacy.getsockname()[1], self.server.profile, self.runtime):
                pass

    def test_packaged_http_check_never_probes_and_port_can_reopen_after_shutdown(self):
        from x6_helper import check_http_bridge

        with mock.patch.object(toolkit, "probe_device") as probe, mock.patch.object(toolkit, "enter_adfu") as enter:
            server = web.WebServer(0, self.server.profile, self.runtime, online=True)
            port = server.server_port
            worker = threading.Thread(target=server.serve_forever, daemon=True)
            worker.start()
            try:
                self.assertEqual(check_http_bridge(server), {"preflight_status": 204, "pair_status": 200})
            finally:
                server.shutdown()
                server.server_close()
                worker.join()
            with web.WebServer(port, self.server.profile, self.runtime):
                pass
            probe.assert_not_called()
            enter.assert_not_called()


if __name__ == "__main__":
    unittest.main()
