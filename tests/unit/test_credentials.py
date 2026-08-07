import pytest

from safefix.credentials import (
    CredentialNotFoundError,
    CredentialsResolver,
    ROLE_API_KEY_ENV,
)
from safefix.models import ModelRole


def test_each_role_reads_its_explicit_environment_variable() -> None:
    environment = {
        "SAFEFIX_TEST_API_KEY": "test-key",
        "SAFEFIX_REPAIR_API_KEY": "repair-key",
        "SAFEFIX_REVIEW_API_KEY": "review-key",
    }
    credentials = CredentialsResolver(environment)

    assert credentials.for_role(ModelRole.TEST).get() == "test-key"
    assert credentials.for_role(ModelRole.REPAIR).get() == "repair-key"
    assert credentials.for_role(ModelRole.REVIEW).get() == "review-key"
    assert ROLE_API_KEY_ENV[ModelRole.REPAIR] == "SAFEFIX_REPAIR_API_KEY"


@pytest.mark.parametrize("role", list(ModelRole))
def test_missing_role_environment_variable_fails_closed(role: ModelRole) -> None:
    with pytest.raises(CredentialNotFoundError, match=ROLE_API_KEY_ENV[role]):
        CredentialsResolver({}).for_role(role).get()


def test_missing_credential_error_does_not_include_environment_contents() -> None:
    secret = "do-not-leak-this-value"

    with pytest.raises(CredentialNotFoundError) as error:
        CredentialsResolver({"UNRELATED_SECRET": secret}).get()

    assert secret not in str(error.value)
    assert "SAFEFIX_REPAIR_API_KEY" in str(error.value)
