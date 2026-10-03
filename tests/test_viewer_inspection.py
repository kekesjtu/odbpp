"""Inspection retains raw unit scopes separately from positive geometry."""
from odbpp.features import FeatureFile, PadFeature, SymbolRef
from odbpp.model import Polarity
from odbpp.units import UnitScope, UnitSystem


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
