from datetime import timedelta
from uuid import UUID

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.provider_key import ProviderAPIKey
from app.utils.datetime_utils import utcnow


class ProviderKeyService:
    """Provider API Key management service."""

    def __init__(self, db: AsyncSession):
        self.db = db

    async def get_available_key(self, provider: str) -> ProviderAPIKey | None:
        """
        Get an available key for the given provider.

        Selection criteria:
        - is_active = True
        - is_banned = False
        - cool_down_until is NULL or in the past
        - Order by: consecutive_errors ASC, last_used_at ASC (NULL first)
        """
        now = utcnow()
        result = await self.db.execute(
            select(ProviderAPIKey)
            .where(
                ProviderAPIKey.provider == provider,
                ProviderAPIKey.is_active == True,
                ProviderAPIKey.is_banned == False,
                (
                    (ProviderAPIKey.cool_down_until == None)
                    | (ProviderAPIKey.cool_down_until <= now)
                ),
            )
            .order_by(
                ProviderAPIKey.consecutive_errors.asc(),
                ProviderAPIKey.last_used_at.asc().nullsfirst(),
            )
            .limit(1)
        )
        return result.scalar_one_or_none()

    async def update_key_success(self, key_id: UUID, input_tokens: int, output_tokens: int) -> None:
        """
        Update key stats after a successful request.
        Uses atomic UPDATE (not read-then-write) to avoid race conditions.
        """
        now = utcnow()
        await self.db.execute(
            update(ProviderAPIKey)
            .where(ProviderAPIKey.id == key_id)
            .values(
                total_requests=ProviderAPIKey.total_requests + 1,
                total_input_tokens=ProviderAPIKey.total_input_tokens + input_tokens,
                total_output_tokens=ProviderAPIKey.total_output_tokens + output_tokens,
                consecutive_errors=0,
                last_used_at=now,
            )
        )
        await self.db.commit()

    async def update_key_error(self, key_id: UUID, status_code: int) -> None:
        """
        Update key stats after a failed request.
        - 429 (rate limit): increment errors, set cool_down_until (60s)
        - 401 (auth error): mark as banned
        - Other errors: just increment consecutive_errors
        Uses atomic UPDATE to avoid race conditions.
        """
        now = utcnow()
        if status_code == 429:
            cool_down = now + timedelta(seconds=60)
            await self.db.execute(
                update(ProviderAPIKey)
                .where(ProviderAPIKey.id == key_id)
                .values(
                    consecutive_errors=ProviderAPIKey.consecutive_errors + 1,
                    cool_down_until=cool_down,
                    last_error_at=now,
                )
            )
        elif status_code == 401:
            await self.db.execute(
                update(ProviderAPIKey)
                .where(ProviderAPIKey.id == key_id)
                .values(
                    is_banned=True,
                    ban_reason="Authentication failed (HTTP 401)",
                    last_error_at=now,
                )
            )
        else:
            await self.db.execute(
                update(ProviderAPIKey)
                .where(ProviderAPIKey.id == key_id)
                .values(
                    consecutive_errors=ProviderAPIKey.consecutive_errors + 1,
                    last_error_at=now,
                )
            )
        await self.db.commit()
