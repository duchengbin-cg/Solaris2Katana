import json
import os
import ast
from Katana import KatanaFile, NodegraphAPI, Utils

JSON_PATH = r"D:/houdini_arnold_materials_export.json"
NAMESPACE = ""
NODE_X_OFFSET = 300
NODE_Y_OFFSET = -200
NETWORK_MATERIAL_CREATE_NAME = "Houdini_Imported_Materials"

# USD material terminal -> Katana NetworkMaterial terminal
TERMINAL_NAME_MAP = {
    "arnold:surface": "arnoldSurface",
    "arnold:displacement": "arnoldDisplacement",
    "arnold:volume": "arnoldVolume",
    "surface": "arnoldSurface",
    "displacement": "arnoldDisplacement",
    "volume": "arnoldVolume",
}

RAMP_FLOAT_INPUT_NAME_MAP = {
    "position": "ramp_Knots",
    "value": "ramp_Floats",
    "interpolation": "ramp_Interpolation",
}

RAMP_INTERPOLATION_NAME_MAP = {
    "constant": 0,
    "linear": 1,
    "catmullrom": 2,
    "catmull_rom": 2,
    "catmull-rom": 2,
    "catclark": 2,
    "monotonecubic": 3,
    "monotone_cubic": 3,
    "monotone-cubic": 3,
}

RAMP_INTERPOLATION_VALUE_MAP = {
    0: "constant",
    1: "linear",
    2: "catmull-rom",
    3: "monotone_cubic",
}

KNOWN_ENUM_VALUE_MAP = {
    ("triplanar", "coord_space"): {
        "world": "0",
        "object": "1",
        "pref": "2",
    },
    ("triplanar", "normal_type"): {
        "shading": "0",
        "smoothanddisplaced": "1",
        "smooth_and_displaced": "1",
        "smooth-and-displaced": "1",
        "smoothbeforedisplacing": "2",
        "smooth_before_displacing": "2",
        "smooth-before-displacing": "2",
    },
    ("cell_noise", "coord_space"): {
        "world": "0",
        "object": "1",
        "pref": "2",
        "uv": "3",
    },
    ("cell_noise", "pattern"): {
        "noise1": "0",
        "noise2": "1",
        "cell1": "2",
        "cell2": "3",
        "worley1": "4",
        "worley2": "5",
        "alligator": "6",
    },
    ("standard_surface", "subsurface_type"): {
        "diffusion": "0",
        "randomwalk": "1",
        "random_walk": "1",
        "random-walk": "1",
        "randomwalkv2": "2",
        "randomwalk_v2": "2",
        "randomwalk-v2": "2",
    },
    ("ramp_float", "coord_space"): {
        "world": "0",
        "object": "1",
    },
    ("ramp_float", "type"): {
        "custom": "0",
        "u": "1",
        "v": "2",
        "diagonal": "3",
        "radial": "4",
        "circular": "5",
        "box": "6",
        "time": "7",
        "3dlinear": "8",
        "3d_linear": "8",
        "3d-linear": "8",
        "3dspherical": "9",
        "3d_spherical": "9",
        "3d-spherical": "9",
        "3dcylindrical": "10",
        "3d_cylindrical": "10",
        "3d-cylindrical": "10",
    },
    ("ramp_float", "wrap"): {
        "periodic": "0",
        "clamp": "1",
        "mirror": "2",
    },
    ("ramp_float", "use_implicit_uvs"): {
        "off": "0",
        "on": "1",
        "curvesonly": "2",
        "curves_only": "2",
        "curves-only": "2",
    },
    ("mix_shader", "mode"): {
        "blend": "0",
        "add": "1",
    },
    ("image", "filter"): {
        "closest": "0",
        "bilinear": "1",
        "bicubic": "2",
        "smartbicubic": "3",
        "smart_bicubic": "3",
        "smart-bicubic": "3",
    },
    ("image", "swrap"): {
        "periodic": "0",
        "black": "1",
        "clamp": "2",
        "mirror": "3",
        "file": "4",
        "missing": "5",
    },
    ("image", "twrap"): {
        "periodic": "0",
        "black": "1",
        "clamp": "2",
        "mirror": "3",
        "file": "4",
        "missing": "5",
    },
    ("curvature", "output"): {
        "convex": "0",
        "concave": "1",
        "both": "2",
    },
}

