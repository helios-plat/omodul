"""Tests for the regional settings domain (SPEC §4.1): create/update/delete_region,
create/update/delete_tax_rate.

Validation-path tests (no pool) always run. Persisted-path tests are integration
tests against real PostgreSQL; they auto-skip when unavailable.
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

from omodul.create_region import CreateRegionConfig, CreateRegionInput, create_region
from omodul.create_tax_rate import CreateTaxRateConfig, CreateTaxRateInput, create_tax_rate
from omodul.delete_region import DeleteRegionConfig, DeleteRegionInput, delete_region
from omodul.delete_tax_rate import DeleteTaxRateConfig, DeleteTaxRateInput, delete_tax_rate
from omodul.update_region import UpdateRegionConfig, UpdateRegionInput, update_region
from omodul.update_tax_rate import UpdateTaxRateConfig, UpdateTaxRateInput, update_tax_rate

TEST_PG_DSN = os.environ.get("TEST_PG_DSN", "postgresql://postgres:test@localhost:5432/obase_test")

_OUT = Path("/tmp")
_TABLES = [
    "region",
    "tax_rate",
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


class TestCreateRegionValidation:
    async def test_missing_code_rejected(self):
        r = await create_region(
            CreateRegionConfig(), CreateRegionInput(code="", name="华东", currency="CNY"), _OUT
        )
        assert r["status"] == "failed"

    async def test_dry_run_without_pool_completes(self):
        r = await create_region(
            CreateRegionConfig(),
            CreateRegionInput(code="cn-east", name="华东", currency="CNY"),
            _OUT,
        )
        assert r["status"] == "completed"

    async def test_unregistered_payment_provider_rejected(self):
        ProviderRegistry.clear()
        r = await create_region(
            CreateRegionConfig(),
            CreateRegionInput(
                code="cn-east", name="华东", currency="CNY", payment_provider_names=["ghost"]
            ),
            _OUT,
        )
        assert r["status"] == "failed"
        assert "not registered" in r["error"]["message"]


class TestUpdateRegionValidation:
    async def test_no_fields_rejected(self):
        r = await update_region(
            UpdateRegionConfig(), UpdateRegionInput(code="cn-east"), _OUT, pool=object()
        )
        assert r["status"] == "failed"
        assert "at least one field" in r["error"]["message"]

    async def test_no_pool_rejected(self):
        r = await update_region(
            UpdateRegionConfig(), UpdateRegionInput(code="cn-east", name="x"), _OUT
        )
        assert r["status"] == "failed"
        assert "pool is required" in r["error"]["message"]


class TestDeleteRegionValidation:
    async def test_no_pool_rejected(self):
        r = await delete_region(DeleteRegionConfig(), DeleteRegionInput(code="cn-east"), _OUT)
        assert r["status"] == "failed"
        assert "pool is required" in r["error"]["message"]


class TestCreateTaxRateValidation:
    async def test_rate_percent_out_of_bounds_rejected(self):
        r = await create_tax_rate(
            CreateTaxRateConfig(),
            CreateTaxRateInput(region_code="cn-east", name="VAT", rate_percent=101),
            _OUT,
        )
        assert r["status"] == "failed"

    async def test_dry_run_without_pool_completes(self):
        r = await create_tax_rate(
            CreateTaxRateConfig(),
            CreateTaxRateInput(region_code="cn-east", name="VAT", rate_percent=10),
            _OUT,
        )
        assert r["status"] == "completed"


class TestUpdateTaxRateValidation:
    async def test_no_fields_rejected(self):
        r = await update_tax_rate(
            UpdateTaxRateConfig(), UpdateTaxRateInput(tax_rate_id="t"), _OUT, pool=object()
        )
        assert r["status"] == "failed"
        assert "at least one field" in r["error"]["message"]

    async def test_no_pool_rejected(self):
        r = await update_tax_rate(
            UpdateTaxRateConfig(), UpdateTaxRateInput(tax_rate_id="t", rate_percent=5), _OUT
        )
        assert r["status"] == "failed"
        assert "pool is required" in r["error"]["message"]


class TestDeleteTaxRateValidation:
    async def test_no_pool_rejected(self):
        r = await delete_tax_rate(DeleteTaxRateConfig(), DeleteTaxRateInput(tax_rate_id="t"), _OUT)
        assert r["status"] == "failed"
        assert "pool is required" in r["error"]["message"]


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

    pool = await PgPool.create(
        name="commerce_region_tax_integ", dsn=TEST_PG_DSN, min_size=1, max_size=10
    )
    await ensure_commerce_batch_schema(pool)
    yield pool
    async with pool.acquire() as conn:
        for table in reversed(_TABLES):
            await conn.execute(f'DROP TABLE IF EXISTS "public"."{table}" CASCADE')
    await pool.close()


class TestCreateRegionIntegration:
    async def test_persists_with_payment_providers(self, commerce_pool, manual_provider):
        r = await create_region(
            CreateRegionConfig(),
            CreateRegionInput(
                code="cn-east", name="华东", currency="CNY", payment_provider_names=["manual"]
            ),
            _OUT,
            pool=commerce_pool,
        )
        assert r["status"] == "completed", r
        async with commerce_pool.acquire() as conn:
            row = await conn.fetchrow('SELECT * FROM "region" WHERE code = $1', "cn-east")
        assert row["name"] == "华东"
        assert row["payment_provider_names"] == ["manual"]

    async def test_unregistered_provider_rejected_with_pool(self, commerce_pool):
        r = await create_region(
            CreateRegionConfig(),
            CreateRegionInput(
                code="cn-east", name="华东", currency="CNY", payment_provider_names=["ghost"]
            ),
            _OUT,
            pool=commerce_pool,
        )
        assert r["status"] == "failed"
        async with commerce_pool.acquire() as conn:
            count = await conn.fetchval('SELECT COUNT(*) FROM "region"')
        assert count == 0

    async def test_duplicate_code_rejected(self, commerce_pool):
        await create_region(
            CreateRegionConfig(),
            CreateRegionInput(code="cn-east", name="华东", currency="CNY"),
            _OUT,
            pool=commerce_pool,
        )
        r = await create_region(
            CreateRegionConfig(),
            CreateRegionInput(code="cn-east", name="华东2", currency="CNY"),
            _OUT,
            pool=commerce_pool,
        )
        assert r["status"] == "failed"


class TestUpdateRegionIntegration:
    async def test_updates_currency(self, commerce_pool):
        await create_region(
            CreateRegionConfig(),
            CreateRegionInput(code="cn-east", name="华东", currency="CNY"),
            _OUT,
            pool=commerce_pool,
        )
        r = await update_region(
            UpdateRegionConfig(),
            UpdateRegionInput(code="cn-east", currency="USD"),
            _OUT,
            pool=commerce_pool,
        )
        assert r["status"] == "completed", r
        async with commerce_pool.acquire() as conn:
            currency = await conn.fetchval(
                'SELECT currency FROM "region" WHERE code = $1', "cn-east"
            )
        assert currency == "USD"

    async def test_unregistered_provider_rejected(self, commerce_pool):
        await create_region(
            CreateRegionConfig(),
            CreateRegionInput(code="cn-east", name="华东", currency="CNY"),
            _OUT,
            pool=commerce_pool,
        )
        r = await update_region(
            UpdateRegionConfig(),
            UpdateRegionInput(code="cn-east", payment_provider_names=["ghost"]),
            _OUT,
            pool=commerce_pool,
        )
        assert r["status"] == "failed"

    async def test_unknown_region_rejected(self, commerce_pool):
        r = await update_region(
            UpdateRegionConfig(),
            UpdateRegionInput(code="ghost-region", name="x"),
            _OUT,
            pool=commerce_pool,
        )
        assert r["status"] == "failed"
        assert "not found" in r["error"]["message"]


class TestDeleteRegionIntegration:
    async def test_deletes_unreferenced_region(self, commerce_pool):
        await create_region(
            CreateRegionConfig(),
            CreateRegionInput(code="cn-east", name="华东", currency="CNY"),
            _OUT,
            pool=commerce_pool,
        )
        r = await delete_region(
            DeleteRegionConfig(), DeleteRegionInput(code="cn-east"), _OUT, pool=commerce_pool
        )
        assert r["status"] == "completed", r

    async def test_rejects_region_with_orders(self, commerce_pool):
        await create_region(
            CreateRegionConfig(),
            CreateRegionInput(code="cn-east", name="华东", currency="CNY"),
            _OUT,
            pool=commerce_pool,
        )
        async with commerce_pool.acquire() as conn:
            await conn.execute(
                'INSERT INTO "customer_order" (id, region_code) VALUES ($1, $2)',
                str(uuid7()),
                "cn-east",
            )
        r = await delete_region(
            DeleteRegionConfig(), DeleteRegionInput(code="cn-east"), _OUT, pool=commerce_pool
        )
        assert r["status"] == "failed"
        assert "cannot delete" in r["error"]["message"]

    async def test_unknown_region_rejected(self, commerce_pool):
        r = await delete_region(
            DeleteRegionConfig(), DeleteRegionInput(code="ghost-region"), _OUT, pool=commerce_pool
        )
        assert r["status"] == "failed"
        assert "not found" in r["error"]["message"]


class TestCreateTaxRateIntegration:
    async def test_persists_referencing_real_region(self, commerce_pool):
        await create_region(
            CreateRegionConfig(),
            CreateRegionInput(code="cn-east", name="华东", currency="CNY"),
            _OUT,
            pool=commerce_pool,
        )
        r = await create_tax_rate(
            CreateTaxRateConfig(),
            CreateTaxRateInput(region_code="cn-east", name="VAT", rate_percent=13),
            _OUT,
            pool=commerce_pool,
        )
        assert r["status"] == "completed", r
        async with commerce_pool.acquire() as conn:
            row = await conn.fetchrow('SELECT * FROM "tax_rate" WHERE id = $1', r["tax_rate_id"])
        assert float(row["rate_percent"]) == 13

    async def test_unknown_region_rejected(self, commerce_pool):
        r = await create_tax_rate(
            CreateTaxRateConfig(),
            CreateTaxRateInput(region_code="ghost-region", name="VAT", rate_percent=13),
            _OUT,
            pool=commerce_pool,
        )
        assert r["status"] == "failed"
        assert "not found" in r["error"]["message"]


class TestUpdateTaxRateIntegration:
    async def test_updates_rate_percent(self, commerce_pool):
        await create_region(
            CreateRegionConfig(),
            CreateRegionInput(code="cn-east", name="华东", currency="CNY"),
            _OUT,
            pool=commerce_pool,
        )
        r0 = await create_tax_rate(
            CreateTaxRateConfig(),
            CreateTaxRateInput(region_code="cn-east", name="VAT", rate_percent=10),
            _OUT,
            pool=commerce_pool,
        )
        r = await update_tax_rate(
            UpdateTaxRateConfig(),
            UpdateTaxRateInput(tax_rate_id=str(r0["tax_rate_id"]), rate_percent=15),
            _OUT,
            pool=commerce_pool,
        )
        assert r["status"] == "completed", r
        async with commerce_pool.acquire() as conn:
            rate = await conn.fetchval(
                'SELECT rate_percent FROM "tax_rate" WHERE id = $1', r0["tax_rate_id"]
            )
        assert float(rate) == 15

    async def test_out_of_bounds_rejected(self, commerce_pool):
        await create_region(
            CreateRegionConfig(),
            CreateRegionInput(code="cn-east", name="华东", currency="CNY"),
            _OUT,
            pool=commerce_pool,
        )
        r0 = await create_tax_rate(
            CreateTaxRateConfig(),
            CreateTaxRateInput(region_code="cn-east", name="VAT", rate_percent=10),
            _OUT,
            pool=commerce_pool,
        )
        r = await update_tax_rate(
            UpdateTaxRateConfig(),
            UpdateTaxRateInput(tax_rate_id=str(r0["tax_rate_id"]), rate_percent=200),
            _OUT,
            pool=commerce_pool,
        )
        assert r["status"] == "failed"

    async def test_unknown_tax_rate_rejected(self, commerce_pool):
        r = await update_tax_rate(
            UpdateTaxRateConfig(),
            UpdateTaxRateInput(tax_rate_id=str(uuid7()), rate_percent=5),
            _OUT,
            pool=commerce_pool,
        )
        assert r["status"] == "failed"
        assert "not found" in r["error"]["message"]


class TestDeleteTaxRateIntegration:
    async def test_soft_deletes(self, commerce_pool):
        await create_region(
            CreateRegionConfig(),
            CreateRegionInput(code="cn-east", name="华东", currency="CNY"),
            _OUT,
            pool=commerce_pool,
        )
        r0 = await create_tax_rate(
            CreateTaxRateConfig(),
            CreateTaxRateInput(region_code="cn-east", name="VAT", rate_percent=10),
            _OUT,
            pool=commerce_pool,
        )
        r = await delete_tax_rate(
            DeleteTaxRateConfig(),
            DeleteTaxRateInput(tax_rate_id=str(r0["tax_rate_id"])),
            _OUT,
            pool=commerce_pool,
        )
        assert r["status"] == "completed", r
        async with commerce_pool.acquire() as conn:
            deleted_at = await conn.fetchval(
                'SELECT deleted_at FROM "tax_rate" WHERE id = $1', r0["tax_rate_id"]
            )
        assert deleted_at is not None

    async def test_unknown_tax_rate_rejected(self, commerce_pool):
        r = await delete_tax_rate(
            DeleteTaxRateConfig(),
            DeleteTaxRateInput(tax_rate_id=str(uuid7())),
            _OUT,
            pool=commerce_pool,
        )
        assert r["status"] == "failed"
        assert "not found" in r["error"]["message"]
