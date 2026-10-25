# SPDX-License-Identifier: GPL-3.0-or-later
"""Dutchman Link: local SolidCraft live mesh link."""
import queue
import threading
import time

import bpy
from bpy.app.handlers import persistent
from bpy.props import BoolProperty, FloatProperty, IntProperty, PointerProperty, StringProperty

from . import transport

_runtime = None


def _remove_object(obj):
    mesh = obj.data
    bpy.data.objects.remove(obj, do_unlink=True)
    if mesh.users == 0:
        materials = list(mesh.materials)
        bpy.data.meshes.remove(mesh)
        for mat in materials:
            if mat and mat.get('dutchman_generated') and mat.users == 0:
                bpy.data.materials.remove(mat)


def _color_material(color):
    name = 'SolidCraft ' + color
    mat = next((m for m in bpy.data.materials
                if m.get('dutchman_color') == color), None)
    if mat is None:
        mat = bpy.data.materials.new(name)
        raw = color.lstrip('#')
        rgba = [int(raw[i:i+2], 16) / 255 for i in range(0, len(raw), 2)]
        if len(rgba) == 3:
            rgba.append(1.0)
        # 3MF display colours are sRGB; Blender material values are linear.
        mat.diffuse_color = [x / 12.92 if x <= .04045 else ((x + .055) / 1.055) ** 2.4
                             for x in rgba[:3]] + [rgba[3]]
        mat.use_nodes = True
        mat.node_tree.nodes.get('Principled BSDF').inputs['Base Color'].default_value = mat.diffuse_color
        mat['dutchman_generated'] = True
        mat['dutchman_color'] = color
    return mat


class LinkSession:
    def __init__(self, scene, port, token):
        self.scene = scene
        self.port, self.token = port, token
        self.revision = None
        self.collection = None
        self.results = queue.Queue(maxsize=1)
        self.busy = False
        self.next_poll = 0
        self.active = True
        self.updates = 0

    def request(self):
        # Capture plain Python values only. This worker never accesses bpy.
        port, token, previous, results = self.port, self.token, self.revision, self.results
        def work():
            try:
                result = (transport.poll(port, token, previous), None)
            except Exception as exc:
                result = (None, str(exc) or type(exc).__name__)
            results.put(result)
        self.busy = True
        threading.Thread(target=work, name='Dutchman socket', daemon=True).start()

    def apply(self, snapshot):
        settings = self.scene.dutchman_link
        if self.collection is None:
            self.collection = bpy.data.collections.new('SolidCraft: ' + snapshot['name'])
            self.scene.collection.children.link(self.collection)
        staging = bpy.data.collections.new('Dutchman staging')
        self.scene.collection.children.link(staging)
        staging.hide_viewport = True
        staging.hide_render = True
        staged = []
        try:
            # Build the whole snapshot before touching linked objects.
            for body in snapshot['bodies']:
                mesh = bpy.data.meshes.new(body['name'])
                obj = bpy.data.objects.new(body['name'], mesh)
                staging.objects.link(obj)
                staged.append(obj)
                mesh.from_pydata(body['vertices'], [], body['faces'])
                mesh.update()
                colors = list(dict.fromkeys(body['colors']))
                for color in colors:
                    mesh.materials.append(_color_material(color))
                indices = {c: i for i, c in enumerate(colors)}
                mesh.polygons.foreach_set('material_index', [indices[c] for c in body['colors']])
                obj['dutchman_body'] = body['name']
                obj['dutchman_hash'] = body['hash']
            existing = {o['dutchman_body']: o for o in self.collection.objects
                        if 'dutchman_body' in o}
            incoming = {o['dutchman_body'] for o in staged}
            if any(o.mode != 'OBJECT' for o in existing.values()):
                raise transport.ControlError('Leave Edit Mode to update linked bodies')
            for obj in staged:
                old = existing.get(obj['dutchman_body'])
                if old is None:
                    self.collection.objects.link(obj)
                    staging.objects.unlink(obj)
                    obj.name = obj['dutchman_body']
                elif old.get('dutchman_hash') != obj['dutchman_hash']:
                    mesh = obj.data
                    if settings.keep_materials:
                        mesh.materials.clear()
                        for slot in old.material_slots:
                            mesh.materials.append(slot.material)
                        # Preserve face assignments when topology still corresponds.
                        previous = [p.material_index for p in old.data.polygons]
                        for i, poly in enumerate(mesh.polygons):
                            poly.material_index = min(previous[i] if i < len(previous) else 0,
                                                      max(0, len(mesh.materials) - 1))
                    old_mesh = old.data
                    old.data = mesh
                    old['dutchman_hash'] = obj['dutchman_hash']
                    if old_mesh.users == 0:
                        bpy.data.meshes.remove(old_mesh)
            for name, obj in existing.items():
                if name not in incoming:
                    _remove_object(obj)
            self.collection.name = 'SolidCraft: ' + snapshot['name']
            self.revision = snapshot['revision']
            self.updates += 1
        finally:
            for obj in list(staging.objects):
                _remove_object(obj)
            bpy.data.collections.remove(staging)
            for mat in list(bpy.data.materials):
                if mat.get('dutchman_generated') and mat.users == 0:
                    bpy.data.materials.remove(mat)

    def tick(self):
        if not self.active:
            return None
        settings = self.scene.dutchman_link
        try:
            if self.busy:
                try:
                    snapshot, error = self.results.get_nowait()
                except queue.Empty:
                    return .05
                self.busy = False
                self.next_poll = time.monotonic() + settings.interval
                if error:
                    settings.status = 'Error: ' + error[:220]
                else:
                    if snapshot is not None:
                        self.apply(snapshot)
                    settings.status = f'Linked — {len(self.collection.objects)} bodies'
            if not self.busy and time.monotonic() >= self.next_poll:
                self.request()
        except Exception as exc:
            settings.status = 'Error: ' + (str(exc) or type(exc).__name__)[:220]
            self.next_poll = time.monotonic() + settings.interval
        for screen in bpy.data.screens:
            for area in screen.areas:
                if area.type == 'VIEW_3D':
                    area.tag_redraw()
        return .05


