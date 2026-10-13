# SPDX-License-Identifier: GPL-3.0-or-later
"""A reader for the maps the Tiled editor writes (.tmx, with tilesets inside them or in .tsx files beside them) - no pygame.

The plan names PyTMX for this. Round RPG5 reads the files itself instead: the part of the format the RPG needs is small, and our own
reader adds no library to install, ship or list in the licences. What it reads (Tiled's documented TMX format, version 1.x):
  - <map orientation="orthogonal"> with fixed width and height (an "infinite" map is refused: its layers come in chunks)
  - tilesets inside the map or in a .tsx file (a path relative to the map): tile size, columns, tile count, margin, spacing, the
    image's file and size, and each tile's <properties>
  - tile layers in csv, or base64 plain, zlib or gzip (zstd is refused - Python has no zstd before 3.14), in the map's order, each
    with its name, visible flag, opacity and properties; the three flip bits on a tile id are kept apart (Tile ids are plain)
  - object groups: rectangles and points with id, name, class (Tiled 1.9+; "type" in older files), x, y, width, height, properties
  - properties of type string, int, float, bool, color, file and object (an id)
Anything else in the file (image layers, groups, Wang sets, animations, templates) is skipped. A file that can't be read raises
TmxError, whose text names the file and what is wrong.
"""
import base64
import gzip
import os
import xml.etree.ElementTree as ET
import zlib

FLIP_H = 0x80000000
FLIP_V = 0x40000000
FLIP_D = 0x20000000
ROTATE_HEX = 0x10000000
GID_MASK = 0x0FFFFFFF


class TmxError(Exception):
    pass


class Tileset:
    __slots__ = ("firstgid", "name", "tilewidth", "tileheight", "tilecount", "columns", "margin", "spacing", "image",
                 "imagewidth", "imageheight", "props", "source")

    def __init__(self):
        self.props = {}               # local tile id -> {name: value}
        self.source = None            # the .tsx file, when the tileset is outside the map

    def local(self, gid):
        return gid - self.firstgid

    def rect(self, local_id):
        """(x, y, w, h) of a tile in the tileset's image."""
        col, row = local_id % self.columns, local_id // self.columns
        x = self.margin + col * (self.tilewidth + self.spacing)
        y = self.margin + row * (self.tileheight + self.spacing)
        return x, y, self.tilewidth, self.tileheight


class Layer:
    __slots__ = ("name", "width", "height", "data", "flips", "visible", "opacity", "props")

    def tile(self, x, y):
        """The plain tile id at (x, y), 0 = empty."""
        return self.data[y * self.width + x]


class MapObject:
    __slots__ = ("id", "name", "kind", "x", "y", "width", "height", "props")

    @property
    def is_point(self):
        return self.width == 0 and self.height == 0

    def __repr__(self):
        return f"MapObject({self.id}, {self.kind!r}, {self.name!r}, {self.x},{self.y} {self.width}x{self.height})"


class TiledMap:
    __slots__ = ("path", "width", "height", "tilewidth", "tileheight", "tilesets", "layers", "objects", "props")

    def tileset_for(self, gid):
        """The tileset a plain tile id belongs to (the one with the highest firstgid not above it), or None for 0."""
        if gid <= 0:
            return None
        found = None
        for ts in self.tilesets:
            if ts.firstgid <= gid:
                found = ts
        return found

    def tile_props(self, gid):
        ts = self.tileset_for(gid)
        if ts is None:
            return {}
        return ts.props.get(ts.local(gid), {})

    def layer(self, name):
        for layer in self.layers:
            if layer.name == name:
                return layer
        return None

    @property
    def pixel_size(self):
        return self.width * self.tilewidth, self.height * self.tileheight


def _int(el, key, where, default=None):
    v = el.get(key)
    if v is None:
        if default is not None:
            return default
        raise TmxError(f"{where}: <{el.tag}> has no {key}")
    try:
        return int(float(v)) if "." in v else int(v)
    except ValueError:
        raise TmxError(f"{where}: <{el.tag}> {key}={v!r} is not a number")


