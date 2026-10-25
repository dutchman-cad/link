# SPDX-License-Identifier: GPL-3.0-or-later
"""Blender adapter using the vendored Dutchman Bridge control client."""
import hashlib
import io
import json

try:
    from .dutchman_link.vendor import solidcraft_bridge as bridge
    from .mesh import read_3mf
except ImportError:
    # Direct loading is useful for the standalone standard-library tests.
    import importlib.util
    from pathlib import Path
    import sys
    root = Path(__file__).resolve().parent
    package_name = 'dutchman_link'
    if package_name not in sys.modules:
        spec = importlib.util.spec_from_file_location(package_name, root / 'dutchman_link/__init__.py',
                                                      submodule_search_locations=[str(root / 'dutchman_link')])
        package = importlib.util.module_from_spec(spec)
        sys.modules[package_name] = package
        spec.loader.exec_module(package)
    from dutchman_link.vendor import solidcraft_bridge as bridge
    spec = importlib.util.spec_from_file_location('_dutchman_mesh', root / 'mesh.py')
    mesh = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mesh)
    read_3mf = mesh.read_3mf

ControlError = bridge.Error
token_path = bridge.token_path


class Client(bridge.Session):
    """Compatibility spelling for existing Blender-side calls."""


def signature(info):
    """Legacy helper retained for callers; polling uses exported content."""
    if 'revision' not in info:
        return None
    return json.dumps({k: info.get(k) for k in
                       ('name', 'path', 'revision', 'bodies', 'components', 'occurrences')}, sort_keys=True)


def export_snapshot(client):
    """Decode Bridge's named 3MF geometry into Blender mesh records."""
    geometry = client.export('3mf')
    return read_3mf(io.BytesIO(geometry.data))


def poll(port, token, previous):
    """Fetch once on Link's worker, suppressing identical decoded content."""
    with Client(port, token) as client:
        info = client.call('document.inspect', {'measure': False})
        bodies = export_snapshot(client)
        after = client.call('document.inspect', {'measure': False})
        fields = lambda value: {k: value.get(k) for k in ('name', 'path', 'bodies', 'parameters')}
        if fields(after) != fields(info):
            raise ControlError('Design changed during export; retrying next poll')
        content = dict(metadata=fields(after), bodies=[(b['name'], b['hash']) for b in bodies])
        revision = hashlib.sha256(json.dumps(content, sort_keys=True).encode()).hexdigest()
        if revision == previous:
            return None
        return dict(revision=revision, name=after.get('name', 'Design'), bodies=bodies)