DEBUG_FOCUSED_PARAMETER_READBACK = True


def process_all_events():
    try:
        Utils.EventModule.ProcessAllEvents()
    except Exception:
        pass


def normalize_enum_key(value):
    text = safe_text_value(value).strip().lower()
    return "".join(ch for ch in text if ch.isalnum())

def load_json_with_better_error(json_path):
    raw_bytes = None
    with open(json_path, "rb") as handle:
        raw_bytes = handle.read()
    text = None
    for encoding in ("utf-8-sig", "utf-8", "gbk", "latin-1"):
        try:
            text = raw_bytes.decode(encoding)
            break
        except Exception:
            pass
    if text is None:
        raise RuntimeError("JSON decoding failed.")
    return json.loads(text)

def safe_text_value(val):
    if isinstance(val, bytes):
        return val.decode("utf-8", errors="replace")
    return str(val)

def sanitize_data(value):
    if isinstance(value, dict):
        return {sanitize_data(k): sanitize_data(v) for k, v in value.items()}
    if isinstance(value, list):
        return [sanitize_data(item) for item in value]
    if isinstance(value, tuple):
        return [sanitize_data(item) for item in value]
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="replace")
    if isinstance(value, str):
        return value.encode("utf-8", errors="ignore").decode("utf-8")
    return value

def normalize_port_name(port_name):
    if not port_name:
        return ""
    text = safe_text_value(port_name).strip()
    if "." in text:
        text = text.split(".")[-1]
    if ":" in text:
        text = text.split(":")[-1]
    return text

def infer_index_encoded_enum_from_hints(param, string_value):
    try:
        raw_text = safe_text_value(string_value).strip()
        normalized_text = normalize_enum_key(raw_text)

        # Arnold/KtoA enum parameters are commonly stored as StringAttr whose
        # payload is the numeric enum index encoded as text, e.g. "0".
        # If we already have an index-like value, preserve it instead of
        # reverse-mapping back to the display label ("world"), which makes the
        # popup show a warning exclamation mark.
        if raw_text.isdigit():
            return raw_text

        params_to_check = [param]
        get_parent = getattr(param, "getParent", None)
        if callable(get_parent):
            parent = get_parent()
            if parent is not None:
                params_to_check.append(parent)

        for candidate_param in params_to_check:
            hints_str = candidate_param.getHintString()
            if not hints_str:
                continue

            hints = ast.literal_eval(hints_str)
            if not isinstance(hints, dict):
                continue

            options = hints.get("options")
            if isinstance(options, dict):
                for k, v in options.items():
                    key_text = safe_text_value(k).strip()
                    value_text = safe_text_value(v).strip()
                    if key_text == raw_text or value_text == raw_text:
                        return raw_text
                    if normalize_enum_key(key_text) == normalized_text:
                        return str(v)
                    if normalize_enum_key(value_text) == normalized_text:
                        return str(k)
            elif isinstance(options, list):
                for i, opt in enumerate(options):
                    option_text = safe_text_value(opt).strip()
                    if option_text == raw_text or str(i) == raw_text:
                        return raw_text
                    if normalize_enum_key(option_text) == normalized_text:
                        return str(i)
    except Exception:
        pass
    return string_value


def normalize_serialized_input_value(input_type, raw_value):
    if raw_value is None:
        return None

    if isinstance(raw_value, tuple):
        return list(raw_value)

    if not isinstance(raw_value, str):
        return raw_value

    text = raw_value.strip()
    parsed_value = None
    if text and text[0] in "([{" and text[-1] in ")]}":
        try:
            parsed_value = ast.literal_eval(text)
        except Exception:
            parsed_value = None

    if parsed_value is None:
        return raw_value

    if isinstance(parsed_value, tuple):
        parsed_value = list(parsed_value)

    if input_type in ("color3f", "vector3f", "normal3f", "point3f"):
        if isinstance(parsed_value, (list, tuple)):
            return [float(v) for v in parsed_value]

    if input_type == "float[]":
        if isinstance(parsed_value, (list, tuple)):
            return [float(v) for v in parsed_value]

    if input_type == "int[]":
        if isinstance(parsed_value, (list, tuple)):
            return [int(v) for v in parsed_value]

    return parsed_value


