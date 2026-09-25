import hashlib
import re

import httpx
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.schemas import (
    CompleteProfileIn,
    DeviceIn,
    PreferencePatch,
    ProfilePatch,
    RegisterIn,
)
from app.core.config import settings
from app.core.errors import ApiError
from app.core.ids import cuid
from app.core.rate_limit import hit
from app.core.security import hash_password, student_token, verify_password
from app.core.timeutil import as_utc, utcnow
from app.database.models import Account, NotificationPreference, User, UserDevice
from app.database.repositories.billing import subscriptions_repository
from app.database.repositories.identity import (
    accounts_repository,
    devices_repository,
    users_repository,
)
from app.database.repositories.notification import (
    preferences_repository,
    push_subscriptions_repository,
)
from app.domain import DEVICE_LIMIT, NIGERIAN_STATES, PHONE_PATTERN, TIER_RANK
from app.services.auth.rules import is_session_revoked
from app.services.billing import RefreshUserTierService
from app.services.billing.rules import resolve_live_tier

PHONE = re.compile(PHONE_PATTERN)
ADMIN_IDENTIFIER = re.compile(r"^[a-z0-9._-]{3,32}$")


def profile_dict(user: User) -> dict:
    return {
        "role": user.role,
        "classLevel": user.class_level,
        "track": user.track,
        "state": user.state,
        "firstName": user.first_name,
        "lastName": user.last_name,
        "image": user.image,
        "tier": user.tier,
    }


def issue_student_token(user: User, device_id: str) -> str:
    now = int(utcnow().timestamp())
    return student_token(
        {
            "sub": user.id,
            "profile": profile_dict(user),
            "profileAt": now * 1000,
            "sessionStartedAt": now,
            "deviceId": device_id,
        }
    )


class RegisterStudentService:
    def __init__(self, session: AsyncSession, body: RegisterIn, ip: str) -> None:
        self.session = session
        self.body = body
        self.ip = ip

    async def process(self) -> dict:
        self._limit()
        email = self.body.email.strip().lower()
        await self._ensure_available(email)
        user = await self._create(email)
        return self._payload(user)

    def _limit(self) -> None:
        hit(f"register:{self.ip}", 5, 600)

    async def _ensure_available(self, email: str) -> None:
        existing = await users_repository.by_email(self.session, email)
        if existing:
            raise ApiError(409, "An account with this email already exists")

    async def _create(self, email: str) -> User:
        user = User(
            id=cuid(),
            email=email,
            first_name=self.body.firstName.strip(),
            last_name=self.body.lastName.strip(),
            password_hash=hash_password(self.body.password),
            role="STUDENT",
            class_level=self.body.classLevel,
            track=self.body.track,
            state=self.body.state,
        )
        try:
            await users_repository.add(self.session, user, flush=True)
        except IntegrityError as exc:
            raise ApiError(409, "An account with this email already exists") from exc
        return user

    def _payload(self, user: User) -> dict:
        return {
            "message": "Account created successfully",
            "user": {
                "id": user.id,
                "email": user.email,
                "firstName": user.first_name,
                "lastName": user.last_name,
                "classLevel": user.class_level,
                "track": user.track,
            },
        }


class RegisterDeviceService:
    def __init__(self, session: AsyncSession, user: User, label: str) -> None:
        self.session = session
        self.user = user
        self.label = label

    async def process(self) -> UserDevice:
        await users_repository.lock_by_id(self.session, self.user.id)
        device = await self._insert()
        await self._revoke_overflow(device)
        return device

    async def _insert(self) -> UserDevice:
        device = UserDevice(
            id=cuid(),
            user_id=self.user.id,
            label=self.label[:120] or "Browser",
            last_seen_at=utcnow(),
        )
        await devices_repository.add(self.session, device, flush=True)
        return device

    async def _revoke_overflow(self, device: UserDevice) -> None:
        if TIER_RANK.get(self.user.tier, 0) < TIER_RANK["STANDARD"]:
            return
        others = await devices_repository.active_except(
            self.session, self.user.id, device.id
        )
        ordered = sorted(
            others, key=lambda row: (as_utc(row.last_seen_at), row.id), reverse=True
        )
        overflow = ordered[DEVICE_LIMIT - 1 :]
        revoked_ids = []
        for row in overflow:
            row.revoked_at = utcnow()
            revoked_ids.append(row.id)
        if revoked_ids:
            await push_subscriptions_repository.delete_for_devices(
                self.session, revoked_ids
            )


