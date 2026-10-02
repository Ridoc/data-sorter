from pathlib import Path
from typing import Dict, List, Optional
import yaml


def load_taxonomy(config_path: Path) -> Dict:
    with open(config_path) as f:
        cfg = yaml.safe_load(f)
    return cfg.get("taxonomy", {})


def taxonomy_to_yaml(taxonomy: Dict) -> str:
    top_level = taxonomy.get("top_level", [])
    categories = taxonomy.get("categories", {})
    lines = ["top_level:"]
    for folder in top_level:
        lines.append(f"  - {folder}")
    lines.append("categories:")
    for parent, children in categories.items():
        if children:
            lines.append(f"  {parent}:")
            for child in children:
                lines.append(f"    - {child}")
        else:
            lines.append(f"  {parent}: []")
    return "\n".join(lines)


def get_taxonomy_yaml_string(config_path: Path) -> str:
    return taxonomy_to_yaml(load_taxonomy(config_path))


def validate_category_path(category_path: str, taxonomy: Dict) -> bool:
    top_level = taxonomy.get("top_level", [])
    if not category_path:
        return False
    parts = category_path.split("/", 1)
    return parts[0] in top_level


def is_adobe_premiere_temp(name: str) -> bool:
    """Redirect to context module."""
    from sorter.context import detect_adobe_premiere
    return detect_adobe_premiere(name)


def resolve_category(llm_result: Dict, taxonomy: Dict) -> Dict:
    cat_path = llm_result.get("category_path", "")
    top_level = taxonomy.get("top_level", [])
    parts = cat_path.split("/", 1)

    if cat_path == "DELETE":
        return {**llm_result, "action": "delete"}

    if not cat_path or parts[0] not in top_level:
        return {**llm_result, "action": "review"}

    # Case-normalize child segment: if the LLM returned "Media/videos" but the
    # taxonomy has "Media/Videos", use the canonical casing to avoid creating
    # duplicate subfolders.
    if len(parts) > 1:
        known_children = taxonomy.get("categories", {}).get(parts[0], [])
        child = parts[1]
        canonical = next((k for k in known_children if k.lower() == child.lower()), None)
        if canonical and canonical != child:
            cat_path = f"{parts[0]}/{canonical}"
            llm_result = {**llm_result, "category_path": cat_path}
            parts = cat_path.split("/", 1)

    # Route Archives/Old_Projects for old user content
    if cat_path.startswith("Archives/"):
        return {**llm_result, "action": "move"}

    if len(parts) > 1:
        known_children = taxonomy.get("categories", {}).get(parts[0], [])
        if parts[1] not in known_children:
            return {**llm_result, "action": "move", "is_new": True}
    return {**llm_result, "action": "move"}