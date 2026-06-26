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
