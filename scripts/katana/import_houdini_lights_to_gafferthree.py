import json
from Katana import NodegraphAPI
import PackageSuperToolAPI.NodeUtils as NU

JSON_PATH = r"D:/houdini_lights_export.json"
GAFFER_NAME = "Houdini_Imported_Lights"

USD_TO_ARNOLD_SHADER = {
    "RectLight": "quad_light",
    "DiskLight": "disk_light",
    "DistantLight": "distant_light",
    "SphereLight": "point_light",
    "DomeLight": "skydome_light",
    "CylinderLight": "cylinder_light",
}

SHADER_TO_PACKAGE_CLASS_CANDIDATES = {
    "quad_light": [
        "ArnoldQuadLightPackage",
        "ArnoldQuadLightGafferPackage",
        "ArnoldRectLightPackage",
        "ArnoldRectLightGafferPackage",
    ],
    "disk_light": [
        "ArnoldDiskLightPackage",
        "ArnoldDiskLightGafferPackage",
    ],
    "distant_light": [
        "ArnoldDistantLightPackage",
        "ArnoldDistantLightGafferPackage",
    ],
    "point_light": [
        "ArnoldPointLightPackage",
        "ArnoldPointLightGafferPackage",
        "ArnoldSphereLightPackage",
        "ArnoldSphereLightGafferPackage",
    ],
    "skydome_light": [
        "ArnoldHDRISkydomeLightPackage",
        "ArnoldSkyDomeLightPackage",
        "ArnoldSkydomeLightPackage",
        "ArnoldSkyDomeLightGafferPackage",
    ],
    "cylinder_light": [
        "ArnoldCylinderLightPackage",
        "ArnoldCylinderLightGafferPackage",
    ],
}

ARNOLD_LIGHT_PARAM_ALIASES = {
    "intensity": "intensity",
    "exposure": "exposure",
    "color": "color",
    "normalize": "normalize",
    "diffuse": "diffuse",
    "specular": "specular",
    "radius": "radius",
    "length": "length",
    "samples": "samples",
    "volume_samples": "volume_samples",
    "aov": "aov",
    "spread": "spread",
    "camera": "camera",
    "transmission": "transmission",
    "indirect": "indirect",
    "volume": "volume",
    "sss": "sss",
    "shadow_density": "shadow_density",
    "cast_volumetric_shadows": "cast_volumetric_shadows",
    "max_bounces": "max_bounces",
    "resolution": "resolution",
}

ARNOLD_SURFACE_PARAM_ALIASES = {
    "texture_file": "filename",
}

IGNORED_PARAMS = {
    "angle",
    "soft_edge",
    "texture_format",
}

QUAD_WIDTH_MULTIPLIER = 1.0
QUAD_HEIGHT_MULTIPLIER = 1.0


