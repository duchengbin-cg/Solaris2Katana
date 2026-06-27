import json
import os
import hou
from pxr import Sdf

# ============================================================
# Python Script LOP
# Export authored USD material bindings from the current stage
# ============================================================

node = hou.pwd()
stage = node.editableStage()

output_path = hou.expandString("$HIP/houdini_material_assignments_export.json")


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


def parse_binding_purpose(relationship_name):
    if relationship_name == "material:binding":
        return "allPurpose"

    prefix = "material:binding:"
    if relationship_name.startswith(prefix):
        suffix = relationship_name[len(prefix):]
        if suffix and not suffix.startswith("collection:"):
            return suffix
    return ""


LIST_OP_ATTRS = (
    "explicitItems",
    "prependedItems",
    "appendedItems",
    "addedItems",
    "orderedItems",
)


def iter_prim_specs(prim_spec):
    if prim_spec is None:
        return
    yield prim_spec
    for child_prim_spec in prim_spec.nameChildren.values():
        for nested_prim_spec in iter_prim_specs(child_prim_spec):
            yield nested_prim_spec


def iter_layer_prim_specs(layer):
    for root_prim_spec in layer.rootPrims:
        for prim_spec in iter_prim_specs(root_prim_spec):
            yield prim_spec


def extract_target_paths_from_list_op(path_list_op):
    target_paths = []
    for attr_name in LIST_OP_ATTRS:
        try:
            items = getattr(path_list_op, attr_name)
        except Exception:
            items = []
        for item in items:
            item_text = safe_text_value(item)
            if item_text and item_text not in target_paths:
                target_paths.append(item_text)
    return target_paths


def collect_authored_material_assignments_from_layer_stack(usd_stage):
    assignments = []
    seen_binding_keys = set()
    layer_identifiers = []

    # We intentionally inspect authored layer specs instead of traversing the
    # composed stage, so class/material opinions stay compact and do not expand
    # to every laid out or instanced prim in the scene.
    for layer in usd_stage.GetLayerStack(includeSessionLayers=False):
        if layer is None:
            continue

        layer_identifier = safe_text_value(getattr(layer, "identifier", ""))
        if layer_identifier:
            layer_identifiers.append(layer_identifier)

        for prim_spec in iter_layer_prim_specs(layer):
            prim_path = safe_text_value(prim_spec.path)
            prim_type_name = safe_text_value(getattr(prim_spec, "typeName", ""))
            for property_spec in prim_spec.properties.values():
                if not isinstance(property_spec, Sdf.RelationshipSpec):
                    continue

                relationship_name = safe_text_value(property_spec.name)
                if not relationship_name.startswith("material:binding"):
                    continue

                binding_key = (prim_path, relationship_name)
                if binding_key in seen_binding_keys:
                    continue

                target_paths = extract_target_paths_from_list_op(property_spec.targetPathList)
                if not target_paths:
                    continue

                seen_binding_keys.add(binding_key)
                assignments.append({
                    "prim_path": prim_path,
                    "material_path": target_paths[0],
                    "all_target_paths": target_paths,
                    "relationship_name": relationship_name,
                    "binding_purpose": parse_binding_purpose(relationship_name),
                    "prim_type": prim_type_name,
                    "source_layer": layer_identifier,
                    "source_specifier": safe_text_value(getattr(prim_spec, "specifier", "")),
                })

    assignments.sort(key=lambda item: (item["material_path"], item["relationship_name"], item["prim_path"]))
    return assignments, layer_identifiers


assignments, source_layers = collect_authored_material_assignments_from_layer_stack(stage)

export_payload = {
    "source": "houdini_lop_material_assignments",
    "mode": "authored_layer_material_bindings",
    "assignments": assignments,
    "source_layers": source_layers,
}

output_dir = os.path.dirname(output_path)
if output_dir and not os.path.exists(output_dir):
    os.makedirs(output_dir)

with open(output_path, "w") as f:
    json.dump(export_payload, f, indent=2, sort_keys=True)

print("已导出 {} 条材质链接 -> {}".format(len(export_payload["assignments"]), output_path))
print("导出模式: 仅 authored material:binding layer specs，不展平 layout/instance 场景")
