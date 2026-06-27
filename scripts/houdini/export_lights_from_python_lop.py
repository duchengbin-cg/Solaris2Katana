import json
import math
import os
import tempfile
import hou
from pxr import Usd, UsdGeom, UsdLux, UsdShade

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

    # USD/Gf colors and vectors are often indexable but not reliably iterable
    # in older Houdini/USD builds. Prefer indexed access so color values land in
    # JSON as numeric arrays instead of fallback strings like "(1, 1, 1)".
    if hasattr(val, "__len__") and hasattr(val, "__getitem__") and not isinstance(val, (str, bytes)):
        try:
            return [to_python_type(val[index]) for index in range(len(val))]
        except Exception:
            pass

    if hasattr(val, "__iter__") and not isinstance(val, (str, bytes)):
        try:
            return [to_python_type(x) for x in val]
        except Exception:
            pass

    return str(val)


def safe_text_value(value):
    if isinstance(value, bytes):
        for encoding in ("utf-8", "gbk", "latin-1"):
            try:
                return value.decode(encoding)
            except Exception:
                pass
        return value.decode("utf-8", "ignore")

    try:
        return value if isinstance(value, str) else str(value)
    except Exception:
        try:
            return repr(value)
        except Exception:
            return "<unprintable>"


def normalize_port_name(port_name):
    text = safe_text_value(port_name).strip()
    if not text:
        return ""
    if "." in text:
        text = text.split(".")[-1]
    if ":" in text:
        text = text.split(":")[-1]
    return text


def get_shader_identifier(shader):
    shader_id_attr = shader.GetIdAttr()
    if shader_id_attr:
        shader_id = shader_id_attr.Get(Usd.TimeCode.Default())
        if shader_id:
            return safe_text_value(shader_id)
    return ""


def extract_connection_data(port):
    try:
        if not port.HasConnectedSource():
            return None
    except Exception:
        return None

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


def get_shader_inputs_payload(shader):
    inputs_payload = {}
    for shader_input in shader.GetInputs():
        input_name = normalize_port_name(shader_input.GetBaseName())
        if not input_name:
            continue

        value = shader_input.Get(Usd.TimeCode.Default())
        input_data = {
            "type": safe_text_value(shader_input.GetTypeName()),
            "value": to_python_type(value),
        }

        connection = extract_connection_data(shader_input)
        if connection:
            input_data["connection"] = connection

        inputs_payload[input_name] = input_data
    return inputs_payload


def extract_connection_data_from_attr(attr):
    try:
        connection_paths = attr.GetConnections()
    except Exception:
        connection_paths = []

    if not connection_paths:
        return None

    source_path = connection_paths[0]
    source_path_text = safe_text_value(source_path)
    source_prim_path = ""
    source_name = ""
    try:
        source_prim_path = str(source_path.GetPrimPath())
        source_name = safe_text_value(source_path.name)
    except Exception:
        source_path_text = source_path_text.replace(chr(92), "/")
        if "." in source_path_text:
            source_prim_path, source_name = source_path_text.rsplit(".", 1)
        else:
            source_prim_path = source_path_text
            source_name = ""

    return {
        "source_path": source_prim_path,
        "source_name": source_name,
        "source_name_base": normalize_port_name(source_name),
        "source_type": "",
    }


def get_prim_identifier(prim):
    try:
        info_id_attr = prim.GetAttribute("info:id")
        if info_id_attr and info_id_attr.IsValid():
            info_id = info_id_attr.Get(Usd.TimeCode.Default())
            if info_id:
                return safe_text_value(info_id)
    except Exception:
        pass
    return ""


