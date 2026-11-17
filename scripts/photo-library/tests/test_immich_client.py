import json
import threading
import unittest
from http.server import BaseHTTPRequestHandler, HTTPServer

from photolib.immich import ImmichClient

KEY = "secret-key-123"


class Handler(BaseHTTPRequestHandler):
    requests = []
    fail = False

    def _do(self):
        n = int(self.headers.get("Content-Length") or 0)
        body = json.loads(self.rfile.read(n)) if n else None
        Handler.requests.append((self.command, self.path, self.headers.get("x-api-key"), body))
        if Handler.fail:
            self.send_response(403)
            self.end_headers()
            self.wfile.write(b'{"message":"Missing required permission: asset.delete"}')
            return
        if self.path.startswith("/api/duplicates"):
            out = [{"duplicateId": "g", "assets": []}]
        elif self.path.startswith("/api/albums?assetId"):
            out = [{"id": "alb1"}, {"id": "alb2"}]
        else:
            out = []
        data = json.dumps(out).encode()
        self.send_response(200)
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    do_GET = do_PUT = do_DELETE = do_POST = _do

    def log_message(self, *a):
        pass


class ClientTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.srv = HTTPServer(("127.0.0.1", 0), Handler)
        threading.Thread(target=cls.srv.serve_forever, daemon=True).start()
        cls.client = ImmichClient(f"http://127.0.0.1:{cls.srv.server_port}", KEY)

    @classmethod
    def tearDownClass(cls):
        cls.srv.shutdown()
        cls.srv.server_close()

    def setUp(self):
        Handler.requests.clear()
        Handler.fail = False

    def test_requests_use_the_documented_endpoints_and_the_key_header(self):
        c = self.client
        self.assertEqual(c.get_duplicates(), [{"duplicateId": "g", "assets": []}])
        self.assertEqual(c.albums_of("a 1"), ["alb1", "alb2"])
        c.add_to_albums(["alb1"], ["e"])
        c.set_favorite(["e"])
        c.trash(["u"])
        c.restore(["u"])
        self.assertEqual(Handler.requests, [
            ("GET", "/api/duplicates", KEY, None),
            ("GET", "/api/albums?assetId=a%201", KEY, None),
            ("PUT", "/api/albums/assets", KEY, {"albumIds": ["alb1"], "assetIds": ["e"]}),
            ("PUT", "/api/assets", KEY, {"ids": ["e"], "isFavorite": True}),
            ("DELETE", "/api/assets", KEY, {"ids": ["u"], "force": False}),
            ("POST", "/api/trash/restore/assets", KEY, {"ids": ["u"]}),
        ])

    def test_http_error_raises_with_the_server_message_and_never_the_key(self):
        Handler.fail = True
        with self.assertRaises(RuntimeError) as cm:
            self.client.trash(["u"])
        self.assertIn("403", str(cm.exception))
        self.assertIn("asset.delete", str(cm.exception))
        self.assertNotIn(KEY, str(cm.exception))


if __name__ == "__main__":
    unittest.main()
