"""Reusable material edges and independent physical raster windows."""
from bisect import bisect_right
from .raster import LayerMask, region_edges


class Scanline:
    def __init__(self, edges):
        self._edges = sorted(((min(y1,y2), max(y1,y2), x1,y1,x2-x1,y2-y1)
                              for x1,y1,x2,y2 in edges if y1 != y2), key=lambda e:e[0])
        self._lows = [e[0] for e in self._edges]
        self._cursor = 0
        self._active = []

    def window(self, low, high):
        scan = object.__new__(Scanline)
        stop = bisect_right(self._lows, high)
        scan._edges = [e for e in self._edges[:stop] if e[1] > low]
        scan._cursor = 0
        scan._active = []
        return scan

    def spans_at(self, y):
        while self._cursor < len(self._edges) and self._edges[self._cursor][0] <= y:
            self._active.append(self._edges[self._cursor])
            self._cursor += 1
        self._active = [edge for edge in self._active if edge[1] > y]
        crossings = sorted(x1+dx*((y-y1)/dy) for _,_,x1,y1,dx,dy in self._active)
        return [(crossings[k],crossings[k+1]) for k in range(0,len(crossings)-1,2)
                if crossings[k+1] > crossings[k]]


def merge_spans(spans):
    out = []
    for a,b in sorted(spans):
        if out and a <= out[-1][1]:
            out[-1] = (out[-1][0],max(b,out[-1][1]))
        else:
            out.append((a,b))
    return out


class PreparedRaster:
    def __init__(self, domain, black, step_domain=None):
        self.domain = Scanline(region_edges(domain))
        self.step = Scanline(region_edges(step_domain)) if step_domain is not None else None
        self.black = [RingIndex(r) for r in black if r]

    def render(self, x_axis, y_axis):
        from .rectify import _intersect_spans
        low, high = y_axis.centre(0), y_axis.centre(y_axis.count-1)
        domain = self.domain.window(low,high)
        step = self.step.window(low,high) if self.step is not None else None
        left, right = x_axis.centre(0), x_axis.centre(x_axis.count-1)
        black = [index.scan(left-x_axis.step*1e-9,low-y_axis.step*1e-9,
                            right+x_axis.step*1e-9,high+y_axis.step*1e-9) for index in self.black]
        mask = LayerMask(x_axis.count,y_axis.count)
        for b in range(y_axis.count):
            y = y_axis.centre(b)
            dom = domain.spans_at(y)
            if step is not None:
                dom = _intersect_spans(dom,step.spans_at(y))
            ink = merge_spans([span for scan in black for span in scan.spans_at(y)])
            mask.set_spans(b,dom,_intersect_spans(dom,ink),x_axis)
        return mask


class RingIndex:
    """Filter whole rings, preserving enclosing contours and island/hole parity."""
    def __init__(self, region):
        self.rings = []
        for ring in region.rings:
            xs,ys = zip(*ring.points)
            edges = [(a,b,c,d) for (a,b),(c,d) in
                     zip(ring.points,ring.points[1:]+ring.points[:1]) if b!=d]
            self.rings.append(((min(xs),min(ys),max(xs),max(ys)),edges))
        self.rings.sort(key=lambda r:r[0][0])
        self.lows = [r[0][0] for r in self.rings]

    def scan(self, left, low, right, high):
        stop = bisect_right(self.lows,right)
        edges = [edge for bounds,ring in self.rings[:stop]
                 if bounds[2]>=left and bounds[1]<=high and bounds[3]>=low
                 for edge in ring if min(edge[1],edge[3])<=high and max(edge[1],edge[3])>low]
        return Scanline(edges)
