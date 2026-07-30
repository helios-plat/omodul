"""Tests for the payment session vertical (SPEC §4.6 payment sessions).

Covers: create_payment_sessions, update_payment_sessions, set_payment_session.

Validation-path tests (no pool) always run. Persisted-path tests are integration
tests against real PostgreSQL + Redis; they auto-skip when either is unavailable.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest
from obase.commerce_batch_schema import ensure_commerce_batch_schema
from obase.payment_providers import ManualPaymentProvider
from obase.persistence.pool import PgPool
from obase.provider_registry import ProviderRegistry
from obase.uuid7 import uuid7

from omodul.add_line_item_to_cart import AddLineItemConfig, AddLineItemInput, add_line_item_to_cart
from omodul.create_cart import CreateCartConfig, CreateCartInput, create_cart
from omodul.create_inventory_batch import (
    CreateInventoryBatchConfig,
    CreateInventoryBatchInput,
    create_inventory_batch,
)
from omodul.create_payment_sessions import (
    CreatePaymentSessionsConfig,
    CreatePaymentSessionsInput,
    create_payment_sessions,
)
from omodul.set_payment_session import (
    SetPaymentSessionConfig,
    SetPaymentSessionInput,
    set_payment_session,
)
from omodul.update_payment_sessions import (
    UpdatePaymentSessionsConfig,
    UpdatePaymentSessionsInput,
    update_payment_sessions,
)

TEST_PG_DSN = os.environ.get("TEST_PG_DSN", "postgresql://postgres:test@localhost:5432/obase_test")
TEST_REDIS_URL = os.environ.get("TEST_REDIS_URL", "redis://localhost:6379/0")

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
]


# ---------------------------------------------------------------------------
# Validation-path tests (no DB) — always run
# ---------------------------------------------------------------------------


class TestCreatePaymentSessionsValidation:
    async def test_empty_provider_names_rejected(self):
        r = await create_payment_sessions(
            CreatePaymentSessionsConfig(),
            CreatePaymentSessionsInput(cart_id="c", provider_names=[]),
            _OUT,
        )
        assert r["status"] == "failed"

    async def test_no_pool_rejected(self):
        r = await create_payment_sessions(
            CreatePaymentSessionsConfig(),
            CreatePaymentSessionsInput(cart_id="c", provider_names=["manual"]),
            _OUT,
        )
        assert r["status"] == "failed"
        assert "pool is required" in r["error"]["message"]


class TestUpdatePaymentSessionsValidation:
    async def test_no_pool_rejected(self):
        r = await update_payment_sessions(
            UpdatePaymentSessionsConfig(), UpdatePaymentSessionsInput(cart_id="c"), _OUT
        )
        assert r["status"] == "failed"
        assert "pool is required" in r["error"]["message"]


class TestSetPaymentSessionValidation:
    async def test_no_pool_rejected(self):
        r = await set_payment_session(
            SetPaymentSessionConfig(),
            SetPaymentSessionInput(cart_id="c", provider_name="manual"),
            _OUT,
        )
        assert r["status"] == "failed"
        assert "pool is required" in r["error"]["message"]


# ---------------------------------------------------------------------------
# Integration tests — real PostgreSQL + Redis, auto-skip if unavailable
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _clear_pool_registry():
    PgPool.clear()
    yield
    PgPool.clear()


@pytest.fixture(autouse=True)
def _clear_provider_registry():
    ProviderRegistry.clear()
    yield
    ProviderRegistry.clear()


@pytest.fixture
def manual_provider():
    provider = ManualPaymentProvider()
    ProviderRegistry.get().register_generic("payment", "manual", provider)
    return provider


@pytest.fixture
async def commerce_pool():
    import asyncpg

    try:
        conn = await asyncpg.connect(TEST_PG_DSN, timeout=3)
        await conn.close()
    except Exception:
        pytest.skip("PostgreSQL not available")

    try:
        import redis.asyncio as redis_lib

        client = redis_lib.Redis.from_url(TEST_REDIS_URL)
        await client.ping()
        await client.aclose()
    except Exception:
        pytest.skip("Redis not available")

    pool = await PgPool.create(
        name="commerce_payment_session_integ", dsn=TEST_PG_DSN, min_size=1, max_size=10
    )
    await ensure_commerce_batch_schema(pool)
    yield pool
    async with pool.acquire() as conn:
        for table in reversed(_TABLES):
            await conn.execute(f'DROP TABLE IF EXISTS "public"."{table}" CASCADE')
    await pool.close()


async def _seed_variant(pool: PgPool) -> tuple[str, str]:
    async with pool.acquire() as conn:
        loc_id = str(uuid7())
        await conn.execute(
            'INSERT INTO "public"."stock_location" (id, name, region_code) VALUES ($1, $2, $3)',
            loc_id,
            "仓A",
            "cn-east",
        )
        prod_id = str(uuid7())
        await conn.execute(
            'INSERT INTO "public"."product" (id, title, slug) VALUES ($1, $2, $3)',
            prod_id,
            "T恤",
            f"tshirt-{prod_id}",
        )
        variant_id = str(uuid7())
        await conn.execute(
            'INSERT INTO "public"."product_variant" (id, product_id, sku_code) VALUES ($1, $2, $3)',
            variant_id,
            prod_id,
            f"SKU-{variant_id}",
        )
    return variant_id, loc_id


async def _make_batch(pool: PgPool, *, variant_id: str, location_id: str) -> str:
    r = await create_inventory_batch(
        CreateInventoryBatchConfig(),
        CreateInventoryBatchInput(
            variant_id=variant_id,
            location_id=location_id,
            batch_no=f"B-{uuid7()}",
            video_url="https://x/v.mp4",
            cost_price_cents=1000,
            retail_price_cents=2000,
            stock_qty=10,
            inspected_by="qc1",
        ),
        _OUT,
        pool=pool,
    )
    assert r["status"] == "completed", r
    return r["batch_id"]


async def _make_cart_with_total(pool: PgPool, *, quantity: int = 1) -> str:
    r = await create_cart(
        CreateCartConfig(), CreateCartInput(region_code="cn-east"), _OUT, pool=pool
    )
    assert r["status"] == "completed", r
    cart_id = r["cart_id"]
    variant_id, loc_id = await _seed_variant(pool)
    batch_id = await _make_batch(pool, variant_id=variant_id, location_id=loc_id)
    await add_line_item_to_cart(
        AddLineItemConfig(redis_url=TEST_REDIS_URL),
        AddLineItemInput(cart_id=cart_id, batch_id=batch_id, quantity=quantity),
        _OUT,
        pool=pool,
    )
    return cart_id


class TestCreatePaymentSessionsIntegration:
    async def test_creates_authorized_session_for_registered_provider(
        self, commerce_pool, manual_provider
    ):
        cart_id = await _make_cart_with_total(commerce_pool)  # grand_total 2000
        r = await create_payment_sessions(
            CreatePaymentSessionsConfig(),
            CreatePaymentSessionsInput(cart_id=cart_id, provider_names=["manual"]),
            _OUT,
            pool=commerce_pool,
        )
        assert r["status"] == "completed", r
        assert r["amount_due_cents"] == 2000
        assert len(r["sessions"]) == 1
        assert r["sessions"][0]["status"] == "authorized"
        assert r["sessions"][0]["already_existed"] is False

    async def test_unregistered_provider_yields_failed_session_not_whole_failure(
        self, commerce_pool
    ):
        cart_id = await _make_cart_with_total(commerce_pool)
        r = await create_payment_sessions(
            CreatePaymentSessionsConfig(),
            CreatePaymentSessionsInput(cart_id=cart_id, provider_names=["ghost_provider"]),
            _OUT,
            pool=commerce_pool,
        )
        assert r["status"] == "completed", r
        assert r["sessions"][0]["status"] == "failed"

    async def test_mixed_providers_partial_success(self, commerce_pool, manual_provider):
        cart_id = await _make_cart_with_total(commerce_pool)
        r = await create_payment_sessions(
            CreatePaymentSessionsConfig(),
            CreatePaymentSessionsInput(cart_id=cart_id, provider_names=["manual", "ghost"]),
            _OUT,
            pool=commerce_pool,
        )
        assert r["status"] == "completed", r
        statuses = {s["provider_name"]: s["status"] for s in r["sessions"]}
        assert statuses == {"manual": "authorized", "ghost": "failed"}

    async def test_second_call_skips_existing_session(self, commerce_pool, manual_provider):
        cart_id = await _make_cart_with_total(commerce_pool)
        cfg = CreatePaymentSessionsConfig()
        r1 = await create_payment_sessions(
            cfg,
            CreatePaymentSessionsInput(cart_id=cart_id, provider_names=["manual"]),
            _OUT,
            pool=commerce_pool,
        )
        r2 = await create_payment_sessions(
            cfg,
            CreatePaymentSessionsInput(cart_id=cart_id, provider_names=["manual"]),
            _OUT,
            pool=commerce_pool,
        )
        assert r2["sessions"][0]["already_existed"] is True
        assert r1["sessions"][0]["session_id"] == r2["sessions"][0]["session_id"]

        async with commerce_pool.acquire() as conn:
            rows = await conn.fetch('SELECT * FROM "payment_session" WHERE cart_id = $1', cart_id)
        assert len(rows) == 1

    async def test_unknown_cart_fails(self, commerce_pool, manual_provider):
        r = await create_payment_sessions(
            CreatePaymentSessionsConfig(),
            CreatePaymentSessionsInput(cart_id=str(uuid7()), provider_names=["manual"]),
            _OUT,
            pool=commerce_pool,
        )
        assert r["status"] == "failed"
        assert "not found" in r["error"]["message"]


class TestUpdatePaymentSessionsIntegration:
    async def test_refreshes_amount_and_intent_when_cart_total_changes(
        self, commerce_pool, manual_provider
    ):
        cart_id = await _make_cart_with_total(commerce_pool)  # grand_total 2000
        await create_payment_sessions(
            CreatePaymentSessionsConfig(),
            CreatePaymentSessionsInput(cart_id=cart_id, provider_names=["manual"]),
            _OUT,
            pool=commerce_pool,
        )
        async with commerce_pool.acquire() as conn:
            old_intent = await conn.fetchval(
                'SELECT provider_intent_id FROM "payment_session" WHERE cart_id = $1', cart_id
            )

        # Add another line to bump the cart total.
        variant_id, loc_id = await _seed_variant(commerce_pool)
        batch_id = await _make_batch(commerce_pool, variant_id=variant_id, location_id=loc_id)
        await add_line_item_to_cart(
            AddLineItemConfig(redis_url=TEST_REDIS_URL),
            AddLineItemInput(cart_id=cart_id, batch_id=batch_id, quantity=1),
            _OUT,
            pool=commerce_pool,
        )  # grand_total now 4000

        r = await update_payment_sessions(
            UpdatePaymentSessionsConfig(),
            UpdatePaymentSessionsInput(cart_id=cart_id),
            _OUT,
            pool=commerce_pool,
        )
        assert r["status"] == "completed", r
        assert r["amount_due_cents"] == 4000
        assert r["sessions"][0]["status"] == "authorized"

        async with commerce_pool.acquire() as conn:
            row = await conn.fetchrow(
                'SELECT amount_cents, provider_intent_id FROM "payment_session" WHERE cart_id = $1',
                cart_id,
            )
        assert row["amount_cents"] == 4000
        assert row["provider_intent_id"] != old_intent  # got a fresh intent, not the stale one

    async def test_does_not_touch_selected_session(self, commerce_pool, manual_provider):
        cart_id = await _make_cart_with_total(commerce_pool)
        await create_payment_sessions(
            CreatePaymentSessionsConfig(),
            CreatePaymentSessionsInput(cart_id=cart_id, provider_names=["manual"]),
            _OUT,
            pool=commerce_pool,
        )
        await set_payment_session(
            SetPaymentSessionConfig(),
            SetPaymentSessionInput(cart_id=cart_id, provider_name="manual"),
            _OUT,
            pool=commerce_pool,
        )

        r = await update_payment_sessions(
            UpdatePaymentSessionsConfig(),
            UpdatePaymentSessionsInput(cart_id=cart_id),
            _OUT,
            pool=commerce_pool,
        )
        assert r["status"] == "completed", r
        assert r["sessions"] == []  # nothing outstanding to refresh — the one session is 'selected'

        async with commerce_pool.acquire() as conn:
            status = await conn.fetchval(
                'SELECT status FROM "payment_session" WHERE cart_id = $1', cart_id
            )
        assert status == "selected"

    async def test_unknown_cart_fails(self, commerce_pool):
        r = await update_payment_sessions(
            UpdatePaymentSessionsConfig(),
            UpdatePaymentSessionsInput(cart_id=str(uuid7())),
            _OUT,
            pool=commerce_pool,
        )
        assert r["status"] == "failed"


class TestSetPaymentSessionIntegration:
    async def test_selects_authorized_session(self, commerce_pool, manual_provider):
        cart_id = await _make_cart_with_total(commerce_pool)
        await create_payment_sessions(
            CreatePaymentSessionsConfig(),
            CreatePaymentSessionsInput(cart_id=cart_id, provider_names=["manual"]),
            _OUT,
            pool=commerce_pool,
        )
        r = await set_payment_session(
            SetPaymentSessionConfig(),
            SetPaymentSessionInput(cart_id=cart_id, provider_name="manual"),
            _OUT,
            pool=commerce_pool,
        )
        assert r["status"] == "completed", r

        async with commerce_pool.acquire() as conn:
            status = await conn.fetchval(
                'SELECT status FROM "payment_session" WHERE cart_id = $1 AND provider_name = $2',
                cart_id,
                "manual",
            )
        assert status == "selected"

    async def test_switching_selection_reverts_previous(self, commerce_pool):
        ProviderRegistry.get().register_generic("payment", "manual_a", ManualPaymentProvider())
        ProviderRegistry.get().register_generic("payment", "manual_b", ManualPaymentProvider())
        cart_id = await _make_cart_with_total(commerce_pool)
        await create_payment_sessions(
            CreatePaymentSessionsConfig(),
            CreatePaymentSessionsInput(cart_id=cart_id, provider_names=["manual_a", "manual_b"]),
            _OUT,
            pool=commerce_pool,
        )
        await set_payment_session(
            SetPaymentSessionConfig(),
            SetPaymentSessionInput(cart_id=cart_id, provider_name="manual_a"),
            _OUT,
            pool=commerce_pool,
        )
        await set_payment_session(
            SetPaymentSessionConfig(),
            SetPaymentSessionInput(cart_id=cart_id, provider_name="manual_b"),
            _OUT,
            pool=commerce_pool,
        )

        async with commerce_pool.acquire() as conn:
            rows = await conn.fetch(
                'SELECT provider_name, status FROM "payment_session" WHERE cart_id = $1', cart_id
            )
        statuses = {r["provider_name"]: r["status"] for r in rows}
        assert statuses == {"manual_a": "authorized", "manual_b": "selected"}

    async def test_selecting_failed_session_rejected(self, commerce_pool):
        cart_id = await _make_cart_with_total(commerce_pool)
        await create_payment_sessions(
            CreatePaymentSessionsConfig(),
            CreatePaymentSessionsInput(cart_id=cart_id, provider_names=["ghost"]),
            _OUT,
            pool=commerce_pool,
        )
        r = await set_payment_session(
            SetPaymentSessionConfig(),
            SetPaymentSessionInput(cart_id=cart_id, provider_name="ghost"),
            _OUT,
            pool=commerce_pool,
        )
        assert r["status"] == "failed"
        assert "not selectable" in r["error"]["message"]

    async def test_unknown_session_rejected(self, commerce_pool):
        cart_id = await _make_cart_with_total(commerce_pool)
        r = await set_payment_session(
            SetPaymentSessionConfig(),
            SetPaymentSessionInput(cart_id=cart_id, provider_name="never_created"),
            _OUT,
            pool=commerce_pool,
        )
        assert r["status"] == "failed"
        assert "no payment session" in r["error"]["message"]
