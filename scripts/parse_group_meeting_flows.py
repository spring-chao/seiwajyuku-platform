"""Parse the formal 2026 group-meeting sections from DOCX source files.

The parser deliberately treats embedded QR images as evidence, not as a
license to infer a course.  A QR is linked to a confirmed course only when a
course alias is explicitly present in the surrounding source text or file
name; otherwise it remains ``QR_REVIEW_REQUIRED`` with a null credit value.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from docx import Document
DEFAULT_SOURCE_ROOT = Path(r"E:\班级运营资料\6.班会+小组会学习会流程")
DEFAULT_INVENTORY = Path("data/learning-plans/group-meeting-source-inventory-2026.json")
DEFAULT_OUTPUT = Path("data/learning-plans/group-meeting-flows-2026.1.json")

W_NS = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
A_NS = "http://schemas.openxmlformats.org/drawingml/2006/main"
R_NS = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
NS = {"w": W_NS, "a": A_NS, "r": R_NS}

DEFAULT_CREDIT_RULES = Path("data/learning-plans/course-credit-rules-2026.json")


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _metadata_from_filename(path: Path) -> tuple[int | None, int | None, list[int]]:
    text = str(path)
    year_match = re.search(r"第\s*(\d+)\s*学年", text)
    cycle_match = re.search(r"第\s*(\d+)\s*个月", path.name)
    # Source filenames use both ``2026年`` and ``2026版`` before the cohort
    # months.  Treat the year/month as source metadata only; cycle mapping is
    # still based on the explicit study-cycle number.
    cohort_match = re.search(r"2026(?:年|版)?([^（）()]{1,24})月开班", path.name)
    months: list[int] = []
    if cohort_match:
        months = sorted({int(value) for value in re.findall(r"\d{1,2}", cohort_match.group(1))})
    return (
        int(year_match.group(1)) if year_match else None,
        int(cycle_match.group(1)) if cycle_match else None,
        months,
    )


def load_course_rules(path: Path = DEFAULT_CREDIT_RULES) -> tuple[tuple[str, int, tuple[str, ...]], ...]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    return tuple(
        (
            str(rule["course_key"]),
            int(rule["credit_points"]),
            tuple(str(alias) for alias in rule.get("aliases", [])),
        )
        for rule in payload.get("rules", [])
    )


def _course_match(
    context: str, rules: tuple[tuple[str, int, tuple[str, ...]], ...]
) -> tuple[str | None, int | None]:
    for key, points, aliases in rules:
        if any(alias in context for alias in aliases):
            return key, points
    return None, None


def _paragraph_records(document: Document) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    for index, paragraph in enumerate(document.paragraphs):
        text = " ".join(paragraph.text.split())
        # python-docx's OxmlElement.xpath already carries the OOXML namespace
        # map and does not accept the lxml ``namespaces=`` keyword.
        image_ids = paragraph._p.xpath(".//a:blip/@r:embed")
        records.append({"paragraph_index": index, "text": text, "image_ids": image_ids})
    return records


def _media_targets(document: Document) -> dict[str, str]:
    return {
        rel.rId: rel.target_ref
        for rel in document.part.rels.values()
        if "image" in rel.reltype
    }


def parse_flow(
    path: Path,
    source_root: Path,
    course_rules: tuple[tuple[str, int, tuple[str, ...]], ...] | None = None,
) -> dict[str, Any]:
    document = Document(str(path))
    records = _paragraph_records(document)
    media_targets = _media_targets(document)
    year_index, cycle_index, eligible_months = _metadata_from_filename(path)
    sha = sha256_file(path)
    flow_key = (
        f"Y{year_index or 0}-C{cycle_index or 0:02d}-COHORT-"
        f"{'-'.join(str(month) for month in eligible_months) or 'UNKNOWN'}-{sha[:10]}"
    )

    group_started = False
    terminal_found = False
    steps: list[dict[str, Any]] = []
    course_nodes: list[dict[str, Any]] = []
    pending_images: list[dict[str, Any]] = []
    current_step: dict[str, Any] | None = None
    for record in records:
        text = record["text"]
        if not group_started:
            if "小组学习会" in text:
                group_started = True
            else:
                continue
        if terminal_found:
            break
        if "空巴" in text or "空吧" in text:
            terminal_found = True
        step_match = re.match(r"^\s*(\d+)\s*[.．、)）]\s*(.*)$", text)
        if step_match:
            current_step = {
                "step_no": int(step_match.group(1)),
                "title": step_match.group(2).strip(),
                "content": step_match.group(2).strip(),
                "is_required": True,
                "source_paragraph_index": record["paragraph_index"],
                "qr_refs": [],
                "is_terminal": bool("空巴" in text or "空吧" in text),
            }
            steps.append(current_step)
        elif current_step is not None and text:
            current_step["content"] = f"{current_step['content']}\n{text}".strip()
            if current_step["is_terminal"]:
                current_step["title"] = current_step["content"]
        if not terminal_found:
            for image_id in record["image_ids"]:
                target = media_targets.get(image_id, image_id)
                image_ref = {
                    "media_target": target,
                    "relationship_id": image_id,
                    "source_paragraph_index": record["paragraph_index"],
                    "context_step_no": current_step["step_no"] if current_step else None,
                    "context_text": current_step["content"] if current_step else text,
                }
                if current_step is not None:
                    current_step["qr_refs"].append(image_ref)
                else:
                    pending_images.append(image_ref)
                course_nodes.append(image_ref)

    if course_rules is None:
        course_rules = load_course_rules()
    for node in course_nodes:
        # The step text is the only safe way to associate an embedded QR with
        # a course.  A filename may mention several courses in one meeting and
        # therefore must not be used to assign every QR to the same course.
        context = node.get("context_text") or ""
        course_key, points = _course_match(context, course_rules)
        node.update(
            {
                "node_type": "COURSE_QR",
                "course_key": course_key,
                "credit_points": points,
                "credit_status": "MAPPED" if course_key else "QR_REVIEW_REQUIRED",
                "qr_url": None,
            }
        )
    terminal_steps = [step for step in steps if step["is_terminal"]]
    relative_path = path.relative_to(source_root).as_posix()
    status = "PARSED" if group_started and terminal_found else "REVIEW_REQUIRED"
    return {
        "flow_key": flow_key,
        "status": status,
        "year_index": year_index,
        "cycle_index": cycle_index,
        "year_cycle_index": ((cycle_index - 1) % 12) + 1 if cycle_index else None,
        "eligible_cohort_months": eligible_months,
        "title": next(
            (record["text"] for record in records if "小组学习会流程" in record["text"]),
            path.stem,
        ),
        "source": {
            "relative_path": relative_path,
            "filename": path.name,
            "sha256": sha,
        },
        "boundary": {
            "starts_at": "first 小组学习会 marker",
            "ends_at": "first 空巴/空吧 marker",
            "terminal_step_count": len(terminal_steps),
            "qr_after_terminal_excluded": True,
        },
        "steps": steps,
        "course_nodes": course_nodes,
        "quality": {
            "group_marker_found": group_started,
            "konpa_found": terminal_found,
            "course_node_count": len(course_nodes),
            "unknown_course_node_count": sum(
                node["credit_status"] == "QR_REVIEW_REQUIRED" for node in course_nodes
            ),
            "pending_images_before_first_step": len(pending_images),
        },
    }


def build_catalog(source_root: Path, inventory_path: Path) -> dict[str, Any]:
    inventory = json.loads(inventory_path.read_text(encoding="utf-8"))
    rules_path = inventory_path.with_name("course-credit-rules-2026.json")
    course_rules = load_course_rules(rules_path)
    flows: list[dict[str, Any]] = []
    for item in inventory.get("included_files", []):
        path = source_root / Path(item["relative_path"])
        try:
            flows.append(parse_flow(path, source_root, course_rules))
        except Exception as exc:  # keep the source visible for manual review
            flows.append(
                {
                    "flow_key": f"UNPARSED-{item['sha256'][:10]}",
                    "status": "REVIEW_REQUIRED",
                    "source": item,
                    "steps": [],
                    "course_nodes": [],
                    "quality": {"parser_error": str(exc)},
                }
            )
    quality = {
        "flow_count": len(flows),
        "parsed_flow_count": sum(flow.get("status") == "PARSED" for flow in flows),
        "review_required_flow_count": sum(
            flow.get("status") == "REVIEW_REQUIRED" for flow in flows
        ),
        "course_node_count": sum(len(flow.get("course_nodes", [])) for flow in flows),
        "qr_review_required_count": sum(
            node.get("credit_status") == "QR_REVIEW_REQUIRED"
            for flow in flows
            for node in flow.get("course_nodes", [])
        ),
        "missing_konpa_count": sum(
            not flow.get("quality", {}).get("konpa_found", False) for flow in flows
        ),
    }
    return {
        "schema_version": 1,
        "plan_key": "STANDARD_3Y_2026",
        "version_label": "2026.1",
        "status": "DRAFT",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "source_folder": str(source_root),
        "source_inventory_sha256": sha256_file(inventory_path),
        "quality_report": quality,
        "flows": flows,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--source-root", type=Path, default=DEFAULT_SOURCE_ROOT)
    parser.add_argument("--inventory", type=Path, default=DEFAULT_INVENTORY)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args()
    payload = build_catalog(args.source_root, args.inventory)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(json.dumps(payload["quality_report"], ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
