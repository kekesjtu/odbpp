"""Reduce physical boundary coordinates within an area-loss budget and topology constraints."""
from collections import Counter
from .region_topology import _topology_source, _topology
from .region_profile import _intersect_spans

MERGE_BOUNDARY_CLEARANCE_MM = 0.1

def _pack(bands):
    """Merge identical neighboring intervals, keeping all other spans intact."""
    from .rectify import Rect
    rectangles=[]
    active={}
    for a,b,spans in bands:
        next_active={}
        for low,high in spans:
            key=(low,high)
            index=active.get(key)
            if index is not None and rectangles[index].x1==a:
                old=rectangles[index]
                rectangles[index]=Rect(old.x0,low,b,high)
            else:
                index=len(rectangles)
                rectangles.append(Rect(a,low,b,high))
            next_active[key]=index
        active=next_active
    return rectangles


def _coarsen(envelopes, original, allowed, topology_source=None,
             reference_x_boundaries_mm=(), reference_y_boundaries_mm=()):
    """Spend boundary-loss budget to reduce distinct physical boundary coordinates.

    Every candidate is a subset of the already inscribed model. Vector topology
    protects narrow spans and connections before accepting any change.
    """
    from shapely.geometry import box
    from shapely.ops import unary_union

    if topology_source is None:
        topology_source = _topology_source(original)

    def packed(bands):
        rects = _pack(bands)
        return rects, unary_union([box(*r.bounds) for r in rects])

    def score(bands):
        rects = _pack(bands)
        xs = {v for r in rects for v in (r.x0, r.x1)}
        ys = {v for r in rects for v in (r.y0, r.y1)}
        return (len(xs) + len(ys), len(rects))

    def axis_counts(bands):
        xs = Counter(x for a,b,spans in bands if spans for x in (a,b))
        ys = Counter(y for _,_,spans in bands for pair in spans for y in pair)
        xs.update(reference_x_boundaries_mm)
        ys.update(reference_y_boundaries_mm)
        return xs,ys

    def crowded(lines):
        return sum(0 < b-a < MERGE_BOUNDARY_CLEARANCE_MM for a,b in zip(lines,lines[1:]))

    rejection_counts = {"area": 0, "topology": 0, "complexity": 0}

    def acceptable(bands, model=None):
        if score(bands) >= score(chosen):
            rejection_counts["complexity"] += 1
            return False
        if model is None:
            _, model = packed(bands)
        if original.area - model.area > allowed + 1e-8 or model.difference(original).area > 1e-8:
            rejection_counts["area"] += 1
            return False
        if not _topology(original, model, topology_source)["preserved"]:
            rejection_counts["topology"] += 1
            return False
        return True

    before = score(envelopes)
    before_counts = axis_counts(envelopes)
    crowded_before = sum(crowded(sorted(c)) for c in before_counts)
    chosen = envelopes
    merged_pairs = 0
    partial_merges = 0
    rejected_topology = 0
    for _ in range(12):
        # Collapse equal envelopes before considering different Y boundaries.
        bands = []
        for a, b, spans in chosen:
            if bands and bands[-1][2] == spans and bands[-1][1]==a:
                bands[-1] = (bands[-1][0], b, spans)
            else:
                bands.append((a, b, spans))
        _, model = packed(bands)
        remaining = allowed - (original.area-model.area)
        cx,cy = axis_counts(bands)
        xs,ys = sorted(cx),sorted(cy)
        crowded_now = crowded(xs)+crowded(ys)
        proposals = []
        def propose(i,left,right,replacement,cost):
            if cost > remaining+1e-8:
                return
            old_y=Counter(y for band in (left,right) for pair in band[2] for y in pair)
            new_y=Counter(y for band in replacement for pair in band[2] for y in pair)
            removed_y={y for y,n in old_y.items() if cy[y]-n+new_y[y] == 0}
            removed_x={left[1]} if len(replacement)==1 and cx[left[1]]==2 else set()
            after=crowded([x for x in xs if x not in removed_x])+crowded([y for y in ys if y not in removed_y])
            proposals.append((after-crowded_now,-len(removed_x)-len(removed_y),cost,i,replacement))

        for i, (left, right) in enumerate(zip(bands, bands[1:])):
            a, b, ls = left
            c, d, rs = right
            spans = _intersect_spans(ls, rs)
            if b!=c or not ls or not rs:
                continue
            if len(ls)==len(rs)==len(spans):
                cost=((b-a)*sum(h-l for l,h in ls)+(d-c)*sum(h-l for l,h in rs)
                      -(d-a)*sum(h-l for l,h in spans))
                propose(i,left,right,[(a,d,spans)],cost)
            # A hole can change the number of spans. Coarsen only mutually
            # matched interval pairs and preserve every unmatched interval.
            matches=[(j,k) for j,(lo,hi) in enumerate(ls) for k,(rl,rh) in enumerate(rs)
                     if min(hi,rh)>max(lo,rl)+1e-8]
            for j,k in matches:
                if len(ls)==len(rs)==1 or sum(q==j for q,_ in matches)!=1 or sum(q==k for _,q in matches)!=1:
                    continue
                common=(max(ls[j][0],rs[k][0]),min(ls[j][1],rs[k][1]))
                if ls[j]==rs[k]:
                    continue
                lsp=list(ls);rsp=list(rs);lsp[j]=rsp[k]=common
                cost=(b-a)*(ls[j][1]-ls[j][0])+(d-c)*(rs[k][1]-rs[k][0])-(d-a)*(common[1]-common[0])
                propose(i,left,right,[(a,b,lsp),(c,d,rsp)],cost)
        selected = {}
        alternatives = {}
        occupied = set()
        for _,_,cost,i,replacement in sorted(proposals):
            alternatives.setdefault(i,[]).append(replacement)
            if i in occupied or i+1 in occupied or cost > remaining + 1e-8:
                continue
            selected[i] = replacement
            occupied.update((i, i+1))
            remaining -= cost
        if not selected:
            chosen = bands
            break
        candidate = [item for i,band in enumerate(bands) if i-1 not in selected
                     for item in selected.get(i,[band])]
        if acceptable(candidate):
            chosen = candidate
            merged_pairs += len(selected)
            partial_merges += sum(len(r)==2 for r in selected.values())
        else:
            # A batch can cut a protected branch. Recheck individual changes;
            # reject them rather than spending area budget on topology loss.
            chosen = bands
            _, chosen_model = packed(chosen)
            accepted = 0
            for i in sorted(selected,reverse=True):
                old_pair=unary_union([box(a,l,b,h) for a,b,spans in chosen[i:i+2] for l,h in spans])
                for replacement in alternatives[i]:
                    candidate=chosen[:i]+replacement+chosen[i+2:]
                    patch=unary_union([box(a,l,b,h) for a,b,spans in replacement for l,h in spans])
                    candidate_model=chosen_model.difference(old_pair.difference(patch))
                    if acceptable(candidate,candidate_model):
                        chosen=candidate;chosen_model=candidate_model;accepted+=1
                        partial_merges+=len(replacement)==2
                        break
                else:
                    rejected_topology+=1
            merged_pairs += accepted
            if not accepted:
                break
    return chosen, {"objective": "simplify adjacent physical boundaries, then reduce region count",
                    "strategy": "adjacent_true_interval_intersection",
                    "boundary_clearance_preference_mm":MERGE_BOUNDARY_CLEARANCE_MM,
                    "crowded_boundary_intervals_before":crowded_before,
                    "crowded_boundary_intervals_after":sum(crowded(sorted(c)) for c in axis_counts(chosen)),
                    "reference_boundary_counts":[len(reference_x_boundaries_mm),len(reference_y_boundaries_mm)],
                    "boundary_score_before": list(before),
                    "boundary_score_after": list(score(chosen)),
                    "merged_adjacent_pairs": merged_pairs,
                    "partial_interval_merges":partial_merges,
                    "topology_rejected_merges": rejected_topology,
                    "constraint_rejections": rejection_counts,
                    "all_constraints_rechecked": True}
