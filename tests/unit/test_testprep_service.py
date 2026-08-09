from dataclasses import dataclass, replace
import json
from pathlib import Path
import shutil

from safefix.models import (
    AcceptanceMode,
    BaselineSource,
    Config,
    ReviewVerdict,
    StopReason,
)
from safefix.approval import DeferredApprovalProvider
from safefix.llm.base import LLMTransportError
from safefix.review import ReviewResult
from safefix.test_manifest import discover_existing_tests
from safefix.testprep import (
    PreparationRequest,
    TestPreparationService as PreparationService,
)
from safefix.testprep.workspace import CandidateWorkspace
from safefix.testrunner import TestRunResult as RunResult
from safefix.testrunner import TestRunner as ProjectTestRunner


@dataclass
class FakeDiscovery:
    collected_count: int
    test_paths: tuple[str, ...] = ()


class CountOnlyDiscovery:
    def __init__(self, collected_count: int) -> None:
        self.collected_count = collected_count


class FakeTestClient:
    def __init__(self, response: str = '{"candidates": []}') -> None:
        self.response = response
        self.prompts: list[str] = []
        self.closed = False

    def complete(self, prompt: str) -> str:
        self.prompts.append(prompt)
        return self.response

    def close(self) -> None:
        self.closed = True


class FailingCloseTestClient(FakeTestClient):
    def close(self) -> None:
        raise OSError("close failed")


class FailingRequestTestClient(FakeTestClient):
    def complete(self, prompt: str) -> str:
        del prompt
        raise LLMTransportError("HTTP Error 401")


class FakeReviewClient:
    def __init__(self, result: ReviewResult) -> None:
        self.result = result
        self.calls = 0

    def review(self, prompt: str) -> ReviewResult:
        self.calls += 1
        return self.result


class FakeApproval:
    def __init__(self, allowed: bool = True) -> None:
        self.allowed = allowed
        self.calls: list[object] = []

    def approve(self, action: object) -> bool:
        self.calls.append(action)
        return self.allowed


class RecordingSink:
    def __init__(self) -> None:
        self.events: list[object] = []

    def emit(self, event: object) -> None:
        self.events.append(event)


def _copy_existing_project(tmp_path: Path) -> Path:
    fixture = Path(__file__).parents[1] / "fixtures" / "projects" / "existing_tests"
    project = tmp_path / "project"
    tmp_path.mkdir(parents=True, exist_ok=True)
    shutil.copytree(fixture, project)
    return project


def _candidate_response(test_name: str = "test_generated_value") -> str:
    return (
        '{"candidates":[{"candidate_id":"c1",'
        f'"test_source":"def {test_name}():\\n    assert True\\n",'
        '"basis":"The public contract requires this behavior.",'
        '"sources":["src/app.py"],"touched_existing_tests":[]}]}'
    )


def _request(
    tmp_path: Path,
    *,
    source: BaselineSource,
    existing_count: int = 0,
    generate_tests: bool = True,
    mode: AcceptanceMode = AcceptanceMode.STANDARD,
    test_client: FakeTestClient | None = None,
    review_client: FakeReviewClient | None = None,
    approval: FakeApproval | None = None,
    high_risk_confirmation: bool = True,
    test_runner=None,
) -> tuple[PreparationRequest, FakeTestClient, CandidateWorkspace, object | None]:
    project = _copy_existing_project(tmp_path)
    client = test_client or FakeTestClient(_candidate_response())
    workspace = CandidateWorkspace(project, "task-8")
    config = Config(
        generate_tests=generate_tests,
        baseline_source=source,
        acceptance_mode=mode,
        stability_runs=1,
        max_auto_accepted_failures=3,
        test_base_url="https://test.example/v1",
        test_model="test-model",
        review_base_url="https://review.example/v1",
        review_model="review-model",
    )
    request = PreparationRequest(
        project_root=project,
        existing_discovery=FakeDiscovery(
            collected_count=existing_count,
            test_paths=("tests/test_existing.py",) if existing_count else (),
        ),
        test_client=client,
        review_client=review_client,
        config=config,
        approval_provider=approval or FakeApproval(),
        workspace=workspace,
        event_sink=None,
        guidance="keep the candidate local",
        high_risk_confirmation=high_risk_confirmation,
    )
    return request, client, workspace, test_runner


