"""Tests for the batch-warehouse commerce cart domain (SPEC §4.6 + custom batch layer).

Covers: create_inventory_batch, create_cart, set_cart_customer, set_cart_region,
add_line_item_to_cart, update_line_item_in_cart, delete_line_item_from_cart.

Validation-path tests (no pool) always run. Persisted-path tests are integration
tests against real PostgreSQL + Redis; they auto-skip when either is unavailable.
Set TEST_PG_DSN / TEST_REDIS_URL env vars, or ensure
postgresql://postgres:test@localhost:5432/obase_test and redis://localhost:6379/0.
"""

from __future__ import annotations

import asyncio
import os
from pathlib import Path

import pytest
from obase.commerce_batch_schema import ensure_commerce_batch_schema
from obase.persistence.pool import PgPool
from obase.uuid7 import uuid7

from omodul.add_line_item_to_cart import AddLineItemConfig, AddLineItemInput, add_line_item_to_cart
from omodul.create_cart import CreateCartConfig, CreateCartInput, create_cart
from omodul.create_inventory_batch import (
    CreateInventoryBatchConfig,
    CreateInventoryBatchInput,
    create_inventory_batch,
)
from omodul.delete_line_item_from_cart import (
    DeleteLineItemConfig,
    DeleteLineItemInput,
    delete_line_item_from_cart,
)
from omodul.set_cart_customer import (
    SetCartCustomerConfig,
    SetCartCustomerInput,
    set_cart_customer,
)
from omodul.set_cart_region import SetCartRegionConfig, SetCartRegionInput, set_cart_region
from omodul.update_line_item_in_cart import (
    UpdateLineItemConfig,
    UpdateLineItemInput,
    update_line_item_in_cart,
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
]


# ---------------------------------------------------------------------------
# Validation-path tests (no DB/Redis) — always run
# ---------------------------------------------------------------------------


class TestCreateInventoryBatchValidation:
    async def test_missing_video_url_rejected(self):
        r = await create_inventory_batch(
            CreateInventoryBatchConfig(),
            CreateInventoryBatchInput(
                variant_id="v",
                location_id="l",
                batch_no="B1",
                video_url="",
                cost_price_cents=100,
                retail_price_cents=200,
                stock_qty=1,
            ),
            _OUT,
        )
        assert r["status"] == "failed"
        assert "video_url" in r["error"]["message"]

    async def test_non_positive_stock_qty_rejected(self):
        r = await create_inventory_batch(
            CreateInventoryBatchConfig(),
            CreateInventoryBatchInput(
                variant_id="v",
                location_id="l",
                batch_no="B1",
                video_url="https://x/v.mp4",
                cost_price_cents=100,
                retail_price_cents=200,
                stock_qty=0,
            ),
            _OUT,
        )
        assert r["status"] == "failed"
        assert "stock_qty" in r["error"]["message"]

    async def test_negative_price_rejected(self):
        r = await create_inventory_batch(
            CreateInventoryBatchConfig(),
            CreateInventoryBatchInput(
                variant_id="v",
                location_id="l",
                batch_no="B1",
                video_url="https://x/v.mp4",
                cost_price_cents=-1,
                retail_price_cents=200,
                stock_qty=1,
            ),
            _OUT,
        )
        assert r["status"] == "failed"
        assert "prices" in r["error"]["message"]

    async def test_dry_run_without_pool_completes(self):
        r = await create_inventory_batch(
            CreateInventoryBatchConfig(),
            CreateInventoryBatchInput(
                variant_id="v",
                location_id="l",
                batch_no="B1",
                video_url="https://x/v.mp4",
                cost_price_cents=100,
                retail_price_cents=200,
                stock_qty=1,
                inspected_by="qc1",
            ),
            _OUT,
        )
        assert r["status"] == "completed"
        assert r["inspection_status"] == "passed"

    async def test_no_inspector_yields_pending_status(self):
        r = await create_inventory_batch(
            CreateInventoryBatchConfig(),
            CreateInventoryBatchInput(
                variant_id="v",
                location_id="l",
                batch_no="B1",
                video_url="https://x/v.mp4",
                cost_price_cents=100,
                retail_price_cents=200,
                stock_qty=1,
            ),
            _OUT,
        )
        assert r["inspection_status"] == "pending"


class TestCreateCartValidation:
    async def test_dry_run_without_pool_completes(self):
        r = await create_cart(CreateCartConfig(), CreateCartInput(region_code="cn-east"), _OUT)
        assert r["status"] == "completed"
        assert r["region_code"] == "cn-east"
        assert r["currency"] == "CNY"

    async def test_anonymous_cart_allows_empty_customer(self):
        r = await create_cart(CreateCartConfig(), CreateCartInput(), _OUT)
        assert r["status"] == "completed"
        assert r["customer_id"] is None


