# SPDX-License-Identifier: GPL-3.0-or-later
"""Local control protocol and SolidCraft 3MF transport; no Blender dependency."""
import hashlib
import json
import math
import os
from pathlib import Path
import socket
import sys
import tempfile
import xml.etree.ElementTree as ET
import zipfile


class ControlError(Exception):
    pass


def token_path(port):
    override = os.environ.get('SOLIDCRAFT_CONTROL_DIR')
    if override:
        return Path(override) / f'{port}.token'
    if sys.platform == 'win32':
        base = Path(os.environ.get('LOCALAPPDATA', Path.home() / 'AppData/Local')) / 'SolidCraft'
    elif sys.platform == 'darwin':
        base = Path.home() / 'Library/Application Support/SolidCraft'
    else:
        base = Path(os.environ.get('XDG_DATA_HOME', Path.home() / '.local/share')) / 'solidcraft'
    return base / 'control' / f'{port}.token'


class Client:
    def __init__(self, port, token):
        self.port, self.token = port, token
        self.seq = 0

    def __enter__(self):
        self.sock = socket.create_connection(('127.0.0.1', self.port), timeout=5)
        self.sock.settimeout(65)
        self.stream = self.sock.makefile('rb')
        return self

    def __exit__(self, *args):
        self.stream.close()
        self.sock.close()

    def call(self, method, params=None):
        self.seq += 1
        req = dict(id=self.seq, method=method, params=params or {})
        if self.seq == 1:
            req['token'] = self.token
        self.sock.sendall((json.dumps(req) + '\n').encode())
        line = self.stream.readline(16 * 1024 * 1024 + 1)
        if not line or len(line) > 16 * 1024 * 1024:
            raise ControlError('SolidCraft closed the connection or sent an oversized reply')
        reply = json.loads(line)
        if not reply.get('ok'):
            raise ControlError(str(reply.get('error', 'Control request failed')))
        return reply['result']

    def execute(self, command, params=None):
        return self.call('engine.execute', dict(command=command, params=params or {}))


def signature(info):
    # revision is present in origin/main even though the protocol table omits it.
    # Include identity and body metadata to catch tab switches and appearance edits.
    if 'revision' not in info:
        return None  # Old versions: export every interval and compare body content hashes.
    return json.dumps({k: info.get(k) for k in
                       ('name', 'path', 'revision', 'bodies', 'components', 'occurrences')}, sort_keys=True)


def read_3mf(path):
    """Read the flat, named mesh + base-material subset SolidCraft exports.

    This deliberately refuses transforms/components rather than silently misplacing parts.
    SolidCraft currently bakes placements into vertices. Millimetres become Blender metres.
    """
    ns = {'m': 'http://schemas.microsoft.com/3dmanufacturing/core/2015/02'}
    with zipfile.ZipFile(path) as z:
        part = z.getinfo('3D/3dmodel.model')
        if part.file_size > 256 * 1024 * 1024:
            raise ControlError('3MF model exceeds the 256 MiB prototype limit')
        root = ET.fromstring(z.read(part))
    if root.get('unit', 'millimeter') != 'millimeter':
        raise ControlError('Expected SolidCraft millimetre export')
    resources = root.find('m:resources', ns)
    palettes = {}
    for base in resources.findall('m:basematerials', ns):
        palettes[base.get('id')] = [v.get('displaycolor', '#B3B3B3FF') for v in base]
    objects = {o.get('id'): o for o in resources.findall('m:object', ns)}
    out = []
    for item in root.findall('m:build/m:item', ns):
        if item.get('transform'):
            raise ControlError('Transformed 3MF build items are not supported yet')
        obj = objects[item.get('objectid')]
        mesh = obj.find('m:mesh', ns)
        if mesh is None:
            raise ControlError('Component 3MF objects are not supported yet')
        verts = [tuple(float(v.get(k)) * .001 for k in ('x', 'y', 'z'))
                 for v in mesh.findall('m:vertices/m:vertex', ns)]
        if any(not math.isfinite(x) for v in verts for x in v):
            raise ControlError('Non-finite mesh coordinate')
        faces, colors = [], []
        for tri in mesh.findall('m:triangles/m:triangle', ns):
            face = tuple(int(tri.get(k)) for k in ('v1', 'v2', 'v3'))
            if any(i < 0 or i >= len(verts) for i in face):
                raise ControlError('Invalid 3MF vertex index')
            faces.append(face)
            pid = tri.get('pid', obj.get('pid'))
            idx = int(tri.get('p1', obj.get('pindex', '0')))
            colors.append(palettes[pid][idx] if pid in palettes else '#B3B3B3FF')
        body = dict(name=obj.get('name', ''), vertices=verts, faces=faces, colors=colors)
        body['hash'] = hashlib.sha256(json.dumps(body, separators=(',', ':')).encode()).hexdigest()
        out.append(body)
    names = [b['name'] for b in out]
    if any(not n for n in names) or len(set(names)) != len(names):
        raise ControlError('Body names must be nonempty and unique')
    return out


def export_snapshot(client):
    """The replaceable transport boundary: export and decode into named mesh records."""
    with tempfile.TemporaryDirectory(prefix='dutchman-link-') as folder:
        path = str(Path(folder) / 'design.3mf')
        client.execute('file.export', {'path': path, 'format': '3mf'})
        return read_3mf(path)


def poll(port, token, previous):
    with Client(port, token) as client:
        info = client.call('document.inspect', {'measure': False})
        rev = signature(info)
        if rev is not None and rev == previous:
            return None
        bodies = export_snapshot(client)
        after = client.call('document.inspect', {'measure': False})
        if signature(after) != rev:
            raise ControlError('Design changed during export; retrying next poll')
        return dict(revision=rev, name=info['name'], bodies=bodies)