def _run_result(
    *failure_ids: str,
    valid: bool = True,
    executed_lines: dict[str, frozenset[int]] | None = None,
) -> RunResult:
    from safefix.junit import TestCaseResult

    return RunResult(
        exit_code=1 if failure_ids else 0,
        cases=tuple(
            TestCaseResult(
                failure_id=failure_id,
                classname="candidate",
                name=failure_id,
                status="failed",
            )
            for failure_id in failure_ids
        ),
        valid=valid,
        executed_lines=executed_lines,
    )


def _review() -> ReviewResult:
    return ReviewResult(
        verdict=ReviewVerdict.PASS,
        basis_supported=True,
        invented_behavior=False,
        implementation_coupling=False,
        risk="low",
        summary="supported",
    )


def test_no_generation_preserves_existing_manifest_entries(tmp_path: Path) -> None:
    request, client, workspace, _ = _request(
        tmp_path,
        source=BaselineSource.EXISTING,
        existing_count=1,
        generate_tests=False,
    )

    result = PreparationService().prepare(request)

    assert result.stop_reason is None
    assert [entry.path for entry in result.manifest_entries] == ["tests/test_existing.py"]
    assert result.manifest_entries[0].origin is BaselineSource.EXISTING
    assert result.summary.existing_test_count == 1
    assert client.prompts == []
    assert not client.closed


def test_real_existing_discovery_keeps_collected_fixture_test_in_manifest(tmp_path: Path) -> None:
    project = _copy_existing_project(tmp_path)
    discovery = discover_existing_tests(project, ProjectTestRunner(project, allow_empty=True))
    workspace = CandidateWorkspace(project, "real-discovery")
    request = PreparationRequest(
        project_root=project,
        existing_discovery=discovery,
        test_client=None,
        review_client=None,
        config=Config(baseline_source=BaselineSource.EXISTING),
        approval_provider=None,
        workspace=workspace,
    )

    result = PreparationService().prepare(request)

    assert discovery.collected_count == 1
    assert discovery.test_paths == ("tests/test_existing.py",)
    assert [entry.path for entry in result.manifest_entries] == ["tests/test_existing.py"]


def test_real_existing_discovery_retains_custom_pytest_filename(tmp_path: Path) -> None:
    project = tmp_path / "custom-tests"
    project.mkdir()
    (project / "pytest.ini").write_text(
        "[pytest]\npython_files = suite.py\n", encoding="utf-8"
    )
    (project / "suite.py").write_text(
        "def test_custom_discovered():\n    assert True\n", encoding="utf-8"
    )
    discovery = discover_existing_tests(project, ProjectTestRunner(project))
    workspace = CandidateWorkspace(project, "custom-discovery")
    request = PreparationRequest(
        project_root=project,
        existing_discovery=discovery,
        test_client=None,
        review_client=None,
        config=Config(baseline_source=BaselineSource.EXISTING),
        approval_provider=None,
        workspace=workspace,
    )

    result = PreparationService().prepare(request)

    assert discovery.collected_count == 1
    assert discovery.test_paths == ("suite.py",)
    assert [entry.path for entry in result.manifest_entries] == ["suite.py"]


def test_existing_source_does_not_generate_when_flag_is_set(tmp_path: Path) -> None:
    request, client, _, _ = _request(
        tmp_path,
        source=BaselineSource.EXISTING,
        existing_count=1,
        generate_tests=True,
    )

    result = PreparationService().prepare(request)

    assert result.stop_reason is None
    assert client.prompts == []


def test_generation_disabled_mixed_source_keeps_existing_only(tmp_path: Path) -> None:
    request, client, _, _ = _request(
        tmp_path,
        source=BaselineSource.MIXED,
        existing_count=1,
        generate_tests=False,
    )

    result = PreparationService().prepare(request)

    assert result.stop_reason is None
    assert [entry.path for entry in result.manifest_entries] == [
        "tests/test_existing.py"
    ]
    assert all(entry.origin is BaselineSource.EXISTING for entry in result.manifest_entries)
    assert result.summary.generated_candidate_count == 0
    assert client.prompts == []
    assert not client.closed


