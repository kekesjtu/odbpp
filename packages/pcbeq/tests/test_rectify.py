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
    result=rectify_region(source,RectifyOptions(tolerance_mm=1))
    audit(source,result)
    assert len(result.audit["local_refinement"]) > 1
    assert result.audit["topology"]["positive_width_connections"]


def test_slanted_staircase_is_inside_and_uses_vertical_strips():
    source=region(([(0,0),(80,0),(80,80)],False))
    result=rectify_region(source)
    audit(source,result)
    assert result.net_area_error_mm2 < 0
    assert result.relative_error <= .05
    assert result.audit["coarsening"]["boundary_score_after"] <= result.audit["coarsening"]["boundary_score_before"]
    assert any(r.height>70 for r in result.rectangles)


def test_budget_is_never_relaxed_by_perimeter():
    source=region(([(0,0),(80,0),(80,80)],False))
    diagnostics=Diagnostics()
    result=rectify_region(source,RectifyOptions(max_relative_area_error=0),diagnostics=diagnostics)
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


def test_area_budget_allows_large_local_retreat_on_a_small_spike():
    source = region(([(0,0),(100,0),(100,100),(51,100),(50,103),(49,100),(0,100)],False))
    result = rectify_region(source,RectifyOptions(tolerance_mm=3))
    assert result.ok
    audit(source,result)
    assert result.relative_error <= .05
    assert result.region.bounds[3] < 102


def test_tight_area_budget_refines_and_survives_coarsening():
    source = region(([(0,0),(12,0),(12,12)],False))
    result = rectify_region(source,RectifyOptions(max_relative_area_error=.01,tolerance_mm=3))
    assert result.ok
    audit(source,result)
    assert result.relative_error <= .01+1e-8
    assert result.audit["coarsening"]["all_constraints_rechecked"]
    assert any(h["reason"] == "area" for h in result.audit["local_refinement"])


def test_area_refinement_resource_limit_reports_the_interval():
    source = region(([(0,0),(10,0),(10,10)],False))
    diagnostics = Diagnostics()
    result = rectify_region(source,RectifyOptions(max_relative_area_error=.001,
                             tolerance_mm=1,max_refinement_rounds=0),diagnostics=diagnostics)
    assert not result.ok
    assert "X=[" in str(diagnostics.errors[0])
    assert "budget=" in str(diagnostics.errors[0])


def test_initial_step_resource_limit_is_checked_before_allocating_bands():
    source = region(([(0,0),(10,0),(10,10)],False))
    diagnostics = Diagnostics()
    result = rectify_region(source,RectifyOptions(tolerance_mm=1e-12),
                            diagnostics=diagnostics)
    assert not result.ok and not result.rectangles
    assert "initial bands" in str(diagnostics.errors[0])


@pytest.mark.parametrize("kwargs", [
    {"max_relative_area_error":1}, {"max_relative_area_error":-.01},
    {"max_relative_area_error":float("nan")}, {"max_relative_area_error":float("inf")},
])
def test_invalid_accuracy_budgets(kwargs):
    with pytest.raises(ValueError):
        RectifyOptions(**kwargs)


def test_true_common_y_limits_are_not_rounded_to_a_strip_grid():
    from pcbeq.rectify import _envelope
    source = region(([(0,1.2),(2,1.5),(2,4.8),(0,5.7)],False))
    assert _envelope(source,0,2) == [(1.5,4.8)]
    result = rectify_region(source,RectifyOptions(tolerance_mm=2,max_relative_area_error=.2))
    assert result.ok
    assert [r.bounds for r in result.rectangles] == [(0,1.5,2,4.8)]
    assert result.audit["y_quantization"] is False


def test_true_y_limits_keep_hole_spans_and_interior_vertex_extrema():
    from pcbeq.rectify import _envelope
    source = region(([(0,1.23),(2,1.23),(2,5.79),(0,5.79)],False),
                    ([(.7,2.17),(1.3,2.17),(1.3,3.61),(.7,3.61)],True))
    assert _envelope(source,0,2) == [(1.23,2.17),(3.61,5.79)]
    valley = region(([(0,1.2),(.7,2.37),(2,1.2),(2,5.9),(1.1,4.63),(0,5.9)],False))
    assert _envelope(valley,0,2) == [(2.37,4.63)]