class LoginStudentService:
    def __init__(
        self, session: AsyncSession, email: str, password: str, ip: str, label: str
    ) -> None:
        self.session = session
        self.email = email
        self.password = password
        self.ip = ip
        self.label = label

    async def process(self) -> dict | None:
        normalised = self.email.strip().lower()
        if len(self.password) < 6:
            return None
        self._limit(normalised)
        user = await self._authenticate(normalised)
        if user is None:
            return None
        await RefreshUserTierService(self.session, user.id).process()
        device = await RegisterDeviceService(self.session, user, self.label).process()
        return self._payload(user, device)

    def _limit(self, normalised: str) -> None:
        hit(f"login:{normalised}:{self.ip}", 5, 900)
        hit(f"login-email:{normalised}", 20, 3600)

    async def _authenticate(self, normalised: str) -> User | None:
        user = await users_repository.by_email(self.session, normalised)
        if user is None or not user.password_hash or not user.is_active:
            return None
        if not verify_password(self.password, user.password_hash):
            return None
        return user

    def _payload(self, user: User, device: UserDevice) -> dict:
        return {
            "accessToken": issue_student_token(user, device.id),
            "user": {
                "id": user.id,
                "email": user.email,
                **profile_dict(user),
                "deviceId": device.id,
            },
        }


class RevokeDevicesService:
    def __init__(
        self,
        session: AsyncSession,
        user_id: str,
        current_device_id: str | None,
        body: DeviceIn,
    ) -> None:
        self.session = session
        self.user_id = user_id
        self.current_device_id = current_device_id
        self.body = body

    async def process(self) -> dict:
        rows = await self._targets()
        ids = self._revoke(rows)
        if ids:
            await push_subscriptions_repository.delete_for_devices(self.session, ids)
        return {"revoked": len(ids)}

    async def _targets(self) -> list[UserDevice]:
        if self.body.allOthers:
            return await devices_repository.active_except(
                self.session, self.user_id, self.current_device_id
            )
        device_id = self.body.deviceId
        if device_id == self.current_device_id:
            raise ApiError(400, "Use Sign out to leave this device")
        row = await devices_repository.by_id(self.session, device_id)
        if row is None or row.user_id != self.user_id:
            raise ApiError(404, "Device not found")
        return [] if row.revoked_at else [row]

    def _revoke(self, rows: list[UserDevice]) -> list[str]:
        now = utcnow()
        ids = []
        for row in rows:
            row.revoked_at = now
            ids.append(row.id)
        return ids


class RevokeOtherDevicesService:
    def __init__(
        self, session: AsyncSession, user_id: str, current_device_id: str | None
    ) -> None:
        self.session = session
        self.user_id = user_id
        self.current_device_id = current_device_id

    async def process(self) -> None:
        try:
            await RevokeDevicesService(
                self.session,
                self.user_id,
                self.current_device_id,
                DeviceIn(allOthers=True),
            ).process()
        except Exception:
            return


class UpdateProfileService:
    def __init__(
        self, session: AsyncSession, user_id: str, patch: ProfilePatch
    ) -> None:
        self.session = session
        self.user_id = user_id
        self.patch = patch

    async def process(self) -> dict:
        if not self.patch.model_fields_set:
            raise ApiError(400, "Nothing to update")
        user = await self._load()
        self._apply(user)
        try:
            await self.session.flush()
        except IntegrityError as exc:
            raise ApiError(409, "That phone number is already in use") from exc
        return {
            "message": "Profile updated",
            "user": profile_dict(user) | {"id": user.id, "phone": user.phone},
        }

    async def _load(self) -> User:
        user = await users_repository.by_id(self.session, self.user_id)
        if user is None:
            raise ApiError(401, "Unauthorized")
        return user

    def _apply(self, user: User) -> None:
        fields = self.patch.model_fields_set
        if "phone" in fields:
            phone = self.patch.phone
            user.phone = None if phone == "" else phone
        if "state" in fields:
            state = self.patch.state
            user.state = None if state == "" else state
        for source, attr in (
            ("firstName", "first_name"),
            ("lastName", "last_name"),
            ("classLevel", "class_level"),
            ("track", "track"),
        ):
            if source in fields:
                setattr(user, attr, getattr(self.patch, source))


class CompleteProfileService:
    def __init__(
        self,
        session: AsyncSession,
        user_id: str,
        body: CompleteProfileIn,
        device_id: str | None,
    ) -> None:
        self.session = session
        self.user_id = user_id
        self.body = body
        self.device_id = device_id

    async def process(self) -> dict:
        user = await self._load()
        self._validate()
        self._apply(user)
        await self.session.flush()
        token = issue_student_token(user, self.device_id or "")
        return {"message": "Profile completed", "accessToken": token}

    async def _load(self) -> User:
        user = await users_repository.by_id(self.session, self.user_id)
        if user is None:
            raise ApiError(401, "Unauthorized")
        return user

    def _validate(self) -> None:
        if self.body.state not in NIGERIAN_STATES:
            raise ApiError(400, "Choose a Nigerian state", details={"state": "invalid"})

    def _apply(self, user: User) -> None:
        user.class_level = self.body.classLevel
        user.track = self.body.track
        user.state = self.body.state


