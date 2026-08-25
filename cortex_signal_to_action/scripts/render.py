#!/usr/bin/env python3
"""
Render Jinja2 SQL templates against a per-environment config YAML.

Usage:
    pip install -r scripts/requirements.txt
    python scripts/render.py dev                                    # render all setup templates
    python scripts/render.py dev 01_databases                       # render one setup template
    python scripts/render.py dev --subdir observability             # render all observability templates
    python scripts/render.py dev 01_observability --subdir observability  # render one observability template

Output:
    snowflake/<subdir>/rendered/<template>.sql   (gitignored)

Convention:
    All Snowflake SQL lives as *.sql.j2 templates in snowflake/<subdir>/.
    Never edit the rendered/ output directly — edit the .j2 template and re-render.
    Config values (database names, role names, RSA keys) all come from config/<env>.yaml.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

import yaml
from jinja2 import Environment, FileSystemLoader, StrictUndefined, UndefinedError


def load_config(env: str) -> dict:
    """Load config/<env>.yaml relative to the project root."""
    root = Path(__file__).parent.parent
    config_path = root / "config" / f"{env}.yaml"
    if not config_path.exists():
        print(f"Error: {config_path} not found", file=sys.stderr)
        sys.exit(1)
    with open(config_path) as f:
        return yaml.safe_load(f)


def render(env: str, template_filter: str | None = None, subdir: str = "setup") -> None:
    """Render matching .sql.j2 templates and write to snowflake/<subdir>/rendered/.

    Args:
        env: Target environment — dev, uat, or prod.
        template_filter: Optional substring filter on template file name.
        subdir: Subdirectory under snowflake/ containing the templates (default: setup).
    """
    root = Path(__file__).parent.parent
    config = load_config(env)

    template_dir = root / "snowflake" / subdir
    if not template_dir.is_dir():
        print(f"Error: template directory {template_dir} not found", file=sys.stderr)
        sys.exit(1)

    rendered_dir = template_dir / "rendered"
    rendered_dir.mkdir(exist_ok=True)

    jinja_env = Environment(
        loader=FileSystemLoader(str(template_dir)),
        keep_trailing_newline=True,
        undefined=StrictUndefined,  # surface missing variables immediately
    )

    templates = sorted(template_dir.glob("*.sql.j2"))
    if template_filter:
        templates = [t for t in templates if template_filter in t.name]

    if not templates:
        print(f"No .sql.j2 templates matched filter: {template_filter!r}", file=sys.stderr)
        sys.exit(1)

    rendered_count = 0
    for template_path in templates:
        try:
            template = jinja_env.get_template(template_path.name)
            output = template.render(config=config)
        except UndefinedError as exc:
            print(f"  ERROR in {template_path.name}: {exc}", file=sys.stderr)
            print(f"  Check that all template variables exist in config/{env}.yaml", file=sys.stderr)
            sys.exit(1)

        output_path = rendered_dir / template_path.stem  # strips .j2 → keeps .sql
        output_path.write_text(output)
        print(f"  {template_path.name:40s} → rendered/{template_path.stem}")
        rendered_count += 1

    print(f"\n{rendered_count} template(s) rendered for environment '{env}' (subdir: {subdir}).")
    print(f"Output: {rendered_dir}")
    if any("REPLACE_WITH_" in v for v in _flatten_values(config)):
        print("\nWARNING: config contains unfilled REPLACE_WITH_* placeholders.")
        print(f"         Fill in config/{env}.yaml before running the rendered SQL.")


def _flatten_values(obj: object) -> list[str]:
    """Recursively collect all string leaf values from a nested dict/list."""
    if isinstance(obj, dict):
        return [v for child in obj.values() for v in _flatten_values(child)]
    if isinstance(obj, list):
        return [v for item in obj for v in _flatten_values(item)]
    return [str(obj)] if obj is not None else []


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Render Jinja2 SQL templates from config/<env>.yaml",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument("env", choices=["dev", "uat", "prod"], help="Target environment")
    parser.add_argument("template", nargs="?", help="Template name filter (optional)")
    parser.add_argument(
        "--subdir",
        default="setup",
        help="Subdirectory under snowflake/ containing *.sql.j2 templates (default: setup)",
    )
    args = parser.parse_args()
    render(args.env, args.template, args.subdir)


if __name__ == "__main__":
    main()
