# Synthetic test fixtures

These hand-authored ODB++ v8.1 jobs reproduce specific parser scenarios,
including negative polarity, nested user-defined symbols, resize, and invalid
layer profiles. The automated suite on `main` does not reference these jobs.
See the [case inventory](../../docs/research/case-inventory.md#手写夹具与自动测试)
for the reference samples used by automated tests.

Everything here is written by us, is tiny, and is deliberately *legal* ODB++
except where a fixture is documenting a failure mode.

## Byte stability

`.gitattributes` disables line-ending normalization for the ODB++ fixture files.

## The fixtures

### `fx_negative_layer/` — polarity and matrix authority

MM, one step `BOARD`, six matrix layers plus one unregistered directory.

| Teaches | Where |
|---|---|
| Layer-level `POLARITY=NEGATIVE` | matrix `GND` layer |
| Record-level `N` inside a positive layer | `f.cu` pad |
| Matrix is authoritative, orphans are ignored | `layers/tenting_front/` (present on disk, absent from the matrix) |
| Case-insensitive entity lookup | matrix `BOARD`/`F.CU` vs directories `board`/`f.cu` |
| Layer profile takes precedence over the step profile | `layers/dielectric_1/profile` (8 × 8 mm) and `steps/board/profile` (20 × 10 mm) |
| Step profile is the fallback | every layer without its own profile |
| Drill sizes use the size semantic, plating comes from the tool | `drill_1_2/tools` (300 µm PLATED, 1000 µm NON_PLATED) |

### `fx_bad_layer_profile/` — invalid layer profile

MM, one step `BOARD`, with two matrix layers (`F.CU` and `DIELECTRIC_1`) and a
20 × 20 mm step profile. `layers/dielectric_1/profile` declares a surface with
no polygons. The parser reports the dielectric layer as failed and the geometry
lookup raises an error.

```
step.base_geometry(layer)  ->  LayerProfileError
job.diagnostics            ->  layer_profile.invalid (error)
```

### `fx_nested_symbol/` — user-defined symbols and resize

MM, one step. `symbols/` holds a three-deep chain
`outer` -> `middle` -> `inner`, where only the innermost defines real geometry.

| Teaches | Where |
|---|---|
| Recursive expansion, dependencies first | `expand_order("outer") == [inner, middle, outer]` |
| Custom symbols appear in `$` lines like standard ones | `f.cu` `$0 outer` |
| Resize form `-1 <sym> <factor>` | `P 12 5 -1 0 40 ...` |
| A custom symbol may nest a *standard* symbol | `middle` uses `r100` |
| Quoted attribute values | `f.cu` `;0=0` against `@0 .net_name` |

### `fx_symbol_cycle/` — cycle detection

`symbols/alpha` references `beta` and `beta` references `alpha`. Spec p.37
forbids this; a parser must detect it instead of recursing until the stack
overflows. Expected result: a `symbol.cycle` **error** diagnostic (so the CLI
exits non-zero), and `SymbolCycleError` from an explicit `expand_order`.

### `fx_flex_form/` — the three ways `FORM` gets its value

MM, one step, six matrix layers covering every branch of the spec p.66 rule:

```
FORM  ... (optional) (default = RIGID)
Layers of the subtype SIGNAL_FLEX, PG_FLEX and MIXED_FLEX have a
default FORM = FLEX.
Layers of type DIELECTRIC are to be defined as RIGID or FLEX.
```

| Layer | `TYPE` | `ADD_TYPE` | `FORM` | resolved `form` | `form_source` |
|---|---|---|---|---|---|
| `SILK_1` | `SILK_SCREEN` | — | absent | `RIGID` | `DEFAULT` |
| `RIGID_1` | `SIGNAL` | — | `RIGID` | `RIGID` | `DECLARED` |
| `POLYIMIDE_1` | `DIELECTRIC` | — | absent | `RIGID` | `DEFAULT` |
| `FLEX_1` | `SIGNAL` | — | `FLEX` | `FLEX` | `DECLARED` |
| `GND_FLEX` | `POWER_GROUND` | `PG_FLEX` | absent | `FLEX` | `FLEX_SUBTYPE` |
| `SIG_CONFLICT` | `SIGNAL` | `SIGNAL_FLEX` | `RIGID` | `RIGID` | `DECLARED` |

Expected diagnostics: `matrix.form_missing_dielectric` for `POLYIMIDE_1` (spec
p.66 makes the field expected on dielectric layers) and `matrix.form_conflict`
for `SIG_CONFLICT` (a declared `RIGID` against a subtype whose default is
`FLEX`).

`POLYIMIDE_1` also carries `.layer_dielectric`, `.dielectric_constant` and
`.layer_hdi_type`.

## Adding a fixture

1. Keep it minimal — a fixture that needs a paragraph to explain itself is
   usually two fixtures.
2. Give it a `misc/info` with an explicit `UNITS`; unit ambiguity is not what
   most fixtures are testing.
3. Mention it in the table above and in the module docstring of whichever test
   uses it.
4. Do not add a fixture to `cases/` for this purpose; `cases/` is reserved for
   real boards (see `cases/README.md`).