def get_generic_prim_inputs_payload(prim):
    inputs_payload = {}
    for attr in prim.GetAttributes():
        try:
            attr_name = attr.GetName()
        except Exception:
            continue

        if not attr_name.startswith("inputs:"):
            continue

        input_name = normalize_port_name(attr_name)
        if not input_name:
            continue

        input_data = {
            "type": safe_text_value(attr.GetTypeName()),
            "value": to_python_type(attr.Get(Usd.TimeCode.Default())),
        }

        connection = extract_connection_data_from_attr(attr)
        if connection:
            source_prim = stage.GetPrimAtPath(connection["source_path"])
            if source_prim and source_prim.IsValid():
                connection["source_type"] = get_prim_identifier(source_prim)
            input_data["connection"] = connection

        inputs_payload[input_name] = input_data

    return inputs_payload


def is_shader_like_prim(prim):
    if prim.IsA(UsdShade.Shader):
        return True

    if get_prim_identifier(prim):
        return True

    try:
        for attr in prim.GetAttributes():
            if attr.GetName().startswith("inputs:"):
                return True
    except Exception:
        pass

    return False


def collect_shader_descendants(root_prim):
    shader_infos = []
    for child in Usd.PrimRange(root_prim):
        if child == root_prim:
            continue
        if not is_shader_like_prim(child):
            continue

        inputs_payload = {}
        info_id = ""
        if child.IsA(UsdShade.Shader):
            shader = UsdShade.Shader(child)
            info_id = get_shader_identifier(shader)
            inputs_payload = get_shader_inputs_payload(shader)
        else:
            info_id = get_prim_identifier(child)
            inputs_payload = get_generic_prim_inputs_payload(child)

        shader_infos.append({
            "path": str(child.GetPath()),
            "name": child.GetName(),
            "info_id": info_id,
            "inputs": inputs_payload,
        })
    return shader_infos


def infer_root_shader_from_shader_infos(shader_infos):
    if not shader_infos:
        return ""

    shader_paths = set(shader_info["path"] for shader_info in shader_infos)
    used_as_source = set()
    for shader_info in shader_infos:
        for input_info in shader_info.get("inputs", {}).values():
            connection = input_info.get("connection")
            if not connection:
                continue
            source_path = connection.get("source_path", "")
            if source_path in shader_paths:
                used_as_source.add(source_path)

    root_candidates = [shader_info for shader_info in shader_infos if shader_info["path"] not in used_as_source]
    if not root_candidates:
        root_candidates = list(shader_infos)

    preferred_ids = ("arnold:light_decay", "arnold:gobo", "arnold:barndoor", "arnold:light_blocker")
    for preferred_id in preferred_ids:
        for shader_info in root_candidates:
            if shader_info.get("info_id") == preferred_id:
                return shader_info["path"]

    for shader_info in root_candidates:
        if shader_info.get("info_id", "").startswith("arnold:"):
            return shader_info["path"]

    return root_candidates[0]["path"]


def get_hou_filter_output_node(filter_material_path):
    filter_hou_node = hou.node(filter_material_path)
    if filter_hou_node is None:
        return None

    def find_out_terminal(vop_node):
        try:
            for child in vop_node.children():
                try:
                    child_type_name = child.type().name()
                except Exception:
                    child_type_name = ""

                if child_type_name == "arnold_light":
                    return child

                nested = find_out_terminal(child)
                if nested is not None:
                    return nested
        except Exception:
            pass
        return None

    try:
        if filter_hou_node.type().name() == "arnold_light":
            return filter_hou_node
    except Exception:
        pass

    return find_out_terminal(filter_hou_node)


def inspect_hou_node_debug(hou_node):
    if hou_node is None:
        return {
            "exists": False,
        }

    data = {
        "exists": True,
        "path": hou_node.path(),
    }

    try:
        data["type_name"] = safe_text_value(hou_node.type().name())
    except Exception:
        data["type_name"] = ""

    try:
        data["type_category"] = safe_text_value(hou_node.type().category().name())
    except Exception:
        data["type_category"] = ""

    try:
        data["shader_type_name"] = get_hou_shader_type_name(hou_node)
    except Exception:
        data["shader_type_name"] = ""

    try:
        data["is_subnetwork"] = bool(hou_node.isSubNetwork())
    except Exception:
        data["is_subnetwork"] = False

    child_types = []
    try:
        for child in hou_node.children():
            try:
                child_types.append("{}:{}".format(child.name(), child.type().name()))
            except Exception:
                pass
    except Exception:
        pass
    data["children"] = child_types[:20]

    input_connections = []
    try:
        for cnx in hou_node.inputConnections():
            try:
                input_connections.append({
                    "input_index": cnx.inputIndex(),
                    "source_path": cnx.inputNode().path() if cnx.inputNode() is not None else "",
                    "source_type": get_hou_shader_type_name(cnx.inputNode()) if cnx.inputNode() is not None else "",
                })
            except Exception:
                pass
    except Exception:
        pass
    data["input_connections"] = input_connections[:20]

    return data