def test_zero_collected_count_does_not_promote_scanned_test_files(tmp_path: Path) -> None:
    project = _copy_existing_project(tmp_path)
    workspace = CandidateWorkspace(project, "zero-collected")
    request = PreparationRequest(
        project_root=project,
        existing_discovery=CountOnlyDiscovery(0),
        test_client=None,
        review_client=None,
        config=Config(baseline_source=BaselineSource.EXISTING),
        approval_provider=None,
        workspace=workspace,
    )

    result = PreparationService().prepare(request)

    assert result.manifest_entries == ()


def test_mixed_flow_keeps_existing_and_promotes_accepted_generated_candidate(tmp_path: Path) -> None:
    request, client, workspace, runner = _request(
        tmp_path,
        source=BaselineSource.MIXED,
        existing_count=1,
        test_runner=lambda path: _run_result(
            executed_lines={"src/app.py": frozenset({3, 4})}
        ),
    )

    result = PreparationService(candidate_runner=runner).prepare(request)

    assert result.stop_reason is None
    assert {entry.origin for entry in result.manifest_entries} == {
        BaselineSource.EXISTING,
        BaselineSource.GENERATED,
    }
    assert result.summary.generated_pass_accepted == 1
    assert client.closed
    assert (workspace.session_root / "accepted" / "c1.py").is_file()
    assert (request.project_root / "tests" / "test_existing.py").read_text(encoding="utf-8").startswith(
        "def test_existing"
    )


def test_generated_only_is_allowed_when_no_existing_tests_are_collected(tmp_path: Path) -> None:
    request, _, _, runner = _request(
        tmp_path,
        source=BaselineSource.GENERATED,
        existing_count=0,
        test_runner=lambda path: _run_result(),
    )

    result = PreparationService(candidate_runner=runner).prepare(request)

    assert result.stop_reason is None
    assert len(result.manifest_entries) == 1
    assert result.manifest_entries[0].origin is BaselineSource.GENERATED


def test_generated_only_rejects_collectible_existing_tests_without_calling_model(tmp_path: Path) -> None:
    request, client, _, _ = _request(
        tmp_path,
        source=BaselineSource.GENERATED,
        existing_count=1,
    )

    result = PreparationService().prepare(request)

    assert result.stop_reason is StopReason.CONFIG_ERROR
    assert client.prompts == []
    assert result.summary.candidate_records[0].reason == (
        "已检测到可收集的已有测试，不能使用 generated-only；请选择 existing 或 mixed。"
    )


def test_absolute_eval_mutation_is_rejected_before_candidate_run(tmp_path: Path) -> None:
    absolute_app = str(tmp_path / "project" / "src" / "app.py")
    mutation_source = (
        "def test_candidate_mutation():\n"
        f"    eval(\"__builtins__['open']({absolute_app!r}, 'w').write('VALUE = 2\\\\n')\")\n"
        "    assert True\n"
    )
    response = json.dumps(
        {
            "candidates": [
                {
                    "candidate_id": "mutating",
                    "test_source": mutation_source,
                    "basis": "The public contract requires this behavior.",
                    "sources": ["src/app.py"],
                    "touched_existing_tests": [],
                }
            ]
        }
    )
    request, _, workspace, _ = _request(
        tmp_path,
        source=BaselineSource.GENERATED,
        existing_count=0,
        test_client=FakeTestClient(response),
    )
    run_calls: list[Path] = []

    def unexpected_run(path: Path) -> RunResult:
        run_calls.append(path)
        raise AssertionError("unsafe candidate reached the runner")

    app = request.project_root / "src" / "app.py"
    existing_test = request.project_root / "tests" / "test_existing.py"
    original = app.read_text(encoding="utf-8")
    existing_original = existing_test.read_text(encoding="utf-8")

    result = PreparationService(candidate_runner=unexpected_run).prepare(request)

    assert result.summary.rejected_count == 1
    assert result.summary.error_count == 0
    assert run_calls == []
    assert not (workspace.session_root / "staged" / "mutating.py").exists()
    assert app.read_text(encoding="utf-8") == original
    assert existing_test.read_text(encoding="utf-8") == existing_original


