import json
from Katana import NodegraphAPI

JSON_PATH = r"D:/houdini_arnold_materials_export.json"
NAMESPACE = ""
NODE_X_OFFSET = 260
NODE_Y_OFFSET = -120

# USD material terminal -> Katana NetworkMaterial terminal
TERMINAL_NAME_MAP = {
    "arnold:surface": "arnoldSurface",
    "arnold:displacement": "arnoldDisplacement",
    "arnold:volume": "arnoldVolume",
}


def load_json_with_better_error(json_path):
    with open(json_path, "r", encoding="utf-8") as handle:
        text = handle.read()

    try:
        return json.loads(text)
    except json.JSONDecodeError as exc:
        lines = text.splitlines()
        bad_line = ""
        if 1 <= exc.lineno <= len(lines):
            bad_line = lines[exc.lineno - 1]
        raise RuntimeError(
            "JSON 文件格式错误: line {}, column {}.\n问题行内容: {}".format(
                exc.lineno, exc.colno, bad_line
            )
        )


def get_children(node):
    for method_name in ("getChildren", "getChildNodes"):
        method = getattr(node, method_name, None)
        if method is None:
            continue
        try:
            return list(method())
        except Exception:
            pass
    return []


def iter_descendants(node):
    stack = get_children(node)
    while stack:
        child = stack.pop(0)
        yield child
        stack.extend(get_children(child))


def find_first_child_by_type(parent_node, node_type_name):
    for child in iter_descendants(parent_node):
        try:
            if child.getType() == node_type_name:
                return child
        except Exception:
            pass
    return None


def unique_node_name(parent_node, base_name):
    candidate = base_name
    index = 1
    while True:
        exists = False
        for child in get_children(parent_node):
            try:
                if child.getName() == candidate:
                    exists = True
                    break
            except Exception:
                pass
        if exists is False:
            return candidate
        candidate = "{}_{}".format(base_name, index)
        index += 1


def set_param_value(param, value):
    if param is None:
        return False

    if isinstance(value, bool):
        param.setValue(1 if value else 0, 0)
        return True

    if isinstance(value, int):
        param.setValue(value, 0)
        return True

    if isinstance(value, float):
        param.setValue(value, 0)
        return True

    param.setValue(str(value), 0)
    return True


def set_parameter_value_group(node, base_path, value):
    """
    ArnoldShadingNode dynamic parameters commonly live under:
    parameters.<param>.enable
    parameters.<param>.value
    """
    enable_param = node.getParameter(base_path + ".enable")
    value_param = node.getParameter(base_path + ".value")
    direct_param = node.getParameter(base_path)

    if enable_param is not None:
        enable_param.setValue(1, 0)

    target_param = value_param if value_param is not None else direct_param
    if target_param is None:
        return False

    if isinstance(value, (list, tuple)):
        # Typical tuple-style value children: i0/i1/i2...
        children = []
        try:
            children = list(target_param.getChildren())
        except Exception:
            children = []

        if children:
            ok = False
            for index, item in enumerate(value):
                child = target_param.getChild("i{}".format(index))
                if child is not None:
                    set_param_value(child, item)
                    ok = True
            if ok:
                return True

        # Fallback when node stores tuple directly on the direct group
        if direct_param is not None:
            ok = False
            for index, item in enumerate(value):
                child = direct_param.getChild("i{}".format(index))
                if child is not None:
                    set_param_value(child, item)
                    ok = True
            return ok

        return False

    return set_param_value(target_param, value)


def set_node_position_safe(node, x, y):
    try:
        NodegraphAPI.SetNodePosition(node, (x, y))
    except Exception:
        pass


def ensure_input_port(node, port_name):
    port = node.getInputPort(port_name)
    if port is not None:
        return port

    try:
        port = node.addInputPort(port_name)
    except Exception:
        port = None
    return port


