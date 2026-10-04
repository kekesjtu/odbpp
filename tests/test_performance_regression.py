"""Independent references for optimised indexing and byte resampling."""
import math
import random

import pytest

from odbpp.clipper import region_from_rings
from odbpp.equiv import _Scanline
from odbpp.raster import LayerMask, region_edges, spans_at
from odbpp.rectify import _ProfileIndex, _crossings, _envelope, _intersect_spans
from odbpp.textio import Token, tokenize, split_sections


@pytest.mark.parametrize("size", [(1,1,7,9), (7,9,1,1), (13,7,13,7),
                                   (13,7,8,11), (13,7,37,22), (13,7,5,3)])
def test_resampling_matches_integer_pixel_reference(size):
    px, py, width, height = size
    mask = LayerMask(px, py)
    mask.pixels[:] = bytes(random.Random(71).randbytes(px*py))
    expected = [bytes(mask.pixels[(py-1-(y*py)//height)*px+(x*px)//width]
                      for x in range(width)) for y in range(height)]
    assert list(mask.resampled_rows(width,height)) == expected


def test_active_edges_match_unindexed_scanlines_at_vertices_and_repeated_rows():
    rng = random.Random(42)
    edges = [(rng.uniform(-5,5), rng.uniform(-3,3),
              rng.uniform(-5,5), rng.uniform(-3,3)) for _ in range(300)]
    edges += [(0,0,10,0), (5,-2,5,2), (1,1,1,1)]
    values = sorted([-4,4,0,0,1,1]+[y for edge in edges for y in (edge[1],edge[3])])
    scan = _Scanline(edges)
    for y in values:
        assert scan.spans_at(y) == spans_at(edges,y)


@pytest.mark.parametrize("orthogonal", [False, True])
def test_indexed_envelopes_match_full_profile_scans(orthogonal):
    region = region_from_rings([
        ([(0,0),(11,0),(10,8),(6,10),(0,9)], False),
        ([(2,2),(4,2),(4,5),(2,5)], True),
        ([(13,1),(15,2),(14,4)], False)])
    index = _ProfileIndex(region)
    for a,b in [(-1,0),(0,2),(1.2,7.8),(2,4),(4,6),(8,13),(13,16)]:
        events = sorted({a,b}|{x for r in region.rings for x,y in r.points if a<x<b})
        spans = None
        for left,right in zip(events,events[1:]):
            for x in (left+(right-left)*1e-8,right-(right-left)*1e-8):
                value = _crossings(region.rings,x)
                spans = value if spans is None else _intersect_spans(spans,value)
        expected = []
        for low,high in spans or []:
            if not orthogonal:
                low = math.ceil(low/.5-1e-7)*.5
                high = math.floor(high/.5+1e-7)*.5
            if high>low+1e-8:
                expected.append((round(low,9),round(high,9)))
        assert _envelope(region,a,b,.5,0,orthogonal,index) == expected


@pytest.mark.parametrize("text", ["", " \t", "P\t1  2\t0 P 0;1=3;;ID=7",
                                   "x\ny z\r", "铜\u00a0层\tabc", "  one\ttwo  "])
def test_unquoted_tokens_keep_exact_columns_and_whitespace_semantics(text):
    expected = []
    start = 0
    while start<len(text):
        if text[start] in " \t":
            start += 1
            continue
        end = start
        while end<len(text) and text[end] not in " \t":
            end += 1
        expected.append(Token(text[start:end],False,start))
        start = end
    assert tokenize(text) == expected
    assert split_sections(text) == text.split(";")


def test_pad_cache_ends_with_run_and_returned_regions_are_independent(monkeypatch):
    from odbpp.expand import Expander
    from odbpp.features import FeatureFile, PadFeature, SymbolRef
    from odbpp.units import UnitScope, UnitSystem
    expander = Expander(None)
    symbol = SymbolRef(index=0,name="s1000")
    pad = PadFeature(line_no=1,symbol=symbol,x_mm=0,y_mm=0)
    features = FeatureFile(path="features",units=UnitScope(UnitSystem.MM),records=[pad,pad])
    import odbpp.expand as module
    convert = module.region_from_geometry
    calls = []

    def count(*args):
        calls.append(1)
        return convert(*args)

    monkeypatch.setattr(module,"region_from_geometry",count)
    first = expander.run_region(features.records,features,depth=0)
    assert len(calls) == 1 and first.area == 1
    first.rings.clear()
    assert expander.run_region(features.records,features,depth=0).area == 1
    assert len(calls) == 2
    a = expander.record_region(pad,features)
    a.rings.clear()
    assert expander.record_region(pad,features).area == 1
    assert len(calls) == 4


def test_profile_probe_rounding_at_large_coordinates_keeps_endpoint_edges():
    from odbpp.clipper import Region, Ring
    source = Region([Ring(((1e6,0),(1e6+.01,1),(1e6+.02,0)),False)])
    a,b = 1e6+.005,1e6+.01
    left = _crossings(source.rings,a+(b-a)*1e-8)
    right = _crossings(source.rings,b-(b-a)*1e-8)
    expected = [(round(lo,9),round(hi,9)) for lo,hi in _intersect_spans(left,right)]
    assert expected
    assert _envelope(source,a,b,.5,0,True,_ProfileIndex(source)) == expected


def test_standard_symbol_cache_separates_units_and_parameter_views():
    from odbpp.symbols import parse_standard_symbol
    from odbpp.units import UnitSystem
    metric = parse_standard_symbol("rect1000x500",UnitSystem.MM)
    inch = parse_standard_symbol("rect1000x500",UnitSystem.INCH)
    assert metric.sizes_mm == (1,.5)
    assert inch.sizes_mm == (25.4,12.7)
    parameters = metric.params
    parameters["w"] = 99
    assert parse_standard_symbol("rect1000x500",UnitSystem.MM).params["w"] == 1000
    assert parse_standard_symbol("rect1000x500 M",UnitSystem.INCH).sizes_mm == (1,.5)
    assert parse_standard_symbol("special_0.670x0.950_nt2520") is None


def test_direct_polygon_flattening_matches_segment_model_reference():
    from odbpp.clipper import region_from_geometry, region_from_rings, polygon_points_of
    from odbpp.geometry import geometry_points
    from odbpp.model import SurfaceGeometry, Polygon, ContourPart
    source = SurfaceGeometry(polygons=[Polygon(is_island=True,start=(2,0),parts=[
        ContourPart(kind="arc",end=(0,2),center=(0,0),clockwise=False),
        ContourPart(kind="segment",end=(0,0)),
        ContourPart(kind="segment",end=(2,0)),
        ContourPart(kind="segment",end=(2,0))]),
        Polygon(is_island=False,start=(.2,.2),parts=[
            ContourPart(kind="segment",end=(.4,.2)),
            ContourPart(kind="segment",end=(.4,.4)),
            ContourPart(kind="segment",end=(.2,.2))])])
    expected = region_from_rings([(polygon_points_of(p),p.is_island is False)
                                 for p in geometry_points(source)])
    assert region_from_geometry(source).rings == expected.rings


def test_archive_children_index_matches_member_scan_and_returns_fresh_lists(tmp_path):
    import zipfile
    from odbpp.archive import OdbArchive
    source = tmp_path/"members.zip"
    with zipfile.ZipFile(source,"w") as archive:
        for name in ("root/misc/info","root/steps/PCB/profile",
                     "root/steps/PCB/layers/F.CU/features", "root/.features.sum",
                     "root/empty/", "UPPER/file"):
            archive.writestr(name,b"" if name.endswith("/") else b"data")
    with OdbArchive.open(str(source)) as archive:
        for parent in ("","root","root/misc","root/steps/PCB", "root/empty", "missing", "upper", "UPPER"):
            prefix = parent+"/" if parent else ""
            expected = set()
            for member in archive.files+archive.dirs:
                if member.startswith(prefix):
                    suffix = member[len(prefix):]
                    if suffix:
                        expected.add(suffix.split("/",1)[0])
            children = archive.list_dir(parent)
            assert children == sorted(expected)
            children.clear()
            assert archive.list_dir(parent) == sorted(expected)
