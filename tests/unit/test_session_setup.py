from pathlib import Path

from safefix.junit import TestCaseResult as _TestCaseResult
from safefix.models import BaselineSource, Config, StopReason
from safefix.test_manifest import ManifestEntry
from safefix.testprep.service import PreparationResult, PreparationSummary
from safefix.testrunner import TestRunResult as _TestRunResult
from safefix.session_setup import SessionSetup, manifest_from_entries


class FakeCredentials:
    def get(self) -> str:
        return "test-api-key"


class FakeTestClient:
    def __init__(self, response: str) -> None:
        self.response = response
        self.closed = False

    def complete(self, prompt: str) -> str:
        del prompt
        return self.response

    def close(self) -> None:
        self.closed = True


def _case(test_id: str, status: str) -> _TestCaseResult:
    return _TestCaseResult(test_id, "tests.test_existing", test_id.rsplit("::", 1)[-1], status)


class FakeRunner:
    def __init__(
        self,
        result: _TestRunResult,
        paths: tuple[str, ...] = (),
        *,
        target_paths: tuple[str, ...] = (),
        allow_empty: bool = False,
    ) -> None:
        self.result = result
        self.paths = paths
        self.target_paths = target_paths
        self.allow_empty = allow_empty

    def run(self) -> _TestRunResult:
        return self.result

    def collect_test_paths(self) -> tuple[str, ...]:
        return self.paths


def _config(source: BaselineSource, *, generate_tests: bool = False) -> Config:
    return Config(
        base_url="https://repair.example",
        model="repair-model",
        baseline_source=source,
        generate_tests=generate_tests,
    )


def _case(test_id: str, status: str) -> _TestCaseResult:
    return _TestCaseResult(test_id, "tests.test_existing", test_id.rsplit("::", 1)[-1], status)


def _entry(path: str, origin: BaselineSource, candidate_id: str | None = None) -> ManifestEntry:
    return ManifestEntry(path, "0" * 64, origin, candidate_id)


def _setup(
    tmp_path: Path,
    *,
    source: BaselineSource,
    existing_paths: tuple[str, ...] = (),
    existing_count: int = 0,
    generated_entries: tuple[ManifestEntry, ...] = (),
    generate_tests: bool = False,
    formal_result: _TestRunResult | None = None,
):
    from safefix.session_setup import SessionSetup

    tests_dir = tmp_path / "tests"
    tests_dir.mkdir()
    for path in existing_paths:
        target = tmp_path / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text("def test_existing():\n    assert True\n", encoding="utf-8")
    config = _config(source, generate_tests=generate_tests)
    existing_result = _TestRunResult(
        0,
        tuple(_case(f"tests.test_existing::{name}", "passed") for name in ("test_existing",)[:existing_count]),
        valid=True,
    )
    formal_result = formal_result or _TestRunResult(
        0,
        (_case("tests.test_existing::test_existing", "passed"),),
        valid=True,
    )
    runners = iter(
        [
            FakeRunner(existing_result, existing_paths),
            FakeRunner(formal_result, existing_paths),
        ]
    )

    def runner_factory(
        _root, _args, *, target_paths, allow_empty
    ):
        selected = next(runners)
        return FakeRunner(
            selected.result,
            selected.paths,
            target_paths=tuple(target_paths),
            allow_empty=allow_empty,
        )

    def preparation_factory(request):
        entries = tuple(
            _entry(path, BaselineSource.EXISTING) for path in existing_paths
        ) + generated_entries
        return PreparationResult(
            entries,
            PreparationSummary(
                baseline_source=source,
                existing_test_count=existing_count,
                generated_accepted_count=len(generated_entries),
            ),
        )

    def manifest_factory(project_root, entries, baseline_source, stability_runs):
        from safefix.session_setup import manifest_from_entries

        return manifest_from_entries(
            project_root, entries, baseline_source, stability_runs
        )

    return SessionSetup(
        tmp_path,
        lambda *_args, **_kwargs: config,
        FakeCredentials(),
        runner_factory,
        preparation_factory,
        manifest_factory,
    )


def test_setup_builds_existing_only_formal_manifest(tmp_path: Path):
    path = "tests/test_existing.py"
    result = _setup(tmp_path, source=BaselineSource.EXISTING, existing_paths=(path,), existing_count=1).prepare()

    assert result.early_stop is not None
    assert result.early_stop.stop_reason is StopReason.SUCCESS
    assert result.manifest is not None
    assert [entry.path for entry in result.manifest.entries] == [path]
    assert result.baseline is not None and result.baseline.valid


