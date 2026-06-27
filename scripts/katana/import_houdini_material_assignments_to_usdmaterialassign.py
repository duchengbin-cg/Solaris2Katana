import json
import os
from Katana import NodegraphAPI

JSON_PATH = r"D:/houdini_material_assignments_export.json"
STACK_NAME_PREFIX = "UsdMaterialAssign"
GROUPSTACK_NAME = "Houdini_UsdMaterialAssigns"
NODE_X = 0
NODE_Y_START = 150
NODE_Y_STEP = -80


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


def sanitize_node_name(text):
    name = safe_text_value(text).strip()
    if not name:
        return "UsdMaterialAssign"
    for ch in "\\/:*?\"<>| ":
        name = name.replace(ch, "_")
    while "__" in name:
        name = name.replace("__", "_")
    return name.strip("_") or "UsdMaterialAssign"


def load_json_with_better_error(json_path):
    if not os.path.exists(json_path):
        raise IOError("JSON 文件不存在: {}".format(json_path))

    with open(json_path, "r") as f:
        payload = json.load(f)

    if isinstance(payload, list):
        return payload

    if isinstance(payload, dict):
        assignments = payload.get("assignments")
        if isinstance(assignments, list):
            return assignments

    raise RuntimeError("JSON 结构不正确，期望 list 或包含 assignments 的 dict")


def group_assignments_by_material(assignments):
    grouped = {}
    for assignment in assignments:
        prim_path = assignment.get("prim_path", "")
        material_path = assignment.get("material_path", "")
        relationship_name = assignment.get("relationship_name", "material:binding")
        binding_purpose = assignment.get("binding_purpose", "")
        prim_type = assignment.get("prim_type", "")

        if not prim_path or not material_path:
            continue

        key = (material_path, relationship_name, binding_purpose)
        entry = grouped.setdefault(key, {
            "material_path": material_path,
            "relationship_name": relationship_name,
            "binding_purpose": binding_purpose,
            "prim_paths": [],
            "prim_types": set(),
        })
        entry["prim_paths"].append(prim_path)
        if prim_type:
            entry["prim_types"].add(prim_type)

    result = []
    for _, entry in sorted(grouped.items(), key=lambda item: (item[0][0], item[0][1], item[0][2])):
        entry["prim_paths"] = sorted(set(entry["prim_paths"]))
        entry["prim_types"] = sorted(entry["prim_types"])
        result.append(entry)
    return result


def iter_parameters(param):
    if param is None:
        return
    yield param
    get_num_children = getattr(param, "getNumChildren", None)
    get_child_by_index = getattr(param, "getChildByIndex", None)
    if callable(get_num_children) and callable(get_child_by_index):
        try:
            child_count = get_num_children()
        except Exception:
            child_count = 0
        for child_index in range(child_count):
            child = get_child_by_index(child_index)
            for nested in iter_parameters(child):
                yield nested


def find_parameter_by_suffix(node, suffixes):
    root_param = node.getParameters()
    suffixes = tuple(suffixes)
    for param in iter_parameters(root_param):
        name = param.getName()
        full_name = ""
        try:
            full_name = param.getFullName()
        except Exception:
            full_name = name
        if name in suffixes:
            return param
        if full_name and any(full_name.endswith(suffix) for suffix in suffixes):
            return param
    return None


def set_string_parameter(node, candidate_suffixes, value):
    param = find_parameter_by_suffix(node, candidate_suffixes)
    if param is None:
        return False

    try:
        param.setValue(safe_text_value(value), 0)
        return True
    except Exception:
        return False


def set_string_array_parameter(node, candidate_suffixes, values):
    param = find_parameter_by_suffix(node, candidate_suffixes)
    if param is None:
        return False

    values = [safe_text_value(v) for v in values if safe_text_value(v)]
    if not values:
        return False

    try:
        if hasattr(param, "resizeArray"):
            param.resizeArray(len(values))
        elif hasattr(param, "setNumChildren"):
            param.setNumChildren(len(values))
    except Exception:
        pass

    children = []
    try:
        children = list(param.getChildren())
    except Exception:
        children = []

    # Some Katana array parameters materialize children lazily. Fall back to
    # creating i0/i1... children only when the parameter is a writable group.
    if len(children) < len(values) and hasattr(param, "createChildString"):
        for child_index in range(len(children), len(values)):
            child_name = "i{}".format(child_index)
            try:
                param.createChildString(child_name, "")
            except Exception:
                break
        try:
            children = list(param.getChildren())
        except Exception:
            children = []

    if not children and param.getType() == "string":
        try:
            param.setValue(" ".join(values), 0)
            return True
        except Exception:
            return False

    success_count = 0
    for index, value in enumerate(values):
        child = None
        if index < len(children):
            child = children[index]
        else:
            child = param.getChild("i{}".format(index))
        if child is None:
            continue
        try:
            child.setValue(value, 0)
            success_count += 1
        except Exception:
            pass
    return success_count == len(values)