def map_known_enum_value(shader_type, input_name, raw_value):
    enum_map = KNOWN_ENUM_VALUE_MAP.get((shader_type, input_name))
    if not enum_map:
        return raw_value

    normalized_value = normalize_enum_key(raw_value)
    for enum_name, encoded_value in enum_map.items():
        if normalize_enum_key(enum_name) == normalized_value:
            return encoded_value
    return raw_value


def collapse_ramp_interpolation_value(raw_value):
    if isinstance(raw_value, str):
        return RAMP_INTERPOLATION_VALUE_MAP.get(
            RAMP_INTERPOLATION_NAME_MAP.get(raw_value.lower(), 1),
            "linear"
        )

    if not isinstance(raw_value, (list, tuple)) or not raw_value:
        return "linear"

    counts = {}
    for item in raw_value:
        key = None
        if isinstance(item, str):
            key = RAMP_INTERPOLATION_NAME_MAP.get(item.lower())
        else:
            try:
                key = int(item)
            except Exception:
                key = None
        if key is None:
            continue
        counts[key] = counts.get(key, 0) + 1

    if not counts:
        return "linear"

    most_common_key = max(sorted(counts.keys()), key=lambda entry: counts[entry])
    if len(counts) > 1:
        print("[提示] ramp_Interpolation 存在混合插值，当前按出现次数最多的模式写入: {}".format(
            RAMP_INTERPOLATION_VALUE_MAP.get(most_common_key, "linear")
        ))
    return RAMP_INTERPOLATION_VALUE_MAP.get(most_common_key, "linear")

def prepare_input_value_for_write(shader_type, input_name, input_type, raw_value):
    value = normalize_serialized_input_value(input_type, raw_value)
    if shader_type == "ramp_float":
        if input_name == "ramp_Interpolation":
            value = collapse_ramp_interpolation_value(value)
    if isinstance(value, str):
        value = map_known_enum_value(shader_type, input_name, value)
    return value


def is_focused_debug_parameter(input_name):
    name = safe_text_value(input_name).lower()
    if name in ("coord_space", "pattern", "subsurface_type"):
        return True
    for token in ("color", "black", "white"):
        if token in name:
            return True
    return False


def read_parameter_value_group(node, base_path):
    value_param = node.getParameter(base_path + ".value")
    if value_param is None:
        return None

    child_values = []
    index = 0
    while True:
        child = value_param.getChild("i{}".format(index))
        if child is None:
            break
        try:
            child_values.append(child.getValue(0))
        except Exception:
            child_values.append(None)
        index += 1

    if child_values:
        return child_values

    try:
        return value_param.getValue(0)
    except Exception:
        return None


def get_connection_source_port_name(connection):
    if not connection:
        return ""

    for key in ("source_name_base", "source_port", "source_name"):
        value = connection.get(key)
        if value:
            return value
    return ""


def get_terminal_connection_data(terminal_payload):
    if not isinstance(terminal_payload, dict):
        return {}
    connection = terminal_payload.get("connection")
    if isinstance(connection, dict):
        return connection
    return terminal_payload

def set_parameter_value_group(node, base_path, value):
    """
    正确的参数赋值方式：对标量直接 setValue，对数组则 resizeArray 后逐个设置 i0, i1
    """
    enable_param = node.getParameter(base_path + ".enable")
    if enable_param is not None:
        enable_param.setValue(1, 0)

    dynamic_array_param = node.getParameter(base_path + ".isDynamicArray")
    if dynamic_array_param is not None and isinstance(value, (list, tuple)):
        dynamic_array_param.setValue(1, 0)
        
    value_param = node.getParameter(base_path + ".value")
    if value_param is None:
        return False
        
    if isinstance(value, str):
        value = infer_index_encoded_enum_from_hints(value_param, value)
        
    try:
        if isinstance(value, (list, tuple)):
            wrote_any = False
            missing_child_names = []

            # 优先直接写已存在的固定 tuple 子参数，避免对颜色类三元参数误用 resizeArray。
            for i, v in enumerate(value):
                child_name = "i{}".format(i)
                child = value_param.getChild(child_name)
                if child is not None:
                    child.setValue(v, 0)
                    wrote_any = True
                else:
                    missing_child_names.append(child_name)

            # 只有在子参数不完整、且参数明确支持动态数组时，才尝试 resizeArray。
            if missing_child_names and hasattr(value_param, 'resizeArray'):
                try:
                    value_param.resizeArray(len(value))
                    process_all_events()
                    for i, v in enumerate(value):
                        child = value_param.getChild("i{}".format(i))
                        if child is not None:
                            child.setValue(v, 0)
                            wrote_any = True
                except Exception:
                    pass

            if not wrote_any:
                return False
        else:
            value_param.setValue(value, 0)
        process_all_events()
        return True
    except Exception as e:
        print("[警告] 赋值失败 {}: {}".format(base_path, e))
        return False

