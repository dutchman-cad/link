# SPDX-License-Identifier: GPL-3.0-or-later
"""Standard-library transport checks; no Blender or SolidCraft process required."""
import importlib.util
import json
from pathlib import Path
import socket
import tempfile
import threading
import unittest
import zipfile

spec = importlib.util.spec_from_file_location('transport', Path(__file__).resolve().parents[1] / 'transport.py')
transport = importlib.util.module_from_spec(spec)
spec.loader.exec_module(transport)


class TransportTests(unittest.TestCase):
    def test_authentication_and_server_error(self):
        requests = []
        with socket.socket() as listener:
            listener.bind(('127.0.0.1', 0))
            listener.listen()
            port = listener.getsockname()[1]
            def serve():
                conn, _ = listener.accept()
                with conn, conn.makefile('rb') as stream:
                    for i in range(2):
                        requests.append(json.loads(stream.readline()))
                        reply = {'id': i+1, 'ok': i == 0, 'result': {'revision': 7}, 'error': 'export failed'}
                        conn.sendall((json.dumps(reply)+'\n').encode())
            worker = threading.Thread(target=serve)
            worker.start()
            with transport.Client(port, 'test-secret') as client:
                self.assertEqual(client.call('document.inspect'), {'revision': 7})
                with self.assertRaisesRegex(transport.ControlError, 'export failed'):
                    client.execute('file.export')
            worker.join(timeout=5)
        self.assertEqual(requests[0]['token'], 'test-secret')
        self.assertNotIn('token', requests[1])
        self.assertEqual(requests[1]['method'], 'engine.execute')

    def test_named_colored_mesh_and_content_hash(self):
        xml = '''<model unit="millimeter" xmlns="http://schemas.microsoft.com/3dmanufacturing/core/2015/02">
        <resources><basematerials id="1"><base name="red" displaycolor="#FF0000FF"/>
        <base name="blue" displaycolor="#0000FFFF"/></basematerials>
        <object id="2" name="Body with spaces" pid="1" pindex="0"><mesh>
        <vertices><vertex x="0" y="0" z="0"/><vertex x="20" y="0" z="0"/>
        <vertex x="0" y="10" z="0"/></vertices><triangles>
        <triangle v1="0" v2="1" v3="2" pid="1" p1="1"/>
        </triangles></mesh></object></resources><build><item objectid="2"/></build></model>'''
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / 'test.3mf'
            def write(text, comment=b''):
                with zipfile.ZipFile(path, 'w') as z:
                    z.writestr('3D/3dmodel.model', text)
                    z.comment = comment
            write(xml)
            body = transport.read_3mf(path)[0]
            self.assertEqual(body['name'], 'Body with spaces')
            self.assertEqual(body['vertices'][1], (.02, 0, 0))
            self.assertEqual(body['colors'], ['#0000FFFF'])
            write(xml, b'irrelevant archive metadata')
            self.assertEqual(body['hash'], transport.read_3mf(path)[0]['hash'])
            write(xml.replace('x="20"', 'x="30"'))
            self.assertNotEqual(body['hash'], transport.read_3mf(path)[0]['hash'])
            write(xml.replace('objectid="2"', 'objectid="2" transform="1 0 0 0 1 0 0 0 1 0 0 0"'))
            with self.assertRaisesRegex(transport.ControlError, 'Transformed'):
                transport.read_3mf(path)

    def test_revision_distinguishes_undo_at_equal_depth(self):
        before = dict(name='Design', path=None, bodies=[], revision=7, undo=3)
        after = dict(before, revision=9)
        self.assertNotEqual(transport.signature(before), transport.signature(after))
        self.assertIsNone(transport.signature({'undo': 3}))


if __name__ == '__main__':
    unittest.main()