def ensure_output_port(node, port_name):
    port = node.getOutputPort(port_name)
    if port is not None:
        return port

    try:
        port = node.addOutputPort(port_name)
    except Exception:
        port = None
    return port


def resolve_output_port(node, port_name):
    if port_name:
        port = node.getOutputPort(port_name)
        if port is not None:
            return port

    try:
        output_ports = list(node.getOutputPorts())
    except Exception:
        output_ports = []

    if len(output_ports) == 1:
        return output_ports[0]

    if output_ports:
        # Common Arnold single-shader output names
        for candidate_name in ("out", "output", "default"):
            for port in output_ports:
                try:
                    if port.getName() == candidate_name:
                        return port
                except Exception:
                    pass
        return output_ports[0]

    return ensure_output_port(node, port_name or "out")


def resolve_input_port(node, port_name):
    if port_name:
        port = node.getInputPort(port_name)
        if port is not None:
            return port

    return ensure_input_port(node, port_name)


def configure_arnold_shading_node(shader_node, shader_data):
    node_type_param = shader_node.getParameter("nodeType")
    if node_type_param is None:
        raise RuntimeError("ArnoldShadingNode 缺少 nodeType 参数")

    info_id = shader_data.get("info_id", "")
    arnold_node_type = info_id

    # Convert USD style identifier like arnold:standard_surface -> standard_surface
    if ":" in arnold_node_type:
        arnold_node_type = arnold_node_type.split(":")[-1]

    node_type_param.setValue(arnold_node_type, 0)

    check_dynamic = getattr(shader_node, "checkDynamicParameters", None)
    if check_dynamic is not None:
        check_dynamic()


def set_shader_parameters(shader_node, shader_data):
    inputs = shader_data.get("inputs", {})

    for input_name, input_data in inputs.items():
        if "connection" in input_data:
            continue
        if "value" not in input_data:
            continue

        value = input_data.get("value")
        base_path = "parameters.{}".format(input_name)
        ok = set_parameter_value_group(shader_node, base_path, value)
        if ok is False:
            print("[警告] 参数写入失败: {}.{}".format(shader_data.get("name"), input_name))


def connect_shader_network(node_by_path, material_data):
    for shader_data in material_data.get("shaders", []):
        target_node = node_by_path.get(shader_data.get("path"))
        if target_node is None:
            continue

        inputs = shader_data.get("inputs", {})
        for input_name, input_data in inputs.items():
            connection_data = input_data.get("connection")
            if not connection_data:
                continue

            source_node = node_by_path.get(connection_data.get("source_path"))
            if source_node is None:
                print("[警告] 找不到上游 shader: {}".format(connection_data.get("source_path")))
                continue

            output_port = resolve_output_port(source_node, connection_data.get("source_name"))
            input_port = resolve_input_port(target_node, input_name)

            if output_port is None or input_port is None:
                print("[警告] 端口连接失败: {} -> {}.{}".format(
                    connection_data.get("source_path"),
                    shader_data.get("name"),
                    input_name
                ))
                continue

            try:
                input_port.connect(output_port)
            except Exception:
                try:
                    output_port.connect(input_port)
                except Exception as exc:
                    print("[警告] 连接失败 {} -> {}.{} : {}".format(
                        connection_data.get("source_path"),
                        shader_data.get("name"),
                        input_name,
                        exc
                    ))


