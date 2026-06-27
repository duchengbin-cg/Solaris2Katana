# Solaris2Katana

Utilities for moving Houdini Solaris/USD lighting and lookdev data into Katana.

## Overview

`Solaris2Katana` is a production-oriented utility repository for transferring data authored in Houdini Solaris LOP into Katana with as little manual rebuilding as possible.

The repository currently focuses on three practical workflows:

- `Material Library LOP -> JSON -> Katana NetworkMaterialCreate`
- `UsdLux / Arnold lights -> JSON -> Katana GafferThree`
- `Assign Material LOP -> JSON -> Katana UsdMaterialAssign`

The current implementation targets Arnold/HtoA on the Houdini side and KtoA on the Katana side, while keeping the repository broad enough to later support more renderer-specific mappings.

## Current Scope

Implemented:

- Arnold material network export from Solaris `Material Library LOP`
- Arnold material import into a single Katana `NetworkMaterialCreate`
- Arnold/USD light export from Solaris
- Arnold/KtoA light import into Katana `GafferThree`
- Shared light filter reconstruction and referencing in Katana
- USD material binding export from authored layer opinions
- Katana `UsdMaterialAssign` generation grouped by material path

Partially implemented / still evolving:

- complex light filter auxiliary networks beyond the currently tested Arnold cases
- native USD assignment edge cases such as collection bindings
- renderer coverage beyond Arnold/KtoA

## Repository Structure

```text
scripts/
  houdini/
    export_arnold_materials_from_materiallibrary_lop.py
    export_lights_from_python_lop.py
    export_material_assignments_from_lop.py
  katana/
    import_houdini_materials_to_networkmaterialcreate.py
    import_houdini_lights_to_gafferthree.py
    import_houdini_material_assignments_to_usdmaterialassign.py
```

## Script Guide

### Houdini

#### `scripts/houdini/export_arnold_materials_from_materiallibrary_lop.py`

Use inside a Houdini `Python Script LOP`.

Purpose:

- scans Arnold material networks authored in Solaris
- exports shader nodes, inputs, connections, and terminals to JSON
- preserves compound values such as `color3f` / `vector3f` as numeric arrays

Default output:

- `$HIP/houdini_arnold_materials_export.json`

Best used when:

- materials are authored through `Material Library LOP`
- Arnold shader networks need to be rebuilt in Katana `NetworkMaterialCreate`

#### `scripts/houdini/export_lights_from_python_lop.py`

Use inside a Houdini `Python Script LOP`.

Purpose:

- scans the editable USD stage for light prims
- exports transforms, standard light attributes, Arnold custom parameters, and texture paths
- exports color temperature state
- exports light filter paths and, when available, light filter shader networks

Default output:

- `$HIP/houdini_lights_export.json`

Best used when:

- Solaris lights need to be rebuilt in Katana `GafferThree`
- Arnold light filters authored in Solaris must be reconstructed as reusable Katana light filters

#### `scripts/houdini/export_material_assignments_from_lop.py`

Use inside a Houdini `Python Script LOP`.

Purpose:

- exports authored USD `material:binding*` opinions from the layer stack
- avoids flattening the fully composed stage
- keeps class/material binding structure compact for later Katana editing

Default output:

- `$HIP/houdini_material_assignments_export.json`

Important behavior:

- reads authored layer specs instead of traversing the composed stage
- this is intentional so layout, instancing, and duplication do not explode into per-instance assignments

### Katana

#### `scripts/katana/import_houdini_materials_to_networkmaterialcreate.py`

Run inside Katana Python.

Purpose:

- reads the material JSON exported from Houdini
- creates one `NetworkMaterialCreate`
- creates one internal `NetworkMaterial` per Solaris material
- rebuilds Arnold shading nodes and internal connections
- restores Arnold terminals such as:
  - `arnoldSurface`
  - `arnoldDisplacement`
  - `arnoldVolume`

Notable compatibility work already included:

- enum/index remapping for KtoA popups
- `ramp_float` dynamic structure handling
- `curvature.output` mapping
- numeric array restoration for color/vector parameters

Default node name:

- `Houdini_Imported_Materials`

#### `scripts/katana/import_houdini_lights_to_gafferthree.py`

Run inside Katana Python.

Purpose:

- reads the light JSON exported from Houdini
- creates a new `GafferThree`
- creates renderer-specific Arnold light packages when available
- restores transforms and light parameters
- rebuilds shared light filters and references them back to lights

Current light mapping:

- `RectLight -> quad_light`
- `DiskLight -> disk_light`
- `DistantLight -> distant_light`
- `SphereLight -> point_light`
- `DomeLight -> skydome_light`

