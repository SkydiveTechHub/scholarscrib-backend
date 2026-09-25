from collections.abc import Sequence
from typing import Any
from uuid import UUID

from sqlalchemy import and_, delete, func, insert, select, update
from sqlalchemy.engine import CursorResult
from sqlalchemy.exc import MultipleResultsFound, NoResultFound
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.sql import ColumnElement

from app.database.db import Base, get_session
from app.database.repositories.utils import Filter


class BaseRepository[T: Base]:
    """
    A generic base repository for common CRUD on SQLAlchemy models.

    This repository provides reusable methods for querying and managing records,
    including filtering, retrieving by ID, creating, updating, and deleting.
    It also supports query building with `Filter` objects, like Django's `Q`.

    Attributes:
        model (type[T]): The SQLAlchemy model associated with this repository.

    Methods:
        all(session: AsyncSession) -> Optional[List[T]]:
            Retrieve all records for the associated model.

        filter(
            session: AsyncSession,
            *filters: "Filter",
            **kwargs: Dict[str, Any]
        ) -> Optional[List[Any]]:
            Filter records using `Filter` objects and keyword arguments.

        get_by_id(session: AsyncSession, id: UUID) -> Optional[T]:
            Retrieve a single record by its primary key.

        create(session: AsyncSession, kwargs: dict) -> Optional[T]:
            Create a new record with the given attributes.

        bulk_create(session: AsyncSession, data_list: list[dict]) -> Optional[bool]:
            Insert multiple records in a single operation.

        update(
            session: AsyncSession,
            obj: Optional[T],
            update_data: dict
        ) -> T:
            Update an existing record with new data.

        delete(session: AsyncSession, obj: Optional[T]):
            Delete the specified record from the database.

    Sample Usage:
        Define a repository for a specific model:
        >>> class UserRepository(BaseRepository[UserDb]):
        >>>     model = UserDb

        Retrieve all records:
        >>> users = await user_repository.all(session)

        Filter records:
        >>> users = await user_repository.filter(session, email="test@example.com")

        Complex filtering:
        >>> email_filter = Filter(UserDb.email == "test@example.com")
        >>> username_filter = Filter(UserDb.username == "testuser")
        >>> users = await user_repository.filter(
        >>>     session, email_filter | username_filter
        >>> )

        Retrieve by ID:
        >>> user = await user_repository.get_by_id(session, user_id)

        Create a new record:
        >>> new_user = await user_repository.create(
        >>>     session, {"email": "new@example.com", "username": "newuser"}
        >>> )

        Bulk create records:
        >>> data_list = [
        >>>     {"email": "user1@example.com", "username": "user1"},
        >>>     {"email": "user2@example.com", "username": "user2"},
        >>> ]
        >>> success = await user_repository.bulk_create(session, data_list)

        Update an existing record:
        >>> updated_user = await user_repository.update(
        >>>     session, user, {"username": "updateduser"}
        >>> )

        Delete a record:
        >>> await user_repository.delete(session, user)
    """

    model: type[T]

    async def all(self, session: AsyncSession) -> list[T] | None:
        result = (await session.execute(select(self.model))).scalars().all()
        return list(result)

    async def all2(self) -> list[T] | None:
        # TODO switching repositories to use this pattern instead.
        # after confirming it works.
        # to avoid rewrites, put the with statement in one place so every
        # repository function runs inside a new session
        async with get_session() as session:
            result = (await session.execute(select(self.model))).scalars().all()
            return list(result)

    async def filter(
        self,
        session: AsyncSession,
        first: bool = False,
        *filters: Filter,  # Accept positional Filter arguments
        **kwargs: dict[str, Any],  # Accept keyword filters
    ) -> list[T] | T | None:
        """
        Filter records from the database using a combination of `Filter` objects
        and key-value arguments.

        Args:
            session (AsyncSession): The SQLAlchemy AsyncSession instance.
            *filters (Filter): One or more `Filter` objects for complex filtering logic.
            **kwargs (Dict[str, Any]): Column filters as key-value pairs.

        Returns:
            Optional[List[Any]]: A list of model instances that match the filters.


        Sample Usage:
          Filtering with Only Keyword Arguments:
          # Simple filtering with kwargs
         >>> users = await repository.filter(
         >>>     session, email="test@example.com", username="testuser"
         >>> )

          Filtering with Filter Objects
          # Using Filter objects for complex conditions
         >>>   email_filter = Filter(UserDb.email == "test@example.com")
         >>>   username_filter = Filter(UserDb.username == "testuser")

            # Combine filters with OR
         >>>   or_filter = email_filter | username_filter

         >>> users = await repository.filter(session, or_filter)

          Filtering with Both Positional and Keyword Arguments:
          # Complex filtering with positional and kwargs
         >>> users = await repository.filter(
         >>>     session,
         >>>     Filter(UserDb.email == "test@example.com"),
         >>>     username="testuser",
         >>> )


        """
        # Build the condition for keyword arguments
        kwargs_filters = [
            getattr(self.model, key) == value for key, value in kwargs.items()
        ]

        # Combine all filters: positional filters + kwargs filters
        all_filters = [f.build() for f in filters] + kwargs_filters

        # Use AND to combine all filters
        final_condition = and_(*all_filters) if all_filters else None

        # Construct and execute the query
        statement = select(self.model)
        if final_condition is not None:
            statement = statement.where(final_condition)

        result = await session.execute(statement)

        if first:
            return result.scalars().first()

        return list(result.scalars().all())

    async def get_by_id(self, session: AsyncSession, id: UUID) -> T | None:
        return await session.get(self.model, id)

    async def create(self, session: AsyncSession, **kwargs) -> T:
        obj = self.model(**kwargs)
        session.add(obj)
        await session.commit()
        await session.refresh(obj)
        return obj

    async def bulk_create(
        self, session: AsyncSession, data_list: list[dict]
    ) -> list[T]:
        # https://docs.sqlalchemy.org/en/20/orm/queryguide/dml.html#orm-queryguide-bulk-insert

        items = await session.scalars(
            insert(self.model).returning(self.model), data_list
        )
        await session.commit()

        # If this does not work, we use the session.add_all() method

        return list(items.all())

    async def update(
        self, session: AsyncSession, obj: T | None, update_data: dict
    ) -> T:
        if obj is None:
            raise ValueError("Cannot update a missing record")
        for attr, value in update_data.items():
            setattr(obj, attr, value)

        await session.commit()
        await session.refresh(obj)
        return obj

    async def delete(self, session: AsyncSession, obj: T | None):
        if obj:
            await session.delete(obj)
            await session.commit()

    async def get_or_create(
        self, session: AsyncSession, defaults: dict | None = None, **kwargs
    ) -> tuple[T, bool]:
        """
        Retrieve an existing object or create a new one if it doesn't exist.

        Args:
            session (AsyncSession): The SQLAlchemy AsyncSession instance.
            defaults (dict, optional): Attributes set when a new object is created.
            **kwargs: Fields used to find an existing object or create one.

        Returns:
            Tuple[T, bool]: A tuple containing the object and a boolean indicating
            whether it was created (True) or retrieved (False).

        Sample Usage:
            >>> user, created = await user_repository.get_or_create(
            >>>     session,
            >>>     email="user@example.com",
            >>>     defaults={"username": "newuser", "is_active": True},
            >>> )
        """

        # Attempt to filter the object using the `filter` method
        existing_objects = await self.filter(session, **kwargs)

        if isinstance(existing_objects, list) and existing_objects:
            return existing_objects[0], False

        # Merge `kwargs` and `defaults` for creation
        obj_data = {**kwargs}
        if defaults:
            obj_data.update(defaults)

        # Create the object using the `create` method
        instance = await self.create(session, **obj_data)
        return instance, True

    async def get(self, session: AsyncSession, **kwargs) -> T:
        """
        Retrieve a single object based on the provided filter criteria.
        If multiple objects are found, raises a `MultipleResultsFound` error.
        If no object is found, raises a `NoResultFound` error.

        Args:
            session (AsyncSession): The SQLAlchemy AsyncSession instance.
            **kwargs: Key-value pairs to filter the object.

        Returns:
            Optional[T]: A single object that matches the filter, or None if not found.

        Raises:
            MultipleResultsFound: If more than one object matches the filter criteria.

        Sample Usage:
            >>> user = await user_repository.get(session, email="user@example.com")
            >>> if user:
            >>>     print(user.username)
            >>> else:
            >>>     print("User not found")
        """
        # Use the `filter` method to find matching records
        result = await self.filter(session, **kwargs)
        rows = result if isinstance(result, list) else []

        if len(rows) > 1:
            raise MultipleResultsFound(
                "Multiple results found for a query that should return a single object."
            )

        if not rows:
            raise NoResultFound("No results found for the provided query.")

        return rows[0]

    async def by_id(self, session: AsyncSession, id: object) -> T | None:
        return await session.get(self.model, id)

    async def add(self, session: AsyncSession, entity: T, *, flush: bool = False) -> T:
        """Stage a row. The request session commits; this method does not."""
        session.add(entity)
        if flush:
            await session.flush()
        return entity

    async def remove(self, session: AsyncSession, entity: T | None) -> None:
        if entity is not None:
            await session.delete(entity)

    async def one(self, session: AsyncSession, statement) -> T | None:
        return await session.scalar(statement)

    async def many(self, session: AsyncSession, statement) -> list[T]:
        return list((await session.scalars(statement)).all())

    async def rows(self, session: AsyncSession, statement):
        return await session.execute(statement)

    async def count(self, session: AsyncSession, *criteria: ColumnElement[bool]) -> int:
        statement = select(func.count()).select_from(self.model)
        if criteria:
            statement = statement.where(*criteria)
        return int(await session.scalar(statement) or 0)

    async def all_ordered(self, session: AsyncSession, *order_by) -> list[T]:
        statement = select(self.model)
        if order_by:
            statement = statement.order_by(*order_by)
        return await self.many(session, statement)

    async def first(
        self, session: AsyncSession, *criteria: ColumnElement[bool]
    ) -> T | None:
        return await session.scalar(select(self.model).where(*criteria))

    async def list_where(
        self,
        session: AsyncSession,
        *criteria: ColumnElement[bool],
        order_by: Sequence = (),
        limit: int | None = None,
        offset: int | None = None,
    ) -> list[T]:
        statement = select(self.model)
        if criteria:
            statement = statement.where(*criteria)
        if order_by:
            statement = statement.order_by(*order_by)
        if offset:
            statement = statement.offset(offset)
        if limit is not None:
            statement = statement.limit(limit)
        return await self.many(session, statement)

    async def lock(
        self, session: AsyncSession, *criteria: ColumnElement[bool]
    ) -> T | None:
        return await session.scalar(
            select(self.model).where(*criteria).with_for_update()
        )

    async def delete_where(
        self, session: AsyncSession, *criteria: ColumnElement[bool]
    ) -> None:
        await session.execute(delete(self.model).where(*criteria))

    async def update_where(
        self,
        session: AsyncSession,
        values: dict,
        *criteria: ColumnElement[bool],
    ) -> CursorResult:
        result = await session.execute(
            update(self.model).where(*criteria).values(**values)
        )
        if not isinstance(result, CursorResult):
            raise TypeError("Expected a cursor result from an UPDATE")
        return result