def _float(el, key, default=0.0):
    v = el.get(key)
    return default if v is None else float(v)


def read_properties(el, where):
    out = {}
    props = el.find("properties")
    if props is None:
        return out
    for p in props.findall("property"):
        name, kind = p.get("name"), p.get("type", "string")
        value = p.get("value")
        if value is None:
            value = p.text or ""
        try:
            if kind == "int" or kind == "object":
                value = int(value)
            elif kind == "float":
                value = float(value)
            elif kind == "bool":
                if value not in ("true", "false"):
                    raise ValueError(value)
                value = value == "true"
        except ValueError:
            raise TmxError(f"{where}: property {name!r} of type {kind} has the value {value!r}")
        out[name] = value
    return out


def _tileset_body(el, ts, where, folder):
    ts.name = el.get("name", "")
    ts.tilewidth = _int(el, "tilewidth", where)
    ts.tileheight = _int(el, "tileheight", where)
    ts.tilecount = _int(el, "tilecount", where, 0)
    ts.margin = _int(el, "margin", where, 0)
    ts.spacing = _int(el, "spacing", where, 0)
    img = el.find("image")
    if img is None:
        raise TmxError(f"{where}: tileset {ts.name!r} has no <image> (a collection of separate images isn't read)")
    ts.image = os.path.normpath(os.path.join(folder, img.get("source", "")))
    ts.imagewidth = _int(img, "width", where)
    ts.imageheight = _int(img, "height", where)
    ts.columns = _int(el, "columns", where, 0) or max(1, (ts.imagewidth - 2 * ts.margin + ts.spacing) //
                                                     (ts.tilewidth + ts.spacing))
    if not ts.tilecount:
        rows = (ts.imageheight - 2 * ts.margin + ts.spacing) // (ts.tileheight + ts.spacing)
        ts.tilecount = rows * ts.columns
    for t in el.findall("tile"):
        tid = _int(t, "id", where)
        props = read_properties(t, where)
        if props:
            ts.props[tid] = props


def read_tileset(path, firstgid=1):
    """A .tsx file on its own."""
    try:
        root = ET.parse(path).getroot()
    except (OSError, ET.ParseError) as e:
        raise TmxError(f"{os.path.basename(path)}: can't be read ({e})")
    if root.tag != "tileset":
        raise TmxError(f"{os.path.basename(path)}: not a Tiled tileset")
    ts = Tileset()
    ts.firstgid = firstgid
    ts.source = path
    _tileset_body(root, ts, os.path.basename(path), os.path.dirname(path))
    return ts


def _decode(data_el, count, where, name):
    enc = data_el.get("encoding")
    comp = data_el.get("compression")
    text = (data_el.text or "").strip()
    if data_el.findall("chunk"):
        raise TmxError(f"{where}: layer {name!r} is in chunks (an infinite map) - not read")
    if enc == "csv":
        try:
            values = [int(v) for v in text.replace("\n", "").split(",") if v.strip() != ""]
        except ValueError:
            raise TmxError(f"{where}: layer {name!r} has a value that isn't a number")
    elif enc == "base64":
        try:
            raw = base64.b64decode(text)
        except ValueError:
            raise TmxError(f"{where}: layer {name!r} isn't valid base64")
        try:
            if comp == "zlib":
                raw = zlib.decompress(raw)
            elif comp == "gzip":
                raw = gzip.decompress(raw)
            elif comp:
                raise TmxError(f"{where}: layer {name!r} uses {comp} compression - save the map with CSV, zlib or gzip")
        except (zlib.error, OSError, EOFError):
            raise TmxError(f"{where}: layer {name!r}'s {comp} data is damaged")
        if len(raw) % 4:
            raise TmxError(f"{where}: layer {name!r}'s data isn't whole tile ids")
        values = [int.from_bytes(raw[i:i + 4], "little") for i in range(0, len(raw), 4)]
    elif enc is None and data_el.findall("tile"):
        values = [int(t.get("gid", "0")) for t in data_el.findall("tile")]
    else:
        raise TmxError(f"{where}: layer {name!r} has an encoding this reader doesn't know ({enc!r})")
    if len(values) != count:
        raise TmxError(f"{where}: layer {name!r} has {len(values)} tiles; the map is {count}")
    return values