class TestSetCartRegionValidation:
    async def test_missing_region_code_rejected(self):
        r = await set_cart_region(
            SetCartRegionConfig(),
            SetCartRegionInput(cart_id="c", region_code="", currency="CNY"),
            _OUT,
        )
        assert r["status"] == "failed"
        assert "region_code" in r["error"]["message"]

    async def test_no_pool_rejected(self):
        r = await set_cart_region(
            SetCartRegionConfig(),
            SetCartRegionInput(cart_id="c", region_code="cn-east", currency="CNY"),
            _OUT,
        )
        assert r["status"] == "failed"
        assert "pool is required" in r["error"]["message"]


class TestSetCartCustomerValidation:
    async def test_missing_customer_id_rejected(self):
        r = await set_cart_customer(
            SetCartCustomerConfig(), SetCartCustomerInput(cart_id="c", customer_id=""), _OUT
        )
        assert r["status"] == "failed"
        assert "customer_id" in r["error"]["message"]

    async def test_no_pool_rejected(self):
        r = await set_cart_customer(
            SetCartCustomerConfig(), SetCartCustomerInput(cart_id="c", customer_id="u"), _OUT
        )
        assert r["status"] == "failed"
        assert "pool is required" in r["error"]["message"]


class TestAddLineItemValidation:
    async def test_non_positive_quantity_rejected(self):
        r = await add_line_item_to_cart(
            AddLineItemConfig(), AddLineItemInput(cart_id="c", batch_id="b", quantity=0), _OUT
        )
        assert r["status"] == "failed"
        assert "quantity" in r["error"]["message"]

    async def test_no_pool_rejected(self):
        r = await add_line_item_to_cart(
            AddLineItemConfig(), AddLineItemInput(cart_id="c", batch_id="b", quantity=1), _OUT
        )
        assert r["status"] == "failed"
        assert "pool is required" in r["error"]["message"]


class TestUpdateLineItemValidation:
    async def test_zero_quantity_rejected_use_delete_instead(self):
        r = await update_line_item_in_cart(
            UpdateLineItemConfig(), UpdateLineItemInput(cart_id="c", batch_id="b", quantity=0), _OUT
        )
        assert r["status"] == "failed"
        assert "delete_line_item_from_cart" in r["error"]["message"]

    async def test_no_pool_rejected(self):
        r = await update_line_item_in_cart(
            UpdateLineItemConfig(), UpdateLineItemInput(cart_id="c", batch_id="b", quantity=1), _OUT
        )
        assert r["status"] == "failed"
        assert "pool is required" in r["error"]["message"]


class TestDeleteLineItemValidation:
    async def test_no_pool_rejected(self):
        r = await delete_line_item_from_cart(
            DeleteLineItemConfig(), DeleteLineItemInput(cart_id="c", batch_id="b"), _OUT
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

    pool = await PgPool.create(name="commerce_cart_integ", dsn=TEST_PG_DSN, min_size=1, max_size=10)
    await ensure_commerce_batch_schema(pool)
    yield pool
    async with pool.acquire() as conn:
        for table in reversed(_TABLES):
            await conn.execute(f'DROP TABLE IF EXISTS "public"."{table}" CASCADE')
    await pool.close()


async def _seed_variant(pool: PgPool) -> tuple[str, str]:
    """Seed a stock_location + product + product_variant. Returns (variant_id, location_id)."""
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
    pool: PgPool,
    *,
    variant_id: str,
    location_id: str,
    stock_qty: int = 10,
    retail_price_cents: int = 2000,
) -> str:
    r = await create_inventory_batch(
        CreateInventoryBatchConfig(),
        CreateInventoryBatchInput(
            variant_id=variant_id,
            location_id=location_id,
            batch_no=f"B-{uuid7()}",
            video_url="https://x/v.mp4",
            cost_price_cents=1000,
            retail_price_cents=retail_price_cents,
            stock_qty=stock_qty,
            inspected_by="qc1",
        ),
        _OUT,
        pool=pool,
    )
    assert r["status"] == "completed", r
    return r["batch_id"]


async def _make_cart(pool: PgPool) -> str:
    r = await create_cart(
        CreateCartConfig(), CreateCartInput(region_code="cn-east"), _OUT, pool=pool
    )
    assert r["status"] == "completed", r
    return r["cart_id"]


