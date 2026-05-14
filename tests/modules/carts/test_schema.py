import app.modules.users.models  # noqa: F401  # FK target: users.id
import app.modules.items.models  # noqa: F401  # FK target: items.id
import app.modules.carts.models  # noqa: F401  # register Cart/CartItem on Base.metadata
import pytest
from sqlalchemy import inspect


pytestmark = [pytest.mark.slow, pytest.mark.asyncio]


async def test_carts_tables_exist(db_session):
    def _names(sync_conn):
        return set(inspect(sync_conn).get_table_names())
    names = await db_session.connection()
    names = await names.run_sync(_names)
    assert {"carts", "cart_items"}.issubset(names)