def read_map(path):
    where = os.path.basename(path)
    try:
        root = ET.parse(path).getroot()
    except (OSError, ET.ParseError) as e:
        raise TmxError(f"{where}: can't be read ({e})")
    if root.tag != "map":
        raise TmxError(f"{where}: not a Tiled map")
    if root.get("orientation", "orthogonal") != "orthogonal":
        raise TmxError(f"{where}: {root.get('orientation')} maps aren't read - only orthogonal ones")
    if root.get("infinite", "0") == "1":
        raise TmxError(f"{where}: an infinite map isn't read - give the map a fixed size in Tiled (Map > Map Properties)")
    m = TiledMap()
    m.path = path
    m.width, m.height = _int(root, "width", where), _int(root, "height", where)
    m.tilewidth, m.tileheight = _int(root, "tilewidth", where), _int(root, "tileheight", where)
    m.props = read_properties(root, where)
    m.tilesets, m.layers, m.objects = [], [], []
    folder = os.path.dirname(path)
    for el in root:
        if el.tag == "tileset":
            firstgid = _int(el, "firstgid", where)
            if el.get("source"):
                ts = read_tileset(os.path.normpath(os.path.join(folder, el.get("source"))), firstgid)
            else:
                ts = Tileset()
                ts.firstgid = firstgid
                _tileset_body(el, ts, where, folder)
            m.tilesets.append(ts)
        elif el.tag == "layer":
            layer = Layer()
            layer.name = el.get("name", "")
            layer.width, layer.height = _int(el, "width", where, m.width), _int(el, "height", where, m.height)
            if (layer.width, layer.height) != (m.width, m.height):
                raise TmxError(f"{where}: layer {layer.name!r} isn't the map's size")
            data = el.find("data")
            if data is None:
                raise TmxError(f"{where}: layer {layer.name!r} has no <data>")
            raw = _decode(data, layer.width * layer.height, where, layer.name)
            layer.data = [g & GID_MASK for g in raw]
            layer.flips = {i: g & ~GID_MASK for i, g in enumerate(raw) if g & ~GID_MASK}
            layer.visible = el.get("visible", "1") != "0"
            layer.opacity = _float(el, "opacity", 1.0)
            layer.props = read_properties(el, where)
            m.layers.append(layer)
        elif el.tag == "objectgroup":
            for o in el.findall("object"):
                ob = MapObject()
                ob.id = _int(o, "id", where, 0)
                ob.name = o.get("name", "")
                ob.kind = o.get("class") or o.get("type") or ""
                ob.x, ob.y = _float(o, "x"), _float(o, "y")
                ob.width, ob.height = _float(o, "width"), _float(o, "height")
                ob.props = read_properties(o, where)
                m.objects.append(ob)
    m.tilesets.sort(key=lambda t: t.firstgid)
    top = 0
    for ts in m.tilesets:
        if ts.firstgid <= top:
            raise TmxError(f"{where}: tileset {ts.name!r} overlaps the one before it")
        top = ts.firstgid + ts.tilecount - 1
    for layer in m.layers:
        for gid in set(layer.data):
            if gid and (m.tileset_for(gid) is None or gid > m.tileset_for(gid).firstgid + m.tileset_for(gid).tilecount - 1):
                raise TmxError(f"{where}: layer {layer.name!r} uses tile {gid}, which no tileset has")
    return m
