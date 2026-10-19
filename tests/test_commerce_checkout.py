"""Tests for the checkout/order domain (SPEC §4.7): authorize_payment_for_cart,
complete_checkout.

Validation-path tests (no pool) always run. Persisted-path tests are integration
tests against real PostgreSQL + Redis; they auto-skip when either is unavailable.
"""

from __future__ import annotations

import asyncio
import os
from pathlib import Path

import pytest
from obase.commerce_batch_schema import ensure_commerce_batch_schema
from obase.payment_providers import ManualPaymentProvider
from obase.persistence.pool import PgPool
from obase.provider_registry import ProviderRegistry
from obase.uuid7 import uuid7

from omodul.add_line_item_to_cart import AddLineItemConfig, AddLineItemInput, add_line_item_to_cart
from omodul.authorize_payment_for_cart import (
    AuthorizePaymentForCartConfig,
    AuthorizePaymentForCartInput,
    authorize_payment_for_cart,
)
from omodul.complete_checkout import (
    CompleteCheckoutConfig,
    CompleteCheckoutInput,
    complete_checkout,
)
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
    "customer_order",
    "order_line_item",
]


# ---------------------------------------------------------------------------
# Validation-path tests (no DB) — always run
# ---------------------------------------------------------------------------


class TestAuthorizePaymentForCartValidation:
    async def test_no_pool_rejected(self):
        r = await authorize_payment_for_cart(
            AuthorizePaymentForCartConfig(), AuthorizePaymentForCartInput(cart_id="c"), _OUT
        )
        assert r["status"] == "failed"
        assert "pool is required" in r["error"]["message"]