def test_workspace_runner_override_is_not_used_at_service_boundary(tmp_path: Path) -> None:
    request, _, _, _ = _request(
        tmp_path,
        source=BaselineSource.GENERATED,
        existing_count=0,
    )

    def untrusted_runner(path: Path) -> RunResult:
        raise AssertionError("workspace runner override must not be trusted")

    request.workspace.run_candidate = untrusted_runner  # type: ignore[attr-defined]

    result = PreparationService().prepare(request)

    assert result.stop_reason is None
    assert result.summary.generated_pass_accepted == 1


def test_dynamic_wrapper_absolute_original_root_is_rejected_before_stability(
    tmp_path: Path,
) -> None:
    request, client, workspace, _ = _request(
        tmp_path,
        source=BaselineSource.GENERATED,
        existing_count=0,
    )
    app = request.project_root / "src" / "app.py"
    existing_test = request.project_root / "tests" / "test_existing.py"
    original_app = app.read_bytes()
    original_test = existing_test.read_bytes()
    source = (
        "import os\n"
        "\n"
        "def get_operation():\n"
        "    return os.truncate\n"
        "\n"
        "def test_dynamic_wrapper():\n"
        "    sep = chr(47)\n"
        "    original = sep.join((os.environ[\"SAFEFIX_ORIGINAL_ROOT\"], \"src\", \"app.py\"))\n"
        "    get_operation()(original, 0)\n"
        "    assert True\n"
    )
    response = json.dumps(
        {
            "candidates": [
                {
                    "candidate_id": "absolute-truncate",
                    "test_source": source,
                    "basis": "The public contract requires this behavior.",
                    "sources": ["src/app.py"],
                    "touched_existing_tests": [],
                }
            ]
        }
    )
    malicious_client = FakeTestClient(response)
    sink = RecordingSink()
    request = PreparationRequest(
        **{
            **request.__dict__,
            "test_client": malicious_client,
            "event_sink": sink,
        }
    )

    result = PreparationService().prepare(request)

    assert result.summary.rejected_count == 1
    assert result.summary.error_count == 0
    assert result.manifest_entries == ()
    assert not (workspace.session_root / "staged" / "absolute-truncate.py").exists()
    assert app.read_bytes() == original_app
    assert existing_test.read_bytes() == original_test
    assert len(malicious_client.prompts) == 1
    assert [event.kind for event in sink.events] == ["model-call", "model-call"]


def test_invalid_existing_discovery_path_returns_configuration_stop(tmp_path: Path) -> None:
    request, _, _, _ = _request(
        tmp_path,
        source=BaselineSource.EXISTING,
        existing_count=1,
        generate_tests=False,
    )
    request = PreparationRequest(
        **{
            **request.__dict__,
            "existing_discovery": FakeDiscovery(
                collected_count=1,
                test_paths=("../outside.py",),
            ),
        }
    )

    result = PreparationService().prepare(request)

    assert result.stop_reason is StopReason.CONFIG_ERROR
    assert result.manifest_entries == ()


def test_standard_pass_is_automatic_and_standard_fail_uses_manual_approval(tmp_path: Path) -> None:
    pass_request, pass_client, _, pass_runner = _request(
        tmp_path / "pass",
        source=BaselineSource.GENERATED,
        test_runner=lambda path: _run_result(),
    )
    pass_approval = pass_request.approval_provider
    pass_result = PreparationService(candidate_runner=pass_runner).prepare(pass_request)

    fail_request, fail_client, _, fail_runner = _request(
        tmp_path / "fail",
        source=BaselineSource.GENERATED,
        approval=FakeApproval(True),
        test_runner=lambda path: _run_result("candidate::test_generated_value"),
    )
    fail_result = PreparationService(candidate_runner=fail_runner).prepare(fail_request)

    assert pass_result.summary.generated_pass_accepted == 1
    assert pass_client.closed
    assert len(pass_approval.calls) == 0  # type: ignore[attr-defined]
    assert fail_result.summary.generated_fail_accepted_manual == 1
    assert len(fail_request.approval_provider.calls) == 1  # type: ignore[attr-defined]
    assert fail_client.closed


