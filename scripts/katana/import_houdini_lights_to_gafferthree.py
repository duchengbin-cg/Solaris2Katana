import json
import math
from Katana import NodegraphAPI, Plugins
import PackageSuperToolAPI.NodeUtils as NU

JSON_PATH = r"D:/houdini_lights_export.json"
GAFFER_NAME = "Houdini_Imported_Lights"
LIGHT_FILTER_GROUP_NAME = "_sharedLightFilters"

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

ARNOLD_LIGHT_FILTER_PARAM_ALIASES = {
    "slidemap": "slidemap",
    "filtermap": "slidemap",
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


def get_package_location_path(package):
    getter = getattr(package, "getLocationPath", None)
    if callable(getter):
        try:
            return getter()
        except Exception:
            pass

    package_node = get_package_node(package)
    for path in (
        "__gaffer.location",
        "packagePath",
        "location",
        "path",
    ):
        param = package_node.getParameter(path)
        if param is None:
            continue
        try:
            value = param.getValue(0)
        except Exception:
            value = None
        if value:
            return value
    return ""


def sanitize_package_name(name):
    text = str(name or "").strip()
    if not text:
        return "package"
    invalid_chars = "\\/:*?\"<>| "
    for ch in invalid_chars:
        text = text.replace(ch, "_")
    return text.strip("_") or "package"


def get_gaffer_package_classes():
    gaffer_api = getattr(Plugins, "GafferThreeAPI", None)
    if gaffer_api is None:
        return None
    return getattr(gaffer_api, "PackageClasses", None)


def ensure_filter_group_package(root_package):
    children_getter = getattr(root_package, "getChildPackages", None)
    if callable(children_getter):
        try:
            for child in children_getter():
                if child.getName() == LIGHT_FILTER_GROUP_NAME:
                    return child
        except Exception:
            pass

    package_classes = get_gaffer_package_classes()
    group_class = getattr(package_classes, "RigPackage", None) if package_classes else None
    if group_class is not None:
        try:
            return root_package.createChildPackage(group_class, LIGHT_FILTER_GROUP_NAME)
        except Exception:
            pass

    for class_name in ("RigPackage", "GroupPackage"):
        try:
            return root_package.createChildPackage(class_name, LIGHT_FILTER_GROUP_NAME)
        except Exception:
            pass

    return root_package


def create_package_opscript_node(light_package, node_name, lua_script):
    package_node = get_package_node(light_package)
    node = NodegraphAPI.CreateNode("OpScript", package_node)
    node.setName(node_name)
    cel_param = node.getParameter("CEL")
    if cel_param is not None:
        cel_param.setExpression("=^/__gaffer.location")
        cel_param.setExpressionFlag(True)
    script_param = node.getParameter("script.lua")
    if script_param is not None:
        script_param.setValue(lua_script, 0)
    try:
        NU.AppendNodes(package_node, (node,))
    except Exception as e:
        print("[FilterAttr] 附加 OpScript 失败: {}".format(e))
        try:
            NodegraphAPI.DeleteNode(node, package_node)
        except Exception:
            pass
        return None
    return node


def try_package_set_shader(light_package, shader_port_name, shader_name):
    set_shader = getattr(light_package, "setShader", None)
    if set_shader is None:
        return False

    try:
        set_shader(shader_port_name, shader_name)
        return True
    except Exception as e:
        print("[Shader] setShader({}, {}) 失败: {}".format(shader_port_name, shader_name, e))
        return False


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


def clamp01(value):
    try:
        return max(0.0, min(1.0, float(value)))
    except Exception:
        return 0.0


def kelvin_to_rgb(temperature_kelvin):
    """
    Approximate blackbody color in 0-1 RGB.
    This is a pragmatic fallback for KtoA light packages that do not expose
    Houdini's color temperature controls directly.
    """
    try:
        kelvin = max(1000.0, min(40000.0, float(temperature_kelvin)))
    except Exception:
        return [1.0, 1.0, 1.0]

    temp = kelvin / 100.0

    if temp <= 66.0:
        red = 255.0
        green = 99.4708025861 * math.log(temp) - 161.1195681661
        if temp <= 19.0:
            blue = 0.0
        else:
            blue = 138.5177312231 * math.log(temp - 10.0) - 305.0447927307
    else:
        red = 329.698727446 * ((temp - 60.0) ** -0.1332047592)
        green = 288.1221695283 * ((temp - 60.0) ** -0.0755148492)
        blue = 255.0

    return [
        clamp01(red / 255.0),
        clamp01(green / 255.0),
        clamp01(blue / 255.0),
    ]


def multiply_rgb(color_a, color_b):
    if not isinstance(color_a, (list, tuple)) or len(color_a) < 3:
        color_a = [1.0, 1.0, 1.0]
    if not isinstance(color_b, (list, tuple)) or len(color_b) < 3:
        color_b = [1.0, 1.0, 1.0]
    return [
        clamp01(color_a[0] * color_b[0]),
        clamp01(color_a[1] * color_b[1]),
        clamp01(color_a[2] * color_b[2]),
    ]


def apply_color_temperature_fallback(light_data):
    enabled = light_data.get("enable_color_temperature")
    if not enabled:
        return light_data

    kelvin = light_data.get("color_temperature")
    if kelvin in (None, ""):
        return light_data

    effective_data = dict(light_data)
    source_color = effective_data.get("color", [1.0, 1.0, 1.0])
    temperature_rgb = kelvin_to_rgb(kelvin)
    effective_data["color"] = multiply_rgb(source_color, temperature_rgb)
    print("[色温] {}K -> RGB {} 应用于 {}".format(
        kelvin,
        ["{:.4f}".format(channel) for channel in temperature_rgb],
        light_data.get("name", "light")
    ))
    return effective_data


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


def ensure_arnold_light_shader(light_package, material_node, shader_name):
    try_package_set_shader(light_package, "arnoldLight", shader_name)

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


def ensure_arnold_surface_shader(light_package, material_node, shader_name):
    try_package_set_shader(light_package, "arnoldSurface", shader_name)

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


def ensure_arnold_light_filter_shader(light_package, material_node, shader_name):
    try_package_set_shader(light_package, "arnoldLightFilter", shader_name)

    add_shader_type = getattr(material_node, "addShaderType", None)
    if add_shader_type is not None:
        try:
            add_shader_type("arnoldLightFilter")
        except Exception:
            pass

    check_dynamic = getattr(material_node, "checkDynamicParameters", None)
    if check_dynamic is not None:
        check_dynamic()

    shader_enable = material_node.getParameter("shaders.arnoldLightFilterShader.enable")
    shader_value = material_node.getParameter("shaders.arnoldLightFilterShader.value")

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


def has_meaningful_value(value):
    if value is None:
        return False
    if isinstance(value, str):
        return value.strip() != ""
    if isinstance(value, (list, tuple, dict)):
        return len(value) > 0
    return True


def set_direct_arnold_light_param(material_node, source_name, value):
    katana_name = ARNOLD_LIGHT_PARAM_ALIASES.get(source_name)
    if not katana_name:
        return False

    base_paths = [
        "material.arnoldLightParams.{}".format(katana_name),
        "shaders.arnoldLightParams.{}".format(katana_name),
        "material.parameters.{}".format(katana_name),
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
        "material.arnoldSurfaceParams.{}".format(katana_name),
        "shaders.arnoldSurfaceParams.{}".format(katana_name),
        "material.parameters.{}".format(katana_name),
        "shaders.parameters.{}".format(katana_name),
    ]

    for base in base_paths:
        if material_node.getParameter(base) or material_node.getParameter(base + ".value"):
            if set_enable_and_value(material_node, base, value):
                return True

    return False


def set_direct_arnold_light_filter_param(material_node, source_name, value):
    katana_name = ARNOLD_LIGHT_FILTER_PARAM_ALIASES.get(source_name, source_name)

    base_paths = [
        "material.arnoldLightFilterParams.{}".format(katana_name),
        "shaders.arnoldLightFilterParams.{}".format(katana_name),
        "material.parameters.{}".format(katana_name),
        "shaders.parameters.{}".format(katana_name),
    ]

    for base in base_paths:
        if material_node.getParameter(base) or material_node.getParameter(base + ".value"):
            if set_enable_and_value(material_node, base, value):
                return True

    return False


def normalize_shader_type_name(shader_info):
    info_id = str(shader_info.get("info_id", "") or "")
    if ":" in info_id:
        return info_id.split(":")[-1]
    return info_id


def build_shader_lookup(shader_list):
    lookup = {}
    for shader_info in shader_list:
        shader_path = shader_info.get("path")
        if shader_path:
            lookup[shader_path] = shader_info
    return lookup


def create_light_filter_package(parent_package, filter_name):
    package_classes = get_gaffer_package_classes()
    class_candidates = []
    if package_classes is not None:
        for attr_name in (
            "LightFilterPackage",
            "LightFilterGafferPackage",
        ):
            package_class = getattr(package_classes, attr_name, None)
            if package_class is not None:
                class_candidates.append(package_class)

    class_candidates.extend([
        "LightFilterPackage",
        "LightFilterGafferPackage",
    ])

    last_error = None
    for package_class in class_candidates:
        try:
            pkg = parent_package.createChildPackage(package_class, filter_name)
            if pkg:
                print("[Filter创建] {} 使用 package: {}".format(
                    filter_name,
                    getattr(package_class, "__name__", str(package_class))
                ))
                return pkg
        except Exception as e:
            last_error = e

    if last_error:
        raise RuntimeError("无法创建 light filter package: {}".format(last_error))
    raise RuntimeError("无法创建 light filter package")


def share_filter_with_light(filter_package, light_package, reference_name):
    share_method = getattr(filter_package, "shareWithLightPackages", None)
    if callable(share_method):
        try:
            share_method([light_package])
            return True
        except Exception as e:
            print("[Filter引用] shareWithLightPackages 失败: {}".format(e))

    package_classes = get_gaffer_package_classes()
    ref_candidates = []
    if package_classes is not None:
        for attr_name in (
            "LightFilterReferencePackage",
            "LightFilterReferenceGafferPackage",
        ):
            package_class = getattr(package_classes, attr_name, None)
            if package_class is not None:
                ref_candidates.append(package_class)
    ref_candidates.extend([
        "LightFilterReferencePackage",
        "LightFilterReferenceGafferPackage",
    ])

    filter_path = get_package_location_path(filter_package)
    if not filter_path:
        return False

    last_error = None
    for package_class in ref_candidates:
        try:
            ref_package = light_package.createChildPackage(package_class, reference_name)
            package_node = get_package_node(ref_package)
            candidate_params = [
                package_node.getParameter("referencePath"),
                package_node.getParameter("args.referencePath"),
            ]
            for node in iter_descendants(package_node):
                candidate_params.append(node.getParameter("referencePath"))
                candidate_params.append(node.getParameter("args.referencePath"))
            for param in candidate_params:
                if param is not None:
                    param.setValue(filter_path, 0)
                    return True
        except Exception as e:
            last_error = e

    if last_error:
        print("[Filter引用] 手动创建 reference 失败: {}".format(last_error))
    return False


def configure_filter_package_material(filter_package, filter_network):
    shader_list = filter_network.get("shaders", [])
    root_shader_path = filter_network.get("root_shader_path", "")
    if not shader_list or not root_shader_path:
        return False, "filter_network_missing_root"

    shader_lookup = build_shader_lookup(shader_list)
    root_shader = shader_lookup.get(root_shader_path)
    if not root_shader:
        return False, "filter_root_shader_not_found"

    filter_shader_name = normalize_shader_type_name(root_shader)
    if not filter_shader_name:
        return False, "filter_shader_name_empty"

    material_node = get_material_node(filter_package)
    if material_node is None:
        return False, "filter_material_node_missing"

    if not ensure_arnold_light_filter_shader(filter_package, material_node, filter_shader_name):
        return False, "filter_shader_setup_failed"

    direct_filter_values = {}
    unresolved = []

    for input_name, input_info in sorted(root_shader.get("inputs", {}).items()):
        connection = input_info.get("connection")
        if connection:
            source_path = connection.get("source_path", "")
            source_shader = shader_lookup.get(source_path)
            source_shader_type = normalize_shader_type_name(source_shader or {})

            if filter_shader_name == "gobo" and input_name in ("slidemap", "filtermap") and source_shader_type == "image":
                if ensure_arnold_surface_shader(filter_package, material_node, "image"):
                    for surface_input_name, surface_input_info in sorted(source_shader.get("inputs", {}).items()):
                        if surface_input_info.get("connection"):
                            unresolved.append("surface_connection:{}".format(surface_input_name))
                            continue
                        surface_value = surface_input_info.get("value")
                        if surface_value is None:
                            continue
                        ok = set_direct_arnold_surface_param(material_node, surface_input_name, surface_value)
                        if not ok:
                            unresolved.append("surface:{}".format(surface_input_name))
                else:
                    unresolved.append("surface_shader")
            else:
                unresolved.append("connection:{}".format(input_name))
            continue

        value = input_info.get("value")
        if value is None:
            continue

        direct_filter_values[input_name] = value
        ok = set_direct_arnold_light_filter_param(material_node, input_name, value)
        if not ok:
            unresolved.append(input_name)

    if unresolved:
        return False, "filter_params_unresolved:{}".format(", ".join(unresolved))

    return True, filter_shader_name


def ensure_shared_light_filter(root_package, filter_cache, filter_network):
    if not isinstance(filter_network, dict):
        return None, "filter_network_invalid"

    filter_key = (
        filter_network.get("source_path")
        or filter_network.get("root_shader_path")
        or ""
    )
    if not filter_key:
        return None, "filter_key_missing"

    cached = filter_cache.get(filter_key)
    if cached:
        return cached, None

    filter_parent_package = ensure_filter_group_package(root_package)
    filter_name = sanitize_package_name(filter_key.split("/")[-1] or "lightFilter")
    filter_package = create_light_filter_package(filter_parent_package, filter_name)
    ok, result = configure_filter_package_material(filter_package, filter_network)
    if not ok:
        return None, result

    filter_cache[filter_key] = filter_package
    print("[Filter共享] {} -> {}".format(filter_key, get_package_location_path(filter_package) or filter_name))
    return filter_package, None


def lua_quote_string(value):
    text = str(value)
    text = text.replace("\\", "\\\\")
    text = text.replace("\"", "\\\"")
    return "\"" + text + "\""


def build_lua_attribute_expression(value):
    if isinstance(value, bool):
        return "IntAttribute({})".format(1 if value else 0)
    if isinstance(value, int):
        return "IntAttribute({})".format(value)
    if isinstance(value, float):
        return "DoubleAttribute({})".format(repr(value))
    if isinstance(value, str):
        return "StringAttribute({})".format(lua_quote_string(value))
    return None


def apply_filter_attr_fallback(light_package, light_name, filter_shader_name, filter_values):
    if not filter_values:
        return False

    lines = [
        "-- Fallback injected by Solaris2Katana importer",
        "Interface.SetAttr(\"material.arnoldLightFilterShader\", StringAttribute({}))".format(
            lua_quote_string(filter_shader_name)
        ),
    ]

    written_count = 0
    for input_name, value in sorted(filter_values.items()):
        attr_expr = build_lua_attribute_expression(value)
        if attr_expr is None:
            continue
        lines.append(
            "Interface.SetAttr(\"material.arnoldLightFilterParams.{}\", {})".format(
                input_name,
                attr_expr,
            )
        )
        written_count += 1

    if written_count == 0:
        return False

    node = create_package_opscript_node(
        light_package,
        "SetLightFilterAttrs_{}".format(filter_shader_name),
        "\n".join(lines),
    )
    if node is None:
        return False

    print("[FilterAttr] {} -> {} 直写 {} 个 material.arnoldLightFilterParams.* 属性".format(
        light_name,
        filter_shader_name,
        written_count
    ))
    return True


def apply_filter_network(light_package, material_node, light_data, unmatched_params):
    filter_network = light_data.get("filter_network")
    if not isinstance(filter_network, dict):
        return

    shader_list = filter_network.get("shaders", [])
    root_shader_path = filter_network.get("root_shader_path", "")
    if not shader_list or not root_shader_path:
        return

    shader_lookup = build_shader_lookup(shader_list)
    root_shader = shader_lookup.get(root_shader_path)
    if not root_shader:
        return

    filter_shader_name = normalize_shader_type_name(root_shader)
    if not filter_shader_name:
        return

    light_name = light_data.get("name", "light")
    if not ensure_arnold_light_filter_shader(light_package, material_node, filter_shader_name):
        unmatched_params.setdefault(light_name, []).append("light_filter_shader")
        return

    print("[Filter] {} -> {}".format(light_name, filter_shader_name))

    direct_filter_values = {}
    unmatched_filter_input_names = []

    for input_name, input_info in sorted(root_shader.get("inputs", {}).items()):
        connection = input_info.get("connection")
        if connection:
            source_path = connection.get("source_path", "")
            source_shader = shader_lookup.get(source_path)
            source_shader_type = normalize_shader_type_name(source_shader or {})

            if filter_shader_name == "gobo" and input_name in ("slidemap", "filtermap") and source_shader_type == "image":
                if ensure_arnold_surface_shader(light_package, material_node, "image"):
                    for surface_input_name, surface_input_info in sorted(source_shader.get("inputs", {}).items()):
                        if surface_input_info.get("connection"):
                            unmatched_params.setdefault(light_name, []).append("filter_surface_connection:{}".format(surface_input_name))
                            continue
                        surface_value = surface_input_info.get("value")
                        if surface_value is None:
                            continue
                        ok = set_direct_arnold_surface_param(material_node, surface_input_name, surface_value)
                        if not ok:
                            unmatched_params.setdefault(light_name, []).append("filter_surface:{}".format(surface_input_name))
                else:
                    unmatched_params.setdefault(light_name, []).append("filter_surface_shader")
            else:
                unmatched_params.setdefault(light_name, []).append("filter_connection:{}".format(input_name))
            continue

        value = input_info.get("value")
        if value is None:
            continue

        direct_filter_values[input_name] = value
        ok = set_direct_arnold_light_filter_param(material_node, input_name, value)
        if not ok:
            unmatched_filter_input_names.append(input_name)

    if unmatched_filter_input_names:
        fallback_values = {
            input_name: direct_filter_values[input_name]
            for input_name in unmatched_filter_input_names
            if input_name in direct_filter_values
        }
        if apply_filter_attr_fallback(light_package, light_name, filter_shader_name, fallback_values):
            return

    for input_name in unmatched_filter_input_names:
        unmatched_params.setdefault(light_name, []).append("filter:{}".format(input_name))


def dump_existing_paths(material_node, light_name):
    interesting = [
        "material.arnoldLightShader.value",
        "material.arnoldLightParams",
        "material.arnoldSurfaceShader.value",
        "material.arnoldSurfaceParams",
        "material.arnoldLightFilterShader.value",
        "material.arnoldLightFilterParams",
        "shaders.arnoldLightShader.value",
        "shaders.arnoldLightParams",
        "shaders.arnoldSurfaceShader.value",
        "shaders.arnoldSurfaceParams",
        "shaders.arnoldLightFilterShader.value",
        "shaders.arnoldLightFilterParams",
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
    lights_with_filters = []
    filter_cache = {}

    print("----- 开始导入 Houdini 灯光到 GafferThree -----")

    for light_data in lights_data:
        light_data = apply_color_temperature_fallback(light_data)
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

            ensure_arnold_light_shader(light_package, material_node, shader_name)

            if shader_name == "skydome_light":
                ensure_arnold_surface_shader(light_package, material_node, "image")

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
                if not has_meaningful_value(value):
                    continue
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
                if not has_meaningful_value(param_value):
                    continue

                ok = set_direct_arnold_light_param(material_node, param_name, param_value)
                if not ok:
                    unmatched_params.setdefault(light_name, []).append(param_name)

            filter_network = light_data.get("filter_network")
            if isinstance(filter_network, dict):
                filter_package, filter_error = ensure_shared_light_filter(root_package, filter_cache, filter_network)
                if filter_package is not None:
                    reference_name = sanitize_package_name((filter_network.get("source_path") or "lightFilterRef").split("/")[-1])
                    shared = share_filter_with_light(filter_package, light_package, reference_name)
                    if not shared:
                        unmatched_params.setdefault(light_name, []).append("filter_reference")
                elif filter_error:
                    unmatched_params.setdefault(light_name, []).append(filter_error)

            filter_paths = light_data.get("filter_paths", [])
            if filter_paths:
                lights_with_filters.append((light_name, filter_paths))

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

    if lights_with_filters:
        print("以下灯检测到 Houdini light filters 路径：")
        for light_name, filter_paths in lights_with_filters:
            print("  {} : {}".format(light_name, ", ".join(filter_paths)))

    return gaffer_node


imported_gaffer = import_houdini_lights()
print("完成。GafferThree 节点名: {}".format(imported_gaffer.getName()))
