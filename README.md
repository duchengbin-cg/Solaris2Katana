# Solaris2Katana

Utilities for migrating Solaris/USD lighting and lookdev data into Katana.

## Overview

`Solaris2Katana` is a pipeline utility repository focused on moving data authored in Houdini Solaris LOP into Katana in a practical, production-oriented way.

The first implemented stage is Arnold light migration:

- Export Arnold/USD light data from Houdini `Python Script LOP`
- Rebuild lights inside Katana `GafferThree`
- Create Arnold/KtoA light packages when available
- Restore transforms, standard light parameters, Arnold custom parameters, and skydome HDR textures

The repository name is intentionally broader than Arnold so it can later grow into:

- Solaris material conversion
- Solaris material assignment conversion
- Support for other renderers and renderer-specific package mappings

## Current Scope

Implemented:

- Houdini Solaris light export to JSON
- Katana light import into `GafferThree`
- Arnold package creation for:
  - quad
  - disk
  - distant
  - point
  - skydome

Planned:

- `Material Library LOP -> Katana NetworkMaterialCreate`
- `Assign Material -> Katana UsdMaterialAssign`
- Textured area light extensions
- Better geometric calibration for quad light size matching

## Repository Structure

```text
scripts/
  houdini/
    export_lights_from_python_lop.py
  katana/
    import_houdini_lights_to_gafferthree.py
```

## How It Works

The current workflow is intentionally simple:

1. A Houdini `Python Script LOP` walks the editable USD stage
2. It finds light prims using `UsdLux.LightAPI`
3. It extracts:
   - light type
   - name and path
   - world transform
   - standard USD light attributes
   - Arnold-specific custom parameters
4. It writes everything into a JSON file
5. Katana reads that JSON file
6. Katana creates a new `GafferThree`
7. For each exported light, Katana creates the closest Arnold/KtoA package
8. Katana restores transform and shader parameters onto the new light package

## Supported Light Mapping

Current default light type mapping:

- `RectLight -> quad_light`
- `DiskLight -> disk_light`
- `DistantLight -> distant_light`
- `SphereLight -> point_light`
- `DomeLight -> skydome_light`

## Houdini Export Script

File:

- `scripts/houdini/export_lights_from_python_lop.py`

### Where To Use It

Use this script inside a Houdini `Python Script LOP`.

It is not written for the Houdini Python Shell.

### What It Reads

The script reads the current editable stage from:

- `hou.pwd()`
- `node.editableStage()`

### What It Exports

Each light record currently contains:

- `name`
- `path`
- `usd_type`
- `usd_type_raw`
- `translate`
- `rotate`
- `scale`
- `world_matrix`
- standard USD light parameters such as:
  - `intensity`
  - `exposure`
  - `color`
  - `width`
  - `height`
  - `radius`
  - `length`
  - `texture_file`
  - `texture_format`
- `arnold_params`
- optional IDs used for Katana-side shader/light-type inference:
  - `shader_id`
  - `light_shader_id`

### Output File

By default the export path is:

- `$HIP/houdini_lights_export.json`

The script writes JSON via a temporary file and then atomically replaces the target file, which helps avoid half-written broken JSON files.

## Katana Import Script

File:

- `scripts/katana/import_houdini_lights_to_gafferthree.py`

### Where To Use It

Run the script inside Katana Python.

Before running, set:

- `JSON_PATH`

to the JSON exported from Houdini.

### What It Creates

The script creates:

- a new `GafferThree` node
- one Arnold/KtoA light package per exported light

It tries renderer-specific Arnold package classes first and only falls back to `LightPackage` if necessary.

### Arnold Package Handling

The importer currently tries package classes such as:

- `ArnoldQuadLightPackage`
- `ArnoldDiskLightPackage`
- `ArnoldDistantLightPackage`
- `ArnoldPointLightPackage`
- `ArnoldHDRISkydomeLightPackage`

### Transform Restoration

The importer restores:

- `translate`
- `rotate`
- `scale`

Quad lights need special handling in Katana:

- `ArnoldQuadLightPackage` can ship with scale expressions enabled
- the importer disables those expressions before writing explicit size values

Current quad size rule:

- `scale.x -> width`
- `scale.y -> height`
- `scale.z -> 1.0`

Two tuning variables are intentionally exposed for later pipeline calibration:

- `QUAD_WIDTH_MULTIPLIER`
- `QUAD_HEIGHT_MULTIPLIER`

Both are currently set to `1.0`.

### Shader Restoration

The importer restores:

- standard Arnold light parameters where Katana/KtoA exposes a clean equivalent
- Arnold custom parameters exported from Solaris
- HDR texture file on skydome lights through the extra Arnold surface shader path

Skydome behavior:

- creates `skydome_light`
- creates/configures the extra surface shader
- restores HDR path through:
  - `shaders.arnoldSurfaceParams.filename`

## Current Parameter Rules

These parameters are currently ignored intentionally:

- `angle`
- `soft_edge`
- `texture_format`

For `SphereLight`:

- current target in Katana is `point_light`
- `spread` is intentionally ignored

For `RectLight`:

- `width` and `height` are not written as direct light shader params
- they are baked into the quad package transform scale instead

## Basic Usage

### Houdini Side

1. Create or select a `Python Script LOP`
2. Paste in `scripts/houdini/export_lights_from_python_lop.py`
3. Cook the node
4. Confirm the JSON file is written successfully

### Katana Side

1. Open Katana
2. Open the Python tab or Script Manager
3. Set `JSON_PATH` in `scripts/katana/import_houdini_lights_to_gafferthree.py`
4. Run the script
5. Check the new `GafferThree` node created in the node graph

## Known Limitations

- Houdini and Katana do not always expose identical size semantics for Arnold area lights
- Quad light physical size may still require multiplier calibration depending on your show scale and package defaults
- Textured area lights are not yet handled as fully as skydome lights
- Material networks and material assignments are not yet part of the public scripts

## Roadmap

- Add rectangle light proxy geometry / corner export for better size matching
- Add `Material Library LOP -> NetworkMaterialCreate`
- Add `Assign Material -> UsdMaterialAssign`
- Add more renderer-specific package mappings
- Expand beyond Arnold/KtoA

## License

MIT