def is_direct_hou_filter_vop(hou_node):
    if hou_node is None:
        return False

    try:
        if hou_node.type().category().name() != "Vop":
            return False
    except Exception:
        return False

    try:
        if hou_node.type().name() in ("arnold_light", "arnold_material", "arnold_environment", "arnold_camera", "arnold_aov"):
            return False
    except Exception:
        pass

    try:
        shader_type_name = get_hou_shader_type_name(hou_node)
        return shader_type_name in ("light_decay", "gobo", "barndoor", "light_blocker")
    except Exception:
        return False


def get_hou_shader_type_name(hou_node):
    try:
        name_components = hou_node.type().nameComponents()
        if len(name_components) >= 3 and name_components[2]:
            return safe_text_value(name_components[2])
    except Exception:
        pass

    try:
        return safe_text_value(hou_node.type().name())
    except Exception:
        return ""


def get_hou_input_name(hou_node, input_index):
    try:
        input_names = list(hou_node.inputNames())
        if 0 <= input_index < len(input_names):
            return normalize_port_name(input_names[input_index])
    except Exception:
        pass
    return "input{}".format(input_index)


def should_export_hou_parm_tuple(parm_tuple):
    try:
        template = parm_tuple.parmTemplate()
        if template is None:
            return False
        if template.isHidden():
            return False
        if parm_tuple.name().startswith(("vm_", "shop_", "ogl_", "vopui", "vopnet", "name")):
            return False
        if len(parm_tuple) == 0:
            return False
    except Exception:
        return False
    return True


def export_hou_parm_tuple_value(parm_tuple):
    try:
        values = parm_tuple.eval()
    except Exception:
        try:
            values = tuple(parm.eval() for parm in parm_tuple)
        except Exception:
            return None

    if not isinstance(values, (list, tuple)):
        return to_python_type(values)
    if len(values) == 1:
        return to_python_type(values[0])
    return [to_python_type(value) for value in values]


def export_hou_filter_vop_network(root_vop):
    exported_shaders = []
    visited = set()
    pending_nodes = [root_vop]

    while pending_nodes:
        vop_node = pending_nodes.pop()
        if vop_node is None:
            continue

        node_path = vop_node.path()
        if node_path in visited:
            continue
        visited.add(node_path)

        shader_data = {
            "path": node_path,
            "name": vop_node.name(),
            "info_id": get_hou_shader_type_name(vop_node),
            "inputs": {},
        }

        for parm_tuple in vop_node.parmTuples():
            if not should_export_hou_parm_tuple(parm_tuple):
                continue
            value = export_hou_parm_tuple_value(parm_tuple)
            if value is None:
                continue
            shader_data["inputs"][normalize_port_name(parm_tuple.name())] = {
                "type": "hou_parm",
                "value": value,
            }

        for connection in vop_node.inputConnections():
            input_index = connection.inputIndex()
            source_node = connection.inputNode()
            if source_node is None:
                continue

            input_name = get_hou_input_name(vop_node, input_index)
            input_data = shader_data["inputs"].setdefault(input_name, {})
            input_data["type"] = input_data.get("type", "connection")
            input_data["connection"] = {
                "source_path": source_node.path(),
                "source_name": source_node.name(),
                "source_name_base": normalize_port_name(source_node.name()),
                "source_type": get_hou_shader_type_name(source_node),
            }
            pending_nodes.append(source_node)

        exported_shaders.append(shader_data)

    return exported_shaders


