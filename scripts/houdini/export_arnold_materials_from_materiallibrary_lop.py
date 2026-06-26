import json
import math
import os
import tempfile
import hou
from pxr import Usd, UsdShade

# ============================================================
# Python Script LOP
# Export Arnold material networks authored by Material Library LOP
# ============================================================

node = hou.pwd()
stage = node.editableStage()
time_code = Usd.TimeCode.Default()

output_path = hou.expandString("$HIP/houdini_arnold_materials_export.json")


def to_python_type(value):
    """Convert USD/Gf/Sdf values into JSON-safe Python values."""
    if value is None:
        return None

    if isinstance(value, bool):
        return value

    if isinstance(value, int):
        return value

    if isinstance(value, float):
        return value if math.isfinite(value) else None

    if isinstance(value, str):
        return value

    type_str = str(type(value))
    class_name = getattr(type(value), "__name__", "")

    # Sdf.AssetPath and similar path-like USD values
    if "AssetPath" in type_str or "AssetPath" in class_name:
        resolved_path = getattr(value, "resolvedPath", None)
        raw_path = getattr(value, "path", None)
        if resolved_path:
            return str(resolved_path)
        if raw_path is not None:
            return str(raw_path)
        return str(value)

    if hasattr(value, "resolvedPath") or hasattr(value, "path"):
        resolved_path = getattr(value, "resolvedPath", None)
        raw_path = getattr(value, "path", None)
        if resolved_path:
            return str(resolved_path)
        if raw_path is not None:
            return str(raw_path)

    if isinstance(value, dict):
        result = {}
        for key, item in value.items():
            result[str(key)] = to_python_type(item)
        return result

    if hasattr(value, "__iter__") and not isinstance(value, (str, bytes)):
        try:
            return [to_python_type(item) for item in value]
        except Exception:
            pass

    return str(value)


def get_shader_identifier(shader):
    shader_id_attr = shader.GetIdAttr()
    if shader_id_attr:
        shader_id = shader_id_attr.Get(time_code)
        if shader_id:
            return str(shader_id)
    return ""


def is_arnold_material(material):
    """
    Keep the first public version focused on Arnold-authored shader networks.
    We mark a material as Arnold if any descendant shader id or terminal output
    looks renderer-specific to Arnold.
    """
    prim = material.GetPrim()

    for child in Usd.PrimRange(prim):
        if child == prim:
            continue
        if child.IsA(UsdShade.Shader):
            shader = UsdShade.Shader(child)
            shader_id = get_shader_identifier(shader).lower()
            if "arnold" in shader_id:
                return True

    for output in material.GetOutputs():
        attr_name = output.GetAttr().GetName().lower()
        if "arnold" in attr_name:
            return True

    return False


def extract_connection_data(port):
    """
    Support multiple UsdShade Python API variants.
    Returns a JSON-safe dictionary describing the upstream source if connected.
    """
    try:
        if not port.HasConnectedSource():
            return None
    except Exception:
        return None

    # Newer API variants
    try:
        connected_sources = port.GetConnectedSources()
        if connected_sources:
            source_api, source_name, source_type = connected_sources[0]
            return {
                "source_path": str(source_api.GetPrim().GetPath()),
                "source_name": str(source_name),
                "source_type": str(source_type),
            }
    except Exception:
        pass

    # Older API variants
    try:
        source_api, source_name, source_type = port.GetConnectedSource()
        if source_api:
            return {
                "source_path": str(source_api.GetPrim().GetPath()),
                "source_name": str(source_name),
                "source_type": str(source_type),
            }
    except Exception:
        pass

    return None


def serialize_input(input_port):
    input_data = {
        "type": str(input_port.GetTypeName()),
    }

    value = None
    try:
        value = input_port.Get(time_code)
    except Exception:
        value = None

    if value is not None:
        input_data["value"] = to_python_type(value)

    connection_data = extract_connection_data(input_port)
    if connection_data:
        input_data["connection"] = connection_data

    return input_data


def serialize_shader(shader):
    prim = shader.GetPrim()

    shader_data = {
        "name": prim.GetName(),
        "path": str(prim.GetPath()),
        "info_id": get_shader_identifier(shader),
        "implementation_source": "",
        "inputs": {},
        "outputs": [],
    }

    try:
        impl_source_attr = prim.GetAttribute("info:implementationSource")
        if impl_source_attr and impl_source_attr.IsValid():
            impl_source = impl_source_attr.Get(time_code)
            if impl_source:
                shader_data["implementation_source"] = str(impl_source)
    except Exception:
        pass

    for input_port in shader.GetInputs():
        shader_data["inputs"][input_port.GetBaseName()] = serialize_input(input_port)

    for output_port in shader.GetOutputs():
        shader_data["outputs"].append({
            "name": output_port.GetBaseName(),
            "full_name": output_port.GetAttr().GetName(),
            "type": str(output_port.GetTypeName()),
        })

    return shader_data


def serialize_material(material):
    prim = material.GetPrim()

    material_data = {
        "name": prim.GetName(),
        "path": str(prim.GetPath()),
        "terminals": {},
        "shaders": [],
    }

    # Material terminals such as outputs:arnold:surface
    for output_port in material.GetOutputs():
        connection_data = extract_connection_data(output_port)
        if not connection_data:
            continue

        terminal_name = output_port.GetAttr().GetName()
        if terminal_name.startswith("outputs:"):
            terminal_name = terminal_name[len("outputs:"):]

        material_data["terminals"][terminal_name] = {
            "type": str(output_port.GetTypeName()),
            "connection": connection_data,
        }

    # Collect all shader prims authored below this material
    for child in Usd.PrimRange(prim):
        if child == prim:
            continue
        if child.IsA(UsdShade.Shader):
            material_data["shaders"].append(serialize_shader(UsdShade.Shader(child)))

    return material_data


materials = []

for prim in stage.Traverse():
    if not prim.IsA(UsdShade.Material):
        continue

    material = UsdShade.Material(prim)
    if not is_arnold_material(material):
        continue

    materials.append(serialize_material(material))


payload = {
    "exporter": "Solaris2Katana",
    "kind": "arnold_materials",
    "count": len(materials),
    "materials": materials,
}


try:
    json_text = json.dumps(
        payload,
        indent=4,
        ensure_ascii=False,
        allow_nan=False
    )

    output_dir = os.path.dirname(output_path)
    if output_dir and not os.path.exists(output_dir):
        os.makedirs(output_dir)

    fd, temp_path = tempfile.mkstemp(
        prefix="houdini_arnold_materials_export_",
        suffix=".json",
        dir=output_dir if output_dir else None
    )
    os.close(fd)

    with open(temp_path, "w", encoding="utf-8") as handle:
        handle.write(json_text)

    if os.path.exists(output_path):
        os.remove(output_path)
    os.replace(temp_path, output_path)

    print("[成功] 已导出 {} 个 Arnold 材质到: {}".format(len(materials), output_path))

except Exception as exc:
    print("[错误] 导出材质 JSON 失败: {}".format(exc))
