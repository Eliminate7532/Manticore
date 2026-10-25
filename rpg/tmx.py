# SPDX-License-Identifier: GPL-3.0-or-later
"""The maps the Tiled editor writes (.tmx, with tilesets inside them or in .tsx files beside them), read by PyTMX - no pygame here.

Round RPG7 (Karl, 13 Oct: "Switch to PyTMX", the library the RPG plan named): PyTMX (bitcraft/PyTMX, LGPL-3.0-or-later, in
requirements.txt) reads the file - the XML, the layer encodings (csv; base64 plain, zlib or gzip), the tilesets inside the map or in
.tsx files, the properties and their types, the objects. This module (Round RPG5's own reader until then) now only:
  - refuses, in plain words naming the file, what the RPG can't use, before PyTMX reads it: a file that isn't a Tiled map, a map
    that isn't orthogonal, an "infinite" map or a layer in chunks;
  - turns what PyTMX read into the small objects the rest of the RPG uses (TiledMap, Tileset, Layer, MapObject - unchanged since
    RPG5), with Tiled's own tile ids: PyTMX numbers the tiles its own way (its gidmap / tiledgidmap say how), and keeps the flip
    bits apart (TileFlags), which become FLIP_H / FLIP_V / FLIP_D here; the hexagonal-rotation bit stays on Tiled's id, so it is
    kept apart as ROTATE_HEX as before;
  - checks what PyTMX doesn't: every layer the map's size with one tile id per cell, tilesets that don't overlap, every tile id in a
    tileset, a tileset with one image (a collection of separate images isn't read);
  - turns PyTMX's errors into TmxError, whose text names the file and what is wrong.
Differences from RPG5's reader, all PyTMX's: a bool property may be written 1/0, yes/no or true/false; layers inside group layers
are read (RPG5 skipped groups); a polygon or polyline object is its bounding box (RPG5 saw a point at its first corner); the old
XML tile-by-tile layer format is refused (Tiled can still write it; it is deprecated); a tile's own attributes (id, type or class,
probability, x, y, width, height) are not kept among its properties, so a custom property with one of those names is lost.
Kept as RPG5 read them (a review of the round found PyTMX did otherwise): image layers are left out before PyTMX reads the map; a
property type PyTMX doesn't know is a string; an empty string property is ""; a tile object keeps the y Tiled wrote and only its
own properties.

Round RPG10 (the Adventure remaster's art, 23 Oct 2026): tiles move.
  - Tiled's own tile animations (a tile's <animation> frames, made in Tiled's tile animation editor) are kept:
    Tileset.anims {local tile id: [(local frame tile id, milliseconds), ...]}.
  - DawnLike animates by pairs of sheets instead (Pit0.png / Pit1.png, Humanoid0.png / Humanoid1.png: the same tile in the same
    place on both), which one Tiled tileset can't say. A tileset property "frame2" names the second sheet (relative to the .tsx
    file, or to the map for a tileset inside it), and "frame_ms" how long each sheet shows (FRAME_MS when not given).
  - TiledMap.frame_at(gid, ms) says what to draw at a moment: (the tile id, 0 for the tileset's own image or 1 for its frame2).
  - A tileset's own properties are kept as Tileset.tprops.
"""
import os
import xml.etree.ElementTree as ET

try:
    import pytmx
except ImportError:                      # requirements.txt has it; the error says what to run
    pytmx = None

FLIP_H = 0x80000000
FLIP_V = 0x40000000
FLIP_D = 0x20000000
ROTATE_HEX = 0x10000000
GID_MASK = 0x0FFFFFFF
FRAME_MS = 500                   # Round RPG10: how long each of a pair of sheets shows (DawnLike's 2-frame tiles) - my estimate
PYTMX_TILE_KEYS = frozenset(("id", "type", "class", "probability", "x", "y", "width", "height", "frames", "source", "trans",
                             "colliders", "path"))     # what PyTMX puts beside a tile's own properties


class TmxError(Exception):
    pass