def test_true_y_limits_include_exact_slab_endpoints_and_full_precision():
    from pcbeq.rectify import _envelope
    source = region(([(0,0),(1,100),(1,101),(0,1)],False))
    assert _envelope(source,0,.005) == [(.5,1)]
    sloped = region(([(0,1.23),(3,2.23),(3,8.11),(0,8.11)],False))
    assert _envelope(sloped,0,1/7)[0][0] == pytest.approx(1.23+1/21,rel=0,abs=1e-14)


def test_moving_concave_hole_gap_cannot_create_a_phantom_solid_interval():
    from pcbeq.rectify import _envelope
    source = region(([(0,0),(10,0),(10,10),(0,10)],False),
                    ([(8,4),(5,5),(5.5,8),(2,7),(2,2),(6,1)],True))
    shape = profile_shape(source)
    spans = _envelope(source,5.25,5.75)
    assert spans[-1] == (8,10)
    for low,high in spans:
        assert box(5.25,low,5.75,high).difference(shape).area < 1e-12


def test_hole_matching_roundoff_does_not_change_geometry_or_hide_real_cuts():
    import math
    from pcbeq.rectify import _covers_with_roundoff
    source = box(0,0,1,1)
    roundoff = box(0,0,math.nextafter(1,-math.inf),1)
    before = roundoff.wkb
    assert _covers_with_roundoff(roundoff,source)
    assert roundoff.wkb == before
    assert not _covers_with_roundoff(box(0,0,1-1e-8,1),source)


def test_simple_profile_and_derived_layer_domain_keep_submicron_intersections():
    from shapely.geometry import Polygon
    from pcbeq.rectify import profile_region
    outer=[(0,0),(4.00000004,0),(3.00000007,4),(0,4)]
    hole=[(1,1),(2,1),(2,2),(1,2)]
    expected=Polygon(outer,[hole])
    source=profile_region(expected)
    assert profile_shape(source).symmetric_difference(expected).area == 0


def test_coarsening_only_uses_existing_true_y_boundaries(monkeypatch):
    import pcbeq.rectify as module
    source = region(([(0,1.23),(12,2.91),(12,12.37),(0,8.29)],False))
    original = module._coarsen
    limits = {}
    def inspect(envelopes,*args,**kwargs):
        limits["before"] = {y for _,_,spans in envelopes for pair in spans for y in pair}
        chosen,audit = original(envelopes,*args,**kwargs)
        limits["after"] = {y for _,_,spans in chosen for pair in spans for y in pair}
        return chosen,audit
    monkeypatch.setattr(module,"_coarsen",inspect)
    result = rectify_region(source,RectifyOptions(tolerance_mm=1))
    assert result.ok
    audit(source,result)
    assert result.audit["coarsening"]["merged_adjacent_pairs"] > 0
    assert limits["after"].issubset(limits["before"])
    assert "shared_y_pitch_mm" not in result.audit["coarsening"]


def test_partial_span_merge_preserves_unmatched_independent_entity():
    from pcbeq.rectify import _pack, _coarsen
    bands=[(0,1,[(.123,2.456),(4.123,6.456)]),
           (1,2,[(.133,2.456),(3.123,3.456),(4.123,6.456)])]
    original=unary_union([box(*r.bounds) for r in _pack(bands)])
    assert (0,4.123,2,6.456) in [r.bounds for r in _pack(bands)]
    chosen,report=_coarsen(bands,original,original.area*.05)
    rectangles=_pack(chosen)
    assert (0,.133,2,2.456) in [r.bounds for r in rectangles]
    assert (1,3.123,2,3.456) in [r.bounds for r in rectangles]
    assert report['partial_interval_merges']>0
    assert unary_union([box(*r.bounds) for r in rectangles]).difference(original).area<1e-12