def test_high_risk_eligible_fail_is_automatic_after_explicit_confirmation(tmp_path: Path) -> None:
    request, _, _, runner = _request(
        tmp_path,
        source=BaselineSource.GENERATED,
        mode=AcceptanceMode.HIGH_RISK,
        review_client=FakeReviewClient(_review()),
        test_runner=lambda path: _run_result("candidate::test_generated_value"),
    )

    result = PreparationService(candidate_runner=runner).prepare(request)

    assert result.stop_reason is None
    assert result.summary.generated_fail_accepted_automatic == 1
    assert request.approval_provider.calls == []  # type: ignore[attr-defined]


def test_high_risk_requires_explicit_confirmation_before_model_generation(tmp_path: Path) -> None:
    request, client, _, _ = _request(
        tmp_path,
        source=BaselineSource.GENERATED,
        mode=AcceptanceMode.HIGH_RISK,
        high_risk_confirmation=False,
    )

    result = PreparationService().prepare(request)

    assert result.stop_reason is StopReason.CONFIG_ERROR
    assert result.manifest_entries == ()
    assert client.prompts == []


def test_test_model_is_closed_after_preparation_returns(tmp_path: Path) -> None:
    request, client, _, runner = _request(
        tmp_path,
        source=BaselineSource.GENERATED,
        test_runner=lambda path: _run_result(),
    )

    PreparationService(candidate_runner=runner).prepare(request)

    assert client.closed


def test_generation_requires_all_documented_behaviors_and_key_branches(
    tmp_path: Path,
) -> None:
    request, client, _, runner = _request(
        tmp_path,
        source=BaselineSource.GENERATED,
        test_runner=lambda path: _run_result(),
    )
    (request.project_root / "README.md").write_text(
        "slugify must lowercase text, remove punctuation, and trim whitespace.\n",
        encoding="utf-8",
    )
    (request.project_root / "src" / "app.py").write_text(
        "def slugify(value):\n"
        "    if not value:\n"
        "        return ''\n"
        "    return value.lower()\n",
        encoding="utf-8",
    )
    client.response = json.dumps(
        {
            "candidates": [
                {
                    "candidate_id": "partial",
                    "test_source": "def test_slugify():\n    assert True\n",
                    "basis": "public behavior",
                    "sources": ["src/app.py"],
                    "touched_existing_tests": [],
                    "covers": ["behavior-1"],
                }
            ]
        }
    )

    result = PreparationService(candidate_runner=runner).prepare(request)

    assert result.stop_reason is StopReason.TEST_PREPARATION_ERROR
    assert result.manifest_entries == ()
    assert [item.requirement_id for item in result.summary.coverage_requirements] == [
        "behavior-1",
        "behavior-2",
        "behavior-3",
        "branch-1",
    ]
    assert result.summary.covered_requirement_ids == ("behavior-1",)
    assert result.summary.candidate_records[-1].candidate_id == "<coverage-gap>"


def test_generation_accepts_a_complete_declared_coverage_bundle(tmp_path: Path) -> None:
    request, client, _, runner = _request(
        tmp_path,
        source=BaselineSource.GENERATED,
        test_runner=lambda path: _run_result(
            executed_lines={"src/app.py": frozenset({3, 4})}
        ),
    )
    (request.project_root / "README.md").write_text(
        "slugify must lowercase text and remove punctuation.\n", encoding="utf-8"
    )
    (request.project_root / "src" / "app.py").write_text(
        "def slugify(value):\n"
        "    if not value:\n"
        "        return ''\n"
        "    return value.lower()\n",
        encoding="utf-8",
    )
    client.response = json.dumps(
        {
            "candidates": [
                {
                    "candidate_id": "complete",
                    "test_source": "def test_slugify():\n    assert True\n",
                    "basis": "public behavior",
                    "sources": ["src/app.py"],
                    "touched_existing_tests": [],
                    "covers": ["behavior-1", "behavior-2", "branch-1"],
                }
            ]
        }
    )

    result = PreparationService(candidate_runner=runner).prepare(request)

    assert result.stop_reason is None
    assert result.summary.covered_requirement_ids == (
        "behavior-1",
        "behavior-2",
        "branch-1",
    )
    assert result.summary.generated_accepted_count == 1
    assert len(result.manifest_entries) == 1