def configure_arnold_shading_node(shader_node, shader_data):
    shader_type = shader_data.get("info_id", "")
    if shader_type.startswith("arnold:"):
        shader_type = shader_type[7:]
        
    node_name = shader_data.get("name", "arnoldShader")
    
    shader_node.setName(node_name)
    shader_node.getParameter("name").setValue(shader_node.getName(), 0)
    shader_node.getParameter("nodeType").setValue(shader_type, 0)
    
    check_dynamic = getattr(shader_node, "checkDynamicParameters", None)
    if check_dynamic:
        try:
            check_dynamic()
            process_all_events()
        except Exception as e:
            print("[警告] checkDynamicParameters 失败 {}: {}".format(node_name, e))

    inputs = shader_data.get("inputs", {})
    deferred_values = []
    for input_name, input_info in inputs.items():
        if input_name.startswith("arnold:"):
            input_name = input_name[7:]
            
        if shader_type == "ramp_float" and input_name in RAMP_FLOAT_INPUT_NAME_MAP:
            input_name = RAMP_FLOAT_INPUT_NAME_MAP[input_name]
            
        value = input_info.get("value")
        if value is None:
            continue

        input_type = input_info.get("type")
        value = prepare_input_value_for_write(shader_type, input_name, input_type, value)
        param_base_path = "parameters." + input_name
        
        if shader_type == "ramp_float" and input_name in ("ramp_Knots", "ramp_Floats", "ramp_Interpolation"):
            if isinstance(value, (list, tuple)):
                ramp_param = shader_node.getParameter("parameters.ramp")
                if ramp_param:
                    ramp_param.setValue(len(value), 0)
                    process_all_events()
                    
        success = set_parameter_value_group(shader_node, param_base_path, value)
        if not success:
            print("[警告] 参数写入失败: {}.{}".format(node_name, input_name))
        deferred_values.append((input_name, param_base_path, value))

    # 第二次重写参数，避免某些动态参数在首次刷新后又被默认值覆盖。
    if check_dynamic:
        try:
            check_dynamic()
            process_all_events()
        except Exception:
            pass

    for input_name, param_base_path, value in deferred_values:
        set_parameter_value_group(shader_node, param_base_path, value)
        if DEBUG_FOCUSED_PARAMETER_READBACK and is_focused_debug_parameter(input_name):
            actual_value = read_parameter_value_group(shader_node, param_base_path)
            if actual_value != value:
                print("[参数回读] {}.{} -> expected={} actual={}".format(
                    node_name,
                    input_name,
                    value,
                    actual_value
                ))

def connect_shader_network(node_by_path, material_data):
    for shader_data in material_data:
        target_path = shader_data.get("path")
        target_node = node_by_path.get(target_path)
        if not target_node:
            continue
            
        shader_type = shader_data.get("info_id", "").replace("arnold:", "")
        inputs = shader_data.get("inputs", {})
        
        for input_name, input_info in inputs.items():
            if input_name.startswith("arnold:"):
                input_name = input_name[7:]
                
            if shader_type == "ramp_float" and input_name in RAMP_FLOAT_INPUT_NAME_MAP:
                input_name = RAMP_FLOAT_INPUT_NAME_MAP[input_name]
                
            connection = input_info.get("connection")
            if not connection:
                continue
                
            source_path = connection.get("source_path")
            source_port_name = get_connection_source_port_name(connection)
            source_node = node_by_path.get(source_path)
            
            if not source_node:
                continue
                
            enable_param = target_node.getParameter("parameters.{}.enable".format(input_name))
            if enable_param:
                enable_param.setValue(0, 0)
                
            target_port = target_node.getInputPort(input_name)
            if not target_port:
                target_port = target_node.addInputPort(input_name)
                
            source_port = source_node.getOutputPort(normalize_port_name(source_port_name))
            if not source_port:
                source_port = source_node.getOutputPort("out")
                if not source_port:
                    source_port = source_node.addOutputPort("out")
                    
            if target_port and source_port:
                target_port.connect(source_port)
                process_all_events()

