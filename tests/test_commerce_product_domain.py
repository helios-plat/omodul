"""Tests for the product taxonomy domain (SPEC §4.3): create/update/delete_product,
product_variant, product_option, product_category (nested set), product_collection.

Validation-path tests (no pool) always run. Persisted-path tests are integration
tests against real PostgreSQL; they auto-skip when unavailable.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest
from obase.commerce_batch_schema import ensure_commerce_batch_schema
from obase.persistence.pool import PgPool
from obase.provider_registry import ProviderRegistry
from obase.search_providers import LogSearchProvider
from obase.uuid7 import uuid7

from omodul.create_product import CreateProductConfig, CreateProductInput, create_product
from omodul.create_product_category import (
    CreateProductCategoryConfig,
    CreateProductCategoryInput,
    create_product_category,
)
from omodul.create_product_collection import (
    CreateProductCollectionConfig,
    CreateProductCollectionInput,
    create_product_collection,
)
from omodul.create_product_option import (
    CreateProductOptionConfig,
    CreateProductOptionInput,
    create_product_option,
)
from omodul.create_product_variant import (
    CreateProductVariantConfig,
    CreateProductVariantInput,
    create_product_variant,
)
from omodul.delete_product import DeleteProductConfig, DeleteProductInput, delete_product
from omodul.delete_product_category import (
    DeleteProductCategoryConfig,
    DeleteProductCategoryInput,
    delete_product_category,
)
from omodul.delete_product_collection import (
    DeleteProductCollectionConfig,
    DeleteProductCollectionInput,
    delete_product_collection,
)
from omodul.delete_product_option import (
    DeleteProductOptionConfig,
    DeleteProductOptionInput,
    delete_product_option,
)
from omodul.delete_product_variant import (
    DeleteProductVariantConfig,
    DeleteProductVariantInput,
    delete_product_variant,
)
from omodul.update_product import UpdateProductConfig, UpdateProductInput, update_product
from omodul.update_product_category import (
    UpdateProductCategoryConfig,
    UpdateProductCategoryInput,
    update_product_category,
)
from omodul.update_product_collection import (
    UpdateProductCollectionConfig,
    UpdateProductCollectionInput,
    update_product_collection,
)
from omodul.update_product_option import (
    UpdateProductOptionConfig,
    UpdateProductOptionInput,
    update_product_option,
)
from omodul.update_product_variant import (
    UpdateProductVariantConfig,
    UpdateProductVariantInput,
    update_product_variant,
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


class TestCreateProductValidation:
    async def test_missing_title_rejected(self):
        r = await create_product(
            CreateProductConfig(), CreateProductInput(title="", slug="s"), _OUT
        )
        assert r["status"] == "failed"

    async def test_dry_run_without_pool_completes(self):
        r = await create_product(
            CreateProductConfig(), CreateProductInput(title="T恤", slug="tshirt"), _OUT
        )
        assert r["status"] == "completed"


class TestCreateProductCategoryValidation:
    async def test_missing_name_rejected(self):
        r = await create_product_category(
            CreateProductCategoryConfig(), CreateProductCategoryInput(name="", slug="s"), _OUT
        )
        assert r["status"] == "failed"

    async def test_dry_run_without_pool_completes(self):
        r = await create_product_category(
            CreateProductCategoryConfig(),
            CreateProductCategoryInput(name="Clothing", slug="clothing"),
            _OUT,
        )
        assert r["status"] == "completed"


class TestCreateProductCollectionValidation:
    async def test_missing_name_rejected(self):
        r = await create_product_collection(
            CreateProductCollectionConfig(), CreateProductCollectionInput(name="", slug="s"), _OUT
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


@pytest.fixture(autouse=True)
def _clear_provider_registry():
    ProviderRegistry.clear()
    yield
    ProviderRegistry.clear()


@pytest.fixture
def log_search_provider():
    provider = LogSearchProvider()
    ProviderRegistry.get().register_generic("search", "log", provider)
    return provider


@pytest.fixture
async def commerce_pool():
    import asyncpg

    try:
        conn = await asyncpg.connect(TEST_PG_DSN, timeout=3)
        await conn.close()
    except Exception:
        pytest.skip("PostgreSQL not available")

    pool = await PgPool.create(
        name="commerce_product_domain_integ", dsn=TEST_PG_DSN, min_size=1, max_size=10
    )
    await ensure_commerce_batch_schema(pool)
    yield pool
    async with pool.acquire() as conn:
        for table in reversed(_TABLES):
            await conn.execute(f'DROP TABLE IF EXISTS "public"."{table}" CASCADE')
    await pool.close()


class TestProductIntegration:
    async def test_creates_with_search_indexing(self, commerce_pool, log_search_provider):
        r = await create_product(
            CreateProductConfig(),
            CreateProductInput(title="T恤", slug="tshirt-1"),
            _OUT,
            pool=commerce_pool,
        )
        assert r["status"] == "completed", r
        assert r["search_indexed"] is True
        assert str(r["product_id"]) in log_search_provider.indexed["product"]

    async def test_creates_without_search_provider_still_completes(self, commerce_pool):
        r = await create_product(
            CreateProductConfig(),
            CreateProductInput(title="T恤2", slug="tshirt-2"),
            _OUT,
            pool=commerce_pool,
        )
        assert r["status"] == "completed", r
        assert r["search_indexed"] is False

    async def test_update_product(self, commerce_pool, log_search_provider):
        r0 = await create_product(
            CreateProductConfig(),
            CreateProductInput(title="Old", slug="p-upd"),
            _OUT,
            pool=commerce_pool,
        )
        r = await update_product(
            UpdateProductConfig(),
            UpdateProductInput(product_id=str(r0["product_id"]), title="New"),
            _OUT,
            pool=commerce_pool,
        )
        assert r["status"] == "completed", r
        async with commerce_pool.acquire() as conn:
            title = await conn.fetchval(
                'SELECT title FROM "product" WHERE id = $1', r0["product_id"]
            )
        assert title == "New"

    async def test_delete_product_deindexes(self, commerce_pool, log_search_provider):
        r0 = await create_product(
            CreateProductConfig(),
            CreateProductInput(title="Del", slug="p-del"),
            _OUT,
            pool=commerce_pool,
        )
        r = await delete_product(
            DeleteProductConfig(),
            DeleteProductInput(product_id=str(r0["product_id"])),
            _OUT,
            pool=commerce_pool,
        )
        assert r["status"] == "completed", r
        assert r["search_deindexed"] is True

    async def test_update_unknown_product_rejected(self, commerce_pool):
        r = await update_product(
            UpdateProductConfig(),
            UpdateProductInput(product_id=str(uuid7()), title="x"),
            _OUT,
            pool=commerce_pool,
        )
        assert r["status"] == "failed"
        assert "not found" in r["error"]["message"]


class TestProductVariantIntegration:
    async def test_create_update_delete(self, commerce_pool):
        r0 = await create_product(
            CreateProductConfig(),
            CreateProductInput(title="P", slug="p-variant"),
            _OUT,
            pool=commerce_pool,
        )
        r1 = await create_product_variant(
            CreateProductVariantConfig(),
            CreateProductVariantInput(
                product_id=str(r0["product_id"]), sku_code="SKU-1", option_values={"Size": "M"}
            ),
            _OUT,
            pool=commerce_pool,
        )
        assert r1["status"] == "completed", r1

        r2 = await update_product_variant(
            UpdateProductVariantConfig(),
            UpdateProductVariantInput(variant_id=str(r1["variant_id"]), reference_price_cents=1000),
            _OUT,
            pool=commerce_pool,
        )
        assert r2["status"] == "completed", r2

        r3 = await delete_product_variant(
            DeleteProductVariantConfig(),
            DeleteProductVariantInput(variant_id=str(r1["variant_id"])),
            _OUT,
            pool=commerce_pool,
        )
        assert r3["status"] == "completed", r3

    async def test_unknown_product_rejected(self, commerce_pool):
        r = await create_product_variant(
            CreateProductVariantConfig(),
            CreateProductVariantInput(product_id=str(uuid7()), sku_code="SKU-X"),
            _OUT,
            pool=commerce_pool,
        )
        assert r["status"] == "failed"
        assert "not found" in r["error"]["message"]


class TestProductOptionIntegration:
    async def test_create_update_delete(self, commerce_pool):
        r0 = await create_product(
            CreateProductConfig(),
            CreateProductInput(title="P", slug="p-option"),
            _OUT,
            pool=commerce_pool,
        )
        r1 = await create_product_option(
            CreateProductOptionConfig(),
            CreateProductOptionInput(product_id=str(r0["product_id"]), name="Size"),
            _OUT,
            pool=commerce_pool,
        )
        assert r1["status"] == "completed", r1

        r2 = await update_product_option(
            UpdateProductOptionConfig(),
            UpdateProductOptionInput(option_id=str(r1["option_id"]), name="尺码"),
            _OUT,
            pool=commerce_pool,
        )
        assert r2["status"] == "completed", r2

        r3 = await delete_product_option(
            DeleteProductOptionConfig(),
            DeleteProductOptionInput(option_id=str(r1["option_id"])),
            _OUT,
            pool=commerce_pool,
        )
        assert r3["status"] == "completed", r3


class TestProductCategoryIntegration:
    async def test_root_category_lft_rgt(self, commerce_pool):
        r = await create_product_category(
            CreateProductCategoryConfig(),
            CreateProductCategoryInput(name="Clothing", slug="clothing-1"),
            _OUT,
            pool=commerce_pool,
        )
        assert r["status"] == "completed", r
        async with commerce_pool.acquire() as conn:
            row = await conn.fetchrow(
                'SELECT lft, rgt FROM "product_category" WHERE id = $1', r["category_id"]
            )
        assert row["lft"] == 1
        assert row["rgt"] == 2

    async def test_parent_child_nested_set_property(self, commerce_pool):
        rp = await create_product_category(
            CreateProductCategoryConfig(),
            CreateProductCategoryInput(name="Clothing", slug="clothing-2"),
            _OUT,
            pool=commerce_pool,
        )
        rc = await create_product_category(
            CreateProductCategoryConfig(),
            CreateProductCategoryInput(
                name="Shirts", slug="shirts-2", parent_id=str(rp["category_id"])
            ),
            _OUT,
            pool=commerce_pool,
        )
        async with commerce_pool.acquire() as conn:
            parent = await conn.fetchrow(
                'SELECT lft, rgt FROM "product_category" WHERE id = $1', rp["category_id"]
            )
            child = await conn.fetchrow(
                'SELECT lft, rgt FROM "product_category" WHERE id = $1', rc["category_id"]
            )
        assert parent["lft"] < child["lft"] < child["rgt"] < parent["rgt"]

    async def test_siblings_ordered_by_name(self, commerce_pool):
        rp = await create_product_category(
            CreateProductCategoryConfig(),
            CreateProductCategoryInput(name="Root", slug="root-3"),
            _OUT,
            pool=commerce_pool,
        )
        await create_product_category(
            CreateProductCategoryConfig(),
            CreateProductCategoryInput(
                name="Zebra", slug="zebra-3", parent_id=str(rp["category_id"])
            ),
            _OUT,
            pool=commerce_pool,
        )
        await create_product_category(
            CreateProductCategoryConfig(),
            CreateProductCategoryInput(
                name="Apple", slug="apple-3", parent_id=str(rp["category_id"])
            ),
            _OUT,
            pool=commerce_pool,
        )
        async with commerce_pool.acquire() as conn:
            rows = await conn.fetch(
                'SELECT name FROM "product_category" WHERE parent_id = $1 ORDER BY lft',
                rp["category_id"],
            )
        assert [r["name"] for r in rows] == ["Apple", "Zebra"]

    async def test_move_category_updates_tree(self, commerce_pool):
        r_a = await create_product_category(
            CreateProductCategoryConfig(),
            CreateProductCategoryInput(name="A", slug="a-4"),
            _OUT,
            pool=commerce_pool,
        )
        r_b = await create_product_category(
            CreateProductCategoryConfig(),
            CreateProductCategoryInput(name="B", slug="b-4"),
            _OUT,
            pool=commerce_pool,
        )
        r_c = await create_product_category(
            CreateProductCategoryConfig(),
            CreateProductCategoryInput(name="C", slug="c-4", parent_id=str(r_a["category_id"])),
            _OUT,
            pool=commerce_pool,
        )
        # Move C from under A to under B.
        r = await update_product_category(
            UpdateProductCategoryConfig(),
            UpdateProductCategoryInput(
                category_id=str(r_c["category_id"]), parent_id=str(r_b["category_id"])
            ),
            _OUT,
            pool=commerce_pool,
        )
        assert r["status"] == "completed", r
        async with commerce_pool.acquire() as conn:
            b = await conn.fetchrow(
                'SELECT lft, rgt FROM "product_category" WHERE id = $1', r_b["category_id"]
            )
            c = await conn.fetchrow(
                'SELECT lft, rgt FROM "product_category" WHERE id = $1', r_c["category_id"]
            )
        assert b["lft"] < c["lft"] < c["rgt"] < b["rgt"]

    async def test_cannot_be_own_parent(self, commerce_pool):
        r = await create_product_category(
            CreateProductCategoryConfig(),
            CreateProductCategoryInput(name="X", slug="x-5"),
            _OUT,
            pool=commerce_pool,
        )
        result = await update_product_category(
            UpdateProductCategoryConfig(),
            UpdateProductCategoryInput(
                category_id=str(r["category_id"]), parent_id=str(r["category_id"])
            ),
            _OUT,
            pool=commerce_pool,
        )
        assert result["status"] == "failed"
        assert "own parent" in result["error"]["message"]

    async def test_cannot_move_under_own_descendant(self, commerce_pool):
        r_a = await create_product_category(
            CreateProductCategoryConfig(),
            CreateProductCategoryInput(name="A", slug="a-6"),
            _OUT,
            pool=commerce_pool,
        )
        r_b = await create_product_category(
            CreateProductCategoryConfig(),
            CreateProductCategoryInput(name="B", slug="b-6", parent_id=str(r_a["category_id"])),
            _OUT,
            pool=commerce_pool,
        )
        result = await update_product_category(
            UpdateProductCategoryConfig(),
            UpdateProductCategoryInput(
                category_id=str(r_a["category_id"]), parent_id=str(r_b["category_id"])
            ),
            _OUT,
            pool=commerce_pool,
        )
        assert result["status"] == "failed"
        assert "descendant" in result["error"]["message"]

    async def test_delete_with_children_rejected(self, commerce_pool):
        r_a = await create_product_category(
            CreateProductCategoryConfig(),
            CreateProductCategoryInput(name="A", slug="a-7"),
            _OUT,
            pool=commerce_pool,
        )
        await create_product_category(
            CreateProductCategoryConfig(),
            CreateProductCategoryInput(name="B", slug="b-7", parent_id=str(r_a["category_id"])),
            _OUT,
            pool=commerce_pool,
        )
        result = await delete_product_category(
            DeleteProductCategoryConfig(),
            DeleteProductCategoryInput(category_id=str(r_a["category_id"])),
            _OUT,
            pool=commerce_pool,
        )
        assert result["status"] == "failed"
        assert "child" in result["error"]["message"]

    async def test_delete_leaf_succeeds(self, commerce_pool):
        r = await create_product_category(
            CreateProductCategoryConfig(),
            CreateProductCategoryInput(name="Leaf", slug="leaf-8"),
            _OUT,
            pool=commerce_pool,
        )
        result = await delete_product_category(
            DeleteProductCategoryConfig(),
            DeleteProductCategoryInput(category_id=str(r["category_id"])),
            _OUT,
            pool=commerce_pool,
        )
        assert result["status"] == "completed", result


class TestProductCollectionIntegration:
    async def test_create_with_initial_products(self, commerce_pool):
        rp = await create_product(
            CreateProductConfig(),
            CreateProductInput(title="P1", slug="p-coll-1"),
            _OUT,
            pool=commerce_pool,
        )
        r = await create_product_collection(
            CreateProductCollectionConfig(),
            CreateProductCollectionInput(
                name="Summer", slug="summer-1", product_ids=[str(rp["product_id"])]
            ),
            _OUT,
            pool=commerce_pool,
        )
        assert r["status"] == "completed", r
        async with commerce_pool.acquire() as conn:
            count = await conn.fetchval(
                'SELECT COUNT(*) FROM "product_collection_item" WHERE collection_id = $1',
                r["collection_id"],
            )
        assert count == 1

    async def test_create_with_unknown_product_rolls_back(self, commerce_pool):
        r = await create_product_collection(
            CreateProductCollectionConfig(),
            CreateProductCollectionInput(name="Bad", slug="bad-1", product_ids=[str(uuid7())]),
            _OUT,
            pool=commerce_pool,
        )
        assert r["status"] == "failed"
        async with commerce_pool.acquire() as conn:
            count = await conn.fetchval(
                'SELECT COUNT(*) FROM "product_collection" WHERE slug = $1', "bad-1"
            )
        assert count == 0

    async def test_update_replaces_membership(self, commerce_pool):
        r_p1 = await create_product(
            CreateProductConfig(),
            CreateProductInput(title="P1", slug="p-coll-2"),
            _OUT,
            pool=commerce_pool,
        )
        r_p2 = await create_product(
            CreateProductConfig(),
            CreateProductInput(title="P2", slug="p-coll-3"),
            _OUT,
            pool=commerce_pool,
        )
        r_coll = await create_product_collection(
            CreateProductCollectionConfig(),
            CreateProductCollectionInput(
                name="Winter", slug="winter-1", product_ids=[str(r_p1["product_id"])]
            ),
            _OUT,
            pool=commerce_pool,
        )
        r = await update_product_collection(
            UpdateProductCollectionConfig(),
            UpdateProductCollectionInput(
                collection_id=str(r_coll["collection_id"]), product_ids=[str(r_p2["product_id"])]
            ),
            _OUT,
            pool=commerce_pool,
        )
        assert r["status"] == "completed", r
        async with commerce_pool.acquire() as conn:
            rows = await conn.fetch(
                'SELECT product_id FROM "product_collection_item" WHERE collection_id = $1',
                r_coll["collection_id"],
            )
        assert [str(row["product_id"]) for row in rows] == [str(r_p2["product_id"])]

    async def test_delete_collection(self, commerce_pool):
        r = await create_product_collection(
            CreateProductCollectionConfig(),
            CreateProductCollectionInput(name="Gone", slug="gone-1"),
            _OUT,
            pool=commerce_pool,
        )
        result = await delete_product_collection(
            DeleteProductCollectionConfig(),
            DeleteProductCollectionInput(collection_id=str(r["collection_id"])),
            _OUT,
            pool=commerce_pool,
        )
        assert result["status"] == "completed", result