class ChangePasswordService:
    def __init__(
        self,
        session: AsyncSession,
        user: User,
        current: str,
        new: str,
        device_id: str | None,
    ) -> None:
        self.session = session
        self.user = user
        self.current = current
        self.new = new
        self.device_id = device_id

    async def process(self) -> dict:
        self._validate()
        self.user.password_hash = hash_password(self.new)
        await self.session.flush()
        await RevokeOtherDevicesService(
            self.session, self.user.id, self.device_id
        ).process()
        return {"message": "Password changed"}

    def _validate(self) -> None:
        hit(f"password:{self.user.id}", 5, 900)
        if not self.user.password_hash:
            raise ApiError(400, "This account has no password")
        if not verify_password(self.current, self.user.password_hash):
            raise ApiError(400, "Current password is wrong")


class GetSettingsProfileService:
    def __init__(self, session: AsyncSession, user_id: str) -> None:
        self.session = session
        self.user_id = user_id

    async def process(self) -> dict:
        user = await self._load()
        device_rows = await devices_repository.for_user(self.session, self.user_id)
        prefs = await preferences_repository.by_id(self.session, self.user_id)
        tier, expires = await self._tier()
        return self._payload(user, device_rows, prefs, tier, expires)

    async def _load(self) -> User:
        user = await users_repository.by_id(self.session, self.user_id)
        if user is None:
            raise ApiError(401, "Unauthorized")
        return user

    async def _tier(self):
        rows = await subscriptions_repository.for_user(self.session, self.user_id)
        return resolve_live_tier(
            [
                {
                    "status": row.status,
                    "tier": row.tier,
                    "startsAt": row.starts_at,
                    "endsAt": row.ends_at,
                }
                for row in rows
            ],
            utcnow(),
        )

    def _payload(self, user, device_rows, prefs, tier, expires) -> dict:
        return {
            "id": user.id,
            "firstName": user.first_name,
            "lastName": user.last_name,
            "email": user.email,
            "phone": user.phone,
            "state": user.state,
            "classLevel": user.class_level,
            "track": user.track,
            "image": user.image,
            "tier": tier,
            "tierExpiresAt": expires.isoformat() if expires else None,
            "hasPassword": bool(user.password_hash),
            "devices": [
                {
                    "id": device.id,
                    "label": device.label,
                    "lastSeenAt": device.last_seen_at.isoformat()
                    if device.last_seen_at
                    else None,
                    "revokedAt": device.revoked_at.isoformat()
                    if device.revoked_at
                    else None,
                }
                for device in device_rows
            ],
            "notificationPreferences": {
                "studyReminders": True if prefs is None else prefs.study_reminders,
                "streakReminders": True if prefs is None else prefs.streak_reminders,
                "announcements": True if prefs is None else prefs.announcements,
            },
        }


class UpdateNotificationPreferencesService:
    def __init__(
        self, session: AsyncSession, user_id: str, patch: PreferencePatch
    ) -> None:
        self.session = session
        self.user_id = user_id
        self.patch = patch

    async def process(self) -> dict:
        prefs = await self._load()
        self._apply(prefs)
        await self.session.flush()
        return {
            "studyReminders": prefs.study_reminders,
            "streakReminders": prefs.streak_reminders,
            "announcements": prefs.announcements,
        }

    async def _load(self) -> NotificationPreference:
        prefs = await preferences_repository.by_id(self.session, self.user_id)
        if prefs is None:
            prefs = NotificationPreference(user_id=self.user_id)
            await preferences_repository.add(self.session, prefs)
        return prefs

    def _apply(self, prefs: NotificationPreference) -> None:
        fields = self.patch.model_fields_set
        if "studyReminders" in fields and self.patch.studyReminders is not None:
            prefs.study_reminders = self.patch.studyReminders
        if "streakReminders" in fields and self.patch.streakReminders is not None:
            prefs.streak_reminders = self.patch.streakReminders
        if "announcements" in fields and self.patch.announcements is not None:
            prefs.announcements = self.patch.announcements


