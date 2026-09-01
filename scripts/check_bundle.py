"""Validate the bundle's YAML against the CLI's own JSON schema, offline.

`databricks bundle validate` cannot run in CI: with `mode: development` it
makes a live SCIM call to resolve the username for resource prefixing, so it
needs real workspace credentials. `databricks bundle schema` needs none, so
this does the half that can be checked without a workspace - that the files
parse and that every key and enum is one the CLI recognises.

What it does NOT check: whether the things the config points at actually exist.
A warehouse id, a Lakebase project, a `database:` block naming an instance that
was never created - all of those are schema-valid and fail only at deploy.
"""
import json
import pathlib
import subprocess
import sys

import jsonschema
import yaml

ROOT = pathlib.Path(__file__).resolve().parents[1]


def fix_patterns(node):
    """Make the CLI's regexes compilable by Python, without weakening checks.

    The schema expresses "either a real value or a ${var.x} reference" as a
    two-branch `oneOf`, where the reference branch carries a Go/ECMA regex
    using Unicode property escapes (\p{L}). Python's `re` cannot compile
    those - it raises "bad escape \p" and takes the whole validation down.

    Deleting the pattern outright does compile, but then the reference branch
    is a bare {"type": "string"} that matches everything, so `oneOf` sees two
    matches where it wants one and every enum check silently stops working -
    verified: a `permission: CAN_DO_ANYTHING` passed clean. So rewrite the
    pattern to an equivalent Python one instead of removing it.
    """
    if isinstance(node, dict):
        pat = node.get("pattern")
        if isinstance(pat, str) and "\\p{" in pat:
            node["pattern"] = r"^\$\{.+\}$"
        for v in list(node.values()):
            fix_patterns(v)
    elif isinstance(node, list):
        for v in node:
            fix_patterns(v)


def deep_merge(base, extra, path=""):
    for k, v in extra.items():
        if k in base and isinstance(base[k], dict) and isinstance(v, dict):
            deep_merge(base[k], v, f"{path}/{k}")
        elif k in base and base[k] != v:
            sys.exit(f"conflicting definition for {path}/{k}")
        else:
            base[k] = v
    return base


def main():
    schema = json.loads(subprocess.run(
        ["databricks", "bundle", "schema"],
        capture_output=True, text=True, check=True).stdout)
    fix_patterns(schema)

    bundle = yaml.safe_load((ROOT / "databricks.yml").read_text())
    includes = sorted((ROOT / "resources").glob("*.yml"))
    if not includes:
        sys.exit("no resource files found under resources/")

    for f in includes:
        loaded = yaml.safe_load(f.read_text())
        if not loaded or "resources" not in loaded:
            sys.exit(f"{f.relative_to(ROOT)}: no top-level `resources:` key")
        deep_merge(bundle, loaded, f.relative_to(ROOT).as_posix())

    # `include:` is how the CLI finds those files; the merged doc has already
    # absorbed them, and the schema does not expect both.
    bundle.pop("include", None)

    validator = jsonschema.Draft202012Validator(schema)
    errors = sorted(validator.iter_errors(bundle), key=lambda e: list(e.path))
    for e in errors:
        # A top-level oneOf failure reports the whole document as the instance,
        # which is unreadable. best_match walks down to the node that actually
        # failed, which is the line someone has to go and fix.
        deepest = jsonschema.exceptions.best_match([e])
        where = "/".join(str(p) for p in deepest.absolute_path) or "(root)"
        print(f"  {where}: {deepest.message.splitlines()[0][:200]}")
    if errors:
        sys.exit(f"\n{len(errors)} schema error(s)")

    n = len(bundle.get("resources", {}))
    print(f"bundle schema OK - {len(includes)} resource file(s), "
          f"{n} resource type(s), {len(bundle.get('variables', {}))} variables")


if __name__ == "__main__":
    main()