def export_filter_shader_network(filter_material_path):
    filter_material_path = safe_text_value(filter_material_path).strip()
    if not filter_material_path:
        return None

    direct_filter_node = hou.node(filter_material_path)
    if is_direct_hou_filter_vop(direct_filter_node):
        return {
            "source_path": filter_material_path,
            "root_shader_path": direct_filter_node.path(),
            "shaders": export_hou_filter_vop_network(direct_filter_node),
            "debug": {
                "source": "houdini_direct_vop",
                "root_node": direct_filter_node.path(),
                "shader_type": get_hou_shader_type_name(direct_filter_node),
            },
        }

    output_vop = get_hou_filter_output_node(filter_material_path)
    if output_vop is not None:
        root_filters = []
        for connection in output_vop.inputConnections():
            # input 0 is color in HtoA's arnold_light output; filters start at 1.
            if connection.inputIndex() == 0:
                continue
            source_node = connection.inputNode()
            if source_node is not None:
                root_filters.append(source_node)

        if root_filters:
            root_filter = root_filters[0]
            return {
                "source_path": filter_material_path,
                "root_shader_path": root_filter.path(),
                "shaders": export_hou_filter_vop_network(root_filter),
                "debug": {
                    "source": "houdini_vop",
                    "output_node": output_vop.path(),
                    "filter_count": len(root_filters),
                    "filter_paths": [node.path() for node in root_filters],
                },
            }

        return {
            "source_path": filter_material_path,
            "root_shader_path": "",
            "shaders": [],
            "warning": "houdini arnold_light output has no filter inputs",
            "debug": {
                "source": "houdini_vop",
                "output_node": output_vop.path(),
                "filter_count": 0,
                "filter_paths": [],
            },
        }

    filter_prim = stage.GetPrimAtPath(filter_material_path)
    if not filter_prim or not filter_prim.IsValid():
        return {
            "source_path": filter_material_path,
            "root_shader_path": "",
            "shaders": [],
            "warning": "filter node not found in hou or usd stage",
            "debug": {
                "source": "unresolved",
                "hou_node": inspect_hou_node_debug(direct_filter_node),
            },
        }

    root_shader_path = ""
    if filter_prim.IsA(UsdShade.Shader):
        root_shader_path = str(filter_prim.GetPath())
    elif filter_prim.IsA(UsdShade.Material) or filter_prim.IsA(UsdShade.NodeGraph):
        connectable = None
        if filter_prim.IsA(UsdShade.Material):
            connectable = UsdShade.Material(filter_prim)
        else:
            connectable = UsdShade.NodeGraph(filter_prim)

        preferred_names = (
            "arnold:light",
            "light",
            "arnold:lightfilter",
            "arnold:lightFilter",
            "lightfilter",
            "lightFilter",
            "arnold:surface",
            "surface",
        )
        outputs = connectable.GetOutputs()

        for preferred_name in preferred_names:
            for output in outputs:
                attr_name = output.GetAttr().GetName()
                if attr_name != preferred_name:
                    continue
                connection = extract_connection_data(output)
                if connection:
                    root_shader_path = connection["source_path"]
                    break
            if root_shader_path:
                break

        if not root_shader_path:
            for output in outputs:
                connection = extract_connection_data(output)
                if connection:
                    root_shader_path = connection["source_path"]
                    break

    if not root_shader_path:
        shader_infos = collect_shader_descendants(filter_prim)
        inferred_root_shader_path = infer_root_shader_from_shader_infos(shader_infos)
        if inferred_root_shader_path:
            return {
                "source_path": filter_material_path,
                "root_shader_path": inferred_root_shader_path,
                "shaders": shader_infos,
                "debug": {
                    "source": "usd_nodegraph_descendants",
                    "hou_node": inspect_hou_node_debug(direct_filter_node),
                    "usd_type_name": safe_text_value(filter_prim.GetTypeName()),
                    "usd_outputs": [safe_text_value(output.GetAttr().GetName()) for output in connectable.GetOutputs()] if 'connectable' in locals() and connectable is not None else [],
                    "shader_count": len(shader_infos),
                },
            }

        return {
            "source_path": filter_material_path,
            "root_shader_path": "",
            "shaders": [],
            "warning": "filter root shader not resolved",
            "debug": {
                "source": "usd_stage",
                "hou_node": inspect_hou_node_debug(direct_filter_node),
                "usd_type_name": safe_text_value(filter_prim.GetTypeName()),
                "usd_outputs": [safe_text_value(output.GetAttr().GetName()) for output in connectable.GetOutputs()] if 'connectable' in locals() and connectable is not None else [],
                "shader_count": len(shader_infos) if 'shader_infos' in locals() else 0,
            },
        }

    exported_shaders = []
    visited = set()
    pending_paths = [root_shader_path]

    while pending_paths:
        shader_path = pending_paths.pop()
        if shader_path in visited:
            continue
        visited.add(shader_path)

        shader_prim = stage.GetPrimAtPath(shader_path)
        if not shader_prim or not shader_prim.IsValid() or not shader_prim.IsA(UsdShade.Shader):
            continue

        shader = UsdShade.Shader(shader_prim)
        shader_data = {
            "path": str(shader_prim.GetPath()),
            "name": shader_prim.GetName(),
            "info_id": get_shader_identifier(shader),
            "inputs": {},
        }

        for shader_input in shader.GetInputs():
            input_name = normalize_port_name(shader_input.GetBaseName())
            if not input_name:
                continue

            value = shader_input.Get(Usd.TimeCode.Default())
            connection = extract_connection_data(shader_input)
            input_data = {
                "type": safe_text_value(shader_input.GetTypeName()),
                "value": to_python_type(value),
            }
            if connection:
                input_data["connection"] = connection
                source_path = connection.get("source_path")
                if source_path and source_path not in visited:
                    pending_paths.append(source_path)

            shader_data["inputs"][input_name] = input_data

        exported_shaders.append(shader_data)

    return {
        "source_path": filter_material_path,
        "root_shader_path": root_shader_path,
        "shaders": exported_shaders,
    }


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
    "inputs:enableColorTemperature": "enable_color_temperature",
    "inputs:colorTemperature": "color_temperature",
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


