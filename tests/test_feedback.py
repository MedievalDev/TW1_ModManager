"""Template: copy into the tool's tests folder and set TOOL, VERSION and the
path of untested.json. Checks the tool's untested.json and the whole
feedback path against a small server on localhost (nothing goes to
alchemy-fox.de). Standard library only.

    py -3.12 -m unittest tests.test_feedback      (from the repo root)
"""

import http.server
import json
import os
import sys
import threading
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)

import foxfeedback  # noqa: E402
import foxfeedback_ui  # noqa: E402

TOOL = 'modmanager'
VERSION = '2.2.0'
UNTESTED = os.path.join(ROOT, 'untested.json')


class _H(http.server.BaseHTTPRequestHandler):
    got = []
    summary = {'schema': 1, 'accept': True, 'tools': {}}

    def log_message(self, *a):
        pass

    def _send(self, code, obj):
        raw = json.dumps(obj).encode()
        self.send_response(code)
        self.send_header('Content-Type', 'application/json')
        self.send_header('Content-Length', str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def do_GET(self):
        self._send(200, _H.summary)

    def do_POST(self):
        n = int(self.headers.get('Content-Length') or 0)
        _H.got.append(json.loads(self.rfile.read(n).decode('utf-8')))
        self._send(201, {'ok': True, 'id': 'T-1'})


class UntestedFile(unittest.TestCase):
    def test_file_is_valid(self):
        if not os.path.isfile(UNTESTED):
            self.skipTest('no untested.json yet')
        with open(UNTESTED, encoding='utf-8') as f:
            tests = json.load(f)['tests']
        ids = [x['id'] for x in tests]
        self.assertEqual(len(ids), len(set(ids)))
        for x in tests:
            self.assertRegex(x['id'], '^[a-z0-9-]{1,60}$')
            self.assertLessEqual(foxfeedback_ui.vkey(x['since']),
                                 foxfeedback_ui.vkey(VERSION), x['id'])
            for field in ('title', 'why', 'expect'):
                self.assertTrue(x[field]['de'] and x[field]['en'], x['id'])
            self.assertTrue(x['steps']['de'])
            self.assertEqual(len(x['steps']['de']), len(x['steps']['en']))


class Server(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.httpd = http.server.ThreadingHTTPServer(('127.0.0.1', 0), _H)
        cls.base = f'http://127.0.0.1:{cls.httpd.server_address[1]}/'
        threading.Thread(target=cls.httpd.serve_forever, daemon=True).start()

    @classmethod
    def tearDownClass(cls):
        cls.httpd.shutdown()
        cls.httpd.server_close()

    def test_payloads_reach_the_server(self):
        _H.got = []
        p = foxfeedback.test_payload(TOOL, VERSION, 'a' * 32, 'de', 'x-y',
                                     True, log='l')
        self.assertEqual(foxfeedback.submit(p, base=self.base), 'T-1')
        b = foxfeedback.bug_payload(TOOL, VERSION, 'a' * 32, 'en',
                                    'some.key: English template',
                                    error_key='some.key',
                                    error_text='text with C:/Users/X/a',
                                    fp_text='English template')
        foxfeedback.submit(b, base=self.base)
        self.assertEqual([g['kind'] for g in _H.got], ['test', 'bug'])
        self.assertNotIn('Users/X', json.dumps(_H.got))

    def test_summary_drives_the_list(self):
        _H.summary = {'schema': 1, 'accept': True, 'tools': {TOOL: {
            'tests': {'a': {'since': VERSION, 'pass': 2, 'fail': 0,
                            'status': 'confirmed'},
                      'b': {'since': VERSION, 'pass': 0, 'fail': 0,
                            'status': 'open'}},
            'issues': []}}}
        s = foxfeedback.fetch_summary(TOOL, VERSION, base=self.base)
        self.assertEqual(s['tests']['a']['status'], 'confirmed')


if __name__ == '__main__':
    unittest.main()
