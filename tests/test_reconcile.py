# SPDX-License-Identifier: GPL-3.0-or-later
"""Blender-only reconciliation checks, independent of control-channel integration.

Run Blender -b --factory-startup --python-exit-code 1 --python tests/test_reconcile.py.
These fixtures do not substitute for the real SolidCraft test in test_link.py.
"""
import importlib.util
from pathlib import Path
import sys

import bpy

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('dutchman_link', ROOT / '__init__.py',
                                               submodule_search_locations=[str(ROOT)])
addon = importlib.util.module_from_spec(spec)
sys.modules['dutchman_link'] = addon
spec.loader.exec_module(addon)
addon.register()
scene = bpy.context.scene
session = addon.LinkSession(scene, 17894, '')


def body(name, height=1, color='#2277CCFF'):
    vertices = [(0, 0, 0), (1, 0, 0), (0, 1, 0), (0, 0, height)]
    faces = [(0, 2, 1), (0, 1, 3), (1, 2, 3), (2, 0, 3)]
    return dict(name=name, vertices=vertices, faces=faces, colors=[color] * 4,
                hash=f'{name}:{height}:{color}')


def apply(bodies, revision):
    session.apply(dict(name='Reconciliation fixture', revision=revision, bodies=bodies))
    bpy.context.view_layer.update()
    assert not any(c.name.startswith('Dutchman staging') for c in bpy.data.collections)


apply([body('Changed body'), body('Untouched body')], '1')
collection = session.collection
assert collection.name == 'SolidCraft: Reconciliation fixture'
changed, untouched = [next(o for o in collection.objects if o['dutchman_body'] == n)
                      for n in ['Changed body', 'Untouched body']]
assert {o.name for o in collection.objects} == {'Changed body', 'Untouched body'}
assert changed.data.materials[0].get('dutchman_color') == '#2277CCFF'
changed_id, untouched_id = changed.as_pointer(), untouched.as_pointer()
changed_mesh, untouched_mesh = changed.data.as_pointer(), untouched.data.as_pointer()
material = bpy.data.materials.new('Hand assigned material')
for obj in (changed, untouched):
    obj.data.materials.clear()
    obj.data.materials.append(material)
changed.data.polygons[0].material_index = 0
modifier = changed.modifiers.new('Preserved modifier', 'BEVEL')
changed.location = (2, 3, 4)
changed.rotation_euler.z = .4
changed.scale = (1.2, .8, 1.5)
bpy.context.view_layer.update()
transform = changed.matrix_world.copy()
# Local display renames must not destroy source-name matching.
changed.name = 'My display name'
apply([body('Changed body', 2), body('Untouched body')], '2')
assert changed.as_pointer() == changed_id and untouched.as_pointer() == untouched_id
assert changed.name == 'My display name'
assert changed.data.as_pointer() != changed_mesh
assert untouched.data.as_pointer() == untouched_mesh
assert changed.data.vertices[3].co.z == 2
assert changed.matrix_world == transform
assert changed.modifiers.get('Preserved modifier') == modifier
assert all(obj.data.materials[0] == material for obj in (changed, untouched))
# Identical content, even with a new document revision, must keep mesh identity.
changed_mesh = changed.data.as_pointer()
apply([body('Changed body', 2), body('Untouched body')], '3')
assert changed.data.as_pointer() == changed_mesh
# With keep-materials off, changed source colours become Blender materials.
scene.dutchman_link.keep_materials = False
apply([body('Changed body', 2, '#FF3300FF'), body('Untouched body'), body('New body')], '4')
assert changed.data.materials[0].get('dutchman_color') == '#FF3300FF'
assert untouched.data.materials[0] == material
assert len(collection.objects) == 3
apply([body('Untouched body'), body('New body')], '5')
assert len(collection.objects) == 2
assert untouched.as_pointer() == untouched_id
# Build errors must clean staging and leave previously linked objects untouched.
try:
    session.apply(dict(name='Broken', revision='6', bodies=[{'name': 'Invalid'}]))
except KeyError:
    pass
else:
    raise AssertionError('Invalid mesh fixture unexpectedly succeeded')
assert len(collection.objects) == 2
assert not any(c.name.startswith('Dutchman staging') for c in bpy.data.collections)
assert material.name in bpy.data.materials
addon.unregister()
print('DUTCHMAN RECONCILIATION PASS: names, geometry, object/mesh identity, transforms, '
      'modifiers, materials, source colours, add/delete, staging cleanup, failed-import isolation')
