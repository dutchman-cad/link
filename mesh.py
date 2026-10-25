# SPDX-License-Identifier: GPL-3.0-or-later
"""Blender mesh decoding in metres; no control-channel implementation."""
import hashlib
import json
import math
import xml.etree.ElementTree as ET
import zipfile

try:
    from .dutchman_link.vendor.solidcraft_bridge import Error as ControlError
except ImportError:
    from dutchman_link.vendor.solidcraft_bridge import Error as ControlError


def _rgba(color):
    """3MF display colours are #RRGGBB or #RRGGBBAA: keep one form, so a colour reads the same
    whichever one the file used."""
    color = color.upper()
    return color + 'FF' if len(color) == 7 else color

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
        palettes[base.get('id')] = [_rgba(v.get('displaycolor', '#B3B3B3FF')) for v in base]
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

