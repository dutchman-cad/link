# SPDX-License-Identifier: GPL-3.0-or-later
"""Real SolidCraft + Blender integration. Run with Blender --python-exit-code 1.

Background Blender has no interactive event loop: pump the registered timer callback
on the main thread while its real socket worker and real poll deadlines run normally.
No mock transport or direct calls to mesh reconciliation are used.
"""
import importlib
import json
import os
from pathlib import Path
import sys
import tempfile
import time
import zipfile

import bpy
from mathutils import Vector

ROOT = Path(__file__).resolve().parents[1]
PORT = int(os.environ.get('DUTCHMAN_TEST_PORT', '17894'))
SHOTS = Path(os.environ.get('DUTCHMAN_SHOTS', '/home/ray/Documents/dutchman-link-shots'))


def install():
    folder = Path(tempfile.mkdtemp(prefix='dutchman-extension-test-'))
    package = folder / 'dutchman_link-0.1.0.zip'
    with zipfile.ZipFile(package, 'w', zipfile.ZIP_DEFLATED) as z:
        for name in ('__init__.py', 'transport.py', 'blender_manifest.toml', 'LICENSE', 'README.md'):
            z.write(ROOT / name, name)
    repo_dir = folder / 'repo'
    repo_dir.mkdir()
    bpy.ops.preferences.extension_repo_add(name='Dutchman local test', type='LOCAL',
                                          use_custom_directory=True, custom_directory=str(repo_dir))
    repo = bpy.context.preferences.extensions.repos[-1]
    result = bpy.ops.extensions.package_install_files(filepath=str(package), repo=repo.module,
                                                      enable_on_install=True)
    assert result == {'FINISHED'}, result
    module = 'bl_ext.' + repo.module + '.dutchman_link'
    assert module in bpy.context.preferences.addons
    return importlib.import_module(module)


def wait_for(predicate, timeout=45):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        if addon._runtime:
            addon._runtime.tick()
        bpy.context.view_layer.update()
        if predicate():
            return
        time.sleep(.03)
    raise AssertionError('Timed out: ' + settings.status)


def execute(command, params=None):
    with addon.transport.Client(PORT, TOKEN) as client:
        return client.execute(command, params)


def inspect():
    with addon.transport.Client(PORT, TOKEN) as client:
        return client.call('document.inspect', {'measure': False})


def geometry(obj):
    return (len(obj.data.vertices), tuple(round(x, 7) for v in obj.bound_box for x in v))


def render(path):
    bpy.context.view_layer.update()
    scene = bpy.context.scene
    scene.render.filepath = str(path)
    bpy.ops.render.render(write_still=True)


def setup_render(objects):
    for obj in list(bpy.context.scene.objects):
        if obj not in objects:
            bpy.data.objects.remove(obj, do_unlink=True)
    scene = bpy.context.scene
    scene.render.engine = 'BLENDER_EEVEE_NEXT'
    scene.render.resolution_x = 640
    scene.render.resolution_y = 480
    scene.render.resolution_percentage = 100
    scene.render.image_settings.file_format = 'PNG'
    points = [o.matrix_world @ Vector(v) for o in objects for v in o.bound_box]
    low = Vector(tuple(min(p[i] for p in points) for i in range(3)))
    high = Vector(tuple(max(p[i] for p in points) for i in range(3)))
    center = (low + high) / 2
    size = max((high - low).length, .08)
    cam_data = bpy.data.cameras.new('Evidence camera')
    cam = bpy.data.objects.new('Evidence camera', cam_data)
    scene.collection.objects.link(cam)
    cam.location = center + Vector((1.3, -1.7, 1.4)) * size
    cam.rotation_euler = (center - cam.location).to_track_quat('-Z', 'Y').to_euler()
    cam_data.type = 'ORTHO'
    cam_data.ortho_scale = size * 1.1
    cam_data.clip_start = .0001
    scene.camera = cam
    for name, offset, energy in [('Key', (1, -1, 2), 70), ('Fill', (-1, 0, 1), 35)]:
        data = bpy.data.lights.new(name, 'AREA')
        data.energy = energy
        data.shape = 'DISK'
        data.size = size
        light = bpy.data.objects.new(name, data)
        scene.collection.objects.link(light)
        light.location = center + Vector(offset) * size
        light.rotation_euler = (center - light.location).to_track_quat('-Z', 'Y').to_euler()
    scene.world.color = (.16, .16, .16)


addon = install()
settings = bpy.context.scene.dutchman_link
TOKEN = addon.transport.token_path(PORT).read_text().strip()
settings.port = PORT
assert settings.interval == 1 and settings.keep_materials
# Start with the actual --sample design, then add two small, deterministic test bodies.
initial = inspect()
print('SAMPLE:', initial['name'], [b['name'] for b in initial['bodies']], flush=True)
for name, x, height in [('Link changed', 70, 18), ('Link untouched', 100, 14)]:
    execute('sketch.create', {'plane': 'XY', 'name': name + ' sketch'})
    execute('sketch.rectangle.two_point', {'p0': [x, 0], 'p1': [x+20, 20]})
    execute('solid.extrude', {'distance': height, 'operation': 'new', 'body_name': name,
                              'name': name + ' extrusion'})
