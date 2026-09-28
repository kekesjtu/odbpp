"""Generate the seven small KiCad source boards for the positive ODB++ suite.

Run with KiCad's bundled Python, never with the project's uv interpreter::

    E:/Program Files/KiCad/10.0/bin/python.exe generate.py

The coordinates below are the independent design oracle for notes/ and pytest.
ODB++ archives are produced separately by ``export.py`` using kicad-cli.
"""

from __future__ import annotations

import argparse
from pathlib import Path

import pcbnew as p


ROOT = Path(__file__).resolve().parent
SOURCE = ROOT / "sources"


def pt(x: float, y: float) -> p.VECTOR2I:
    return p.VECTOR2I(p.FromMM(x), p.FromMM(y))


def layers(*ids: int) -> p.LSET:
    result = p.LSET()
    for layer in ids:
        result.AddLayer(layer)
    return result


def base(name: str, copper: int = 2) -> p.BOARD:
    board = p.BOARD()
    board.SetCopperLayerCount(copper)
    copper_ids = (p.F_Cu, p.B_Cu) if copper == 2 else (
        p.F_Cu, p.In1_Cu, p.In2_Cu, p.B_Cu)
    board.SetEnabledLayers(layers(*copper_ids, p.Edge_Cuts))
    # Closed 20 x 10 mm rectangle. Only the mandatory board outline is on
    # Edge.Cuts; there are no title, dimension, or decorative drawings.
    for start, end in [((0, 0), (20, 0)), ((20, 0), (20, 10)),
                       ((20, 10), (0, 10)), ((0, 10), (0, 0))]:
        edge = p.PCB_SHAPE(board)
        edge.SetShape(p.SHAPE_T_SEGMENT)
        edge.SetStart(pt(*start))
        edge.SetEnd(pt(*end))
        edge.SetLayer(p.Edge_Cuts)
        edge.SetWidth(p.FromMM(0.05))
        board.Add(edge)
    return board


def pad(board: p.BOARD, number: str, x: float, y: float,
        shape: int, sx: float, sy: float, angle: float = 0) -> None:
    footprint = p.FOOTPRINT(board)
    footprint.SetReference("J" + number)
    footprint.SetValue("pad-" + number)
    footprint.SetPosition(pt(x, y))
    footprint.Reference().SetVisible(False)
    footprint.Value().SetVisible(False)
    item = p.PAD(footprint)
    item.SetNumber(number)
    item.SetAttribute(p.PAD_ATTRIB_SMD)
    item.SetShape(shape)
    item.SetSize(pt(sx, sy))
    item.SetPosition(pt(x, y))
    item.SetLayerSet(layers(p.F_Cu))
    item.SetOrientationDegrees(angle)
    footprint.Add(item)
    board.Add(footprint)


def track(board: p.BOARD, start: tuple[float, float],
          end: tuple[float, float], width: float = 0.2) -> None:
    item = p.PCB_TRACK(board)
    item.SetStart(pt(*start))
    item.SetEnd(pt(*end))
    item.SetWidth(p.FromMM(width))
    item.SetLayer(p.F_Cu)
    board.Add(item)


def via(board: p.BOARD, x: float, y: float, top: int, bottom: int,
        kind: int = p.VIATYPE_THROUGH) -> None:
    item = p.PCB_VIA(board)
    item.SetPosition(pt(x, y))
    item.SetWidth(p.FromMM(0.8))
    item.SetDrill(p.FromMM(0.4))
    item.SetViaType(kind)
    item.SetLayerPair(top, bottom)
    board.Add(item)


def outline_only() -> p.BOARD:
    return base("outline-only")


def pad_shapes() -> p.BOARD:
    board = base("pad-shapes")
    for args in [
        ("1", 3, 3, p.PAD_SHAPE_CIRCLE, 1, 1),
        ("2", 7, 3, p.PAD_SHAPE_RECT, 1, 1),
        ("3", 11, 3, p.PAD_SHAPE_RECT, 2, 1),
        ("4", 16, 3, p.PAD_SHAPE_OVAL, 2, 1),
    ]:
        pad(board, *args)
    return board


def tracks_arcs() -> p.BOARD:
    board = base("tracks-arcs")
    track(board, (2, 3), (7, 3))
    arc = p.PCB_ARC(board)
    arc.SetStart(pt(11, 3))
    arc.SetMid(pt(13, 5))
    arc.SetEnd(pt(15, 3))
    arc.SetWidth(p.FromMM(0.2))
    arc.SetLayer(p.F_Cu)
    board.Add(arc)
    return board


def through_hole() -> p.BOARD:
    board = base("through-hole")
    via(board, 10, 5, p.F_Cu, p.B_Cu)
    return board


def zone_hole_rotation() -> p.BOARD:
    board = base("zone-hole-rotation")
    pad(board, "1", 16, 5, p.PAD_SHAPE_RECT, 2, 1, 30)
    net = p.NETINFO_ITEM(board, "GND")
    board.Add(net)
    zone = p.ZONE(board)
    zone.SetLayer(p.F_Cu)
    zone.SetNet(net)
    zone.SetLocalClearance(p.FromMM(0.2))
    zone.SetMinThickness(p.FromMM(0.1))
    contour = zone.Outline()
    contour.NewOutline()
    for x, y in [(2, 2), (12, 2), (12, 8), (2, 8)]:
        contour.Append(pt(x, y))
    hole = contour.NewHole(0)
    for x, y in [(6, 4), (8, 4), (8, 6), (6, 6)]:
        contour.Append(pt(x, y), 0, hole)
    board.Add(zone)
    filler = p.ZONE_FILLER(board)
    filler.Fill(board.Zones())
    return board


def four_layer_drills() -> p.BOARD:
    board = base("four-layer-drills", 4)
    via(board, 4, 5, p.F_Cu, p.B_Cu, p.VIATYPE_THROUGH)
    via(board, 10, 5, p.F_Cu, p.In1_Cu, p.VIATYPE_BLIND)
    via(board, 16, 5, p.In1_Cu, p.In2_Cu, p.VIATYPE_BURIED)
    return board


BUILDERS = {
    "outline-only": outline_only,
    "pad-shapes": pad_shapes,
    "tracks-arcs": tracks_arcs,
    "through-hole": through_hole,
    "zone-hole-rotation": zone_hole_rotation,
    "four-layer-drills": four_layer_drills,
}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--case", choices=BUILDERS)
    args = parser.parse_args()
    SOURCE.mkdir(exist_ok=True)
    for name, builder in BUILDERS.items():
        if args.case and name != args.case:
            continue
        path = SOURCE / f"{name}.kicad_pcb"
        p.SaveBoard(str(path), builder())
        print(path)


if __name__ == "__main__":
    main()
