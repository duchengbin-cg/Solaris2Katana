import json
import math
import os
import tempfile
import hou
from pxr import Usd, UsdGeom, UsdLux

# ============================================================
# Python Script LOP
# Export Arnold/USD light data from the current editable stage
# ============================================================

node = hou.pwd()
stage = node.editableStage()

output_path = hou.expandString("$HIP/houdini_lights_export.json")

exported_lights = []


def safe_float(val):
    try:
        f = float(val)
        if math.isfinite(f):
            return f
        return None
    except Exception:
        return None


def to_python_type(val):
    """Convert USD / Gf / Sdf values into JSON-serializable Python values."""
    if val is None:
        return None

    if isinstance(val, bool):
        return val

    if isinstance(val, int):
        return val

    if isinstance(val, float):
        if math.isfinite(val):
            return val
        return None

    if isinstance(val, str):
        return val

    type_str = str(type(val))
    class_name = getattr(type(val), "__name__", "")

    if "AssetPath" in type_str or "AssetPath" in class_name:
        resolved_path = getattr(val, "resolvedPath", None)
        raw_path = getattr(val, "path", None)
        if resolved_path:
            return str(resolved_path)
        if raw_path is not None:
            return str(raw_path)
        return str(val)

    if hasattr(val, "resolvedPath") or hasattr(val, "path"):
        resolved_path = getattr(val, "resolvedPath", None)
        raw_path = getattr(val, "path", None)
        if resolved_path:
            return str(resolved_path)
        if raw_path is not None:
            return str(raw_path)

    if isinstance(val, dict):
        result = {}
        for k, v in val.items():
            result[str(k)] = to_python_type(v)
        return result

    if hasattr(val, "__iter__") and not isinstance(val, (str, bytes)):
        try:
            return [to_python_type(x) for x in val]
        except Exception:
            pass

    return str(val)


def get_resolved_usd_light_type(prim):
    if prim.IsA(UsdLux.RectLight):
        return "RectLight"
    if prim.IsA(UsdLux.DiskLight):
        return "DiskLight"
    if prim.IsA(UsdLux.DistantLight):
        return "DistantLight"
    if prim.IsA(UsdLux.SphereLight):
        return "SphereLight"
    if prim.IsA(UsdLux.DomeLight):
        return "DomeLight"

    raw_type = str(prim.GetTypeName())
    return raw_type if raw_type else "UnknownLight"


def matrix4_to_nested_list(matrix4):
    result = []
    for r in range(4):
        row = []
        for c in range(4):
            row.append(safe_float(matrix4[r][c]))
        result.append(row)
    return result


def matrix4_to_hou_matrix(matrix4):
    flat = []
    for r in range(4):
        for c in range(4):
            value = safe_float(matrix4[r][c])
            flat.append(0.0 if value is None else value)
    return hou.Matrix4(tuple(flat))


def get_world_transform_data(prim):
    xformable = UsdGeom.Xformable(prim)
    time_code = Usd.TimeCode.Default()

    world_mtx = xformable.ComputeLocalToWorldTransform(time_code)
    hou_mtx = matrix4_to_hou_matrix(world_mtx)
    exploded = hou_mtx.explode()

    return {
        "translate": [safe_float(x) for x in exploded["translate"]],
        "rotate": [safe_float(x) for x in exploded["rotate"]],
        "scale": [safe_float(x) for x in exploded["scale"]],
        "world_matrix": matrix4_to_nested_list(world_mtx),
    }


STANDARD_ATTR_MAP = {
    "inputs:intensity": "intensity",
    "inputs:exposure": "exposure",
    "inputs:color": "color",
    "inputs:normalize": "normalize",
    "inputs:diffuse": "diffuse",
    "inputs:specular": "specular",
    "inputs:width": "width",
    "inputs:height": "height",
    "inputs:radius": "radius",
    "inputs:length": "length",
    "inputs:angle": "angle",
    "inputs:texture:file": "texture_file",
    "inputs:texture:format": "texture_format",
}


def export_standard_attrs(prim, light_info):
    time_code = Usd.TimeCode.Default()

    for usd_attr_name, json_name in STANDARD_ATTR_MAP.items():
        attr = prim.GetAttribute(usd_attr_name)
        if not attr or not attr.IsValid():
            continue

        value = attr.Get(time_code)
        if value is None:
            continue

        light_info[json_name] = to_python_type(value)


def export_arnold_attrs(prim, light_info):
    time_code = Usd.TimeCode.Default()
    arnold_params = {}

    for attr in prim.GetAttributes():
        attr_name = attr.GetName()
        lower_name = attr_name.lower()

        if "arnold" not in lower_name:
            continue

        clean_name = attr_name
        for prefix in (
            "inputs:arnold:",
            "primvars:arnold:",
            "arnold:",
        ):
            if clean_name.startswith(prefix):
                clean_name = clean_name[len(prefix):]
                break

        value = attr.Get(time_code)
        if value is None:
            continue

        arnold_params[clean_name] = to_python_type(value)

    if arnold_params:
        light_info["arnold_params"] = arnold_params


def export_optional_ids(prim, light_info):
    time_code = Usd.TimeCode.Default()

    for attr_name, json_name in (
        ("light:shaderId", "light_shader_id"),
        ("info:id", "shader_id"),
    ):
        attr = prim.GetAttribute(attr_name)
        if attr and attr.IsValid():
            value = attr.Get(time_code)
            if value:
                light_info[json_name] = str(value)


for prim in stage.Traverse():
    if not prim.HasAPI(UsdLux.LightAPI):
        continue

    light_info = {}
    light_info["name"] = prim.GetName()
    light_info["path"] = str(prim.GetPath())
    light_info["usd_type_raw"] = str(prim.GetTypeName())
    light_info["usd_type"] = get_resolved_usd_light_type(prim)

    light_info.update(get_world_transform_data(prim))
    export_standard_attrs(prim, light_info)
    export_arnold_attrs(prim, light_info)
    export_optional_ids(prim, light_info)

    exported_lights.append(light_info)

try:
    json_text = json.dumps(
        exported_lights,
        indent=4,
        ensure_ascii=False,
        allow_nan=False
    )

    output_dir = os.path.dirname(output_path)
    if output_dir and not os.path.exists(output_dir):
        os.makedirs(output_dir)

    fd, temp_path = tempfile.mkstemp(
        prefix="houdini_lights_export_",
        suffix=".json",
        dir=output_dir if output_dir else None
    )
    os.close(fd)

    with open(temp_path, "w", encoding="utf-8") as f:
        f.write(json_text)

    if os.path.exists(output_path):
        os.remove(output_path)
    os.replace(temp_path, output_path)

    print("[成功] 已导出 {} 盏灯到: {}".format(len(exported_lights), output_path))

except Exception as e:
    print("[错误] 导出 JSON 失败: {}".format(e))
