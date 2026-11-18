"""Parse WCRF recipe markdown files from the Obsidian vault into a JSON dataset."""

import ast
import glob
import json
import os
import re

import yaml

VAULT_DIR = "/Users/shrho/Documents/Obsidian Vault/WCRF Recipes"
RECIPES_GLOB = os.path.join(VAULT_DIR, "Recipes", "*", "*.md")
OUTPUT_JSON = os.path.join(os.path.dirname(__file__), "recipes.json")

NON_CATEGORY_TAGS = {"wcrf", "recipe", "cancer-patients"}

CATALOGUE_JSON = os.path.join(os.path.dirname(__file__), "ingredients_catalogue.json")

# Keywords used to assign a season to an ingredient. Checked in order;
# the first matching season wins. Ingredients matching none default to "all year".
SEASON_KEYWORDS = {
    "spring": ["asparagus", "pea", "spinach", "strawberry", "radish", "artichoke",
               "lettuce", "arugula", "chive", "fennel", "rhubarb", "apricot", "cherry",
               "morel", "scallion", "spring onion", "new potato"],
    "summer": ["tomato", "zucchini", "corn", "peach", "nectarine", "plum",
               "blueberry", "raspberry", "blackberry", "watermelon", "cucumber",
               "eggplant", "basil", "bell pepper", "green bean", "cantaloupe",
               "honeydew", "fig", "chili", "chilli", "aubergine"],
    "fall": ["pumpkin", "squash", "apple", "pear", "sweet potato", "brussels sprout",
             "kale", "cranberry", "mushroom", "grape", "pomegranate", "cauliflower",
             "broccoli", "cabbage", "carrot", "beet", "turnip", "parsnip", "butternut",
             "acorn"],
    "winter": ["orange", "lemon", "grapefruit", "leek", "rutabaga", "celeriac",
               "turnip", "parsnip", "potato", "cabbage", "brussels sprout"],
}


def season_for_ingredient(ingredient_text):
    """Return a season value for an ingredient based on keyword matching."""
    text = ingredient_text.lower()
    for season, keywords in SEASON_KEYWORDS.items():
        for kw in keywords:
            if kw in text:
                return season
    return "all year"


def build_catalogue(recipes):
    """Build a deduplicated ingredient catalogue with season assignments."""
    seen = {}
    for r in recipes:
        for ing in r.get("ingredients", []):
            key = ing.strip().lower()
            if not key or key in seen:
                continue
            seen[key] = {
                "ingredient": ing.strip(),
                "season": season_for_ingredient(ing),
            }
    return list(seen.values())


def parse_frontmatter_block(block):
    """Parse YAML frontmatter, converting stringified list fields to real lists."""
    data = yaml.safe_load(block) or {}
    for key in ("tags", "categories"):
        val = data.get(key)
        if isinstance(val, str):
            try:
                data[key] = ast.literal_eval(val)
            except (ValueError, SyntaxError):
                data[key] = [t.strip().strip("'\"") for t in val.strip("[]").split(",") if t.strip()]
    return data


def split_english_korean(body, title):
    """Split body into English and Korean sections.

    The Korean section begins at a header like `## Title (한글제목)`.
    """
    lines = body.split("\n")
    split_idx = None
    for i, line in enumerate(lines):
        if line.startswith("## ") and "(" in line and ")" in line:
            split_idx = i
            break
    if split_idx is None:
        return body, ""
    return "\n".join(lines[:split_idx]), "\n".join(lines[split_idx:])


def extract_section(english_body, header_name):
    """Extract list items under a given ## header from the English body."""
    lines = english_body.split("\n")
    items = []
    capturing = False
    for line in lines:
        if line.strip().lower() == f"## {header_name.lower()}":
            capturing = True
            continue
        if capturing:
            if line.startswith("## "):
                break
            stripped = line.strip()
            if stripped:
                # Remove leading list markers (- or 1. )
                cleaned = re.sub(r"^[-*]\s+", "", stripped)
                cleaned = re.sub(r"^\d+\.\s+", "", cleaned)
                if cleaned:
                    items.append(cleaned)
    return items


def extract_description(english_body):
    """Pull the blockquote description (>...) from the English body."""
    for line in english_body.split("\n"):
        stripped = line.strip()
        if stripped.startswith(">"):
            return stripped.lstrip(">").strip()
    return ""


def folder_name_from_path(path):
    """Return the Recipes/<folder> segment, e.g. 'Cancer-Patients'."""
    parts = path.split(os.sep)
    if "Recipes" in parts:
        idx = parts.index("Recipes")
        if idx + 1 < len(parts):
            return parts[idx + 1]
    return ""


def parse_recipe(path):
    with open(path, "r", encoding="utf-8") as f:
        content = f.read()

    # Split frontmatter
    if content.startswith("---\n"):
        parts = content.split("---\n", 2)
        frontmatter = parts[1]
        body = parts[2] if len(parts) > 2 else ""
    else:
        frontmatter, body = "", content

    meta = parse_frontmatter_block(frontmatter)
    title = meta.get("title", "")
    title_ko = meta.get("title_ko", "")

    english_body, korean_body = split_english_korean(body, title)

    tags = meta.get("tags", []) or []
    categories = meta.get("categories", []) or []

    # Use the vault folder as the canonical category (matches the Index organization).
    # Fall back to the topical tag if the folder can't be determined.
    category = folder_name_from_path(path)
    if category:
        category = category.lower()
    if not category:
        for t in tags:
            if t not in NON_CATEGORY_TAGS:
                category = t
                break
    if not category and categories:
        for c in categories:
            if c not in NON_CATEGORY_TAGS:
                category = c
                break

    slug = os.path.splitext(os.path.basename(path))[0]

    return {
        "id": slug,
        "title": title,
        "title_ko": title_ko,
        "category": category,
        "categories": categories,
        "tags": tags,
        "servings": meta.get("servings", ""),
        "image": meta.get("image", ""),
        "source": meta.get("source", ""),
        "source_url": meta.get("source_url", ""),
        "description": extract_description(english_body),
        "ingredients": extract_section(english_body, "Ingredients"),
        "method": extract_section(english_body, "Method"),
        "korean_body": korean_body.strip(),
    }


def main():
    paths = sorted(glob.glob(RECIPES_GLOB))
    recipes = [parse_recipe(p) for p in paths]
    with open(OUTPUT_JSON, "w", encoding="utf-8") as f:
        json.dump(recipes, f, ensure_ascii=False, indent=2)
    print(f"Parsed {len(recipes)} recipes -> {OUTPUT_JSON}")

    catalogue = build_catalogue(recipes)
    with open(CATALOGUE_JSON, "w", encoding="utf-8") as f:
        json.dump(catalogue, f, ensure_ascii=False, indent=2)
    print(f"Built ingredient catalogue with {len(catalogue)} ingredients -> {CATALOGUE_JSON}")


if __name__ == "__main__":
    main()
