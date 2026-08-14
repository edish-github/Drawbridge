"""Turn indexes.yaml into the --field-config arguments gcloud expects.

Emits one tab-separated line per index: the collection group, then the argument string.

The gcloud shorthand for a vector field is
``--field-config=field-path=embedding,vector-config={dimension=768,flat}``; an ordered field
is ``--field-config=field-path=review_id,order=ascending``. Exactly one of order,
array-config or vector-config may be set per field.

Failure semantics: a field declaring neither an order nor a vector config raises rather than
being emitted without one, because gcloud would reject it halfway through the bootstrap with
a less useful message.
"""

from __future__ import annotations

import argparse
import sys

import yaml


class BadIndexSpec(Exception):
    """An index field is missing its configuration."""


def field_arg(field: dict) -> str:
    path = field["field_path"]
    if "vector_config" in field:
        vc = field["vector_config"]
        flat = ",flat" if vc.get("flat", True) else ""
        return (
            f"--field-config=field-path={path},"
            f"vector-config={{dimension={vc['dimension']}{flat}}}"
        )
    if "order" in field:
        return f"--field-config=field-path={path},order={field['order']}"
    if "array_config" in field:
        return f"--field-config=field-path={path},array-config={field['array_config']}"
    raise BadIndexSpec(f"field {path!r} declares neither order, array-config nor vector-config")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--indexes", required=True)
    args = parser.parse_args()

    with open(args.indexes) as fh:
        spec = yaml.safe_load(fh)

    try:
        for index in spec["indexes"]:
            parts = [field_arg(f) for f in index["fields"]]
            print(f'{index["collection_group"]}\t{" ".join(parts)}')
    except BadIndexSpec as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