def get_first_valid_attr(prim, attr_names):
    for attr_name in attr_names:
        attr = prim.GetAttribute(attr_name)
        if attr and attr.IsValid():
            return attr
    return None


def export_standard_attrs(prim, light_info):
    time_code = Usd.TimeCode.Default()

    for usd_attr_name, json_name in STANDARD_ATTR_MAP.items():
        attr = get_first_valid_attr(
            prim,
            (
                usd_attr_name,
                usd_attr_name.replace("inputs:", "", 1) if usd_attr_name.startswith("inputs:") else usd_attr_name,
            )
        )
        if attr is None:
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


def export_light_filters(prim, light_info):
    filter_paths = []
    try:
        filters_rel = UsdLux.LightAPI(prim).GetFiltersRel()
        if filters_rel:
            for target in filters_rel.GetTargets():
                filter_paths.append(str(target))
    except Exception:
        pass

    if filter_paths:
        light_info["filter_paths"] = filter_paths

    arnold_shader_path = ""
    arnold_params = light_info.get("arnold_params", {})
    if isinstance(arnold_params, dict):
        arnold_shader_path = arnold_params.get("shaders", "")

    filter_network = export_filter_shader_network(arnold_shader_path)
    if filter_network:
        if filter_network.get("shaders"):
            light_info["filter_network"] = filter_network
        elif arnold_shader_path:
            light_info["filter_network_debug"] = filter_network
            print("[Filter导出] {} <- {} 未导出到 shader 网络: {} | debug={}".format(
                light_info.get("name", "light"),
                arnold_shader_path,
                filter_network.get("warning", "unknown"),
                filter_network.get("debug", {})
            ))


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
    export_light_filters(prim, light_info)

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
