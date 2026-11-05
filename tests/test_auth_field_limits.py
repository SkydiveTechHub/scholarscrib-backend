import pytest
from pydantic import ValidationError

from app.api.schemas import PasswordIn, ProfilePatch, RegisterIn, ResetPasswordIn

VALID = {
    "firstName": "Ada",
    "lastName": "Obi",
    "email": "ada@example.com",
    "password": "secret1",
    "classLevel": "SS1",
    "track": "SCIENCE",
    "state": "Lagos",
}


def _fields_failing(**overrides) -> set[str]:
    with pytest.raises(ValidationError) as exc:
        RegisterIn(**{**VALID, **overrides})
    return {str(e["loc"][0]) for e in exc.value.errors()}


def test_register_accepts_boundary_lengths():
    RegisterIn(
        **{**VALID, "firstName": "a" * 50, "lastName": "b" * 50, "password": "p" * 25}
    )
    RegisterIn(**{**VALID, "password": "p" * 6})


@pytest.mark.parametrize("field", ["firstName", "lastName"])
def test_register_rejects_names_over_50(field):
    assert _fields_failing(**{field: "a" * 51}) == {field}


def test_register_rejects_password_outside_6_to_25():
    assert _fields_failing(password="p" * 5) == {"password"}
    assert _fields_failing(password="p" * 26) == {"password"}


def test_profile_patch_caps_names():
    ProfilePatch(firstName="a" * 50)
    with pytest.raises(ValidationError):
        ProfilePatch(lastName="a" * 51)


def test_new_passwords_share_the_same_limits():
    for model, extra in (
        (PasswordIn, {"currentPassword": "x"}),
        (ResetPasswordIn, {"email": "a@b.co", "token": "t"}),
    ):
        model(newPassword="p" * 25, **extra)
        with pytest.raises(ValidationError):
            model(newPassword="p" * 26, **extra)
        with pytest.raises(ValidationError):
            model(newPassword="p" * 5, **extra)
