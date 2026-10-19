"""Tests for the order lifecycle (SPEC §4.7 remainder): update_order,
cancel_order, archive_order, and the draft-order quartet.

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
from omodul.archive_order import ArchiveOrderConfig, ArchiveOrderInput, archive_order
from omodul.authorize_payment_for_cart import (
    AuthorizePaymentForCartConfig,
    AuthorizePaymentForCartInput,
    authorize_payment_for_cart,
)
from omodul.cancel_order import CancelOrderConfig, CancelOrderInput, cancel_order
from omodul.complete_checkout import (
    CompleteCheckoutConfig,
    CompleteCheckoutInput,
    complete_checkout,
)
from omodul.create_cart import CreateCartConfig, CreateCartInput, create_cart
from omodul.create_draft_order import (
    CreateDraftOrderConfig,
    CreateDraftOrderInput,
    DraftOrderLineItem,
    create_draft_order,
)
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
from omodul.delete_draft_order import (
    DeleteDraftOrderConfig,
    DeleteDraftOrderInput,
    delete_draft_order,
)
from omodul.mark_draft_order_paid import (
    MarkDraftOrderPaidConfig,
    MarkDraftOrderPaidInput,
    mark_draft_order_paid,
)
from omodul.set_payment_session import (
    SetPaymentSessionConfig,
    SetPaymentSessionInput,
    set_payment_session,
)
from omodul.update_draft_order import (
    UpdateDraftOrderConfig,
    UpdateDraftOrderInput,
    update_draft_order,
)
from omodul.update_order import UpdateOrderConfig, UpdateOrderInput, update_order

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


class TestUpdateOrderValidation:
    async def test_no_fields_rejected(self):
        r = await update_order(
            UpdateOrderConfig(), UpdateOrderInput(order_id="o"), _OUT, pool=object()
        )
        assert r["status"] == "failed"
        assert "at least one field" in r["error"]["message"]

    async def test_no_pool_rejected(self):
        r = await update_order(
            UpdateOrderConfig(), UpdateOrderInput(order_id="o", status="pending"), _OUT
        )
        assert r["status"] == "failed"
        assert "pool is required" in r["error"]["message"]


class TestCancelOrderValidation:
    async def test_no_pool_rejected(self):
        r = await cancel_order(CancelOrderConfig(), CancelOrderInput(order_id="o"), _OUT)
        assert r["status"] == "failed"
        assert "pool is required" in r["error"]["message"]


class TestArchiveOrderValidation:
    async def test_no_pool_rejected(self):
        r = await archive_order(ArchiveOrderConfig(), ArchiveOrderInput(order_id="o"), _OUT)
        assert r["status"] == "failed"
        assert "pool is required" in r["error"]["message"]


class TestCreateDraftOrderValidation:
    async def test_empty_line_items_rejected(self):
        r = await create_draft_order(
            CreateDraftOrderConfig(), CreateDraftOrderInput(line_items=[]), _OUT
        )
        assert r["status"] == "failed"

    async def test_no_pool_rejected(self):
        r = await create_draft_order(
            CreateDraftOrderConfig(),
            CreateDraftOrderInput(line_items=[DraftOrderLineItem(batch_id="b", quantity=1)]),
            _OUT,
        )
        assert r["status"] == "failed"
        assert "pool is required" in r["error"]["message"]


class TestUpdateDraftOrderValidation:
    async def test_no_fields_rejected(self):
        r = await update_draft_order(
            UpdateDraftOrderConfig(), UpdateDraftOrderInput(order_id="o"), _OUT, pool=object()
        )
        assert r["status"] == "failed"
        assert "at least one field" in r["error"]["message"]

    async def test_no_pool_rejected(self):
        r = await update_draft_order(
            UpdateDraftOrderConfig(),
            UpdateDraftOrderInput(order_id="o", customer_id="c"),
            _OUT,
        )
        assert r["status"] == "failed"
        assert "pool is required" in r["error"]["message"]


class TestDeleteDraftOrderValidation:
    async def test_no_pool_rejected(self):
        r = await delete_draft_order(
            DeleteDraftOrderConfig(), DeleteDraftOrderInput(order_id="o"), _OUT
        )
        assert r["status"] == "failed"
        assert "pool is required" in r["error"]["message"]


class TestMarkDraftOrderPaidValidation:
    async def test_no_pool_rejected(self):
        r = await mark_draft_order_paid(
            MarkDraftOrderPaidConfig(), MarkDraftOrderPaidInput(order_id="o"), _OUT
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
        name="commerce_order_lifecycle_integ", dsn=TEST_PG_DSN, min_size=1, max_size=10
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


async def _completed_order(pool: PgPool, *, quantity: int = 1) -> tuple[str, str]:
    """Full cart -> checkout flow. Returns (order_id, batch_id)."""
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
    await authorize_payment_for_cart(
        AuthorizePaymentForCartConfig(),
        AuthorizePaymentForCartInput(cart_id=cart_id),
        _OUT,
        pool=pool,
    )
    r2 = await complete_checkout(
        CompleteCheckoutConfig(redis_url=TEST_REDIS_URL),
        CompleteCheckoutInput(cart_id=cart_id),
        _OUT,
        pool=pool,
    )
    assert r2["status"] == "completed", r2
    return str(r2["order_id"]), batch_id


class TestUpdateOrderIntegration:
    async def test_updates_status(self, commerce_pool, manual_provider):
        order_id, _ = await _completed_order(commerce_pool)
        r = await update_order(
            UpdateOrderConfig(),
            UpdateOrderInput(order_id=order_id, status="processing"),
            _OUT,
            pool=commerce_pool,
        )
        assert r["status"] == "completed", r
        async with commerce_pool.acquire() as conn:
            status = await conn.fetchval(
                'SELECT status FROM "customer_order" WHERE id = $1', order_id
            )
        assert status == "processing"

    async def test_terminal_order_rejected(self, commerce_pool, manual_provider):
        order_id, _ = await _completed_order(commerce_pool)
        await archive_order(
            ArchiveOrderConfig(), ArchiveOrderInput(order_id=order_id), _OUT, pool=commerce_pool
        )
        r = await update_order(
            UpdateOrderConfig(),
            UpdateOrderInput(order_id=order_id, status="processing"),
            _OUT,
            pool=commerce_pool,
        )
        assert r["status"] == "failed"
        assert "terminal" in r["error"]["message"]

    async def test_unknown_order_rejected(self, commerce_pool):
        r = await update_order(
            UpdateOrderConfig(),
            UpdateOrderInput(order_id=str(uuid7()), status="processing"),
            _OUT,
            pool=commerce_pool,
        )
        assert r["status"] == "failed"
        assert "not found" in r["error"]["message"]


class TestCancelOrderIntegration:
    async def test_releases_inventory_and_refunds(self, commerce_pool, manual_provider):
        order_id, batch_id = await _completed_order(commerce_pool, quantity=3)
        async with commerce_pool.acquire() as conn:
            before = await conn.fetchval(
                'SELECT stock_qty FROM "inventory_batch" WHERE id = $1', batch_id
            )

        r = await cancel_order(
            CancelOrderConfig(redis_url=TEST_REDIS_URL),
            CancelOrderInput(order_id=order_id),
            _OUT,
            pool=commerce_pool,
        )
        assert r["status"] == "completed", r
        assert r["refund_result"]["status"] == "refunded"

        async with commerce_pool.acquire() as conn:
            after = await conn.fetchval(
                'SELECT stock_qty FROM "inventory_batch" WHERE id = $1', batch_id
            )
            status = await conn.fetchval(
                'SELECT status FROM "customer_order" WHERE id = $1', order_id
            )
        assert after == before + 3
        assert status == "canceled"

    async def test_double_cancel_rejected(self, commerce_pool, manual_provider):
        order_id, _ = await _completed_order(commerce_pool)
        cfg = CancelOrderConfig(redis_url=TEST_REDIS_URL)
        r1 = await cancel_order(cfg, CancelOrderInput(order_id=order_id), _OUT, pool=commerce_pool)
        assert r1["status"] == "completed"
        r2 = await cancel_order(cfg, CancelOrderInput(order_id=order_id), _OUT, pool=commerce_pool)
        assert r2["status"] == "failed"
        assert "cannot be canceled" in r2["error"]["message"]

    async def test_draft_order_rejected(self, commerce_pool):
        variant_id, loc_id = await _seed_variant(commerce_pool)
        batch_id = await _make_batch(commerce_pool, variant_id=variant_id, location_id=loc_id)
        r0 = await create_draft_order(
            CreateDraftOrderConfig(),
            CreateDraftOrderInput(line_items=[DraftOrderLineItem(batch_id=batch_id, quantity=1)]),
            _OUT,
            pool=commerce_pool,
        )
        r = await cancel_order(
            CancelOrderConfig(redis_url=TEST_REDIS_URL),
            CancelOrderInput(order_id=str(r0["order_id"])),
            _OUT,
            pool=commerce_pool,
        )
        assert r["status"] == "failed"
        assert "cannot be canceled" in r["error"]["message"]


class TestArchiveOrderIntegration:
    async def test_archives_order(self, commerce_pool, manual_provider):
        order_id, _ = await _completed_order(commerce_pool)
        r = await archive_order(
            ArchiveOrderConfig(), ArchiveOrderInput(order_id=order_id), _OUT, pool=commerce_pool
        )
        assert r["status"] == "completed", r
        async with commerce_pool.acquire() as conn:
            status = await conn.fetchval(
                'SELECT status FROM "customer_order" WHERE id = $1', order_id
            )
        assert status == "archived"

    async def test_double_archive_rejected(self, commerce_pool, manual_provider):
        order_id, _ = await _completed_order(commerce_pool)
        cfg = ArchiveOrderConfig()
        r1 = await archive_order(
            cfg, ArchiveOrderInput(order_id=order_id), _OUT, pool=commerce_pool
        )
        assert r1["status"] == "completed"
        r2 = await archive_order(
            cfg, ArchiveOrderInput(order_id=order_id), _OUT, pool=commerce_pool
        )
        assert r2["status"] == "failed"
        assert "already archived" in r2["error"]["message"]


class TestCreateDraftOrderIntegration:
    async def test_creates_draft_and_reserves_stock(self, commerce_pool):
        variant_id, loc_id = await _seed_variant(commerce_pool)
        batch_id = await _make_batch(commerce_pool, variant_id=variant_id, location_id=loc_id)
        r = await create_draft_order(
            CreateDraftOrderConfig(),
            CreateDraftOrderInput(line_items=[DraftOrderLineItem(batch_id=batch_id, quantity=2)]),
            _OUT,
            pool=commerce_pool,
        )
        assert r["status"] == "completed", r
        assert r["subtotal_cents"] == 4000
        assert r["grand_total_cents"] == 4000

        async with commerce_pool.acquire() as conn:
            order = await conn.fetchrow(
                'SELECT * FROM "customer_order" WHERE id = $1', r["order_id"]
            )
            batch = await conn.fetchrow(
                'SELECT reserved_qty FROM "inventory_batch" WHERE id = $1', batch_id
            )
        assert order["cart_id"] is None
        assert order["status"] == "draft"
        assert batch["reserved_qty"] == 2

    async def test_insufficient_stock_rolls_back_earlier_reservations(self, commerce_pool):
        variant_id, loc_id = await _seed_variant(commerce_pool)
        ok_batch = await _make_batch(
            commerce_pool, variant_id=variant_id, location_id=loc_id, stock_qty=10
        )
        short_batch = await _make_batch(
            commerce_pool, variant_id=variant_id, location_id=loc_id, stock_qty=1
        )

        r = await create_draft_order(
            CreateDraftOrderConfig(),
            CreateDraftOrderInput(
                line_items=[
                    DraftOrderLineItem(batch_id=ok_batch, quantity=2),
                    DraftOrderLineItem(batch_id=short_batch, quantity=5),
                ]
            ),
            _OUT,
            pool=commerce_pool,
        )
        assert r["status"] == "failed"
        assert "insufficient stock" in r["error"]["message"]

        async with commerce_pool.acquire() as conn:
            ok_reserved = await conn.fetchval(
                'SELECT reserved_qty FROM "inventory_batch" WHERE id = $1', ok_batch
            )
        assert ok_reserved == 0  # rolled back, not left dangling

    async def test_unknown_batch_rejected(self, commerce_pool):
        r = await create_draft_order(
            CreateDraftOrderConfig(),
            CreateDraftOrderInput(
                line_items=[DraftOrderLineItem(batch_id=str(uuid7()), quantity=1)]
            ),
            _OUT,
            pool=commerce_pool,
        )
        assert r["status"] == "failed"
        assert "not found" in r["error"]["message"]


class TestUpdateDraftOrderIntegration:
    async def test_updates_customer_and_address(self, commerce_pool):
        variant_id, loc_id = await _seed_variant(commerce_pool)
        batch_id = await _make_batch(commerce_pool, variant_id=variant_id, location_id=loc_id)
        r0 = await create_draft_order(
            CreateDraftOrderConfig(),
            CreateDraftOrderInput(line_items=[DraftOrderLineItem(batch_id=batch_id, quantity=1)]),
            _OUT,
            pool=commerce_pool,
        )
        customer_id = str(uuid7())
        r = await update_draft_order(
            UpdateDraftOrderConfig(),
            UpdateDraftOrderInput(
                order_id=str(r0["order_id"]),
                customer_id=customer_id,
                billing_address={"city": "Shanghai"},
            ),
            _OUT,
            pool=commerce_pool,
        )
        assert r["status"] == "completed", r

        async with commerce_pool.acquire() as conn:
            row = await conn.fetchrow(
                'SELECT customer_id, billing_address FROM "customer_order" WHERE id = $1',
                r0["order_id"],
            )
        assert str(row["customer_id"]) == customer_id

    async def test_non_draft_order_rejected(self, commerce_pool, manual_provider):
        order_id, _ = await _completed_order(commerce_pool)
        r = await update_draft_order(
            UpdateDraftOrderConfig(),
            UpdateDraftOrderInput(order_id=order_id, customer_id=str(uuid7())),
            _OUT,
            pool=commerce_pool,
        )
        assert r["status"] == "failed"
        assert "not a draft" in r["error"]["message"]


class TestDeleteDraftOrderIntegration:
    async def test_deletes_and_releases_reservation(self, commerce_pool):
        variant_id, loc_id = await _seed_variant(commerce_pool)
        batch_id = await _make_batch(commerce_pool, variant_id=variant_id, location_id=loc_id)
        r0 = await create_draft_order(
            CreateDraftOrderConfig(),
            CreateDraftOrderInput(line_items=[DraftOrderLineItem(batch_id=batch_id, quantity=2)]),
            _OUT,
            pool=commerce_pool,
        )
        r = await delete_draft_order(
            DeleteDraftOrderConfig(),
            DeleteDraftOrderInput(order_id=str(r0["order_id"])),
            _OUT,
            pool=commerce_pool,
        )
        assert r["status"] == "completed", r

        async with commerce_pool.acquire() as conn:
            batch = await conn.fetchrow(
                'SELECT reserved_qty FROM "inventory_batch" WHERE id = $1', batch_id
            )
            order = await conn.fetchrow(
                'SELECT * FROM "customer_order" WHERE id = $1', r0["order_id"]
            )
        assert batch["reserved_qty"] == 0
        assert order is None

    async def test_non_draft_order_rejected(self, commerce_pool, manual_provider):
        order_id, _ = await _completed_order(commerce_pool)
        r = await delete_draft_order(
            DeleteDraftOrderConfig(),
            DeleteDraftOrderInput(order_id=order_id),
            _OUT,
            pool=commerce_pool,
        )
        assert r["status"] == "failed"
        assert "not a draft" in r["error"]["message"]


class TestMarkDraftOrderPaidIntegration:
    async def test_converts_to_pending_and_converts_reservation(self, commerce_pool):
        variant_id, loc_id = await _seed_variant(commerce_pool)
        batch_id = await _make_batch(commerce_pool, variant_id=variant_id, location_id=loc_id)
        r0 = await create_draft_order(
            CreateDraftOrderConfig(),
            CreateDraftOrderInput(line_items=[DraftOrderLineItem(batch_id=batch_id, quantity=2)]),
            _OUT,
            pool=commerce_pool,
        )
        async with commerce_pool.acquire() as conn:
            before = await conn.fetchval(
                'SELECT stock_qty FROM "inventory_batch" WHERE id = $1', batch_id
            )

        r = await mark_draft_order_paid(
            MarkDraftOrderPaidConfig(),
            MarkDraftOrderPaidInput(
                order_id=str(r0["order_id"]),
                payment_provider_name="manual",
                payment_intent_id="offline-123",
            ),
            _OUT,
            pool=commerce_pool,
        )
        assert r["status"] == "completed", r

        async with commerce_pool.acquire() as conn:
            order = await conn.fetchrow(
                'SELECT * FROM "customer_order" WHERE id = $1', r0["order_id"]
            )
            batch = await conn.fetchrow(
                'SELECT stock_qty, reserved_qty FROM "inventory_batch" WHERE id = $1', batch_id
            )
        assert order["status"] == "pending"
        assert order["payment_provider_name"] == "manual"
        assert order["payment_intent_id"] == "offline-123"
        assert batch["stock_qty"] == before - 2
        assert batch["reserved_qty"] == 0

    async def test_already_paid_rejected(self, commerce_pool):
        variant_id, loc_id = await _seed_variant(commerce_pool)
        batch_id = await _make_batch(commerce_pool, variant_id=variant_id, location_id=loc_id)
        r0 = await create_draft_order(
            CreateDraftOrderConfig(),
            CreateDraftOrderInput(line_items=[DraftOrderLineItem(batch_id=batch_id, quantity=1)]),
            _OUT,
            pool=commerce_pool,
        )
        cfg = MarkDraftOrderPaidConfig()
        r1 = await mark_draft_order_paid(
            cfg, MarkDraftOrderPaidInput(order_id=str(r0["order_id"])), _OUT, pool=commerce_pool
        )
        assert r1["status"] == "completed"
        r2 = await mark_draft_order_paid(
            cfg, MarkDraftOrderPaidInput(order_id=str(r0["order_id"])), _OUT, pool=commerce_pool
        )
        assert r2["status"] == "failed"
        assert "not a draft" in r2["error"]["message"]
