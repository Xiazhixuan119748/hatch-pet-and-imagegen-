# Codex V2 Pet Contract

## Sprite Atlas

- Version: `spriteVersionNumber: 2`.
- Format: PNG or WebP.
- Dimensions: `1536x2288`.
- Grid: 8 columns x 11 rows.
- Cell: `192x208`.
- Background: transparent.
- Rows `0-8`: standard animation states.
- Rows `9-10`: 16 clockwise look directions.
- Unused standard-row cells: fully transparent.

The 8x9 `1536x1872` atlas is an intermediate assembly artifact only. Never package it as a newly hatched pet.

## Look Directions

- Row `9`: `000`, `022.5`, `045`, `067.5`, `090`, `112.5`, `135`, `157.5` degrees.
- Row `10`: `180`, `202.5`, `225`, `247.5`, `270`, `292.5`, `315`, `337.5` degrees.
- `000` means up / 12 o'clock, not neutral/front.
- Neutral/front is the no-vector deadzone and falls back to idle.

## Local Custom Pet Package

Place files under:

```text
${CODEX_HOME:-$HOME/.codex}/pets/<pet-name>/
├── pet.json
└── spritesheet.webp
```

Required manifest shape:

```json
{
  "id": "pet-name",
  "displayName": "Pet Name",
  "description": "One short sentence.",
  "spriteVersionNumber": 2,
  "spritesheetPath": "spritesheet.webp"
}
```

The app derives the 11-row layout and look-direction behavior from `spriteVersionNumber: 2`. Omitting it defaults the pet to v1 and causes the 2288-pixel-tall spritesheet to be rejected.
