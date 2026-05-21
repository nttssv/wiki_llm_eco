"""Validate processed extraction artifacts and their manifest."""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass

from .processed_registry import rebuild_processed_manifest


@dataclass(slots=True)
class ValidationIssue:
    check: str
    detail: str


def run_validation() -> list[ValidationIssue]:
    _manifest_path, records = rebuild_processed_manifest()
    issues: list[ValidationIssue] = []

    for record in records:
        missing_fields = [
            field_name
            for field_name in ("id", "title", "source", "published_date", "summary", "original_file_path")
            if not str(record.get(field_name) or "").strip()
        ]
        if missing_fields:
            issues.append(
                ValidationIssue(
                    check="required_processed_fields",
                    detail=f"article_id={record.get('id', '')} missing {', '.join(missing_fields)}",
                )
            )

    id_counts = Counter(str(record.get("id") or "") for record in records)
    for article_id, count in sorted(id_counts.items()):
        if article_id and count > 1:
            issues.append(
                ValidationIssue(
                    check="duplicate_article_id",
                    detail=f"article_id={article_id} appears {count} times in processed artifacts",
                )
            )

    file_key_counts = Counter(str(record.get("file_key") or "") for record in records if record.get("file_key"))
    for file_key, count in sorted(file_key_counts.items()):
        if count > 1:
            issues.append(
                ValidationIssue(
                    check="duplicate_file_key",
                    detail=f"file_key={file_key} appears {count} times in processed artifacts",
                )
            )

    return issues


def main() -> None:
    issues = run_validation()
    if issues:
        print(f"processed registry validation status: failed ({len(issues)} issues)")
        for issue in issues:
            print(f"- [{issue.check}] {issue.detail}")
        raise SystemExit(1)

    print("processed registry validation status: passed")
    print("issues found: 0")


if __name__ == "__main__":
    main()
