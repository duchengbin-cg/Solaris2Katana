# Solaris2Katana

Utilities for migrating Solaris/USD lighting and lookdev data into Katana.

## Overview

This project contains pipeline utility scripts for moving Solaris LOP data into Katana.

Current scope:

- Export Arnold lights from a Houdini `Python Script LOP`
- Rebuild lights inside Katana `GafferThree`
- Create proper Arnold/KtoA light packages when possible
- Restore transforms, standard light parameters, Arnold custom parameters, and skydome HDR textures

Planned scope:

- Convert Houdini `Material Library LOP` shader networks to Katana `NetworkMaterialCreate`
- Convert Houdini material assignments to Katana `UsdMaterialAssign`
- Extend support for additional renderers and renderer-specific mappings

## Repository Structure

```text
scripts/
  houdini/
    export_lights_from_python_lop.py
  katana/
    import_houdini_lights_to_gafferthree.py
```

## Current Status

The first version focuses on Arnold light migration from Solaris LOP to Katana KtoA. The repository name is intentionally broader so it can be extended later to other renderers and more Solaris data types.

## License

MIT
