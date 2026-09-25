from sqlalchemy import and_, or_


class Filter:
    """
    A class to construct and combine SQLAlchemy filter conditions dynamically,
    mimicking the behavior of Django's Q objects for complex query building.

    Attributes:
        conditions (tuple): SQLAlchemy filter conditions, such as column comparisons.

    Methods:
        __and__(other: Filter) -> Filter:
            Combines this filter with another using logical AND.

        __or__(other: Filter) -> Filter:
            Combines this filter with another using logical OR.

        build() -> Any:
            Compiles the filter conditions into a single SQLAlchemy expression.



        Sample Usage:

            from sqlalchemy import or_
            from sqlalchemy.ext.asyncio import AsyncSession

            email_filter = Filter(UserDb.email == "test@example.com")
            username_filter = Filter(UserDb.username == "testuser")

            # Combine filters with AND
            and_filter = email_filter & username_filter

            # Combine filters with OR
            or_filter = email_filter | username_filter

            # Use the filter in a query
            async def filter_users(session: AsyncSession):
                statement = select(UserDb).where(and_filter.build())
                result = await session.execute(statement)
                return result.scalars().all()
    """

    def __init__(self, *conditions):
        """
        Initialize a Filter instance with one or more SQLAlchemy filter conditions.

        Args:
            *conditions: One or more SQLAlchemy expressions (e.g., column == value).
        """
        self.conditions = conditions

    def __and__(self, other: "Filter"):
        """
        Combine this filter with another Filter using logical AND.

        Args:
            other (Filter): Another Filter instance to combine with.

        Returns:
            Filter: A new Filter instance representing the combined conditions.
        """
        return Filter(and_(*self.conditions, *other.conditions))

    def __or__(self, other: "Filter"):
        """
        Combine this filter with another Filter using logical OR.

        Args:
            other (Filter): Another Filter instance to combine with.

        Returns:
            Filter: A new Filter instance representing the combined conditions.
        """
        return Filter(or_(*self.conditions, *other.conditions))

    def build(self):
        """
        Compile the filter conditions into a single SQLAlchemy expression.

        Returns:
             A single SQLAlchemy expression combining all filter conditions.
                  If only one condition is present, it returns that condition directly.
        """
        if len(self.conditions) == 1:
            return self.conditions[0]
        return and_(*self.conditions)
