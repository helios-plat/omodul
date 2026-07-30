"""Tests for the pricing + inventory domain (SPEC §4.4): price_list CRUD,
add/remove_prices_from_list, stock_location CRUD, adjust_inventory_level,
sales_channel CRUD, publish/unpublish_products_to_channel.

Validation-path tests (no pool) always run. Persisted-path tests are integration
tests against real PostgreSQL; they auto-skip when unavailable.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest
from obase.commerce_batch_schema import ensure_commerce_batch_schema
from obase.persistence.pool import PgPool
from obase.uuid7 import uuid7

from omodul.add_prices_to_list import (
    AddPricesToListConfig,
    AddPricesToListInput,
    PriceListPriceItem,
    add_prices_to_list,
)
from omodul.adjust_inventory_level import (
    AdjustInventoryLevelConfig,
    AdjustInventoryLevelInput,
    adjust_inventory_level,
)
from omodul.create_inventory_batch import (
    CreateInventoryBatchConfig,
    CreateInventoryBatchInput,
    create_inventory_batch,
)
from omodul.create_price_list import CreatePriceListConfig, CreatePriceListInput, create_price_list
from omodul.create_product import CreateProductConfig, CreateProductInput, create_product
from omodul.create_product_variant import (
    CreateProductVariantConfig,
    CreateProductVariantInput,
    create_product_variant,
)
from omodul.create_sales_channel import (
    CreateSalesChannelConfig,
    CreateSalesChannelInput,
    create_sales_channel,
)
from omodul.create_stock_location import (
    CreateStockLocationConfig,
    CreateStockLocationInput,
    create_stock_location,
)
from omodul.delete_price_list import DeletePriceListConfig, DeletePriceListInput, delete_price_list
from omodul.delete_sales_channel import (
    DeleteSalesChannelConfig,
    DeleteSalesChannelInput,
    delete_sales_channel,
)
from omodul.delete_stock_location import (
    DeleteStockLocationConfig,
    DeleteStockLocationInput,
    delete_stock_location,
)
from omodul.publish_products_to_channel import (
    PublishProductsToChannelConfig,
    PublishProductsToChannelInput,
    publish_products_to_channel,
)
from omodul.remove_prices_from_list import (
    RemovePricesFromListConfig,
    RemovePricesFromListInput,
    remove_prices_from_list,
)
from omodul.unpublish_products_from_channel import (
    UnpublishProductsFromChannelConfig,
    UnpublishProductsFromChannelInput,
    unpublish_products_from_channel,
)
from omodul.update_price_list import UpdatePriceListConfig, UpdatePriceListInput, update_price_list
from omodul.update_sales_channel import (
    UpdateSalesChannelConfig,
    UpdateSalesChannelInput,
    update_sales_channel,
)
from omodul.update_stock_location import (
    UpdateStockLocationConfig,
    UpdateStockLocationInput,
    update_stock_location,
)

TEST_PG_DSN = os.environ.get("TEST_PG_DSN", "postgresql://postgres:test@localhost:5432/obase_test")

_OUT = Path("/tmp")
_TABLES = [
    "region",
    "tax_rate",
    "app_user",
    "customer_group",
    "customer",
    "customer_address",
    "stock_location",
    "product",
    "product_variant",
    "product_option",
    "product_category",
    "product_collection",
    "product_collection_item",
    "price_list",
    "price_list_item",
    "sales_channel",
    "sales_channel_product",
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


class TestCreatePriceListValidation:
    async def test_missing_name_rejected(self):
        r = await create_price_list(CreatePriceListConfig(), CreatePriceListInput(name=""), _OUT)
        assert r["status"] == "failed"


class TestAddPricesToListValidation:
    async def test_negative_price_rejected(self):
        r = await add_prices_to_list(
            AddPricesToListConfig(),
            AddPricesToListInput(
                price_list_id="pl", items=[PriceListPriceItem(variant_id="v", price_cents=-1)]
            ),
            _OUT,
        )
        assert r["status"] == "failed"

    async def test_no_pool_rejected(self):
        r = await add_prices_to_list(
            AddPricesToListConfig(),
            AddPricesToListInput(
                price_list_id="pl", items=[PriceListPriceItem(variant_id="v", price_cents=100)]
            ),
            _OUT,
        )
        assert r["status"] == "failed"
        assert "pool is required" in r["error"]["message"]


class TestCreateStockLocationValidation:
    async def test_missing_name_rejected(self):
        r = await create_stock_location(
            CreateStockLocationConfig(),
            CreateStockLocationInput(name="", region_code="cn-east"),
            _OUT,
        )
        assert r["status"] == "failed"

    async def test_dry_run_without_pool_completes(self):
        r = await create_stock_location(
            CreateStockLocationConfig(),
            CreateStockLocationInput(name="仓A", region_code="cn-east"),
            _OUT,
        )
        assert r["status"] == "completed"


class TestAdjustInventoryLevelValidation:
    async def test_zero_delta_rejected(self):
        r = await adjust_inventory_level(
            AdjustInventoryLevelConfig(),
            AdjustInventoryLevelInput(batch_id="b", delta=0, reason="count"),
            _OUT,
            pool=object(),
        )
        assert r["status"] == "failed"

    async def test_missing_reason_rejected(self):
        r = await adjust_inventory_level(
            AdjustInventoryLevelConfig(),
            AdjustInventoryLevelInput(batch_id="b", delta=1, reason=""),
            _OUT,
            pool=object(),
        )
        assert r["status"] == "failed"

    async def test_no_pool_rejected(self):
        r = await adjust_inventory_level(
            AdjustInventoryLevelConfig(),
            AdjustInventoryLevelInput(batch_id="b", delta=1, reason="count"),
            _OUT,
        )
        assert r["status"] == "failed"
        assert "pool is required" in r["error"]["message"]


class TestCreateSalesChannelValidation:
    async def test_missing_name_rejected(self):
        r = await create_sales_channel(
            CreateSalesChannelConfig(), CreateSalesChannelInput(name=""), _OUT
        )
        assert r["status"] == "failed"


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
        name="commerce_pricing_inventory_integ", dsn=TEST_PG_DSN, min_size=1, max_size=10
    )
    await ensure_commerce_batch_schema(pool)
    yield pool
    async with pool.acquire() as conn:
        for table in reversed(_TABLES):
            await conn.execute(f'DROP TABLE IF EXISTS "public"."{table}" CASCADE')
    await pool.close()


async def _make_variant(pool: PgPool) -> str:
    rp = await create_product(
        CreateProductConfig(), CreateProductInput(title="P", slug=f"p-{uuid7()}"), _OUT, pool=pool
    )
    rv = await create_product_variant(
        CreateProductVariantConfig(),
        CreateProductVariantInput(product_id=str(rp["product_id"]), sku_code=f"SKU-{uuid7()}"),
        _OUT,
        pool=pool,
    )
    return str(rv["variant_id"])


class TestPriceListIntegration:
    async def test_create_update_delete(self, commerce_pool):
        r0 = await create_price_list(
            CreatePriceListConfig(),
            CreatePriceListInput(name="Sale", currency="CNY"),
            _OUT,
            pool=commerce_pool,
        )
        assert r0["status"] == "completed", r0

        r1 = await update_price_list(
            UpdatePriceListConfig(),
            UpdatePriceListInput(price_list_id=str(r0["price_list_id"]), name="Big Sale"),
            _OUT,
            pool=commerce_pool,
        )
        assert r1["status"] == "completed", r1

        r2 = await delete_price_list(
            DeletePriceListConfig(),
            DeletePriceListInput(price_list_id=str(r0["price_list_id"])),
            _OUT,
            pool=commerce_pool,
        )
        assert r2["status"] == "completed", r2


class TestAddRemovePricesIntegration:
    async def test_add_creates_then_updates_existing(self, commerce_pool):
        r0 = await create_price_list(
            CreatePriceListConfig(),
            CreatePriceListInput(name="Sale", currency="CNY"),
            _OUT,
            pool=commerce_pool,
        )
        variant_id = await _make_variant(commerce_pool)

        r1 = await add_prices_to_list(
            AddPricesToListConfig(),
            AddPricesToListInput(
                price_list_id=str(r0["price_list_id"]),
                items=[PriceListPriceItem(variant_id=variant_id, price_cents=1000)],
            ),
            _OUT,
            pool=commerce_pool,
        )
        assert r1["status"] == "completed", r1
        assert r1["created"] == 1
        assert r1["updated"] == 0

        r2 = await add_prices_to_list(
            AddPricesToListConfig(),
            AddPricesToListInput(
                price_list_id=str(r0["price_list_id"]),
                items=[PriceListPriceItem(variant_id=variant_id, price_cents=1500)],
            ),
            _OUT,
            pool=commerce_pool,
        )
        assert r2["created"] == 0
        assert r2["updated"] == 1

        async with commerce_pool.acquire() as conn:
            price = await conn.fetchval(
                'SELECT price_cents FROM "price_list_item" WHERE price_list_id = $1 '
                "AND variant_id = $2",
                r0["price_list_id"],
                variant_id,
            )
        assert price == 1500

    async def test_unknown_variant_rolls_back(self, commerce_pool):
        r0 = await create_price_list(
            CreatePriceListConfig(),
            CreatePriceListInput(name="Sale", currency="CNY"),
            _OUT,
            pool=commerce_pool,
        )
        r = await add_prices_to_list(
            AddPricesToListConfig(),
            AddPricesToListInput(
                price_list_id=str(r0["price_list_id"]),
                items=[PriceListPriceItem(variant_id=str(uuid7()), price_cents=1000)],
            ),
            _OUT,
            pool=commerce_pool,
        )
        assert r["status"] == "failed"

    async def test_remove_prices(self, commerce_pool):
        r0 = await create_price_list(
            CreatePriceListConfig(),
            CreatePriceListInput(name="Sale", currency="CNY"),
            _OUT,
            pool=commerce_pool,
        )
        variant_id = await _make_variant(commerce_pool)
        await add_prices_to_list(
            AddPricesToListConfig(),
            AddPricesToListInput(
                price_list_id=str(r0["price_list_id"]),
                items=[PriceListPriceItem(variant_id=variant_id, price_cents=1000)],
            ),
            _OUT,
            pool=commerce_pool,
        )
        r = await remove_prices_from_list(
            RemovePricesFromListConfig(),
            RemovePricesFromListInput(
                price_list_id=str(r0["price_list_id"]), variant_ids=[variant_id]
            ),
            _OUT,
            pool=commerce_pool,
        )
        assert r["status"] == "completed", r
        assert r["removed"] == 1
        async with commerce_pool.acquire() as conn:
            count = await conn.fetchval(
                'SELECT COUNT(*) FROM "price_list_item" WHERE price_list_id = $1',
                r0["price_list_id"],
            )
        assert count == 0


class TestStockLocationIntegration:
    async def test_create_update(self, commerce_pool):
        r0 = await create_stock_location(
            CreateStockLocationConfig(),
            CreateStockLocationInput(name="仓A", region_code="cn-east"),
            _OUT,
            pool=commerce_pool,
        )
        assert r0["status"] == "completed", r0
        r1 = await update_stock_location(
            UpdateStockLocationConfig(),
            UpdateStockLocationInput(location_id=str(r0["location_id"]), name="仓A-2"),
            _OUT,
            pool=commerce_pool,
        )
        assert r1["status"] == "completed", r1

    async def test_delete_without_batches_succeeds(self, commerce_pool):
        r0 = await create_stock_location(
            CreateStockLocationConfig(),
            CreateStockLocationInput(name="仓B", region_code="cn-east"),
            _OUT,
            pool=commerce_pool,
        )
        r = await delete_stock_location(
            DeleteStockLocationConfig(),
            DeleteStockLocationInput(location_id=str(r0["location_id"])),
            _OUT,
            pool=commerce_pool,
        )
        assert r["status"] == "completed", r

    async def test_delete_with_active_batch_rejected(self, commerce_pool):
        r_loc = await create_stock_location(
            CreateStockLocationConfig(),
            CreateStockLocationInput(name="仓C", region_code="cn-east"),
            _OUT,
            pool=commerce_pool,
        )
        variant_id = await _make_variant(commerce_pool)
        await create_inventory_batch(
            CreateInventoryBatchConfig(),
            CreateInventoryBatchInput(
                variant_id=variant_id,
                location_id=str(r_loc["location_id"]),
                batch_no=f"B-{uuid7()}",
                video_url="https://x/v.mp4",
                cost_price_cents=100,
                retail_price_cents=200,
                stock_qty=5,
            ),
            _OUT,
            pool=commerce_pool,
        )
        r = await delete_stock_location(
            DeleteStockLocationConfig(),
            DeleteStockLocationInput(location_id=str(r_loc["location_id"])),
            _OUT,
            pool=commerce_pool,
        )
        assert r["status"] == "failed"
        assert "cannot delete" in r["error"]["message"]


class TestAdjustInventoryLevelIntegration:
    async def _make_batch(self, pool: PgPool, *, stock_qty: int = 10) -> str:
        variant_id = await _make_variant(pool)
        r_loc = await create_stock_location(
            CreateStockLocationConfig(),
            CreateStockLocationInput(name="仓", region_code="cn-east"),
            _OUT,
            pool=pool,
        )
        r_batch = await create_inventory_batch(
            CreateInventoryBatchConfig(),
            CreateInventoryBatchInput(
                variant_id=variant_id,
                location_id=str(r_loc["location_id"]),
                batch_no=f"B-{uuid7()}",
                video_url="https://x/v.mp4",
                cost_price_cents=100,
                retail_price_cents=200,
                stock_qty=stock_qty,
            ),
            _OUT,
            pool=pool,
        )
        return str(r_batch["batch_id"])

    async def test_positive_delta_increases_stock(self, commerce_pool):
        batch_id = await self._make_batch(commerce_pool, stock_qty=10)
        r = await adjust_inventory_level(
            AdjustInventoryLevelConfig(),
            AdjustInventoryLevelInput(batch_id=batch_id, delta=5, reason="盘盈"),
            _OUT,
            pool=commerce_pool,
        )
        assert r["status"] == "completed", r
        assert r["old_stock_qty"] == 10
        assert r["new_stock_qty"] == 15

    async def test_negative_delta_decreases_stock(self, commerce_pool):
        batch_id = await self._make_batch(commerce_pool, stock_qty=10)
        r = await adjust_inventory_level(
            AdjustInventoryLevelConfig(),
            AdjustInventoryLevelInput(batch_id=batch_id, delta=-3, reason="盘亏"),
            _OUT,
            pool=commerce_pool,
        )
        assert r["status"] == "completed", r
        assert r["new_stock_qty"] == 7

    async def test_negative_delta_below_zero_rejected(self, commerce_pool):
        batch_id = await self._make_batch(commerce_pool, stock_qty=5)
        r = await adjust_inventory_level(
            AdjustInventoryLevelConfig(),
            AdjustInventoryLevelInput(batch_id=batch_id, delta=-10, reason="盘亏"),
            _OUT,
            pool=commerce_pool,
        )
        assert r["status"] == "failed"
        assert "negative" in r["error"]["message"]

    async def test_delta_below_reserved_qty_rejected(self, commerce_pool):
        batch_id = await self._make_batch(commerce_pool, stock_qty=10)
        async with commerce_pool.acquire() as conn:
            await conn.execute(
                'UPDATE "inventory_batch" SET reserved_qty = 8 WHERE id = $1', batch_id
            )
        r = await adjust_inventory_level(
            AdjustInventoryLevelConfig(),
            AdjustInventoryLevelInput(batch_id=batch_id, delta=-5, reason="盘亏"),
            _OUT,
            pool=commerce_pool,
        )
        assert r["status"] == "failed"
        assert "oversell" in r["error"]["message"]

    async def test_unknown_batch_rejected(self, commerce_pool):
        r = await adjust_inventory_level(
            AdjustInventoryLevelConfig(),
            AdjustInventoryLevelInput(batch_id=str(uuid7()), delta=1, reason="盘盈"),
            _OUT,
            pool=commerce_pool,
        )
        assert r["status"] == "failed"
        assert "not found" in r["error"]["message"]


class TestSalesChannelAndPublishIntegration:
    async def test_create_update_delete_channel(self, commerce_pool):
        r0 = await create_sales_channel(
            CreateSalesChannelConfig(),
            CreateSalesChannelInput(name="Storefront"),
            _OUT,
            pool=commerce_pool,
        )
        assert r0["status"] == "completed", r0
        r1 = await update_sales_channel(
            UpdateSalesChannelConfig(),
            UpdateSalesChannelInput(channel_id=str(r0["channel_id"]), name="Storefront-2"),
            _OUT,
            pool=commerce_pool,
        )
        assert r1["status"] == "completed", r1
        r2 = await delete_sales_channel(
            DeleteSalesChannelConfig(),
            DeleteSalesChannelInput(channel_id=str(r0["channel_id"])),
            _OUT,
            pool=commerce_pool,
        )
        assert r2["status"] == "completed", r2

    async def test_publish_and_unpublish_products(self, commerce_pool):
        r_channel = await create_sales_channel(
            CreateSalesChannelConfig(),
            CreateSalesChannelInput(name="POS"),
            _OUT,
            pool=commerce_pool,
        )
        r_p1 = await create_product(
            CreateProductConfig(),
            CreateProductInput(title="P1", slug=f"p1-{uuid7()}"),
            _OUT,
            pool=commerce_pool,
        )
        r_p2 = await create_product(
            CreateProductConfig(),
            CreateProductInput(title="P2", slug=f"p2-{uuid7()}"),
            _OUT,
            pool=commerce_pool,
        )
        r = await publish_products_to_channel(
            PublishProductsToChannelConfig(),
            PublishProductsToChannelInput(
                channel_id=str(r_channel["channel_id"]),
                product_ids=[str(r_p1["product_id"]), str(r_p2["product_id"])],
            ),
            _OUT,
            pool=commerce_pool,
        )
        assert r["status"] == "completed", r
        assert r["published"] == 2

        # Re-publishing is idempotent — nothing new gets published.
        r_again = await publish_products_to_channel(
            PublishProductsToChannelConfig(),
            PublishProductsToChannelInput(
                channel_id=str(r_channel["channel_id"]), product_ids=[str(r_p1["product_id"])]
            ),
            _OUT,
            pool=commerce_pool,
        )
        assert r_again["published"] == 0

        r_unpub = await unpublish_products_from_channel(
            UnpublishProductsFromChannelConfig(),
            UnpublishProductsFromChannelInput(
                channel_id=str(r_channel["channel_id"]), product_ids=[str(r_p1["product_id"])]
            ),
            _OUT,
            pool=commerce_pool,
        )
        assert r_unpub["status"] == "completed", r_unpub
        assert r_unpub["unpublished"] == 1

        async with commerce_pool.acquire() as conn:
            count = await conn.fetchval(
                'SELECT COUNT(*) FROM "sales_channel_product" WHERE channel_id = $1',
                r_channel["channel_id"],
            )
        assert count == 1

    async def test_publish_unknown_product_rejected(self, commerce_pool):
        r_channel = await create_sales_channel(
            CreateSalesChannelConfig(),
            CreateSalesChannelInput(name="POS2"),
            _OUT,
            pool=commerce_pool,
        )
        r = await publish_products_to_channel(
            PublishProductsToChannelConfig(),
            PublishProductsToChannelInput(
                channel_id=str(r_channel["channel_id"]), product_ids=[str(uuid7())]
            ),
            _OUT,
            pool=commerce_pool,
        )
        assert r["status"] == "failed"
        assert "not found" in r["error"]["message"]