class Tileset:
    __slots__ = ("firstgid", "name", "tilewidth", "tileheight", "tilecount", "columns", "margin", "spacing", "image",
                 "imagewidth", "imageheight", "props", "source", "tprops", "anims", "image2", "frame_ms")

    def __init__(self):
        self.props = {}               # local tile id -> {name: value}
        self.source = None            # the .tsx file, when the tileset is outside the map
        self.tprops = {}              # Round RPG10: the tileset's own properties
        self.anims = {}               # Round RPG10: local tile id -> [(local frame tile id, ms), ...] (Tiled's tile animations)
        self.image2 = None            # Round RPG10: the second sheet of a pair (tileset property "frame2"), or None
        self.frame_ms = FRAME_MS

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

    def frame_at(self, gid, ms):
        """Round RPG10: what to draw for tile `gid` at `ms` milliseconds: (tile id, 0 = the tileset's image or 1 = its frame2).
        A Tiled animation picks the frame; a pair of sheets switches every frame_ms; a tile with neither is (gid, 0)."""
        ts = self.tileset_for(gid)
        if ts is None:
            return gid, 0
        seq = ts.anims.get(gid - ts.firstgid)
        if seq:
            t = int(ms) % sum(d for _f, d in seq)
            for f, d in seq:
                if t < d:
                    gid = ts.firstgid + f
                    break
                t -= d
        sheet = (int(ms) // ts.frame_ms) % 2 if ts.image2 else 0
        return gid, sheet

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


# ---- before PyTMX: what the RPG can't use, in plain words -------------------------------------------------------------------

def _first_look(path, where):
    """The parsed XML, made ready for PyTMX (which then reads this tree, not the file again): (root, {firstgid: .tsx path},
    [(each <objectgroup> element in document order, is it a map's own - not a tile's collision shapes inside a tileset)]).
    Made ready = what PyTMX can't take is taken out first: image layers (the RPG never draws them, and PyTMX fails on one with no
    picture chosen), a property type PyTMX doesn't know (it raised KeyError; RPG5 read such a property as a string - so it does
    again), and empty values in CSV layer data (a trailing comma: PyTMX fails on it, RPG5 skipped them)."""
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
    for layer in root.iter("layer"):
        data = layer.find("data")
        if data is not None and data.find("chunk") is not None:
            raise TmxError(f"{where}: layer {layer.get('name', '')!r} is in chunks (an infinite map) - not read")
    folder = os.path.dirname(path)
    tsx = {}
    in_tilesets = set()
    for ts in root.findall("tileset"):
        if ts.get("source"):
            try:
                tsx[int(ts.get("firstgid", "0"))] = os.path.normpath(os.path.join(folder, ts.get("source")))
            except ValueError:
                raise TmxError(f"{where}: <tileset> firstgid={ts.get('firstgid')!r} is not a number")
        in_tilesets.update(id(og) for og in ts.iter("objectgroup"))
    own = [(og, id(og) not in in_tilesets) for og in root.iter("objectgroup")]
    parents = {child: parent for parent in root.iter() for child in parent}
    for el in list(root.iter("imagelayer")):
        parents[el].remove(el)
    for data in root.iter("data"):                                  # CSV as RPG5 read it: empty values (a trailing comma) skipped
        if data.get("encoding") == "csv" and data.text:
            data.text = ",".join(v.strip() for v in data.text.replace("\n", "").split(",") if v.strip())
    known = _casts()
    for prop in root.iter("property"):
        kind = prop.get("type")
        if kind not in (None, "class") and kind not in known:
            del prop.attrib["type"]
    return root, tsx, own


def _casts():
    return getattr(getattr(pytmx, "pytmx", None), "prop_type", {}) or {}


def _bad_property(roots, e):
    """(file, name, type, value) of the typed property whose cast fails with exactly PyTMX's error (its message gives only the
    first letter of a bad bool) - in the map or one of its .tsx files - or None. A property that fails some other way is not
    blamed for this error."""
    casts = _casts()
    for where, root in roots:
        for p in root.iter("property"):
            kind = p.get("type")
            cast = casts.get(kind) if kind not in (None, "class") else None
            if cast is None:
                continue
            value = p.get("value")
            try:
                cast(value)
            except (TypeError, ValueError) as mine:
                if type(mine) is type(e) and str(mine) == str(e):
                    return where, p.get("name"), kind, value
    return None


def _said(where, e, roots=()):
    """A PyTMX error as a sentence naming the file."""
    import binascii
    import struct
    import zlib
    text = str(e).strip()
    if isinstance(e, (zlib.error, struct.error, binascii.Error, EOFError)) or "decompress" in text.lower():
        return f"{where}: a layer's data is damaged ({type(e).__name__}: {text})"
    if "compression" in text and "not supported" in text:
        return f"{where}: {text} Save the map with CSV, zlib or gzip in Tiled (Map > Map Properties > Tile Layer Format)."
    if isinstance(e, OSError):
        return f"{where}: can't be read ({text})"
    bad = _bad_property(roots, e) if isinstance(e, (TypeError, ValueError)) else None
    if bad:
        return f"{bad[0]}: property {bad[1]!r} of type {bad[2]} has the value {bad[3]!r}"
    return f"{where}: PyTMX couldn't read it ({type(e).__name__}: {text or 'no reason given'})"


# ---- PyTMX reads it; we keep what the RPG uses -----------------------------------------------------------------------------

def _props(d):
    """PyTMX's properties, with an empty string property as "" (PyTMX gives None: it reads `value or text`)."""
    return {k: ("" if v is None else v) for k, v in dict(d).items()}


def read_map(path):
    where = os.path.basename(path)
    if pytmx is None:
        raise TmxError(f"{where}: PyTMX isn't installed - run: python -m pip install -r requirements.txt")
    root, tsx, own_groups = _first_look(path, where)
    try:
        # no image loader: the tilesets' images are left to the view; invert_y off: a tile object keeps the y Tiled wrote (as RPG5)
        pm = pytmx.TiledMap(None, allow_duplicate_names=True, invert_y=False)
        pm.filename = path
        pm.parse_xml(root)
    except Exception as e:                                          # PyTMX raises ValueError, zlib.error, plain Exception, ...
        roots = [(where, root)]
        for t in tsx.values():
            try:
                roots.append((os.path.basename(t), ET.parse(t).getroot()))
            except (OSError, ET.ParseError):
                pass
        raise TmxError(_said(where, e, roots))
    folder = os.path.dirname(path)
    m = TiledMap()
    m.path = path
    m.width, m.height = int(pm.width), int(pm.height)
    m.tilewidth, m.tileheight = int(pm.tilewidth), int(pm.tileheight)
    m.props = _props(pm.properties)
    m.tilesets, m.layers, m.objects = [], [], []
    for pts in sorted(pm.tilesets, key=lambda t: t.firstgid):
        ts = Tileset()
        ts.firstgid = int(pts.firstgid)
        ts.name = pts.name or ""
        ts.tilewidth, ts.tileheight = int(pts.tilewidth), int(pts.tileheight)
        ts.margin, ts.spacing = int(pts.margin or 0), int(pts.spacing or 0)
        if not pts.source:
            raise TmxError(f"{where}: tileset {ts.name!r} has no <image> (a collection of separate images isn't read)")
        ts.image = os.path.normpath(os.path.join(folder, pts.source))
        ts.imagewidth, ts.imageheight = int(pts.width), int(pts.height)
        ts.columns = int(pts.columns or 0) or max(1, (ts.imagewidth - 2 * ts.margin + ts.spacing) // (ts.tilewidth + ts.spacing))
        ts.tilecount = int(pts.tilecount or 0)
        if not ts.tilecount:
            rows = (ts.imageheight - 2 * ts.margin + ts.spacing) // (ts.tileheight + ts.spacing)
            ts.tilecount = rows * ts.columns
        ts.source = tsx.get(ts.firstgid)
        ts.tprops = _props(getattr(pts, "properties", None) or {})
        for local in range(ts.tilecount):
            mapped = pm.gidmap.get(ts.firstgid + local)
            if not mapped:
                continue
            raw = pm.tile_properties.get(mapped[0][0]) or {}
            props = {k: v for k, v in _props(raw).items() if k not in PYTMX_TILE_KEYS}
            if props:
                ts.props[local] = props
            frames = raw.get("frames") or []
            if frames:
                seq = []
                for fr in frames:
                    f = (pm.tiledgidmap.get(fr.gid, 0) & GID_MASK) - ts.firstgid
                    if not 0 <= f < ts.tilecount or int(fr.duration) <= 0:
                        raise TmxError(f"{where}: tileset {ts.name!r} tile {local}'s animation has a frame that isn't one of its "
                                       f"tiles, or lasts no time (tile {f}, {fr.duration} ms)")
                    seq.append((f, int(fr.duration)))
                ts.anims[local] = seq
        pair = ts.tprops.get("frame2")
        if pair:
            ts.image2 = os.path.normpath(os.path.join(os.path.dirname(ts.source) if ts.source else folder, str(pair)))
            if not os.path.exists(ts.image2):
                raise TmxError(f"{where}: tileset {ts.name!r}'s frame2 picture {pair!r} isn't there")
        try:
            ts.frame_ms = int(ts.tprops.get("frame_ms") or FRAME_MS)
        except (TypeError, ValueError):
            raise TmxError(f"{where}: tileset {ts.name!r}'s frame_ms {ts.tprops.get('frame_ms')!r} is not a number")
        if ts.frame_ms <= 0:
            raise TmxError(f"{where}: tileset {ts.name!r}'s frame_ms must be above 0")
        m.tilesets.append(ts)
    flags_of = {v[0]: v[1] for v in pm.imagemap.values() if isinstance(v, tuple) and v[0]}     # PyTMX's tile -> its flips
    groups = [layer for layer in pm.layers if isinstance(layer, pytmx.TiledObjectGroup)]
    for player in pm.layers:
        if not isinstance(player, pytmx.TiledTileLayer):
            continue
        layer = Layer()
        layer.name = player.name or ""
        layer.width, layer.height = int(player.width), int(player.height)
        if (layer.width, layer.height) != (m.width, m.height):
            raise TmxError(f"{where}: layer {layer.name!r} isn't the map's size")
        flat = [g for row in player.data for g in row]
        if len(flat) != layer.width * layer.height:
            raise TmxError(f"{where}: layer {layer.name!r} has {len(flat)} tiles; the map is {layer.width * layer.height}")
        layer.data, layer.flips = [], {}
        for i, g in enumerate(flat):
            tiled = pm.tiledgidmap.get(g, 0) if g else 0
            f = flags_of.get(g)
            bits = (tiled & ~GID_MASK) | (FLIP_H if f and f.flipped_horizontally else 0) | \
                (FLIP_V if f and f.flipped_vertically else 0) | (FLIP_D if f and f.flipped_diagonally else 0)
            layer.data.append(tiled & GID_MASK)
            if bits:
                layer.flips[i] = bits
        layer.visible = bool(player.visible)
        layer.opacity = float(player.opacity)
        layer.props = _props(player.properties)
        m.layers.append(layer)
    for group, (element, own) in zip(groups, own_groups):
        if not own:                                                 # a tile's collision shapes, not the map's objects
            continue
        for o, o_el in zip(group, element.findall("object")):
            ob = MapObject()
            ob.id = int(o.id or 0)
            ob.name = o.name or ""
            ob.kind = o.__dict__.get("class") or o.__dict__.get("type") or ""    # Tiled 1.9+ writes class, older files type
            points = o.__dict__.get("points")                        # a polygon or polyline: PyTMX gives its corners
            if points:
                xs, ys = [p[0] for p in points], [p[1] for p in points]
                ob.x, ob.y = float(min(xs)), float(min(ys))
                ob.width, ob.height = float(max(xs) - min(xs)), float(max(ys) - min(ys))
            else:
                ob.x, ob.y = float(o.x), float(o.y)
                ob.width, ob.height = float(o.width), float(o.height)
            ob.props = _props(pytmx.parse_properties(o_el))          # its own: PyTMX adds a tile object's tile's to o.properties
            m.objects.append(ob)
    top = 0
    for ts in m.tilesets:
        if ts.firstgid <= top:
            raise TmxError(f"{where}: tileset {ts.name!r} overlaps the one before it")
        top = ts.firstgid + ts.tilecount - 1
    for layer in m.layers:
        for gid in set(layer.data):
            ts = m.tileset_for(gid) if gid else None
            if gid and (ts is None or gid > ts.firstgid + ts.tilecount - 1):
                raise TmxError(f"{where}: layer {layer.name!r} uses tile {gid}, which no tileset has")
    return m
