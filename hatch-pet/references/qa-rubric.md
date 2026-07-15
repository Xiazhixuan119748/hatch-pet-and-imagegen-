# V2 Pet QA Rubric

Do not package a pet until every section passes.

## Geometry And Package

- Final atlas is exactly `1536x2288`, 8 columns x 11 rows, with `192x208` cells.
- `pet.json` contains `spriteVersionNumber: 2` and points to the packaged spritesheet.
- Used cells are non-empty; unused standard-row cells are transparent.
- Fully transparent pixels have zero RGB residue.
- The 8x9 intermediate atlas is never packaged.
- `qa/review.json` has no errors.
- Standard rows use component extraction unless `stable-slots` was deliberately approved after playback review.
- Coherent look rows recover their ordered pose groups and pass near-edge clipping checks after shared-scale registration into final cells.

## Character And Style

- Silhouette, proportions, face, expression language, material, palette, lighting, markings, and props remain the same across all 11 rows.
- The pet reads clearly inside a `192x208` cell in the chosen style.
- No frame introduces an unintended character, object, logo, text, scene, or effect.

## Standard Animation

- Rows `0-8` contain the exact required frame counts and recognizable state semantics.
- Loops do not pop, reverse cadence, face the wrong direction, or remain effectively static.
- The first idle frame works as a reduced-motion still.
- `waiting`, `running`, `review`, and `failed` remain visually distinct.

## Look Directions

- All 16 directions are present in fixed clockwise order and visibly distinct from neutral/rest.
- Cardinal directions read unmistakably as up, right, down, and left; diagonals and intermediates read in the correct quadrant.
- `qa/look-directions.png` includes full-body and zoomed head/upper-body views.
- `qa/direction-semantics.json` records `pass`, `expected`, `observed`, and `reason` for every direction.
- `qa/look-continuity.json` has no unexplained holes, center jumps, area jumps, or local difference outliers.
- Eyes, eyelids, head, body, appendages, and props follow the pet-specific look mechanics plan.
- No whole-sprite rotation, replacement/googly eyes, visual clipping, seam bands, or transparent interior holes.
- A repaired direction is approved by an independent visual QA worker or explicit user inspection, not the repairing parent alone.

## Repair Policy

Repair the smallest packaging-eligible scope: one standard row or one complete coherent look row. Never mix an individually generated repair cell into a new pet's final look row. Re-run assembly, deterministic validation, direction QA, continuity measurement, and semantic review after every relevant repair.