def test_setup_preserves_existing_and_generated_entries_in_mixed_manifest(tmp_path: Path):
    existing = "tests/test_existing.py"
    generated = "generated/test_generated.py"
    generated_path = tmp_path / generated
    generated_path.parent.mkdir(parents=True)
    generated_path.write_text("def test_generated():\n    assert True\n", encoding="utf-8")

    result = _setup(
        tmp_path,
        source=BaselineSource.MIXED,
        existing_paths=(existing,),
        existing_count=1,
        generated_entries=(_entry(generated, BaselineSource.GENERATED, "candidate-1"),),
        generate_tests=True,
    ).prepare()

    assert result.manifest is not None
    assert {entry.path for entry in result.manifest.entries} == {existing, generated}
    assert {entry.origin for entry in result.manifest.entries} == {
        BaselineSource.EXISTING,
        BaselineSource.GENERATED,
    }


def test_setup_allows_generated_only_after_empty_existing_discovery(tmp_path: Path):
    generated = "generated/test_generated.py"
    generated_path = tmp_path / generated
    generated_path.parent.mkdir(parents=True)
    generated_path.write_text("def test_generated():\n    assert True\n", encoding="utf-8")

    result = _setup(
        tmp_path,
        source=BaselineSource.GENERATED,
        generated_entries=(_entry(generated, BaselineSource.GENERATED, "candidate-1"),),
        generate_tests=True,
    ).prepare()

    assert result.early_stop is not None
    assert result.early_stop.stop_reason is StopReason.SUCCESS
    assert result.manifest is not None
    assert [entry.origin for entry in result.manifest.entries] == [BaselineSource.GENERATED]


def test_setup_rejects_generated_only_with_existing_tests(tmp_path: Path):
    path = "tests/test_existing.py"
    result = _setup(
        tmp_path,
        source=BaselineSource.GENERATED,
        existing_paths=(path,),
        existing_count=1,
        generate_tests=True,
    ).prepare()

    assert result.early_stop is not None
    assert result.early_stop.stop_reason is StopReason.CONFIG_ERROR
    assert result.baseline is None


def test_setup_closes_test_model_before_formal_baseline(tmp_path: Path):
    from safefix.models import AcceptanceMode
    from safefix.testprep.service import TestPreparationService

    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "app.py").write_text("value = 1\n", encoding="utf-8")
    client = FakeTestClient(
        '{"candidates":[{"candidate_id":"c1",'
        '"test_source":"def test_generated():\\n    assert True\\n",'
        '"basis":"The public contract requires this behavior.",'
        '"sources":["src/app.py"],"touched_existing_tests":[]}]} '
    )
    formal_calls: list[tuple[str, ...]] = []

    baseline = _TestRunResult(
        1,
        (_case("generated::test_generated", "failed"),),
        valid=True,
    )

    class Runner:
        def __init__(self, target_paths: tuple[str, ...], allow_empty: bool):
            self.target_paths = target_paths
            self.allow_empty = allow_empty

        def run(self) -> _TestRunResult:
            formal_calls.append(self.target_paths)
            if self.target_paths:
                assert client.closed
                return baseline
            return _TestRunResult(0, (), valid=True)

        def collect_test_paths(self) -> tuple[str, ...]:
            return ()

    def runner_factory(
        project_root: Path,
        pytest_args: list[str],
        *,
        target_paths: tuple[str, ...],
        allow_empty: bool,
    ) -> Runner:
        del project_root, pytest_args
        return Runner(tuple(target_paths), allow_empty)

    config = Config(
        base_url="https://repair.example",
        model="repair-model",
        baseline_source=BaselineSource.GENERATED,
        generate_tests=True,
        acceptance_mode=AcceptanceMode.STANDARD,
        test_base_url="https://test.example",
        test_model="test-model",
    )
    setup = SessionSetup(
        tmp_path,
        lambda *_args, **_kwargs: config,
        FakeCredentials(),
        runner_factory,
        TestPreparationService(candidate_runner=lambda path: _TestRunResult(
            0, (_case("candidate::test_generated", "passed"),), valid=True
        )),
        manifest_from_entries,
        test_client=client,
    )

    result = setup.prepare()

    assert result.early_stop is None
    assert client.closed
    assert len(formal_calls) == 2
    assert result.manifest is not None
    assert result.baseline is baseline