class SetAvatarService:
    def __init__(
        self, session: AsyncSession, user_id: str, content_type: str | None, data: bytes
    ) -> None:
        self.session = session
        self.user_id = user_id
        self.content_type = content_type
        self.data = data

    async def process(self) -> dict:
        extension = self._validate()
        image = await self._upload(extension)
        await self._save(image)
        return {"message": "Photo updated", "image": image}

    def _validate(self) -> str:
        if not settings.cloudinary_enabled:
            raise ApiError(503, "Cloudinary is not configured")
        allowed = {"image/jpeg": "jpg", "image/png": "png", "image/webp": "webp"}
        if self.content_type not in allowed:
            raise ApiError(400, "Photo must be JPEG, PNG, or WebP")
        if not self.data:
            raise ApiError(400, "Missing file")
        if len(self.data) > 2_000_000:
            raise ApiError(400, "Photo must be 2 MB or smaller")
        return allowed[self.content_type]

    async def _upload(self, extension: str) -> str:
        folder = "prepwell/avatars"
        timestamp = int(utcnow().timestamp())
        signed = (
            f"folder={folder}&timestamp={timestamp}{settings.cloudinary_api_secret}"
        )
        signature = hashlib.sha1(signed.encode()).hexdigest()
        url = (
            f"https://api.cloudinary.com/v1_1/{settings.cloudinary_cloud_name}"
            "/image/upload"
        )
        try:
            async with httpx.AsyncClient(timeout=20) as client:
                response = await client.post(
                    url,
                    data={
                        "api_key": settings.cloudinary_api_key,
                        "timestamp": str(timestamp),
                        "signature": signature,
                        "folder": folder,
                    },
                    files={
                        "file": (
                            f"avatar.{extension}",
                            self.data,
                            self.content_type,
                        )
                    },
                )
        except httpx.HTTPError as exc:
            raise ApiError(400, "Photo upload failed") from exc
        if response.status_code >= 400:
            raise ApiError(400, "Photo upload failed")
        image = response.json().get("secure_url")
        if not image:
            raise ApiError(400, "Photo upload failed")
        return image

    async def _save(self, image: str) -> None:
        user = await users_repository.by_id(self.session, self.user_id)
        if user is None:
            raise ApiError(404, "Account not found")
        user.image = image


class GoogleSignInService:
    def __init__(self, session: AsyncSession, profile: dict, label: str) -> None:
        self.session = session
        self.profile = profile
        self.label = label

    async def process(self) -> dict:
        account, user = await self._find()
        user = await self._ensure_user(user)
        await self._ensure_account(account, user)
        self._assert_active(user)
        await RefreshUserTierService(self.session, user.id).process()
        device = await RegisterDeviceService(self.session, user, self.label).process()
        return {
            "accessToken": issue_student_token(user, device.id),
            "user": {"id": user.id, **profile_dict(user)},
        }

    async def _find(self) -> tuple[Account | None, User | None]:
        account = await accounts_repository.by_provider(
            self.session, "google", self.profile["sub"]
        )
        user = (
            await users_repository.by_id(self.session, account.user_id)
            if account
            else None
        )
        if user is None and self.profile.get("email"):
            user = await users_repository.by_email(
                self.session, self.profile["email"].lower()
            )
        return account, user

    async def _ensure_user(self, user: User | None) -> User:
        if user is not None:
            return user
        created = User(
            id=cuid(),
            email=self.profile.get("email", "").lower() or None,
            first_name=self.profile.get("given_name"),
            last_name=self.profile.get("family_name"),
            image=self.profile.get("picture"),
            role="STUDENT",
        )
        await users_repository.add(self.session, created, flush=True)
        return created

    async def _ensure_account(self, account: Account | None, user: User) -> None:
        if account is not None:
            return
        await accounts_repository.add(
            self.session,
            Account(
                id=cuid(),
                user_id=user.id,
                type="oauth",
                provider="google",
                provider_account_id=self.profile["sub"],
            ),
        )

    def _assert_active(self, user: User) -> None:
        if not user.is_active or is_session_revoked(
            user.is_active, user.sessions_valid_from, int(utcnow().timestamp())
        ):
            raise ApiError(401, "Unauthorized")


async def exchange_google_code(code: str, redirect_uri: str) -> dict:
    if not settings.google_enabled:
        raise ApiError(503, "Google sign-in is not configured")
    async with httpx.AsyncClient(timeout=10) as client:
        token_response = await client.post(
            "https://oauth2.googleapis.com/token",
            data={
                "code": code,
                "client_id": settings.auth_google_id,
                "client_secret": settings.auth_google_secret,
                "redirect_uri": redirect_uri,
                "grant_type": "authorization_code",
            },
        )
        if token_response.status_code >= 400:
            raise ApiError(400, "Google sign-in failed")
        access = token_response.json().get("access_token")
        profile_response = await client.get(
            "https://openidconnect.googleapis.com/v1/userinfo",
            headers={"Authorization": f"Bearer {access}"},
        )
        profile_response.raise_for_status()
        return profile_response.json()


def blank_to_none(value: str | None) -> str | None:
    if value is None or value == "":
        return None
    return value


def validate_phone(value: str | None) -> str | None:
    if value in (None, ""):
        return value
    if not PHONE.match(value):
        raise ApiError(
            400, "Validation failed", details={"phone": "Enter a Nigerian phone number"}
        )
    return value
