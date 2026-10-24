"""Inscribed geometry: no growth, explicit budgets and protected topology."""
import pytest
from shapely.geometry import box
from shapely.ops import unary_union
from pcbeq.geometry import region_from_rings
from pcbeq.rectify import RectifyOptions, rectify_region, profile_shape
from pcbeq.diagnostics import Diagnostics


def region(*rings):
    return region_from_rings(rings)


def audit(source, result):
    original=profile_shape(source)
    model=unary_union([box(*r.bounds) for r in result.rectangles])
    assert model.difference(original).area < 1e-8
    assert sum(r.area for r in result.rectangles) == pytest.approx(model.area)
    assert result.audit["topology"]["preserved"]
    assert result.lost_area_mm2 <= result.audit["area_budget_mm2"]+1e-8


def test_rectangle_and_l_shape_are_exact():
    for points,area in [([(0,0),(10,0),(10,10),(0,10)],100),
                        ([(0,0),(10,0),(10,5),(4,5),(4,10),(0,10)],70)]:
        source=region((points,False))
        result=rectify_region(source)
        audit(source,result)
        assert result.rectified_area_mm2 == pytest.approx(area)


def test_small_hole_and_independent_island_are_preserved():
    source=region(([(0,0),(10,0),(10,10),(0,10)],False),
                  ([(4.9,4),(5.1,4),(5.1,6),(4.9,6)],True),
                  ([(12,4),(12.2,4),(12.2,6),(12,6)],False))
    result=rectify_region(source,RectifyOptions(tolerance_mm=1))
    audit(source,result)
    assert result.audit["topology"]["original_components"] == 2
    assert result.audit["topology"]["model_holes"] == 1


def test_narrow_neck_is_exact_and_connected():
    points=[(0,0),(4,0),(4,1.9),(8,1.9),(8,0),(12,0),(12,4),
            (8,4),(8,2.1),(4,2.1),(4,4),(0,4)]
    source=region((points,False))
    result=rectify_region(source)
    audit(source,result)
    assert result.lost_area_mm2 == 0


def test_bending_flex_connection_refines_locally():
    from shapely.geometry import LineString
    shape=unary_union([box(0,0,5,5),box(10,4,15,9),
                       LineString([(3,2),(7,2),(8,6),(11,6)]).buffer(.3)])
    source=region((list(shape.exterior.coords)[:-1],False))
    result=rectify_region(source)
    audit(source,result)
    assert len(result.audit["local_refinement"]) > 1
    assert result.audit["topology"]["positive_width_connections"]


def test_slanted_staircase_is_inside_and_uses_vertical_strips():
    source=region(([(0,0),(80,0),(80,80)],False))
    result=rectify_region(source)
    audit(source,result)
    assert result.net_area_error_mm2 < 0
    assert len(result.rectangles) < 40
    assert result.relative_error <= .05
    assert result.audit["coarsening"]["axis_score_after"] <= result.audit["coarsening"]["axis_score_before"]
    assert any(r.height>70 for r in result.rectangles)


def test_budget_is_never_relaxed_by_perimeter():
    source=region(([(0,0),(80,0),(80,80)],False))
    diagnostics=Diagnostics()
    result=rectify_region(source,RectifyOptions(max_relative_area_error=0,
                         max_absolute_area_error_mm2=0),diagnostics=diagnostics)
    assert not result.ok
    assert "rectify.area_error" in diagnostics.codes()


def test_boundary_ripples_share_y_envelopes_without_cutting_hole_or_neck():
    points=[(0,0),(20,0),(20,10)]
    points += [(x,10 + (.02 if x % 2 else 0)) for x in range(19,-1,-1)]
    source=region((points,False), ([(8,4),(8.2,4),(8.2,6),(8,6)],True))
    result=rectify_region(source)
    audit(source,result)
    assert result.relative_error <= .05
    assert len(result.rectangles) < 10
    assert result.audit["topology"]["model_holes"] == 1