class TestCommerceCartDomainIntegration:
    async def test_create_inventory_batch_persists_row(self, commerce_pool):
        variant_id, loc_id = await _seed_variant(commerce_pool)
        batch_id = await _make_batch(commerce_pool, variant_id=variant_id, location_id=loc_id)
        async with commerce_pool.acquire() as conn:
            row = await conn.fetchrow(
                'SELECT * FROM "public"."inventory_batch" WHERE id = $1', batch_id
            )
        assert row is not None
        assert row["stock_qty"] == 10
        assert row["reserved_qty"] == 0

    async def test_set_cart_customer_persists(self, commerce_pool):
        cart_id = await _make_cart(commerce_pool)
        customer_id = str(uuid7())
        r = await set_cart_customer(
            SetCartCustomerConfig(),
            SetCartCustomerInput(cart_id=cart_id, customer_id=customer_id),
            _OUT,
            pool=commerce_pool,
        )
        assert r["status"] == "completed"
        async with commerce_pool.acquire() as conn:
            row = await conn.fetchrow(
                'SELECT customer_id FROM "public"."cart" WHERE id = $1', cart_id
            )
        assert str(row["customer_id"]) == customer_id

    async def test_set_cart_customer_unknown_cart_fails(self, commerce_pool):
        r = await set_cart_customer(
            SetCartCustomerConfig(),
            SetCartCustomerInput(cart_id=str(uuid7()), customer_id=str(uuid7())),
            _OUT,
            pool=commerce_pool,
        )
        assert r["status"] == "failed"
        assert "not found" in r["error"]["message"]

    async def test_set_cart_region_switches_region_and_currency(self, commerce_pool):
        cart_id = await _make_cart(commerce_pool)
        r = await set_cart_region(
            SetCartRegionConfig(),
            SetCartRegionInput(cart_id=cart_id, region_code="cn-south", currency="USD"),
            _OUT,
            pool=commerce_pool,
        )
        assert r["status"] == "completed"
        async with commerce_pool.acquire() as conn:
            row = await conn.fetchrow(
                'SELECT region_code, currency FROM "public"."cart" WHERE id = $1', cart_id
            )
        assert row["region_code"] == "cn-south"
        assert row["currency"] == "USD"

    async def test_add_line_item_computes_totals_and_reserves_stock(self, commerce_pool):
        variant_id, loc_id = await _seed_variant(commerce_pool)
        batch_id = await _make_batch(
            commerce_pool,
            variant_id=variant_id,
            location_id=loc_id,
            stock_qty=10,
            retail_price_cents=2000,
        )
        cart_id = await _make_cart(commerce_pool)

        r = await add_line_item_to_cart(
            AddLineItemConfig(redis_url=TEST_REDIS_URL),
            AddLineItemInput(cart_id=cart_id, batch_id=batch_id, quantity=3),
            _OUT,
            pool=commerce_pool,
        )
        assert r["status"] == "completed", r
        assert r["quantity"] == 3
        assert r["subtotal_cents"] == 6000
        assert r["grand_total_cents"] == 6000

        async with commerce_pool.acquire() as conn:
            batch_row = await conn.fetchrow(
                'SELECT reserved_qty FROM "public"."inventory_batch" WHERE id = $1', batch_id
            )
        assert batch_row["reserved_qty"] == 3

    async def test_add_line_item_twice_accumulates_same_row(self, commerce_pool):
        variant_id, loc_id = await _seed_variant(commerce_pool)
        batch_id = await _make_batch(commerce_pool, variant_id=variant_id, location_id=loc_id)
        cart_id = await _make_cart(commerce_pool)

        cfg = AddLineItemConfig(redis_url=TEST_REDIS_URL)
        r1 = await add_line_item_to_cart(
            cfg,
            AddLineItemInput(cart_id=cart_id, batch_id=batch_id, quantity=2),
            _OUT,
            pool=commerce_pool,
        )
        r2 = await add_line_item_to_cart(
            cfg,
            AddLineItemInput(cart_id=cart_id, batch_id=batch_id, quantity=3),
            _OUT,
            pool=commerce_pool,
        )
        assert r1["line_item_id"] == r2["line_item_id"]
        assert r2["quantity"] == 5

        async with commerce_pool.acquire() as conn:
            rows = await conn.fetch(
                'SELECT * FROM "public"."cart_line_item" WHERE cart_id = $1 AND deleted_at IS NULL',
                cart_id,
            )
        assert len(rows) == 1

    async def test_add_line_item_oversell_rejected(self, commerce_pool):
        variant_id, loc_id = await _seed_variant(commerce_pool)
        batch_id = await _make_batch(
            commerce_pool, variant_id=variant_id, location_id=loc_id, stock_qty=2
        )
        cart_id = await _make_cart(commerce_pool)

        r = await add_line_item_to_cart(
            AddLineItemConfig(redis_url=TEST_REDIS_URL),
            AddLineItemInput(cart_id=cart_id, batch_id=batch_id, quantity=5),
            _OUT,
            pool=commerce_pool,
        )
        assert r["status"] == "failed"
        assert "insufficient stock" in r["error"]["message"]

    async def test_add_line_item_unsellable_batch_rejected(self, commerce_pool):
        variant_id, loc_id = await _seed_variant(commerce_pool)
        r_batch = await create_inventory_batch(
            CreateInventoryBatchConfig(),
            CreateInventoryBatchInput(
                variant_id=variant_id,
                location_id=loc_id,
                batch_no=f"B-{uuid7()}",
                video_url="https://x/v.mp4",
                cost_price_cents=100,
                retail_price_cents=200,
                stock_qty=5,
                # no inspected_by -> inspection_status stays "pending"
            ),
            _OUT,
            pool=commerce_pool,
        )
        cart_id = await _make_cart(commerce_pool)
        r = await add_line_item_to_cart(
            AddLineItemConfig(redis_url=TEST_REDIS_URL),
            AddLineItemInput(cart_id=cart_id, batch_id=r_batch["batch_id"], quantity=1),
            _OUT,
            pool=commerce_pool,
        )
        assert r["status"] == "failed"
        assert "not sellable" in r["error"]["message"]

    async def test_update_line_item_increases_quantity_and_reserved(self, commerce_pool):
        variant_id, loc_id = await _seed_variant(commerce_pool)
        batch_id = await _make_batch(
            commerce_pool, variant_id=variant_id, location_id=loc_id, stock_qty=10
        )
        cart_id = await _make_cart(commerce_pool)
        await add_line_item_to_cart(
            AddLineItemConfig(redis_url=TEST_REDIS_URL),
            AddLineItemInput(cart_id=cart_id, batch_id=batch_id, quantity=3),
            _OUT,
            pool=commerce_pool,
        )

        r = await update_line_item_in_cart(
            UpdateLineItemConfig(redis_url=TEST_REDIS_URL),
            UpdateLineItemInput(cart_id=cart_id, batch_id=batch_id, quantity=7),
            _OUT,
            pool=commerce_pool,
        )
        assert r["status"] == "completed", r
        assert r["quantity"] == 7

        async with commerce_pool.acquire() as conn:
            batch_row = await conn.fetchrow(
                'SELECT reserved_qty FROM "public"."inventory_batch" WHERE id = $1', batch_id
            )
        assert batch_row["reserved_qty"] == 7

    async def test_update_line_item_decreases_releases_reserved(self, commerce_pool):
        variant_id, loc_id = await _seed_variant(commerce_pool)
        batch_id = await _make_batch(
            commerce_pool, variant_id=variant_id, location_id=loc_id, stock_qty=10
        )
        cart_id = await _make_cart(commerce_pool)
        await add_line_item_to_cart(
            AddLineItemConfig(redis_url=TEST_REDIS_URL),
            AddLineItemInput(cart_id=cart_id, batch_id=batch_id, quantity=8),
            _OUT,
            pool=commerce_pool,
        )

        r = await update_line_item_in_cart(
            UpdateLineItemConfig(redis_url=TEST_REDIS_URL),
            UpdateLineItemInput(cart_id=cart_id, batch_id=batch_id, quantity=2),
            _OUT,
            pool=commerce_pool,
        )
        assert r["status"] == "completed", r

        async with commerce_pool.acquire() as conn:
            batch_row = await conn.fetchrow(
                'SELECT reserved_qty FROM "public"."inventory_batch" WHERE id = $1', batch_id
            )
        assert batch_row["reserved_qty"] == 2

    async def test_update_line_item_oversell_on_increase_rejected(self, commerce_pool):
        variant_id, loc_id = await _seed_variant(commerce_pool)
        batch_id = await _make_batch(
            commerce_pool, variant_id=variant_id, location_id=loc_id, stock_qty=5
        )
        cart_id = await _make_cart(commerce_pool)
        await add_line_item_to_cart(
            AddLineItemConfig(redis_url=TEST_REDIS_URL),
            AddLineItemInput(cart_id=cart_id, batch_id=batch_id, quantity=3),
            _OUT,
            pool=commerce_pool,
        )

        r = await update_line_item_in_cart(
            UpdateLineItemConfig(redis_url=TEST_REDIS_URL),
            UpdateLineItemInput(cart_id=cart_id, batch_id=batch_id, quantity=99),
            _OUT,
            pool=commerce_pool,
        )
        assert r["status"] == "failed"
        assert "insufficient stock" in r["error"]["message"]

    async def test_update_line_item_without_existing_row_rejected(self, commerce_pool):
        variant_id, loc_id = await _seed_variant(commerce_pool)
        batch_id = await _make_batch(commerce_pool, variant_id=variant_id, location_id=loc_id)
        cart_id = await _make_cart(commerce_pool)

        r = await update_line_item_in_cart(
            UpdateLineItemConfig(redis_url=TEST_REDIS_URL),
            UpdateLineItemInput(cart_id=cart_id, batch_id=batch_id, quantity=1),
            _OUT,
            pool=commerce_pool,
        )
        assert r["status"] == "failed"
        assert "no existing line item" in r["error"]["message"]

    async def test_delete_line_item_releases_reserved_and_soft_deletes(self, commerce_pool):
        variant_id, loc_id = await _seed_variant(commerce_pool)
        batch_id = await _make_batch(
            commerce_pool, variant_id=variant_id, location_id=loc_id, stock_qty=10
        )
        cart_id = await _make_cart(commerce_pool)
        await add_line_item_to_cart(
            AddLineItemConfig(redis_url=TEST_REDIS_URL),
            AddLineItemInput(cart_id=cart_id, batch_id=batch_id, quantity=4),
            _OUT,
            pool=commerce_pool,
        )

        r = await delete_line_item_from_cart(
            DeleteLineItemConfig(redis_url=TEST_REDIS_URL),
            DeleteLineItemInput(cart_id=cart_id, batch_id=batch_id),
            _OUT,
            pool=commerce_pool,
        )
        assert r["status"] == "completed", r
        assert r["subtotal_cents"] == 0
        assert r["grand_total_cents"] == 0

        async with commerce_pool.acquire() as conn:
            batch_row = await conn.fetchrow(
                'SELECT reserved_qty FROM "public"."inventory_batch" WHERE id = $1', batch_id
            )
            line_row = await conn.fetchrow(
                'SELECT deleted_at FROM "public"."cart_line_item" '
                "WHERE cart_id = $1 AND batch_id = $2",
                cart_id,
                batch_id,
            )
        assert batch_row["reserved_qty"] == 0
        assert line_row["deleted_at"] is not None

    async def test_delete_line_item_without_existing_row_rejected(self, commerce_pool):
        variant_id, loc_id = await _seed_variant(commerce_pool)
        batch_id = await _make_batch(commerce_pool, variant_id=variant_id, location_id=loc_id)
        cart_id = await _make_cart(commerce_pool)

        r = await delete_line_item_from_cart(
            DeleteLineItemConfig(redis_url=TEST_REDIS_URL),
            DeleteLineItemInput(cart_id=cart_id, batch_id=batch_id),
            _OUT,
            pool=commerce_pool,
        )
        assert r["status"] == "failed"
        assert "no existing line item" in r["error"]["message"]

    async def test_concurrent_add_line_item_never_oversells(self, commerce_pool):
        """Two carts racing to add_line_item_to_cart against the same low-stock batch
        must be serialized by DistributedLock — total reserved must never exceed stock,
        and exactly one of the two oversized requests must be rejected."""
        variant_id, loc_id = await _seed_variant(commerce_pool)
        batch_id = await _make_batch(
            commerce_pool, variant_id=variant_id, location_id=loc_id, stock_qty=5
        )
        cart_a = await _make_cart(commerce_pool)
        cart_b = await _make_cart(commerce_pool)

        cfg = AddLineItemConfig(redis_url=TEST_REDIS_URL, lock_timeout_seconds=5.0)

        results = await asyncio.gather(
            add_line_item_to_cart(
                cfg,
                AddLineItemInput(cart_id=cart_a, batch_id=batch_id, quantity=3),
                _OUT,
                pool=commerce_pool,
            ),
            add_line_item_to_cart(
                cfg,
                AddLineItemInput(cart_id=cart_b, batch_id=batch_id, quantity=3),
                _OUT,
                pool=commerce_pool,
            ),
        )

        statuses = [r["status"] for r in results]
        assert statuses.count("completed") == 1
        assert statuses.count("failed") == 1

        async with commerce_pool.acquire() as conn:
            batch_row = await conn.fetchrow(
                'SELECT reserved_qty FROM "public"."inventory_batch" WHERE id = $1', batch_id
            )
        assert batch_row["reserved_qty"] == 3