def test_generation_rejects_declared_branch_without_executing_both_outcomes(
    tmp_path: Path,
) -> None:
    request, client, _, runner = _request(
        tmp_path,
        source=BaselineSource.GENERATED,
        test_runner=lambda path: _run_result(
            executed_lines={"src/app.py": frozenset({3})}
        ),
    )
    (request.project_root / "src" / "app.py").write_text(
        "def slugify(value):\n"
        "    if not value:\n"
        "        return ''\n"
        "    return value.lower()\n",
        encoding="utf-8",
    )
    client.response = json.dumps(
        {"candidates": [{
            "candidate_id": "one-path",
            "test_source": "def test_slugify():\n    assert True\n",
            "basis": "public behavior",
            "sources": ["src/app.py"],
            "touched_existing_tests": [],
            "covers": ["branch-1"],
        }]}
    )

    result = PreparationService(candidate_runner=runner).prepare(request)

    assert result.stop_reason is StopReason.TEST_PREPARATION_ERROR
    assert result.summary.candidate_records[0].reason == (
        "branch execution was not verified: branch-1 run 1 missing lines 4"
    )


def test_production_candidate_runner_verifies_branch_execution(tmp_path: Path) -> None:
    request, client, _, _ = _request(tmp_path, source=BaselineSource.GENERATED)
    (request.project_root / "calculator.py").write_text(
        "def price(value):\n"
        "    if value:\n"
        "        return 1\n"
        "    return 0\n",
        encoding="utf-8",
    )
    client.response = json.dumps(
        {"candidates": [{
            "candidate_id": "two-paths",
            "test_source": "from calculator import price\n\n"
            "def test_price():\n"
            "    assert price(True) == 1\n"
            "    assert price(False) == 0\n",
            "basis": "public behavior",
            "sources": ["calculator.py"],
            "touched_existing_tests": [],
            "covers": ["branch-1"],
        }]}
    )

    result = PreparationService().prepare(request)

    assert result.stop_reason is None
    assert result.summary.covered_requirement_ids == ("branch-1",)


def test_generated_failing_candidate_waits_for_tui_approval_without_stdin(
    tmp_path: Path,
) -> None:
    request, _, _, runner = _request(
        tmp_path,
        source=BaselineSource.GENERATED,
        approval=DeferredApprovalProvider(),
        test_runner=lambda path: _run_result("candidate::test_generated_value"),
    )
    sink = RecordingSink()
    request = PreparationRequest(**{**request.__dict__, "event_sink": sink})
    result: list[object] = []
    import threading

    worker = threading.Thread(
        target=lambda: result.append(PreparationService(candidate_runner=runner).prepare(request))
    )
    worker.start()
    approval = request.approval_provider
    assert isinstance(approval, DeferredApprovalProvider)
    assert approval.wait_until_pending(timeout=0.5)
    assert any(event.kind == "approval" for event in sink.events)

    assert approval.approve_pending() is True
    worker.join(timeout=0.5)

    assert len(result) == 1
    prepared = result[0]
    assert prepared.summary.generated_fail_accepted_manual == 1  # type: ignore[union-attr]


def test_generation_prompt_requires_declared_coverage_ids(tmp_path: Path) -> None:
    request, client, _, runner = _request(
        tmp_path,
        source=BaselineSource.GENERATED,
        test_runner=lambda path: _run_result(),
    )
    (request.project_root / "README.md").write_text(
        "slugify must lowercase text.\n", encoding="utf-8"
    )

    PreparationService(candidate_runner=runner).prepare(request)

    assert '"covers":["behavior-1"]' in client.prompts[0]
    assert "behavior-1: lowercase text" in client.prompts[0]


def test_malformed_test_model_output_is_recorded_as_rejected_candidate(tmp_path: Path) -> None:
    request, _, _, _ = _request(
        tmp_path,
        source=BaselineSource.GENERATED,
        test_client=FakeTestClient("not-json"),
    )

    result = PreparationService().prepare(request)

    assert result.stop_reason is None
    assert result.summary.generated_candidate_count == 0
    assert result.summary.rejected_count == 1
    assert result.manifest_entries == ()