def connect_material_terminals(network_material_node, node_by_path, material_data):
    terminals = material_data.get("terminals", {})

    for terminal_name, terminal_data in terminals.items():
        mapped_terminal = TERMINAL_NAME_MAP.get(terminal_name)
        if not mapped_terminal:
            print("[提示] 未映射的 material terminal: {}".format(terminal_name))
            continue

        connection_data = terminal_data.get("connection")
        if not connection_data:
            continue

        source_node = node_by_path.get(connection_data.get("source_path"))
        if source_node is None:
            print("[警告] 找不到 terminal 上游 shader: {}".format(connection_data.get("source_path")))
            continue

        source_output = resolve_output_port(source_node, connection_data.get("source_name"))
        terminal_input = ensure_input_port(network_material_node, mapped_terminal)

        if source_output is None or terminal_input is None:
            print("[警告] terminal 连接失败: {}".format(mapped_terminal))
            continue

        try:
            terminal_input.connect(source_output)
        except Exception:
            try:
                source_output.connect(terminal_input)
            except Exception as exc:
                print("[警告] terminal 连接失败 {} -> {} : {}".format(
                    source_node.getName(),
                    mapped_terminal,
                    exc
                ))


def configure_network_material_create(nmc_node, network_material_node, material_data):
    material_name = material_data.get("name", "ImportedMaterial")

    try:
        nmc_node.setName(unique_node_name(NodegraphAPI.GetRootNode(), material_name + "_NMC"))
    except Exception:
        pass

    for node in (nmc_node, network_material_node):
        if node is None:
            continue
        name_param = node.getParameter("name")
        if name_param is not None:
            name_param.setValue(material_name, 0)
        namespace_param = node.getParameter("namespace")
        if namespace_param is not None:
            namespace_param.setValue(NAMESPACE, 0)


def create_material_network(material_data, parent_node, x_origin=0, y_origin=0):
    nmc_node = NodegraphAPI.CreateNode("NetworkMaterialCreate", parent_node)
    set_node_position_safe(nmc_node, x_origin, y_origin)

    network_material_node = find_first_child_by_type(nmc_node, "NetworkMaterial")
    if network_material_node is None:
        network_material_node = NodegraphAPI.CreateNode("NetworkMaterial", nmc_node)
        set_node_position_safe(network_material_node, x_origin + NODE_X_OFFSET * 2, y_origin)

    configure_network_material_create(nmc_node, network_material_node, material_data)

    node_by_path = {}

    # 1. Create all Arnold shader nodes
    for index, shader_data in enumerate(material_data.get("shaders", [])):
        shader_node = NodegraphAPI.CreateNode("ArnoldShadingNode", nmc_node)
        shader_name = shader_data.get("name") or "arnoldShader"
        shader_node.setName(unique_node_name(nmc_node, shader_name))
        set_node_position_safe(
            shader_node,
            x_origin + index * NODE_X_OFFSET,
            y_origin + (index % 6) * NODE_Y_OFFSET
        )

        configure_arnold_shading_node(shader_node, shader_data)
        set_shader_parameters(shader_node, shader_data)
        node_by_path[shader_data.get("path")] = shader_node

    # 2. Connect shader-to-shader links
    connect_shader_network(node_by_path, material_data)

    # 3. Connect material terminals
    connect_material_terminals(network_material_node, node_by_path, material_data)

    return nmc_node


def import_arnold_materials():
    payload = load_json_with_better_error(JSON_PATH)

    if payload.get("kind") != "arnold_materials":
        raise RuntimeError("JSON kind 不是 arnold_materials")

    materials = payload.get("materials", [])
    root_node = NodegraphAPI.GetRootNode()

    created_nodes = []

    print("----- 开始导入 Houdini Arnold 材质到 NetworkMaterialCreate -----")

    for index, material_data in enumerate(materials):
        try:
            nmc_node = create_material_network(
                material_data,
                root_node,
                x_origin=index * 500,
                y_origin=0
            )
            created_nodes.append(nmc_node)
            print("[成功] {} -> {}".format(material_data.get("name"), nmc_node.getName()))
        except Exception as exc:
            print("[失败] {} : {}".format(material_data.get("name"), exc))

    print("----- 导入结束 -----")
    print("成功创建 {} 个材质".format(len(created_nodes)))
    return created_nodes


created_material_nodes = import_arnold_materials()
print("完成。创建的 NetworkMaterialCreate 数量: {}".format(len(created_material_nodes)))
