# Copyright (C) 2012 Tim Radvan
#
# This file is part of Kurt.
#
# Kurt is free software: you can redistribute it and/or modify it under the
# terms of the GNU Lesser General Public License as published by the Free
# Software Foundation, either version 3 of the License, or (at your option) any
# later version.
#
# Kurt is distributed in the hope that it will be useful, but WITHOUT ANY
# WARRANTY; without even the implied warranty of MERCHANTABILITY or FITNESS FOR
# A PARTICULAR PURPOSE. See the GNU Lesser General Public License for more
# details.
#
# You should have received a copy of the GNU Lesser General Public License along
# with Kurt. If not, see <http://www.gnu.org/licenses/>.

"""A Kurt plugin for Scratch 2.0."""

import zipfile
import json
import time
import os
import hashlib
import struct

import kurt
from kurt.plugin import Kurt, KurtPlugin

from kurt.scratch20.blocks import make_block_types


WATCHER_MODES = [None,
    'normal',
    'large',
    'slider',
]

CATEGORY_COLORS = {
    'variables': kurt.Color('#ee7d16'),
    'motion': kurt.Color('#4a6cd4'),
    'looks': kurt.Color('#8a55d7'),
    'sound': kurt.Color('#bb42c3'),
    'sensing': kurt.Color('#2ca5e2'),
}

class ZipReader(object):
    def __init__(self, path):
        self.zip_file = zipfile.ZipFile(path)
        self.json = json.load(self.zip_file.open("project.json"))
        self.project = kurt.Project()
        self.list_watchers = []

        # stage
        self.project.stage = self.load_scriptable(self.json, is_stage=True)

        # sprites
        actors = []
        for cd in self.json['children']:
            if 'objName' in cd:
                sprite = self.load_scriptable(cd)
                self.project.sprites.append(sprite)
                actors.append(sprite)
            else:
                actors.append(cd)

        # watchers
        for actor in actors:
            if not isinstance(actor, kurt.Sprite):
                actor = self.load_watcher(actor)
            self.project.actors.append(actor)

        self.project.actors += self.list_watchers

    def finish(self):
        self.zip_file.close()

    def load_scriptable(self, sd, is_stage=False):
        if is_stage:
            scriptable = kurt.Stage(self.project)
        elif 'objName' in sd:
            scriptable = kurt.Sprite(self.project,
                    sd["objName"])
        else:
            return self.load_watcher(sd)

        for script_array in sd.get("scripts", []):
            scriptable.scripts.append(self.load_script(script_array))

        target = self.project if is_stage else scriptable

        for vd in sd.get("variables", []):
            var = kurt.Variable(vd['value'], vd['isPersistent'])
            target.variables[vd['name']] = var

        for ld in sd.get("lists", []):
            name = ld['listName']
            target.lists[name] = kurt.List(ld['contents'],
                    ld['isPersistent'])
            self.list_watchers.append(kurt.Watcher(target,
                    kurt.Block("contentsOfList:", name), visible=ld['visible'],
                    pos=(ld['x'], ld['y'])))

        if not is_stage:
            pass

        return scriptable

    def load_watcher(self, wd):
        command = 'readVariable' if wd['cmd'] == 'getVar:' else wd['cmd']
        if wd['target'] == 'Stage':
            target = self.project
        else:
            target = self.project.get_sprite(wd['target'])
        watcher = kurt.Watcher(target,
            kurt.Block(command, *(wd['param'].split(',') if wd['param']
                                                         else [])),
            style=WATCHER_MODES[wd['mode']],
            visible=wd['visible'],
            pos=(wd['x'], wd['y']),
        )
        watcher.slider_min = wd['sliderMin']
        watcher.slider_max = wd['sliderMax']
        return watcher

    def load_block(self, block_array):
        command = block_array.pop(0)
        block_type = kurt.BlockType.get(command)

        inserts = list(block_type.inserts)
        args = []
        for arg in block_array:
            insert = inserts.pop(0) if inserts else None
            if isinstance(arg, list):
                if isinstance(arg[0], list): # 'stack'-shaped Insert
                    arg = map(self.load_block, arg)
                else: # Block
                    arg = self.load_block(arg)
            elif insert:
                if insert.kind == 'spriteOrStage' and arg == '_stage_':
                    arg = 'Stage'
                elif insert.shape == 'color':
                    arg = self.load_color(arg)
            args.append(arg)

        return kurt.Block(block_type, *args)

    def load_script(self, script_array):
        (x, y, blocks) = script_array
        blocks = map(self.load_block, blocks)
        return kurt.Script(blocks, pos=(x, y))

    def load_color(self, value):
        # convert signed to unsigned 32-bit int
        value = struct.unpack('=I', struct.pack('=i', value))[0]
        # throw away leading ff, if any
        value &= 0x00ffffff
        return kurt.Color(
            (value & 0xff0000) >> 16,
            (value & 0x00ff00) >> 8,
            (value & 0x0000ff),
        )