class TestCompleteCheckoutValidation:
    async def test_no_pool_rejected(self):
        r = await complete_checkout(
            CompleteCheckoutConfig(), CompleteCheckoutInput(cart_id="c"), _OUT
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
        name="commerce_checkout_integ", dsn=TEST_PG_DSN, min_size=1, max_size=10
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


async def _make_batch(
    pool: PgPool, *, variant_id: str, location_id: str, stock_qty: int = 10
) -> str:
    r = await create_inventory_batch(
        CreateInventoryBatchConfig(),
        CreateInventoryBatchInput(
            variant_id=variant_id,
            location_id=location_id,
            batch_no=f"B-{uuid7()}",
            video_url="https://x/v.mp4",
            cost_price_cents=1000,
            retail_price_cents=2000,
            stock_qty=stock_qty,
            inspected_by="qc1",
        ),
        _OUT,
        pool=pool,
    )
    assert r["status"] == "completed", r
    return r["batch_id"]


async def _ready_cart_for_checkout(pool: PgPool, *, quantity: int = 1) -> tuple[str, str]:
    """Builds a cart through add-line -> payment session -> select -> authorize.
    Returns (cart_id, batch_id)."""
    r = await create_cart(
        CreateCartConfig(), CreateCartInput(region_code="cn-east"), _OUT, pool=pool
    )
    cart_id = r["cart_id"]
    variant_id, loc_id = await _seed_variant(pool)
    batch_id = await _make_batch(pool, variant_id=variant_id, location_id=loc_id)
    await add_line_item_to_cart(
        AddLineItemConfig(redis_url=TEST_REDIS_URL),
        AddLineItemInput(cart_id=cart_id, batch_id=batch_id, quantity=quantity),
        _OUT,
        pool=pool,
    )
    await create_payment_sessions(
        CreatePaymentSessionsConfig(),
        CreatePaymentSessionsInput(cart_id=cart_id, provider_names=["manual"]),
        _OUT,
        pool=pool,
    )
    await set_payment_session(
        SetPaymentSessionConfig(),
        SetPaymentSessionInput(cart_id=cart_id, provider_name="manual"),
        _OUT,
        pool=pool,
    )
    r2 = await authorize_payment_for_cart(
        AuthorizePaymentForCartConfig(),
        AuthorizePaymentForCartInput(cart_id=cart_id),
        _OUT,
        pool=pool,
    )
    assert r2["status"] == "completed", r2
    return cart_id, batch_id


class TestAuthorizePaymentForCartIntegration:
    async def test_happy_path_advances_cart_status(self, commerce_pool, manual_provider):
        cart_id, _ = await _ready_cart_for_checkout(commerce_pool)
        async with commerce_pool.acquire() as conn:
            status = await conn.fetchval('SELECT status FROM "cart" WHERE id = $1', cart_id)
        assert status == "payment_authorized"

    async def test_no_selected_session_rejected(self, commerce_pool, manual_provider):
        r = await create_cart(
            CreateCartConfig(), CreateCartInput(region_code="cn-east"), _OUT, pool=commerce_pool
        )
        cart_id = r["cart_id"]
        r2 = await authorize_payment_for_cart(
            AuthorizePaymentForCartConfig(),
            AuthorizePaymentForCartInput(cart_id=cart_id),
            _OUT,
            pool=commerce_pool,
        )
        assert r2["status"] == "failed"
        assert "no selected payment session" in r2["error"]["message"]

    async def test_stale_session_amount_rejected(self, commerce_pool, manual_provider):
        cart_id, _ = await _ready_cart_for_checkout(commerce_pool)
        # Bump the cart total behind the session's back (simulating a change
        # that should have gone through update_payment_sessions).
        variant_id, loc_id = await _seed_variant(commerce_pool)
        batch_id = await _make_batch(commerce_pool, variant_id=variant_id, location_id=loc_id)
        await add_line_item_to_cart(
            AddLineItemConfig(redis_url=TEST_REDIS_URL),
            AddLineItemInput(cart_id=cart_id, batch_id=batch_id, quantity=1),
            _OUT,
            pool=commerce_pool,
        )

        r = await authorize_payment_for_cart(
            AuthorizePaymentForCartConfig(),
            AuthorizePaymentForCartInput(cart_id=cart_id),
            _OUT,
            pool=commerce_pool,
        )
        assert r["status"] == "failed"
        assert "stale" in r["error"]["message"]

    async def test_stale_session_recovers_after_update_payment_sessions(
        self, commerce_pool, manual_provider
    ):
        cart_id, _ = await _ready_cart_for_checkout(commerce_pool)
        variant_id, loc_id = await _seed_variant(commerce_pool)
        batch_id = await _make_batch(commerce_pool, variant_id=variant_id, location_id=loc_id)
        await add_line_item_to_cart(
            AddLineItemConfig(redis_url=TEST_REDIS_URL),
            AddLineItemInput(cart_id=cart_id, batch_id=batch_id, quantity=1),
            _OUT,
            pool=commerce_pool,
        )

        await update_payment_sessions(
            UpdatePaymentSessionsConfig(),
            UpdatePaymentSessionsInput(cart_id=cart_id),
            _OUT,
            pool=commerce_pool,
        )
        r = await authorize_payment_for_cart(
            AuthorizePaymentForCartConfig(),
            AuthorizePaymentForCartInput(cart_id=cart_id),
            _OUT,
            pool=commerce_pool,
        )
        assert r["status"] == "completed", r

    async def test_already_completed_cart_rejected(self, commerce_pool, manual_provider):
        cart_id, _ = await _ready_cart_for_checkout(commerce_pool)
        await complete_checkout(
            CompleteCheckoutConfig(redis_url=TEST_REDIS_URL),
            CompleteCheckoutInput(cart_id=cart_id),
            _OUT,
            pool=commerce_pool,
        )
        r = await authorize_payment_for_cart(
            AuthorizePaymentForCartConfig(),
            AuthorizePaymentForCartInput(cart_id=cart_id),
            _OUT,
            pool=commerce_pool,
        )
        assert r["status"] == "failed"
        assert "already been checked out" in r["error"]["message"]


class TestCompleteCheckoutIntegration:
    async def test_happy_path_creates_order_and_converts_reservation(
        self, commerce_pool, manual_provider
    ):
        cart_id, batch_id = await _ready_cart_for_checkout(commerce_pool, quantity=3)

        r = await complete_checkout(
            CompleteCheckoutConfig(redis_url=TEST_REDIS_URL),
            CompleteCheckoutInput(cart_id=cart_id),
            _OUT,
            pool=commerce_pool,
        )
        assert r["status"] == "completed", r
        order_id = r["order_id"]
        assert r["grand_total_cents"] == 6000
        assert r["capture_result"]["status"] == "captured"

        async with commerce_pool.acquire() as conn:
            order = await conn.fetchrow('SELECT * FROM "customer_order" WHERE id = $1', order_id)
            lines = await conn.fetch(
                'SELECT * FROM "order_line_item" WHERE order_id = $1', order_id
            )
            batch = await conn.fetchrow(
                'SELECT stock_qty, reserved_qty FROM "inventory_batch" WHERE id = $1', batch_id
            )
            cart_status = await conn.fetchval('SELECT status FROM "cart" WHERE id = $1', cart_id)

        assert str(order["cart_id"]) == cart_id
        assert order["grand_total_cents"] == 6000
        assert order["status"] == "pending"
        assert len(lines) == 1
        assert lines[0]["quantity"] == 3
        # Reservation converted to a permanent sale: both counters drop by 3,
        # not just reserved_qty released.
        assert batch["stock_qty"] == 7
        assert batch["reserved_qty"] == 0
        assert cart_status == "completed"

    async def test_capture_actually_happened_not_just_authorized(
        self, commerce_pool, manual_provider
    ):
        """Prove complete_checkout's capture() call is real by refunding
        afterwards — ManualPaymentProvider.refund() only succeeds on a
        captured intent, never on a merely-authorized one."""
        cart_id, _ = await _ready_cart_for_checkout(commerce_pool)
        r = await complete_checkout(
            CompleteCheckoutConfig(redis_url=TEST_REDIS_URL),
            CompleteCheckoutInput(cart_id=cart_id),
            _OUT,
            pool=commerce_pool,
        )
        intent_id = r["capture_result"]["intent_id"]
        refund = await manual_provider.refund(intent_id=intent_id, amount=r["grand_total_cents"])
        assert refund["status"] == "refunded"

    async def test_cart_not_authorized_rejected(self, commerce_pool, manual_provider):
        r = await create_cart(
            CreateCartConfig(), CreateCartInput(region_code="cn-east"), _OUT, pool=commerce_pool
        )
        cart_id = r["cart_id"]
        r2 = await complete_checkout(
            CompleteCheckoutConfig(redis_url=TEST_REDIS_URL),
            CompleteCheckoutInput(cart_id=cart_id),
            _OUT,
            pool=commerce_pool,
        )
        assert r2["status"] == "failed"
        assert "not ready for checkout" in r2["error"]["message"]

    async def test_stale_amount_between_authorize_and_checkout_rejected(
        self, commerce_pool, manual_provider
    ):
        cart_id, _ = await _ready_cart_for_checkout(commerce_pool)
        # Cart total changes after authorize_payment_for_cart already ran.
        variant_id, loc_id = await _seed_variant(commerce_pool)
        batch_id = await _make_batch(commerce_pool, variant_id=variant_id, location_id=loc_id)
        await add_line_item_to_cart(
            AddLineItemConfig(redis_url=TEST_REDIS_URL),
            AddLineItemInput(cart_id=cart_id, batch_id=batch_id, quantity=1),
            _OUT,
            pool=commerce_pool,
        )
        # Cart status reverted to 'active' by add_line_item? No — status stays
        # 'payment_authorized' since add_line_item doesn't touch cart.status;
        # complete_checkout must still catch the stale amount independently.

        r = await complete_checkout(
            CompleteCheckoutConfig(redis_url=TEST_REDIS_URL),
            CompleteCheckoutInput(cart_id=cart_id),
            _OUT,
            pool=commerce_pool,
        )
        assert r["status"] == "failed"
        assert "no longer matches" in r["error"]["message"]

    async def test_double_checkout_rejected(self, commerce_pool, manual_provider):
        cart_id, _ = await _ready_cart_for_checkout(commerce_pool)
        r1 = await complete_checkout(
            CompleteCheckoutConfig(redis_url=TEST_REDIS_URL),
            CompleteCheckoutInput(cart_id=cart_id),
            _OUT,
            pool=commerce_pool,
        )
        assert r1["status"] == "completed"
        r2 = await complete_checkout(
            CompleteCheckoutConfig(redis_url=TEST_REDIS_URL),
            CompleteCheckoutInput(cart_id=cart_id),
            _OUT,
            pool=commerce_pool,
        )
        assert r2["status"] == "failed"
        assert "not ready for checkout" in r2["error"]["message"]

    async def test_concurrent_checkout_only_one_succeeds(self, commerce_pool, manual_provider):
        """Two concurrent complete_checkout calls for the same cart must be
        serialized by DistributedLock — exactly one creates an order."""
        cart_id, _ = await _ready_cart_for_checkout(commerce_pool)
        cfg = CompleteCheckoutConfig(redis_url=TEST_REDIS_URL, lock_timeout_seconds=5.0)

        results = await asyncio.gather(
            complete_checkout(
                cfg, CompleteCheckoutInput(cart_id=cart_id), _OUT, pool=commerce_pool
            ),
            complete_checkout(
                cfg, CompleteCheckoutInput(cart_id=cart_id), _OUT, pool=commerce_pool
            ),
        )
        statuses = [r["status"] for r in results]
        assert statuses.count("completed") == 1
        assert statuses.count("failed") == 1

        async with commerce_pool.acquire() as conn:
            count = await conn.fetchval(
                'SELECT COUNT(*) FROM "customer_order" WHERE cart_id = $1', cart_id
            )
        assert count == 1