def get_input_port(node):
    for port_name in ("input", "in", "i0"):
        port = node.getInputPort(port_name)
        if port is not None:
            return port
    input_ports = node.getInputPorts()
    return input_ports[0] if input_ports else None


def get_output_port(node):
    for port_name in ("output", "out", "o0"):
        port = node.getOutputPort(port_name)
        if port is not None:
            return port
    output_ports = node.getOutputPorts()
    return output_ports[0] if output_ports else None


def connect_nodes(upstream_node, downstream_node):
    if upstream_node is None or downstream_node is None:
        return False

    output_port = get_output_port(upstream_node)
    input_port = get_input_port(downstream_node)
    if output_port is None or input_port is None:
        return False

    output_port.connect(input_port)
    return True


def get_single_selected_node():
    selected_nodes = NodegraphAPI.GetAllSelectedNodes()
    if len(selected_nodes) == 1:
        return selected_nodes[0]
    return None


def create_group_stack(root_node):
    stack_node = NodegraphAPI.CreateNode("GroupStack", root_node)
    stack_node.setName(GROUPSTACK_NAME)
    NodegraphAPI.SetNodePosition(stack_node, (NODE_X, NODE_Y_START + 120))
    return stack_node


def add_node_to_group_stack(group_stack_node, child_node):
    build_child = getattr(group_stack_node, "buildChildNode", None)
    if callable(build_child):
        try:
            build_child(child_node)
            return True
        except Exception:
            pass

    try:
        child_node.setParent(group_stack_node)
        return True
    except Exception:
        return False


def import_houdini_material_assignments():
    assignments = load_json_with_better_error(JSON_PATH)
    grouped_assignments = group_assignments_by_material(assignments)
    root_node = NodegraphAPI.GetRootNode()
    upstream_node = get_single_selected_node()
    group_stack_node = create_group_stack(root_node)

    created_nodes = []
    skipped = []

    print("----- 开始导入 Houdini 材质链接到 UsdMaterialAssign -----")

    for index, assignment in enumerate(grouped_assignments, 1):
        prim_paths = assignment.get("prim_paths", [])
        material_path = assignment.get("material_path", "")
        relationship_name = assignment.get("relationship_name", "material:binding")

        if not prim_paths or not material_path:
            skipped.append("{} -> 缺少 prim_paths/material_path".format(index))
            continue

        if "collection:" in relationship_name:
            skipped.append("{} -> 暂不支持 collection binding: {}".format(index, ", ".join(prim_paths[:3])))
            continue

        node = NodegraphAPI.CreateNode("UsdMaterialAssign", root_node)
        node.setName("{}_{}_{}".format(
            STACK_NAME_PREFIX,
            index,
            sanitize_node_name(material_path.split("/")[-1] or "Material")
        ))
        NodegraphAPI.SetNodePosition(node, (NODE_X, NODE_Y_START + (index - 1) * NODE_Y_STEP))

        prim_ok = set_string_parameter(
            node,
            ("primPaths.i0", "args.primPaths.i0", "primPaths.0"),
            prim_paths[0],
        )
        prims_ok = set_string_array_parameter(
            node,
            ("primPaths", "primPaths.value", "args.primPaths", "args.primPaths.value"),
            prim_paths,
        )
        material_ok = set_string_parameter(
            node,
            ("materialAssign", "materialAssign.value", "args.materialAssign.value"),
            material_path,
        )

        # Some versions expose primPaths as an array widget and some only expose
        # the first child path immediately. Accept either path-writing strategy
        # as long as the material path lands successfully.
        if (not prim_ok and not prims_ok) or not material_ok:
            skipped.append("{} -> 参数写入失败: {}".format(index, material_path))
            try:
                NodegraphAPI.DeleteNode(node, root_node)
            except Exception:
                pass
            continue

        added_to_stack = add_node_to_group_stack(group_stack_node, node)
        if not added_to_stack:
            skipped.append("{} -> GroupStack 收纳失败: {}".format(index, material_path))
            try:
                NodegraphAPI.DeleteNode(node, root_node)
            except Exception:
                pass
            continue

        created_nodes.append(node)
        print("[成功] {} 个 prim -> {}".format(len(prim_paths), material_path))

    if upstream_node is not None:
        connect_nodes(upstream_node, group_stack_node)

    print("----- 导入结束 -----")
    print("成功创建 {} 个 UsdMaterialAssign 节点".format(len(created_nodes)))

    if skipped:
        print("以下链接未导入：")
        for item in skipped:
            print("  {}".format(item))

    return group_stack_node, created_nodes


group_stack, imported_nodes = import_houdini_material_assignments()
if imported_nodes:
    print("完成。GroupStack 节点名: {}".format(group_stack.getName()))
else:
    print("完成。未创建任何 UsdMaterialAssign 节点")
