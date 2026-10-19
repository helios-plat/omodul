"""Tests for update_cart (SPEC §4.6): the general-purpose raw cart container updater.

Validation-path tests (no pool) always run. Persisted-path tests are integration
tests against real PostgreSQL; they auto-skip when it is unavailable.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest
from obase.commerce_batch_schema import ensure_commerce_batch_schema
from obase.persistence.pool import PgPool
from obase.uuid7 import uuid7
from pydantic import ValidationError

from omodul.create_cart import CreateCartConfig, CreateCartInput, create_cart
from omodul.update_cart import UpdateCartConfig, UpdateCartInput, update_cart

TEST_PG_DSN = os.environ.get("TEST_PG_DSN", "postgresql://postgres:test@localhost:5432/obase_test")

_OUT = Path("/tmp")
_TABLES = [
    "stock_location",
    "product",
    "product_variant",
    "inventory_batch",
    "cart",
    "cart_line_item",
    "discount",
    "discount_rule",
    "discount_condition",
    "gift_card",
    "cart_discount",
    "cart_gift_card",
    "payment_session",
    "customer_order",
    "order_line_item",
]


# ---------------------------------------------------------------------------
# Validation-path tests (no DB) — always run
# ---------------------------------------------------------------------------


class TestUpdateCartValidation:
    async def test_no_pool_rejected(self):
        r = await update_cart(UpdateCartConfig(), UpdateCartInput(cart_id="c"), _OUT)
        assert r["status"] == "failed"
        assert "pool is required" in r["error"]["message"]

    async def test_invalid_status_rejected_before_db(self):
        r = await update_cart(
            UpdateCartConfig(), UpdateCartInput(cart_id="c", status="bogus"), _OUT
        )
        assert r["status"] == "failed"
        assert "invalid status" in r["error"]["message"]

    def test_unknown_field_rejected(self):
        # region_code is a real cart column but belongs to set_cart_region —
        # update_cart must not accept it (or any other unknown field).
        with pytest.raises(ValidationError):
            UpdateCartInput(cart_id="c", region_code="cn-east")


# ---------------------------------------------------------------------------
# Integration tests — real PostgreSQL, auto-skip if unavailable
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _clear_pool_registry():
    PgPool.clear()
    yield
    PgPool.clear()


@pytest.fixture
async def commerce_pool():
    import asyncpg

    try:
        conn = await asyncpg.connect(TEST_PG_DSN, timeout=3)
        await conn.close()
    except Exception:
        pytest.skip("PostgreSQL not available")

    pool = await PgPool.create(
        name="update_cart_integ", dsn=TEST_PG_DSN, min_size=1, max_size=10
    )
    await ensure_commerce_batch_schema(pool)
    yield pool
    async with pool.acquire() as conn:
        for table in reversed(_TABLES):
            await conn.execute(f'DROP TABLE IF EXISTS "public"."{table}" CASCADE')
    await pool.close()


class TestUpdateCartIntegration:
    async def test_status_change_persisted_and_updated_at_touched(self, commerce_pool):
        r = await create_cart(
            CreateCartConfig(), CreateCartInput(region_code="cn-east"), _OUT, pool=commerce_pool
        )
        cart_id = r["cart_id"]
        async with commerce_pool.acquire() as conn:
            before = await conn.fetchval('SELECT updated_at FROM "cart" WHERE id = $1', cart_id)

        r2 = await update_cart(
            UpdateCartConfig(),
            UpdateCartInput(cart_id=cart_id, status="abandoned"),
            _OUT,
            pool=commerce_pool,
        )
        assert r2["status"] == "completed", r2
        assert r2["cart_id"] == cart_id
        assert r2["updated_fields"] == ["status"]
        assert r2["status_value"] == "abandoned"
        assert r2["fingerprint"]

        async with commerce_pool.acquire() as conn:
            row = await conn.fetchrow(
                'SELECT status, region_code, updated_at FROM "cart" WHERE id = $1', cart_id
            )
        assert row["status"] == "abandoned"
        # Raw write touched updated_at; raw write did NOT touch unrelated fields.
        assert row["updated_at"] is not None
        assert row["updated_at"] != before
        assert row["region_code"] == "cn-east"

    async def test_partial_multi_field_raw_write_no_side_effects(self, commerce_pool):
        r = await create_cart(
            CreateCartConfig(), CreateCartInput(region_code="cn-east"), _OUT, pool=commerce_pool
        )
        cart_id = r["cart_id"]
        customer_id = str(uuid7())

        r2 = await update_cart(
            UpdateCartConfig(),
            UpdateCartInput(cart_id=cart_id, customer_id=customer_id, currency="USD"),
            _OUT,
            pool=commerce_pool,
        )
        assert r2["status"] == "completed", r2
        assert sorted(r2["updated_fields"]) == ["currency", "customer_id"]
        assert r2["customer_id"] == customer_id
        assert r2["currency"] == "USD"

        async with commerce_pool.acquire() as conn:
            row = await conn.fetchrow(
                'SELECT customer_id, currency, status, region_code FROM "cart" WHERE id = $1',
                cart_id,
            )
        assert str(row["customer_id"]) == customer_id
        assert row["currency"] == "USD"
        # Raw writes trigger no lifecycle: status stays at the schema default.
        assert row["status"] == "active"
        assert row["region_code"] == "cn-east"

    async def test_unknown_cart_rejected(self, commerce_pool):
        r = await update_cart(
            UpdateCartConfig(),
            UpdateCartInput(cart_id=str(uuid7()), status="abandoned"),
            _OUT,
            pool=commerce_pool,
        )
        assert r["status"] == "failed"
        assert "not found" in r["error"]["message"]

    async def test_no_updatable_fields_rejected(self, commerce_pool):
        r = await create_cart(
            CreateCartConfig(), CreateCartInput(region_code="cn-east"), _OUT, pool=commerce_pool
        )
        r2 = await update_cart(
            UpdateCartConfig(), UpdateCartInput(cart_id=r["cart_id"]), _OUT, pool=commerce_pool
        )
        assert r2["status"] == "failed"
        assert "no updatable fields" in r2["error"]["message"]
