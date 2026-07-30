"""Tests for cart address snapshots + shipping method (SPEC §4.6 remainder).

Covers: set_cart_billing_address, set_cart_shipping_address,
add_shipping_method_to_cart.

Validation-path tests (no pool) always run. Persisted-path tests are integration
tests against real PostgreSQL + Redis; they auto-skip when either is unavailable.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest
from obase.commerce_batch_schema import ensure_commerce_batch_schema
from obase.persistence.pool import PgPool
from obase.uuid7 import uuid7

from omodul.add_line_item_to_cart import AddLineItemConfig, AddLineItemInput, add_line_item_to_cart
from omodul.add_shipping_method_to_cart import (
    AddShippingMethodToCartConfig,
    AddShippingMethodToCartInput,
    add_shipping_method_to_cart,
)
from omodul.apply_discount_to_cart import (
    ApplyDiscountToCartConfig,
    ApplyDiscountToCartInput,
    apply_discount_to_cart,
)
from omodul.create_cart import CreateCartConfig, CreateCartInput, create_cart
from omodul.create_discount import CreateDiscountConfig, CreateDiscountInput, create_discount
from omodul.create_discount_rule import (
    CreateDiscountRuleConfig,
    CreateDiscountRuleInput,
    create_discount_rule,
)
from omodul.create_inventory_batch import (
    CreateInventoryBatchConfig,
    CreateInventoryBatchInput,
    create_inventory_batch,
)
from omodul.set_cart_billing_address import (
    SetCartBillingAddressConfig,
    SetCartBillingAddressInput,
    set_cart_billing_address,
)
from omodul.set_cart_shipping_address import (
    SetCartShippingAddressConfig,
    SetCartShippingAddressInput,
    set_cart_shipping_address,
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
]

_SAMPLE_ADDRESS = dict(
    recipient_name="张三",
    phone="13800000000",
    address_line1="人民路1号",
    city="上海",
    region_code="cn-east",
    postal_code="200000",
)


# ---------------------------------------------------------------------------
# Validation-path tests (no DB) — always run
# ---------------------------------------------------------------------------


class TestAddressValidation:
    async def test_set_billing_address_no_pool_rejected(self):
        r = await set_cart_billing_address(
            SetCartBillingAddressConfig(),
            SetCartBillingAddressInput(cart_id="c", **_SAMPLE_ADDRESS),
            _OUT,
        )
        assert r["status"] == "failed"
        assert "pool is required" in r["error"]["message"]

    async def test_set_shipping_address_no_pool_rejected(self):
        r = await set_cart_shipping_address(
            SetCartShippingAddressConfig(),
            SetCartShippingAddressInput(cart_id="c", **_SAMPLE_ADDRESS),
            _OUT,
        )
        assert r["status"] == "failed"
        assert "pool is required" in r["error"]["message"]


class TestShippingMethodValidation:
    async def test_negative_price_rejected(self):
        r = await add_shipping_method_to_cart(
            AddShippingMethodToCartConfig(),
            AddShippingMethodToCartInput(cart_id="c", method_name="Standard", price_cents=-1),
            _OUT,
        )
        assert r["status"] == "failed"

    async def test_no_pool_rejected(self):
        r = await add_shipping_method_to_cart(
            AddShippingMethodToCartConfig(),
            AddShippingMethodToCartInput(cart_id="c", method_name="Standard", price_cents=500),
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
        name="commerce_address_shipping_integ", dsn=TEST_PG_DSN, min_size=1, max_size=10
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


async def _make_cart(pool: PgPool) -> str:
    r = await create_cart(
        CreateCartConfig(), CreateCartInput(region_code="cn-east"), _OUT, pool=pool
    )
    assert r["status"] == "completed", r
    return r["cart_id"]


class TestAddressIntegration:
    async def test_set_billing_address_persists(self, commerce_pool):
        cart_id = await _make_cart(commerce_pool)
        r = await set_cart_billing_address(
            SetCartBillingAddressConfig(),
            SetCartBillingAddressInput(cart_id=cart_id, **_SAMPLE_ADDRESS),
            _OUT,
            pool=commerce_pool,
        )
        assert r["status"] == "completed", r
        assert r["billing_address"]["city"] == "上海"

        import json

        async with commerce_pool.acquire() as conn:
            row = await conn.fetchrow('SELECT billing_address FROM "cart" WHERE id = $1', cart_id)
        assert json.loads(row["billing_address"])["recipient_name"] == "张三"

    async def test_set_shipping_address_persists(self, commerce_pool):
        cart_id = await _make_cart(commerce_pool)
        r = await set_cart_shipping_address(
            SetCartShippingAddressConfig(),
            SetCartShippingAddressInput(cart_id=cart_id, **_SAMPLE_ADDRESS),
            _OUT,
            pool=commerce_pool,
        )
        assert r["status"] == "completed", r

        import json

        async with commerce_pool.acquire() as conn:
            row = await conn.fetchrow('SELECT shipping_address FROM "cart" WHERE id = $1', cart_id)
        assert json.loads(row["shipping_address"])["postal_code"] == "200000"

    async def test_set_address_unknown_cart_fails(self, commerce_pool):
        r = await set_cart_billing_address(
            SetCartBillingAddressConfig(),
            SetCartBillingAddressInput(cart_id=str(uuid7()), **_SAMPLE_ADDRESS),
            _OUT,
            pool=commerce_pool,
        )
        assert r["status"] == "failed"
        assert "not found" in r["error"]["message"]


class TestShippingMethodIntegration:
    async def test_adds_shipping_and_recomputes_grand_total(self, commerce_pool):
        variant_id, loc_id = await _seed_variant(commerce_pool)
        batch_id = await _make_batch(commerce_pool, variant_id=variant_id, location_id=loc_id)
        cart_id = await _make_cart(commerce_pool)
        await add_line_item_to_cart(
            AddLineItemConfig(redis_url=TEST_REDIS_URL),
            AddLineItemInput(cart_id=cart_id, batch_id=batch_id, quantity=1),
            _OUT,
            pool=commerce_pool,
        )  # subtotal 2000

        r = await add_shipping_method_to_cart(
            AddShippingMethodToCartConfig(),
            AddShippingMethodToCartInput(cart_id=cart_id, method_name="Standard", price_cents=500),
            _OUT,
            pool=commerce_pool,
        )
        assert r["status"] == "completed", r
        assert r["shipping_cents"] == 500
        assert r["grand_total_cents"] == 2500

    async def test_replacing_shipping_method_updates_totals(self, commerce_pool):
        variant_id, loc_id = await _seed_variant(commerce_pool)
        batch_id = await _make_batch(commerce_pool, variant_id=variant_id, location_id=loc_id)
        cart_id = await _make_cart(commerce_pool)
        await add_line_item_to_cart(
            AddLineItemConfig(redis_url=TEST_REDIS_URL),
            AddLineItemInput(cart_id=cart_id, batch_id=batch_id, quantity=1),
            _OUT,
            pool=commerce_pool,
        )
        await add_shipping_method_to_cart(
            AddShippingMethodToCartConfig(),
            AddShippingMethodToCartInput(cart_id=cart_id, method_name="Standard", price_cents=500),
            _OUT,
            pool=commerce_pool,
        )
        r = await add_shipping_method_to_cart(
            AddShippingMethodToCartConfig(),
            AddShippingMethodToCartInput(cart_id=cart_id, method_name="Express", price_cents=1500),
            _OUT,
            pool=commerce_pool,
        )
        assert r["status"] == "completed", r
        assert r["grand_total_cents"] == 3500

    async def test_free_shipping_discount_overrides_paid_shipping_method(self, commerce_pool):
        variant_id, loc_id = await _seed_variant(commerce_pool)
        batch_id = await _make_batch(commerce_pool, variant_id=variant_id, location_id=loc_id)
        cart_id = await _make_cart(commerce_pool)
        await add_line_item_to_cart(
            AddLineItemConfig(redis_url=TEST_REDIS_URL),
            AddLineItemInput(cart_id=cart_id, batch_id=batch_id, quantity=1),
            _OUT,
            pool=commerce_pool,
        )  # subtotal 2000

        rd = await create_discount(
            CreateDiscountConfig(), CreateDiscountInput(code="FREESHIP2"), _OUT, pool=commerce_pool
        )
        await create_discount_rule(
            CreateDiscountRuleConfig(),
            CreateDiscountRuleInput(discount_id=rd["discount_id"], rule_type="free_shipping"),
            _OUT,
            pool=commerce_pool,
        )
        await apply_discount_to_cart(
            ApplyDiscountToCartConfig(redis_url=TEST_REDIS_URL),
            ApplyDiscountToCartInput(cart_id=cart_id, code="FREESHIP2"),
            _OUT,
            pool=commerce_pool,
        )

        # Now add a paid shipping method — the free_shipping discount must still win.
        r = await add_shipping_method_to_cart(
            AddShippingMethodToCartConfig(),
            AddShippingMethodToCartInput(cart_id=cart_id, method_name="Express", price_cents=999),
            _OUT,
            pool=commerce_pool,
        )
        assert r["status"] == "completed", r
        assert r["shipping_cents"] == 999  # the raw fee is still recorded
        assert r["grand_total_cents"] == 2000  # but not charged — free shipping wins

    async def test_shipping_method_unknown_cart_fails(self, commerce_pool):
        r = await add_shipping_method_to_cart(
            AddShippingMethodToCartConfig(),
            AddShippingMethodToCartInput(
                cart_id=str(uuid7()), method_name="Standard", price_cents=500
            ),
            _OUT,
            pool=commerce_pool,
        )
        assert r["status"] == "failed"
        assert "not found" in r["error"]["message"]