def test_empty_candidate_array_records_its_explicit_model_response_reason(tmp_path: Path) -> None:
    request, _, _, _ = _request(
        tmp_path,
        source=BaselineSource.GENERATED,
        test_client=FakeTestClient('{"candidates":[]}'),
    )

    result = PreparationService().prepare(request)

    assert result.summary.candidate_records[0].reason == (
        "测试模型未返回候选测试：JSON 的 candidates 数组为空。"
    )


def test_test_model_request_failure_records_a_safe_authentication_reason(tmp_path: Path) -> None:
    request, _, _, _ = _request(
        tmp_path,
        source=BaselineSource.GENERATED,
        test_client=FailingRequestTestClient(),
    )

    result = PreparationService().prepare(request)

    assert result.stop_reason is StopReason.TEST_PREPARATION_ERROR
    assert result.summary.candidate_records[0].reason == (
        "测试模型认证被拒绝。请检查 SAFEFIX_TEST_API_KEY。"
    )


def test_generation_without_a_test_client_reports_its_missing_credential(tmp_path: Path) -> None:
    request, _, _, _ = _request(
        tmp_path,
        source=BaselineSource.GENERATED,
        test_client=None,
    )
    request = replace(request, test_client=None)

    result = PreparationService().prepare(request)

    assert result.stop_reason is StopReason.CONFIG_ERROR
    assert result.summary.candidate_records[0].reason == (
        "测试模型未配置。请配置 test_base_url、test_model 和 SAFEFIX_TEST_API_KEY。"
    )


def test_mixed_source_keeps_existing_tests_and_gives_model_project_context(tmp_path: Path) -> None:
    request, client, _, runner = _request(
        tmp_path,
        source=BaselineSource.MIXED,
        existing_count=1,
        test_runner=lambda path: _run_result(),
    )

    result = PreparationService(candidate_runner=runner).prepare(request)

    assert result.stop_reason is None
    assert {entry.origin for entry in result.manifest_entries} == {
        BaselineSource.EXISTING,
        BaselineSource.GENERATED,
    }
    prompt = client.prompts[0]
    assert '"candidates"' in prompt
    assert '"candidate_id"' in prompt
    assert "src/app.py" in prompt
    assert "tests/test_existing.py" in prompt


def test_static_rule_rejection_does_not_stage_or_run_candidate(tmp_path: Path) -> None:
    client = FakeTestClient(
        '{"candidates":[{"candidate_id":"private",'
        '"test_source":"def test_private():\\n    assert True\\n",'
        '"basis":"supported", "sources":["missing.py"],'
        '"touched_existing_tests":[]}]}'
    )
    request, _, workspace, runner = _request(
        tmp_path,
        source=BaselineSource.GENERATED,
        test_client=client,
        test_runner=lambda path: (_ for _ in ()).throw(AssertionError("runner called")),
    )

    result = PreparationService(candidate_runner=runner).prepare(request)

    assert result.summary.rejected_count == 1
    assert not (workspace.session_root / "staged" / "private.py").exists()


def test_test_model_close_failure_is_preparation_error(tmp_path: Path) -> None:
    client = FailingCloseTestClient(_candidate_response())
    request, _, _, runner = _request(
        tmp_path,
        source=BaselineSource.GENERATED,
        test_client=client,
        test_runner=lambda path: _run_result(),
    )

    result = PreparationService(candidate_runner=runner).prepare(request)

    assert result.stop_reason is StopReason.TEST_PREPARATION_ERROR
    assert result.manifest_entries == ()


def test_preparation_emits_safe_model_and_acceptance_events(tmp_path: Path) -> None:
    request, _, _, runner = _request(
        tmp_path,
        source=BaselineSource.GENERATED,
        test_runner=lambda path: _run_result(),
    )
    sink = RecordingSink()
    request = PreparationRequest(
        **{**request.__dict__, "event_sink": sink},
    )

    PreparationService(candidate_runner=runner).prepare(request)

    assert [event.kind for event in sink.events] == [
        "model-call",
        "model-call",
        "stability-run",
        "stability-run",
        "acceptance",
    ]
    assert all("source_code" not in event.safe_payload for event in sink.events)
