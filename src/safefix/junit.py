from __future__ import annotations

from dataclasses import dataclass
from hashlib import sha256
from pathlib import Path
import re
import xml.etree.ElementTree as ET


@dataclass(frozen=True)
class TestCaseResult:
    """One testcase from a JUnit report."""

    failure_id: str
    classname: str
    name: str
    status: str
    message: str = ""

    @property
    def is_failure(self) -> bool:
        return self.status in {"failed", "error"}


def parse_junit_report(report_path: str | Path) -> tuple[TestCaseResult, ...]:
    """Parse a valid JUnit XML report into stable testcase identities."""
    try:
        root = ET.parse(Path(report_path)).getroot()
    except ET.ParseError as exc:
        raise ValueError(f"invalid JUnit XML: {report_path}") from exc

    results: list[TestCaseResult] = []
    for suite in _iter_suites(root):
        suite_name = suite.attrib.get("name", "")
        testcases = [
            child for child in suite if _local_name(child.tag) == "testcase"
        ]
        for testcase in testcases:
            classname = testcase.attrib.get("classname", "")
            name = testcase.attrib.get("name", "")
            status, message = _status_and_message(testcase)
            if not classname or not name:
                results.append(
                    _collection_error(suite_name, message or "collection error")
                )
                continue
            results.append(
                TestCaseResult(
                    failure_id=f"{classname}::{name}",
                    classname=classname,
                    name=name,
                    status=status,
                    message=message,
                )
            )

        if not testcases:
            for child in suite:
                if _local_name(child.tag) in {"error", "failure"}:
                    message = child.attrib.get("message") or "".join(child.itertext())
                    results.append(
                        _collection_error(suite_name, message or "collection error")
                    )
    return tuple(results)


def _iter_suites(root: ET.Element):
    if _local_name(root.tag) == "testsuite":
        yield root
    else:
        yield from (
            element for element in root.iter() if _local_name(element.tag) == "testsuite"
        )


def _status_and_message(element: ET.Element) -> tuple[str, str]:
    for status in ("error", "failure", "skipped"):
        child = next(
            (
                candidate
                for candidate in element
                if _local_name(candidate.tag) == status
            ),
            None,
        )
        if child is not None:
            message = child.attrib.get("message") or "".join(child.itertext())
            return ("failed" if status == "failure" else status), message.strip()
    return "passed", ""


def _local_name(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def _collection_error(suite: str, message: str) -> TestCaseResult:
    normalized = re.sub(r"\s+", " ", message).strip()
    digest = sha256(normalized.encode("utf-8")).hexdigest()[:16]
    return TestCaseResult(
        failure_id=f"collection_error::{suite}::{digest}",
        classname=suite,
        name="",
        status="error",
        message=normalized,
    )