class ZipWriter(object):
    def __init__(self, path, project):
        self.zip_file = zipfile.ZipFile(path, "w")
        self.image_dicts = {}

        self.json = {
            "penLayerMD5": "279467d0d49e152706ed66539b577c00.png",
            "info": {},
            "tempoBPM": project.tempo,
            "children": [],

            "info": {
                "flashVersion": "MAC 11,7,700,203",
                "projectID": "10442014",
                "scriptCount": 0,
                "spriteCount": 0,
                "userAgent": "",
                "videoOn": False,
                "hasCloudData": False, # TODO
            },
            "videoAlpha": 0.5,
        }

        self.json.update(self.save_scriptable(project.stage))
        sprites = {}
        for (i, sprite) in enumerate(project.sprites):
            sprites[sprite.name] = self.save_scriptable(sprite, i)
        for actor in project.actors:
            if isinstance(actor, kurt.Sprite):
                actor = sprites[actor.name]
            elif isinstance(actor, kurt.Watcher):
                actor = self.save_watcher(actor)

            if actor:
                self.json["children"].append(actor)

        self.write_file("project.json", json.dumps(self.json))

    def finish(self):
        self.zip_file.close()

    def write_file(self, name, contents):
        """Write file contents string into archive."""
        # TODO: find a way to make ZipFile accept a file object.
        zi = zipfile.ZipInfo(name)
        zi.date_time = time.localtime(time.time())[:6]
        zi.compress_type = zipfile.ZIP_DEFLATED
        zi.external_attr = 0777 << 16L
        self.zip_file.writestr(zi, contents)

    def write_image(self, image):
        if image not in self.image_dicts:
            image_id = len(self.image_dicts)
            image = image.convert("SVG", "JPEG", "PNG")
            filename = str(image_id) + (image.extension or ".png")
            self.write_file(filename, image.contents)

            self.image_dicts[image] = {
                "baseLayerID": image_id, # -1 for download
                "bitmapResolution": 1,
                "baseLayerMD5": hashlib.md5(image.contents).hexdigest(),
            }
        return self.image_dicts[image]

    def save_watcher(self, watcher):
        if watcher.kind == 'list':
            return

        tbt = watcher.block.type.translate('scratch20')
        if tbt.command == 'senseVideoMotion':
            label = 'video ' + watcher.block.args[0]
        elif tbt.command == 'timeAndDate':
            label = watcher.block.args[0]
        else:
            label = tbt.text % tuple(watcher.block.args)

        if not isinstance(watcher.target, kurt.Project):
            label = watcher.target.name + " " + label

        return {
            'cmd': 'getVar:' if tbt.command == 'readVariable' else tbt.command,
            'param': ",".join(map(unicode, watcher.block.args))
                    if watcher.block.args else None,
            'label': label,
            'target': ('Stage' if isinstance(watcher.target, kurt.Project)
                               else watcher.target.name),
            'mode': WATCHER_MODES.index(watcher.style),
            'sliderMax': watcher.slider_max,
            'sliderMin': watcher.slider_min,
            'visible': watcher.visible,
            'x': watcher.pos[0],
            'y': watcher.pos[1],
            'color': self.save_color(CATEGORY_COLORS[tbt.category]),
            'isDiscrete': True,
        }

    def save_scriptable(self, scriptable, i=None):
        is_sprite = isinstance(scriptable, kurt.Sprite)

        sd = {
            "objName": scriptable.name,
            "currentCostumeIndex": scriptable.costume_index or 0,
            "scripts": [self.save_script(s) for s in scriptable.scripts],
            "costumes": [self.save_costume(c) for c in scriptable.costumes],
            "sounds": [],
            "variables": [],
            "lists": [],
        }

        if is_sprite:
            sd.update({
                "scratchX": 0,
                "scratchY": 0,
                "scale": 1,
                "direction": 90,
                "indexInLibrary": i+1,
                "isDraggable": False,
                "rotationStyle": "normal",
                "spriteInfo": {},
                "visible": True,
            })

        target = scriptable if is_sprite else scriptable.project

        for (name, variable) in target.variables.items():
            sd["variables"].append({
                "name": name,
                "value": variable.value,
                "isPersistent": variable.is_cloud,
            })

        for (name, _list) in target.lists.items():
            watcher = _list.watcher or kurt.Watcher(target,
                        kurt.Block("contentsOfList:", name), visible=False)

            sd["lists"].append({
                "listName": name,
                "contents": _list.items,
                "isPersistent": _list.is_cloud,
                "visible": watcher.visible,
                "x": watcher.pos[0],
                "y": watcher.pos[1],
                "width": 120,
                "height": 117,
            })

        return sd

    def save_block(self, block):
        command = block.type.translate("scratch20").command
        args = []
        inserts = list(block.type.inserts)
        for arg in block.args:
            insert = inserts.pop(0) if inserts else None
            if isinstance(arg, kurt.Block):
                arg = self.save_block(arg)
            elif isinstance(arg, list):
                arg = map(self.save_block, arg)
            elif isinstance(arg, kurt.Color):
                arg = self.save_color(arg)
            elif insert:
                if insert.kind == 'spriteOrStage':
                    if arg == 'Stage':
                        arg = '_stage_'
            args.append(arg)
        return [command] + args

    def save_script(self, script):
        (x, y) = script.pos or (10, 10)
        return [x, y, map(self.save_block, script.blocks)]

    def save_costume(self, costume):
        cd = self.write_image(costume.image)
        (rx, ry) = costume.rotation_center
        cd.update({
            "costumeName": costume.name,
            "rotationCenterX": rx,
            "rotationCenterY": ry,
        })
        return cd

    def save_color(self, color):
        # build RGB values
        value = (color.r << 16) + (color.g << 8) + color.b
        # convert unsigned to signed 32-bit int
        value = struct.unpack('=i', struct.pack('=I', value))[0]
        return value

class Scratch20Plugin(KurtPlugin):
    name = "scratch20"
    display_name = "Scratch 2.0"
    extension = ".sb2"

    def make_blocks(self):
        return make_block_types()

    def load(self, path):
        zl = ZipReader(path)
        zl.project._original = zl.json
        zl.finish()
        return zl.project

    def save(self, path, project):
        zw = ZipWriter(path, project)
        zw.finish()
        return zw.json



Kurt.register(Scratch20Plugin())