def create_all_materials(materials, root_node):
    """
    统一创建：在最外层只建一个 NetworkMaterialCreate 节点。
    里面为每个材质建一个 NetworkMaterial 节点，保留正确名字。
    """
    nmc_node = NodegraphAPI.CreateNode("NetworkMaterialCreate", root_node)
    nmc_node.setName(NAMESPACE + NETWORK_MATERIAL_CREATE_NAME)
    
    created_count = 0
    
    for i, material_dict in enumerate(materials):
        material_path = material_dict.get("path", "/Root/Material_{}".format(i))
        material_name = material_path.split("/")[-1] or "Material_{}".format(i)
        
        # 在 NMC 内部创建真正的 NetworkMaterial 终端节点，并赋予 Houdini 的名字！
        mat_node = NodegraphAPI.CreateNode("NetworkMaterial", nmc_node)
        mat_node.setName(NAMESPACE + material_name)
        mat_name_param = mat_node.getParameter("name")
        if mat_name_param is not None:
            mat_name_param.setValue(material_name, 0)
        namespace_param = mat_node.getParameter("namespace")
        if namespace_param is not None:
            namespace_param.setValue("", 0)
        process_all_events()
        
        # 排版：材质终端节点放在右边
        NodegraphAPI.SetNodePosition(mat_node, (800, i * NODE_Y_OFFSET))
        
        shaders_data = material_dict.get("shaders", [])
        node_by_path = {}
        
        # 1. 实例化所有节点并设置参数
        for j, shader_data in enumerate(shaders_data):
            shader_data = sanitize_data(shader_data)
            
            shader_node = NodegraphAPI.CreateNode("ArnoldShadingNode", nmc_node)
            configure_arnold_shading_node(shader_node, shader_data)
            
            # 排版：着色节点依次往左排
            NodegraphAPI.SetNodePosition(shader_node, (800 - (j + 1) * NODE_X_OFFSET, i * NODE_Y_OFFSET))
            node_by_path[shader_data.get("path")] = shader_node

        # 2. 连接所有节点
        connect_shader_network(node_by_path, shaders_data)
        
        # 3. 输出给对应的 NetworkMaterial 节点
        terminals_data = material_dict.get("terminals", {})
        for usd_terminal, conn in terminals_data.items():
            connection = get_terminal_connection_data(conn)
            katana_terminal = TERMINAL_NAME_MAP.get(usd_terminal, "arnoldSurface")
            source_path = connection.get("source_path")
            source_port_name = get_connection_source_port_name(connection) or "out"
            
            source_node = node_by_path.get(source_path)
            if not source_node:
                print("[警告] 找不到材质终端上游 shader: {} -> {}".format(
                    material_name,
                    source_path
                ))
                continue
                
            target_port = mat_node.getInputPort(katana_terminal)
            if not target_port:
                target_port = mat_node.addInputPort(katana_terminal)
                
            source_port = source_node.getOutputPort(normalize_port_name(source_port_name))
            if not source_port:
                source_port = source_node.getOutputPort("out")
                if not source_port:
                    source_port = source_node.addOutputPort("out")
                    
            if target_port and source_port:
                target_port.connect(source_port)
                process_all_events()
                print("[连接] {}.{} <- {}.{}".format(
                    material_name,
                    katana_terminal,
                    source_node.getName(),
                    normalize_port_name(source_port_name) or "out"
                ))
                
        created_count += 1
        
    return created_count

def main():
    print("----- 开始导入 Houdini Arnold 材质到 NetworkMaterialCreate -----")
    try:
        data = load_json_with_better_error(JSON_PATH)
    except Exception as e:
        print("[错误] 无法加载 JSON:", e)
        return
        
    materials = data.get("materials", [])
    if not materials:
        print("[警告] JSON 中没有找到材质数据")
        return
        
    root_node = NodegraphAPI.GetRootNode()
    
    created_count = create_all_materials(materials, root_node)
        
    print("----- 导入结束 -----")
    print("成功创建 {} 个材质".format(created_count))

main()
