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

SUPPORTED_TERMINAL_NAMES = {
    "surface",
    "displacement",
    "volume",
    "arnold:surface",
    "arnold:displacement",
    "arnold:volume",
}


def safe_text_value(value):
    if isinstance(value, bytes):
        for encoding in ("utf-8", "gbk", "latin-1"):
            try:
                return value.decode(encoding)
            except Exception:
                pass
        return value.decode("utf-8", "ignore")

    try:
        text = value if isinstance(value, str) else str(value)
    except Exception:
        try:
            text = repr(value)
        except Exception:
            text = "<unprintable>"

    try:
        text.encode("utf-8")
        return text
    except Exception:
        pass

    try:
        return text.encode("latin-1", "ignore").decode("latin-1")
    except Exception:
        pass

    return "".join(ch for ch in text if ord(ch) < 128)


def normalize_port_name(port_name):
    text = safe_text_value(port_name).strip()
    if not text:
        return ""
    if "." in text:
        text = text.split(".")[-1]
    if ":" in text:
        text = text.split(":")[-1]
    return text


def safe_number(value):
    try:
        number = float(value)
        if math.isfinite(number):
            return number
        return None
    except Exception:
        return None


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

    if isinstance(value, bytes):
        return safe_text_value(value)

    if isinstance(value, str):
        return safe_text_value(value)

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
            result[safe_text_value(key)] = to_python_type(item)
        return result

    if hasattr(value, "__iter__") and not isinstance(value, (str, bytes)):
        try:
            return [to_python_type(item) for item in value]
        except Exception:
            pass

    return safe_text_value(value)


def get_shader_identifier(shader):
    shader_id_attr = shader.GetIdAttr()
    if shader_id_attr:
        shader_id = shader_id_attr.Get(time_code)
        if shader_id:
            return safe_text_value(shader_id)
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
                "source_name": safe_text_value(source_name),
                "source_name_base": normalize_port_name(source_name),
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
                "source_name": safe_text_value(source_name),
                "source_name_base": normalize_port_name(source_name),
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
        "name": safe_text_value(prim.GetName()),
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
                shader_data["implementation_source"] = safe_text_value(impl_source)
    except Exception:
        pass

    for input_port in shader.GetInputs():
        shader_data["inputs"][safe_text_value(input_port.GetBaseName())] = serialize_input(input_port)

    for output_port in shader.GetOutputs():
        shader_data["outputs"].append({
            "name": safe_text_value(output_port.GetBaseName()),
            "name_base": normalize_port_name(output_port.GetBaseName()),
            "full_name": safe_text_value(output_port.GetAttr().GetName()),
            "type": str(output_port.GetTypeName()),
        })

    return shader_data


def serialize_material(material):
    prim = material.GetPrim()

    material_data = {
        "name": safe_text_value(prim.GetName()),
        "path": str(prim.GetPath()),
        "terminals": {},
        "shaders": [],
    }

    # Material terminals such as outputs:arnold:surface or outputs:surface.
    # If Arnold-specific terminals exist, prefer them and ignore generic preview terminals.
    terminal_candidates = []
    for output_port in material.GetOutputs():
        connection_data = extract_connection_data(output_port)
        if not connection_data:
            continue

        terminal_name = safe_text_value(output_port.GetAttr().GetName())
        if terminal_name.startswith("outputs:"):
            terminal_name = terminal_name[len("outputs:"):]

        if terminal_name not in SUPPORTED_TERMINAL_NAMES:
            continue

        terminal_candidates.append((terminal_name, {
            "type": str(output_port.GetTypeName()),
            "connection": connection_data,
        }))

    has_arnold_terminal = any(name.startswith("arnold:") for name, _ in terminal_candidates)

    connected_shader_paths = set()
    for terminal_name, terminal_payload in terminal_candidates:
        if has_arnold_terminal and not terminal_name.startswith("arnold:"):
            continue

        material_data["terminals"][terminal_name] = terminal_payload
        connected_shader_paths.add(terminal_payload["connection"]["source_path"])

    # Follow only the shader subgraph that is actually connected to exported terminals.
    visited_shader_paths = set()
    shader_queue = list(connected_shader_paths)

    while shader_queue:
        shader_path = shader_queue.pop(0)
        if shader_path in visited_shader_paths:
            continue

        visited_shader_paths.add(shader_path)

        shader_prim = stage.GetPrimAtPath(shader_path)
        if not shader_prim or not shader_prim.IsA(UsdShade.Shader):
            continue

        shader = UsdShade.Shader(shader_prim)
        material_data["shaders"].append(serialize_shader(shader))

        for input_port in shader.GetInputs():
            upstream_data = extract_connection_data(input_port)
            if not upstream_data:
                continue
            upstream_path = upstream_data.get("source_path")
            if upstream_path and upstream_path not in visited_shader_paths:
                shader_queue.append(upstream_path)

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