def unlink():
    global _runtime
    if _runtime:
        _runtime.active = False
        _runtime.token = ''
        _runtime = None


def link(scene):
    global _runtime
    unlink()
    settings = scene.dutchman_link
    try:
        token = settings.token.strip() or transport.token_path(settings.port).read_text().strip()
        if not token:
            raise transport.ControlError('The control token is empty')
        _runtime = LinkSession(scene, settings.port, token)
        settings.status = 'Connecting…'
        bpy.app.timers.register(_runtime.tick, first_interval=0.0)
        return True
    except Exception as exc:
        settings.status = 'Error: ' + str(exc)[:220]
        return False


class DutchmanSettings(bpy.types.PropertyGroup):
    port: IntProperty(name='Port', default=7878, min=1, max=65535)
    token: StringProperty(name='Token', subtype='PASSWORD', options={'SKIP_SAVE'},
                          description='Leave empty to read the SolidCraft token file automatically')
    interval: FloatProperty(name='Poll interval', default=1.0, min=.1, max=3600, unit='TIME')
    keep_materials: BoolProperty(name='Keep my materials', default=True)
    status: StringProperty(default='Unlinked', options={'SKIP_SAVE'})


class DUTCHMAN_OT_link(bpy.types.Operator):
    bl_idname = 'dutchman.link'
    bl_label = 'Link'
    def execute(self, context):
        link(context.scene)
        return {'FINISHED'}


class DUTCHMAN_OT_unlink(bpy.types.Operator):
    bl_idname = 'dutchman.unlink'
    bl_label = 'Unlink'
    def execute(self, context):
        unlink()
        context.scene.dutchman_link.status = 'Unlinked — imported objects kept'
        return {'FINISHED'}


class DUTCHMAN_PT_link(bpy.types.Panel):
    bl_label = 'Dutchman Link'
    bl_idname = 'DUTCHMAN_PT_link'
    bl_space_type = 'VIEW_3D'
    bl_region_type = 'UI'
    bl_category = 'Dutchman Link'
    def draw(self, context):
        layout = self.layout
        settings = context.scene.dutchman_link
        col = layout.column()
        col.enabled = _runtime is None
        col.prop(settings, 'port')
        col.prop(settings, 'token')
        if not settings.token:
            col.label(text='Token: automatic from local file', icon='LOCKED')
        layout.operator('dutchman.unlink' if _runtime else 'dutchman.link')
        layout.prop(settings, 'interval')
        layout.prop(settings, 'keep_materials')
        # Wrap long server errors so the message remains readable in a narrow sidebar.
        import textwrap
        for line in textwrap.wrap(settings.status, width=38):
            layout.label(text=line)


@persistent
def _load_pre(_):
    unlink()


_classes = (DutchmanSettings, DUTCHMAN_OT_link, DUTCHMAN_OT_unlink, DUTCHMAN_PT_link)


def register():
    for cls in _classes:
        bpy.utils.register_class(cls)
    bpy.types.Scene.dutchman_link = PointerProperty(type=DutchmanSettings)
    bpy.app.handlers.load_pre.append(_load_pre)


def unregister():
    unlink()
    if _load_pre in bpy.app.handlers.load_pre:
        bpy.app.handlers.load_pre.remove(_load_pre)
    del bpy.types.Scene.dutchman_link
    for cls in reversed(_classes):
        bpy.utils.unregister_class(cls)
