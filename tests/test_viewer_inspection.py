"""Inspection retains raw unit scopes separately from positive geometry."""
from odbpp.features import FeatureFile, PadFeature, SymbolRef
from odbpp.model import Polarity
from odbpp.units import UnitScope, UnitSystem
from odbpp.svg import _matrix_panel
from html.parser import HTMLParser


def test_histogram_separates_polarity_and_explicit_symbol_scope():
    metric = SymbolRef(0, 'r10')
    imperial = SymbolRef(1, 'r10', UnitSystem.INCH)
    features = FeatureFile('features', UnitScope(UnitSystem.MM), records=[
        PadFeature(1, symbol=metric), PadFeature(2, symbol=metric),
        PadFeature(3, symbol=metric, polarity=Polarity.NEGATIVE),
        PadFeature(4, symbol=imperial)])
    groups=features.histogram()
    assert sum(g['count'] for g in groups) == 4
    assert {(g['polarity'],g['symbol_units'],g['count']) for g in groups} == {
        ('P','MM',2),('N','MM',1),('P','INCH',1)}


class MatrixElements(HTMLParser):
    def __init__(self, text):
        super().__init__()
        self.spans = {}
        self.rows = []
        self.feed(text)

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if attrs.get('class') == 'drill-span':
            self.spans[attrs['data-name']] = attrs
        if attrs.get('class') == 'matrix-row':
            self.rows.append(attrs['data-name'])


def matrix_layer(row, name, start=None, end=None):
    return dict(row=row, name=name, type='DRILL' if start is not None else 'SIGNAL',
                context='BOARD', polarity='P', form='RIGID',
                start_layer=start, end_layer=end)


def test_matrix_resolves_authored_spans_in_row_order_and_separates_overlaps():
    layers = [matrix_layer(80, 'through', 'top', 'BOTTOM'),
              matrix_layer(20, 'inner1'), matrix_layer(40, 'bottom'),
              matrix_layer(10, 'TOP'), matrix_layer(30, 'inner2'),
              matrix_layer(90, 'buried', 'inner1', 'inner2'),
              matrix_layer(100, 'reverse', 'inner2', 'inner1')]
    panel = MatrixElements(_matrix_panel(layers))
    assert panel.rows == ['TOP', 'inner1', 'inner2', 'bottom', 'through', 'buried', 'reverse']
    spans = panel.spans
    assert (spans['through']['data-start'], spans['through']['data-end']) == ('0', '3')
    assert (spans['buried']['data-start'], spans['buried']['data-end']) == ('1', '2')
    assert (spans['reverse']['data-start'], spans['reverse']['data-end']) == ('2', '1')
    assert len({s['data-lane'] for s in spans.values()}) == 3


def test_matrix_reuses_disjoint_lanes_and_reports_unresolvable_endpoints():
    layers = [matrix_layer(i, name) for i, name in enumerate(('top', 'a', 'b', 'bottom'))]
    layers += [matrix_layer(10, 'blind-top', 'top', 'a'),
               matrix_layer(11, 'blind-bottom', 'b', 'bottom'),
               matrix_layer(12, 'missing', 'absent', 'bottom'),
               matrix_layer(13, 'no-start', '', 'bottom')]
    html = _matrix_panel(layers)
    panel = MatrixElements(html)
    assert set(panel.spans) == {'blind-top', 'blind-bottom'}
    assert len({s['data-lane'] for s in panel.spans.values()}) == 1
    assert '跨度未解析：起止层缺失、不在当前矩阵或名称不唯一' in html
    layers.append(matrix_layer(14, 'TOP'))
    assert 'blind-top' not in MatrixElements(_matrix_panel(layers)).spans


def test_matrix_names_and_span_labels_are_html_escaped():
    name = '<top & "edge">'
    html = _matrix_panel([matrix_layer(1, name), matrix_layer(2, 'bottom'),
                          matrix_layer(3, 'drill', name, 'bottom')])
    panel = MatrixElements(html)
    assert panel.rows[0] == name
    assert panel.spans['drill']['aria-label'] == f'drill：{name} → bottom'
    assert '<top' not in html