def load_json_with_better_error(json_path):
    with open(json_path, "r", encoding="utf-8") as f:
        text = f.read()

    try:
        return json.loads(text)
    except json.JSONDecodeError as e:
        lines = text.splitlines()
        bad_line = ""
        if 1 <= e.lineno <= len(lines):
            bad_line = lines[e.lineno - 1]
        raise RuntimeError(
            "JSON 文件格式错误: line {}, column {}.\n问题行内容: {}".format(
                e.lineno, e.colno, bad_line
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


def get_package_node(light_package):
    getter = getattr(light_package, "getPackageNode", None)
    if getter is None:
        raise RuntimeError("当前 package 对象没有 getPackageNode()")
    return getter()


def get_ref_node(package_node, ref_name):
    try:
        node = NU.GetRefNode(package_node, ref_name)
        if node:
            return node
    except Exception:
        pass
    return None


def find_first_node(package_node, node_types=None, param_paths=None):
    node_types = node_types or []
    param_paths = param_paths or []

    for node in iter_descendants(package_node):
        try:
            if node_types and node.getType() in node_types:
                return node
            for path in param_paths:
                if node.getParameter(path):
                    return node
        except Exception:
            pass
    return None


def get_create_node(light_package):
    package_node = get_package_node(light_package)

    node = get_ref_node(package_node, "create")
    if node:
        return node

    return find_first_node(
        package_node,
        node_types=["LightCreate", "PrimitiveCreate"],
        param_paths=["transform.translate.x", "transform.translate.i0"]
    )


def get_material_node(light_package):
    package_node = get_package_node(light_package)

    node = get_ref_node(package_node, "material")
    if node:
        return node

    return find_first_node(
        package_node,
        node_types=["Material"],
        param_paths=["shaders.arnoldLightShader.value", "shaders"]
    )


def set_param_value(param, value):
    if param is None:
        return False

    if isinstance(value, bool):
        param.setValue(1 if value else 0, 0)
    elif isinstance(value, (int, float)):
        param.setValue(value, 0)
    else:
        param.setValue(str(value), 0)

    return True


def break_expression(param):
    """Disable existing expressions so explicit scale values can override them."""
    if param is None:
        return
    try:
        if param.isExpression():
            try:
                old_expr = param.getExpression()
                print("[表达式] 断开 {} <- {}".format(param.getFullName(), old_expr))
            except Exception:
                pass
            param.setExpressionFlag(False)
    except Exception:
        pass


def get_xyz_params(node, base_path):
    for suffixes in (("x", "y", "z"), ("i0", "i1", "i2"), ("X", "Y", "Z")):
        params = [
            node.getParameter(base_path + "." + suffixes[0]),
            node.getParameter(base_path + "." + suffixes[1]),
            node.getParameter(base_path + "." + suffixes[2]),
        ]
        if all(params):
            return params
    return [None, None, None]


def set_xyz(node, base_path, values, break_expressions=False):
    if not values or len(values) < 3:
        return False

    values = list(values[:3])
    params = get_xyz_params(node, base_path)

    if not all(params):
        return False

    if break_expressions:
        for param in params:
            break_expression(param)

    for param, value in zip(params, values):
        set_param_value(param, value)

    return True


def resolve_shader_name(light_data):
    usd_type = str(light_data.get("usd_type", ""))
    raw_type = str(light_data.get("usd_type_raw", ""))
    shader_id = str(light_data.get("shader_id", "")).lower()
    light_shader_id = str(light_data.get("light_shader_id", "")).lower()
    light_name = str(light_data.get("name", "")).lower()
    texture_file = light_data.get("texture_file")

    if usd_type == "DomeLight":
        return "skydome_light"

    joined = " ".join([usd_type, raw_type, shader_id, light_shader_id, light_name]).lower()
    if "dome" in joined or "skydome" in joined:
        return "skydome_light"

    if texture_file and any(x in light_name for x in ("dome", "hdri", "sky", "env")):
        return "skydome_light"

    if usd_type == "SphereLight":
        return "point_light"

    return USD_TO_ARNOLD_SHADER.get(usd_type, "quad_light")


def create_light_package(root_package, light_name, shader_name):
    candidates = SHADER_TO_PACKAGE_CLASS_CANDIDATES.get(shader_name, [])
    candidates = list(candidates) + ["LightPackage"]

    last_error = None
    for package_class_name in candidates:
        try:
            pkg = root_package.createChildPackage(package_class_name, light_name)
            if pkg:
                print("[创建] {} 使用 package: {}".format(light_name, package_class_name))
                return pkg, package_class_name
        except Exception as e:
            last_error = e

    if last_error:
        raise RuntimeError("无法创建灯 package: {}".format(last_error))
    raise RuntimeError("无法创建灯 package")


def ensure_arnold_light_shader(material_node, shader_name):
    add_shader_type = getattr(material_node, "addShaderType", None)
    if add_shader_type is not None:
        try:
            add_shader_type("arnoldLight")
        except Exception:
            pass

    check_dynamic = getattr(material_node, "checkDynamicParameters", None)
    if check_dynamic is not None:
        check_dynamic()

    shader_enable = material_node.getParameter("shaders.arnoldLightShader.enable")
    shader_value = material_node.getParameter("shaders.arnoldLightShader.value")

    if shader_enable is None or shader_value is None:
        raise RuntimeError("Material 节点上没有 arnoldLightShader，KtoA 可能未正确加载。")

    shader_enable.setValue(1, 0)
    shader_value.setValue(shader_name, 0)

    if check_dynamic is not None:
        check_dynamic()


def ensure_arnold_surface_shader(material_node, shader_name):
    add_shader_type = getattr(material_node, "addShaderType", None)
    if add_shader_type is not None:
        try:
            add_shader_type("arnoldSurface")
        except Exception:
            pass

    check_dynamic = getattr(material_node, "checkDynamicParameters", None)
    if check_dynamic is not None:
        check_dynamic()

    shader_enable = material_node.getParameter("shaders.arnoldSurfaceShader.enable")
    shader_value = material_node.getParameter("shaders.arnoldSurfaceShader.value")

    if shader_enable is None or shader_value is None:
        return False

    shader_enable.setValue(1, 0)
    shader_value.setValue(shader_name, 0)

    if check_dynamic is not None:
        check_dynamic()

    return True


def set_enable_and_value(material_node, base_path, value):
    enable_param = material_node.getParameter(base_path + ".enable")
    value_param = material_node.getParameter(base_path + ".value")

    if enable_param is not None:
        enable_param.setValue(1, 0)

    if value_param is None:
        group_param = material_node.getParameter(base_path)
        if group_param is not None:
            if isinstance(value, (list, tuple)):
                ok = False
                for i, item in enumerate(value):
                    child = group_param.getChild("i{}".format(i))
                    if child is not None:
                        set_param_value(child, item)
                        ok = True
                return ok
            return set_param_value(group_param, value)
        return False

    if isinstance(value, (list, tuple)):
        ok = False
        for i, item in enumerate(value):
            child = value_param.getChild("i{}".format(i))
            if child is not None:
                set_param_value(child, item)
                ok = True
        return ok

    return set_param_value(value_param, value)


def set_direct_arnold_light_param(material_node, source_name, value):
    katana_name = ARNOLD_LIGHT_PARAM_ALIASES.get(source_name)
    if not katana_name:
        return False

    base_paths = [
        "shaders.arnoldLightParams.{}".format(katana_name),
        "shaders.parameters.{}".format(katana_name),
    ]

    for base in base_paths:
        if material_node.getParameter(base) or material_node.getParameter(base + ".value"):
            if set_enable_and_value(material_node, base, value):
                return True

    return False


def set_direct_arnold_surface_param(material_node, source_name, value):
    katana_name = ARNOLD_SURFACE_PARAM_ALIASES.get(source_name)
    if not katana_name:
        return False

    base_paths = [
        "shaders.arnoldSurfaceParams.{}".format(katana_name),
        "shaders.parameters.{}".format(katana_name),
    ]

    for base in base_paths:
        if material_node.getParameter(base) or material_node.getParameter(base + ".value"):
            if set_enable_and_value(material_node, base, value):
                return True

    return False


def dump_existing_paths(material_node, light_name):
    interesting = [
        "shaders.arnoldLightShader.value",
        "shaders.arnoldLightParams",
        "shaders.arnoldSurfaceShader.value",
        "shaders.arnoldSurfaceParams",
    ]
    found = []
    for path in interesting:
        if material_node.getParameter(path):
            found.append(path)
    print("[结构] {} -> {}".format(light_name, ", ".join(found)))


def should_ignore_param(light_data, shader_name, param_name):
    if param_name in IGNORED_PARAMS:
        return True

    if shader_name == "quad_light" and param_name in ("width", "height"):
        return True

    if light_data.get("usd_type") == "SphereLight" and shader_name == "point_light" and param_name == "spread":
        return True

    return False


def apply_quad_size_to_scale(light_data, shader_name, scale):
    """
    Quad light size handling based on current production observations:
    - scale.x -> width
    - scale.y -> height
    - scale.z -> keep default as 1
    """
    if shader_name != "quad_light":
        return list(scale)

    width = light_data.get("width", 1.0)
    height = light_data.get("height", 1.0)

    try:
        width = float(width if width is not None else 1.0)
    except Exception:
        width = 1.0

    try:
        height = float(height if height is not None else 1.0)
    except Exception:
        height = 1.0

    return [
        width * QUAD_WIDTH_MULTIPLIER,
        height * QUAD_HEIGHT_MULTIPLIER,
        1.0,
    ]


def import_houdini_lights():
    lights_data = load_json_with_better_error(JSON_PATH)

    root_node = NodegraphAPI.GetRootNode()

    gaffer_node = NodegraphAPI.CreateNode("GafferThree", root_node)
    gaffer_node.setName(GAFFER_NAME)
    NodegraphAPI.SetNodePosition(gaffer_node, (0, 200))

    root_package = gaffer_node.getRootPackage()

    created_count = 0
    failed_lights = []
    unmatched_params = {}
    fallback_lights = []

    print("----- 开始导入 Houdini 灯光到 GafferThree -----")

    for light_data in lights_data:
        light_name = light_data.get("name") or "importedLight"
        shader_name = resolve_shader_name(light_data)

        try:
            light_package, package_class_name = create_light_package(root_package, light_name, shader_name)
            if package_class_name == "LightPackage":
                fallback_lights.append(light_name)

            create_node = get_create_node(light_package)
            material_node = get_material_node(light_package)

            if create_node is None:
                raise RuntimeError("找不到 create 节点")
            if material_node is None:
                raise RuntimeError("找不到 material 节点")

            translate = light_data.get("translate", [0.0, 0.0, 0.0])
            rotate = light_data.get("rotate", [0.0, 0.0, 0.0])
            scale = list(light_data.get("scale", [1.0, 1.0, 1.0]))

            if not set_xyz(create_node, "transform.translate", translate):
                print("[警告] {} translate 写入失败".format(light_name))
            if not set_xyz(create_node, "transform.rotate", rotate):
                print("[警告] {} rotate 写入失败".format(light_name))

            scale = apply_quad_size_to_scale(light_data, shader_name, scale)

            break_scale_expr = (shader_name == "quad_light")
            if not set_xyz(create_node, "transform.scale", scale, break_expressions=break_scale_expr):
                print("[警告] {} scale 写入失败".format(light_name))

            ensure_arnold_light_shader(material_node, shader_name)

            if shader_name == "skydome_light":
                ensure_arnold_surface_shader(material_node, "image")

            material_node.checkDynamicParameters()
            dump_existing_paths(material_node, light_name)

            standard_param_names = [
                "intensity",
                "exposure",
                "color",
                "normalize",
                "diffuse",
                "specular",
                "width",
                "height",
                "radius",
                "length",
                "angle",
                "texture_file",
                "texture_format",
            ]

            for param_name in standard_param_names:
                if param_name not in light_data:
                    continue

                if should_ignore_param(light_data, shader_name, param_name):
                    continue

                value = light_data[param_name]
                ok = False

                if param_name == "texture_file":
                    ok = set_direct_arnold_surface_param(material_node, param_name, value)
                else:
                    ok = set_direct_arnold_light_param(material_node, param_name, value)

                if not ok:
                    unmatched_params.setdefault(light_name, []).append(param_name)

            arnold_params = light_data.get("arnold_params", {})
            for param_name, param_value in arnold_params.items():
                if param_name == "shaders":
                    continue

                if should_ignore_param(light_data, shader_name, param_name):
                    continue

                ok = set_direct_arnold_light_param(material_node, param_name, param_value)
                if not ok:
                    unmatched_params.setdefault(light_name, []).append(param_name)

            created_count += 1
            print("[成功] {} -> {}".format(light_name, shader_name))

        except Exception as e:
            failed_lights.append(light_name)
            print("[失败] {} : {}".format(light_name, e))

    print("----- 导入结束 -----")
    print("成功创建 {} 盏灯".format(created_count))

    if failed_lights:
        print("创建失败的灯: {}".format(", ".join(failed_lights)))

    if fallback_lights:
        print("以下灯回退成了 LightPackage，所以 UI 可能仍显示为 Katana 原生灯：")
        print("  {}".format(", ".join(fallback_lights)))

    if unmatched_params:
        print("以下参数在当前 KtoA 版本中未成功匹配：")
        for light_name in sorted(unmatched_params.keys()):
            names = sorted(set(unmatched_params[light_name]))
            print("  {} : {}".format(light_name, ", ".join(names)))

    return gaffer_node


imported_gaffer = import_houdini_lights()
print("完成。GafferThree 节点名: {}".format(imported_gaffer.getName()))