Notable compatibility work already included:

- Kelvin-to-RGB fallback when KtoA does not expose a native color temperature toggle
- skydome texture restoration through the extra Arnold surface shader path
- shared light filter reconstruction under `_sharedLightFilters`
- light filter sharing back onto lights using GafferThree light filter reference mechanics

Default node name:

- `Houdini_Imported_Lights`

#### `scripts/katana/import_houdini_material_assignments_to_usdmaterialassign.py`

Run inside Katana Python.

Purpose:

- reads authored USD material binding JSON from Houdini
- groups assignments by material path
- creates Katana `UsdMaterialAssign` nodes
- writes grouped `primPaths` and `materialAssign`
- organizes all created assignment nodes into a `GroupStack`

Important behavior:

- one material can drive many `primPaths`
- this is designed to preserve class-based look assignment workflows instead of expanding to a per-instance shot result

Default GroupStack name:

- `Houdini_UsdMaterialAssigns`

## End-to-End Workflows

### 1. Arnold Materials

Houdini:

1. Create or select a `Python Script LOP`
2. Paste `scripts/houdini/export_arnold_materials_from_materiallibrary_lop.py`
3. Cook the node
4. Confirm `houdini_arnold_materials_export.json` is written

Katana:

1. Open `scripts/katana/import_houdini_materials_to_networkmaterialcreate.py`
2. Set `JSON_PATH`
3. Run the script in Katana Python
4. Confirm a new `NetworkMaterialCreate` was created

### 2. Arnold Lights And Filters

Houdini:

1. Create or select a `Python Script LOP`
2. Paste `scripts/houdini/export_lights_from_python_lop.py`
3. Cook the node
4. Confirm `houdini_lights_export.json` is written

Katana:

1. Open `scripts/katana/import_houdini_lights_to_gafferthree.py`
2. Set `JSON_PATH`
3. Run the script in Katana Python
4. Confirm a new `GafferThree` was created
5. Check `_sharedLightFilters` for imported reusable light filters

### 3. USD Material Assignments

Houdini:

1. Place a `Python Script LOP` after the relevant `Assign Material LOP` authoring
2. Paste `scripts/houdini/export_material_assignments_from_lop.py`
3. Cook the node
4. Confirm `houdini_material_assignments_export.json` is written

Katana:

1. Open `scripts/katana/import_houdini_material_assignments_to_usdmaterialassign.py`
2. Set `JSON_PATH`
3. Optionally select one upstream USD node before running
4. Run the script in Katana Python
5. Confirm a `GroupStack` of `UsdMaterialAssign` nodes was created

## Important Notes

### Why The Assignment Export Uses Authored Layer Specs

The material assignment export intentionally does **not** traverse the fully composed USD scene and collect final per-object bindings.

Instead, it exports authored `material:binding*` opinions from the layer stack so that:

- class-based assignments stay compact
- layout and instancing do not blow up into a large flattened result
- the imported Katana assignment graph remains editable at the same abstraction level as the original Solaris authoring

### Why Light Filters Are Shared

The light importer rebuilds light filters as reusable shared filters instead of baking filter parameters directly under each light package. This matches production reuse better and keeps the Katana setup closer to how the Solaris filter networks are authored.

## Known Limitations

- Houdini and Katana do not always expose identical size semantics for Arnold area lights
- quad light width/height may still require show-specific multiplier calibration
- textured area lights are not yet covered as fully as skydome lights
- some complex Arnold light filter auxiliary networks may still need more renderer-specific handling
- `UsdMaterialAssign` import currently targets direct binding workflows; collection bindings are skipped
- the material assignment transfer assumes Solaris and Katana use matching USD prim and material paths
- material importer compatibility currently focuses on Arnold/HtoA to KtoA rather than generic USD shading translation

## Current Validation Status

Tested in the repository workflow so far:

- material colors and vector values restore correctly in Katana
- most KtoA enum popup issues have been remapped for current tested nodes
- `ramp_float` compatibility has been improved for the tested Arnold cases
- `curvature.output` mapping has been corrected for the tested Arnold setup
- light color temperature fallback works for tested light setups where KtoA lacks a direct temperature control
- shared light filter reconstruction and reuse work for the tested Solaris/Katana Arnold setup
- authored class-based material assignment export avoids per-instance scene flattening

## Roadmap

- improve support for more Arnold light filter graph variants
- expand native USD assignment support beyond direct bindings
- add more renderer-specific package mappings
- reduce remaining manual calibration for some area light sizing cases
- expand beyond Arnold/KtoA where practical

## License

MIT