info = inspect()
assert addon.link(bpy.context.scene)
wait_for(lambda: addon._runtime.collection is not None and addon._runtime.updates == 1)
session = addon._runtime
collection = session.collection
expected = {b['name'] for b in info['bodies']}
assert {o.name for o in collection.objects} == expected, (expected, list(collection.objects))
assert collection.name == 'SolidCraft: ' + info['name']
assert all(len(o.data.materials) for o in collection.objects)
changed = next(o for o in collection.objects if o['dutchman_body'] == 'Link changed')
untouched = next(o for o in collection.objects if o['dutchman_body'] == 'Link untouched')
old_geometry = geometry(changed)
old_mesh = changed.data.as_pointer()
untouched_mesh = untouched.data.as_pointer()
changed_pointer, untouched_pointer = changed.as_pointer(), untouched.as_pointer()
material = bpy.data.materials.new('Ray test material')
material.diffuse_color = (.025, .35, .8, 1)
changed.data.materials.clear()
changed.data.materials.append(material)
untouched.data.materials.clear()
untouched.data.materials.append(material)
modifier = changed.modifiers.new('Keep this modifier', 'BEVEL')
modifier.width = .0002
changed.location.x = .002
bpy.context.view_layer.update()
matrix = changed.matrix_world.copy()
SHOTS.mkdir(parents=True, exist_ok=True)
setup_render(list(collection.objects))
render(SHOTS / 'before.png')
# A face extrusion joins only the chosen body, preserving its body name.
execute('solid.extrude', {'face': [80, 10, 18], 'face_body': 'Link changed',
                          'distance': 14, 'operation': 'join', 'targets': ['Link changed']})
wait_for(lambda: geometry(changed) != old_geometry)
assert changed.as_pointer() == changed_pointer
assert changed.data.as_pointer() != old_mesh
assert changed.matrix_world == matrix
assert changed.modifiers.get('Keep this modifier') == modifier
assert changed.data.materials[0] == material
assert untouched.as_pointer() == untouched_pointer
assert untouched.data.as_pointer() == untouched_mesh
assert untouched.data.materials[0] == material
render(SHOTS / 'after.png')
# No edit: a completed poll must leave all meshes alone.
updates = session.updates
deadline = session.next_poll
wait_for(lambda: not session.busy and session.next_poll > deadline, timeout=10)
assert session.updates == updates
# Add/delete propagation through the same poll path.
execute('sketch.create', {'plane': 'XY', 'name': 'Transient sketch'})
execute('sketch.rectangle.two_point', {'p0': [130, 0], 'p1': [140, 10]})
execute('solid.extrude', {'distance': 10, 'operation': 'new', 'body_name': 'Transient', 'name': 'Transient extrusion'})
wait_for(lambda: any(o.get('dutchman_body') == 'Transient' for o in collection.objects))
execute('timeline.delete', {'feature': 'Transient sketch'})
wait_for(lambda: not any(o.get('dutchman_body') == 'Transient' for o in collection.objects))
assert not any(c.name.startswith('Dutchman staging') for c in bpy.data.collections)
# Source appearance edits are revisions too; turning off material preservation follows them.
settings.keep_materials = False
execute('appearance.assign', {'bodies': ['Link changed'], 'color': '#22CC44'})
wait_for(lambda: changed.data.materials[0].get('dutchman_color') == '#22CC44FF')
assert untouched.data.materials[0] == material
settings.keep_materials = True
# Inject a transport export failure after a real inspection, then prove retry recovery.
original_export = addon.transport.export_snapshot
def fail_export(client):
    raise addon.transport.ControlError('export failure test')
addon.transport.export_snapshot = fail_export
session.revision = None
wait_for(lambda: settings.status == 'Error: export failure test')
addon.transport.export_snapshot = original_export
wait_for(lambda: settings.status.startswith('Linked'))
addon.unlink()
assert session.tick() is None
# Authentication and connection errors are panel status, with no thrown exception.
settings.token = '0' * 64
assert addon.link(bpy.context.scene)
wait_for(lambda: settings.status.startswith('Error:'))
assert 'unauthorized' in settings.status.lower(), settings.status
addon.unlink()
settings.token = TOKEN
settings.port = 1
assert addon.link(bpy.context.scene)
wait_for(lambda: settings.status.startswith('Error:'))
addon.unlink()
settings.port = PORT
settings.token = ''
report = dict(blender=bpy.app.version_string, sample=initial['name'], names=sorted(expected),
              changed_before=old_geometry, changed_after=geometry(changed),
              checks=['extension install/enable', 'sample names/count', 'live geometry update',
                      'unchanged mesh identity', 'object identity', 'transform', 'modifier',
                      'materials', 'add/delete', 'staging cleanup', 'source colours', 'export error/recovery', 'unlink', 'bad token', 'offline'],
              shots=[str(SHOTS / 'before.png'), str(SHOTS / 'after.png')])
(SHOTS / 'test-results.json').write_text(json.dumps(report, indent=2))
print('DUTCHMAN LINK PASS: ' + json.dumps(report), flush=True)
