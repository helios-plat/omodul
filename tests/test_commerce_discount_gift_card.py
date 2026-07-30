"""Tests for the batch-warehouse commerce discount + gift-card vertical (SPEC §3.2/§4.5/§4.6).

Covers: create/update/delete_discount, create/update_discount_rule,
create/delete_discount_condition, create/update/delete_gift_card,
apply/remove_discount_to_cart, apply/remove_gift_card_to_cart.

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
from omodul.apply_discount_to_cart import (
    ApplyDiscountToCartConfig,
    ApplyDiscountToCartInput,
    apply_discount_to_cart,
)
from omodul.apply_gift_card_to_cart import (
    ApplyGiftCardToCartConfig,
    ApplyGiftCardToCartInput,
    apply_gift_card_to_cart,
)
from omodul.create_cart import CreateCartConfig, CreateCartInput, create_cart
from omodul.create_discount import CreateDiscountConfig, CreateDiscountInput, create_discount
from omodul.create_discount_condition import (
    CreateDiscountConditionConfig,
    CreateDiscountConditionInput,
    create_discount_condition,
)
from omodul.create_discount_rule import (
    CreateDiscountRuleConfig,
    CreateDiscountRuleInput,
    create_discount_rule,
)
from omodul.create_gift_card import CreateGiftCardConfig, CreateGiftCardInput, create_gift_card
from omodul.create_inventory_batch import (
    CreateInventoryBatchConfig,
    CreateInventoryBatchInput,
    create_inventory_batch,
)
from omodul.delete_discount import DeleteDiscountConfig, DeleteDiscountInput, delete_discount
from omodul.delete_discount_condition import (
    DeleteDiscountConditionConfig,
    DeleteDiscountConditionInput,
    delete_discount_condition,
)
from omodul.delete_gift_card import DeleteGiftCardConfig, DeleteGiftCardInput, delete_gift_card
from omodul.remove_discount_from_cart import (
    RemoveDiscountFromCartConfig,
    RemoveDiscountFromCartInput,
    remove_discount_from_cart,
)
from omodul.remove_gift_card_from_cart import (
    RemoveGiftCardFromCartConfig,
    RemoveGiftCardFromCartInput,
    remove_gift_card_from_cart,
)
from omodul.update_discount import UpdateDiscountConfig, UpdateDiscountInput, update_discount
from omodul.update_discount_rule import (
    UpdateDiscountRuleConfig,
    UpdateDiscountRuleInput,
    update_discount_rule,
)
from omodul.update_gift_card import UpdateGiftCardConfig, UpdateGiftCardInput, update_gift_card

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


# ---------------------------------------------------------------------------
# Validation-path tests (no DB/Redis) — always run
# ---------------------------------------------------------------------------


class TestDiscountShellValidation:
    async def test_create_discount_requires_code(self):
        r = await create_discount(CreateDiscountConfig(), CreateDiscountInput(code=""), _OUT)
        assert r["status"] == "failed"

    async def test_create_discount_dry_run_without_pool(self):
        r = await create_discount(CreateDiscountConfig(), CreateDiscountInput(code="SAVE10"), _OUT)
        assert r["status"] == "completed"

    async def test_update_discount_invalid_status_rejected(self):
        r = await update_discount(
            UpdateDiscountConfig(), UpdateDiscountInput(discount_id="d", status="bogus"), _OUT
        )
        assert r["status"] == "failed"

    async def test_delete_discount_no_pool_rejected(self):
        r = await delete_discount(
            DeleteDiscountConfig(), DeleteDiscountInput(discount_id="d"), _OUT
        )
        assert r["status"] == "failed"


class TestDiscountRuleValidation:
    async def test_fixed_requires_amount_cents(self):
        r = await create_discount_rule(
            CreateDiscountRuleConfig(),
            CreateDiscountRuleInput(discount_id="d", rule_type="fixed"),
            _OUT,
        )
        assert r["status"] == "failed"
        assert "amount_cents" in r["error"]["message"]

    async def test_percentage_requires_percent(self):
        r = await create_discount_rule(
            CreateDiscountRuleConfig(),
            CreateDiscountRuleInput(discount_id="d", rule_type="percentage"),
            _OUT,
        )
        assert r["status"] == "failed"
        assert "percent" in r["error"]["message"]

    async def test_invalid_rule_type_rejected(self):
        r = await create_discount_rule(
            CreateDiscountRuleConfig(),
            CreateDiscountRuleInput(discount_id="d", rule_type="bogus"),
            _OUT,
        )
        assert r["status"] == "failed"

    async def test_free_shipping_needs_no_extra_fields(self):
        r = await create_discount_rule(
            CreateDiscountRuleConfig(),
            CreateDiscountRuleInput(discount_id="d", rule_type="free_shipping"),
            _OUT,
        )
        assert r["status"] == "completed"

    async def test_update_discount_rule_requires_at_least_one_field(self):
        r = await update_discount_rule(
            UpdateDiscountRuleConfig(), UpdateDiscountRuleInput(rule_id="r"), _OUT, pool=object()
        )
        assert r["status"] == "failed"
        assert "at least one field" in r["error"]["message"]


class TestDiscountConditionValidation:
    async def test_product_condition_requires_target_id(self):
        r = await create_discount_condition(
            CreateDiscountConditionConfig(),
            CreateDiscountConditionInput(discount_id="d", condition_type="product"),
            _OUT,
        )
        assert r["status"] == "failed"

    async def test_all_condition_rejects_target_id(self):
        r = await create_discount_condition(
            CreateDiscountConditionConfig(),
            CreateDiscountConditionInput(discount_id="d", condition_type="all", target_id="x"),
            _OUT,
        )
        assert r["status"] == "failed"

    async def test_all_condition_dry_run_completes(self):
        r = await create_discount_condition(
            CreateDiscountConditionConfig(),
            CreateDiscountConditionInput(discount_id="d", condition_type="all"),
            _OUT,
        )
        assert r["status"] == "completed"


class TestGiftCardShellValidation:
    async def test_create_gift_card_requires_positive_balance(self):
        r = await create_gift_card(
            CreateGiftCardConfig(),
            CreateGiftCardInput(code="GC1", initial_balance_cents=0),
            _OUT,
        )
        assert r["status"] == "failed"

    async def test_create_gift_card_dry_run_completes(self):
        r = await create_gift_card(
            CreateGiftCardConfig(),
            CreateGiftCardInput(code="GC1", initial_balance_cents=1000),
            _OUT,
        )
        assert r["status"] == "completed"
        assert r["balance_cents"] == 1000

    async def test_update_gift_card_invalid_status_rejected(self):
        r = await update_gift_card(
            UpdateGiftCardConfig(),
            UpdateGiftCardInput(gift_card_id="g", status="bogus"),
            _OUT,
            pool=object(),
        )
        assert r["status"] == "failed"

    async def test_delete_gift_card_no_pool_rejected(self):
        r = await delete_gift_card(
            DeleteGiftCardConfig(), DeleteGiftCardInput(gift_card_id="g"), _OUT
        )
        assert r["status"] == "failed"


class TestApplyRemoveValidation:
    async def test_apply_discount_no_pool_rejected(self):
        r = await apply_discount_to_cart(
            ApplyDiscountToCartConfig(),
            ApplyDiscountToCartInput(cart_id="c", code="X"),
            _OUT,
        )
        assert r["status"] == "failed"

    async def test_remove_discount_no_pool_rejected(self):
        r = await remove_discount_from_cart(
            RemoveDiscountFromCartConfig(),
            RemoveDiscountFromCartInput(cart_id="c", discount_id="d"),
            _OUT,
        )
        assert r["status"] == "failed"

    async def test_apply_gift_card_no_pool_rejected(self):
        r = await apply_gift_card_to_cart(
            ApplyGiftCardToCartConfig(),
            ApplyGiftCardToCartInput(cart_id="c", code="X"),
            _OUT,
        )
        assert r["status"] == "failed"

    async def test_remove_gift_card_no_pool_rejected(self):
        r = await remove_gift_card_from_cart(
            RemoveGiftCardFromCartConfig(),
            RemoveGiftCardFromCartInput(cart_id="c", gift_card_id="g"),
            _OUT,
        )
        assert r["status"] == "failed"


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
        name="commerce_discount_integ", dsn=TEST_PG_DSN, min_size=1, max_size=10
    )
    await ensure_commerce_batch_schema(pool)
    yield pool
    async with pool.acquire() as conn:
        for table in reversed(_TABLES):
            await conn.execute(f'DROP TABLE IF EXISTS "public"."{table}" CASCADE')
    await pool.close()


async def _seed_variant(pool: PgPool, *, product_id: str | None = None) -> tuple[str, str]:
    async with pool.acquire() as conn:
        loc_id = str(uuid7())
        await conn.execute(
            'INSERT INTO "public"."stock_location" (id, name, region_code) VALUES ($1, $2, $3)',
            loc_id,
            "仓A",
            "cn-east",
        )
        if product_id is None:
            product_id = str(uuid7())
            await conn.execute(
                'INSERT INTO "public"."product" (id, title, slug) VALUES ($1, $2, $3)',
                product_id,
                "T恤",
                f"tshirt-{product_id}",
            )
        variant_id = str(uuid7())
        await conn.execute(
            'INSERT INTO "public"."product_variant" (id, product_id, sku_code) VALUES ($1, $2, $3)',
            variant_id,
            product_id,
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


async def _add_line(pool: PgPool, *, cart_id: str, batch_id: str, quantity: int) -> dict:
    r = await add_line_item_to_cart(
        AddLineItemConfig(redis_url=TEST_REDIS_URL),
        AddLineItemInput(cart_id=cart_id, batch_id=batch_id, quantity=quantity),
        _OUT,
        pool=pool,
    )
    assert r["status"] == "completed", r
    return r


async def _make_discount(
    pool: PgPool,
    *,
    code: str,
    rule_type: str,
    amount_cents: int | None = None,
    percent: float | None = None,
    min_subtotal_cents: int | None = None,
    max_uses: int | None = None,
) -> str:
    r = await create_discount(
        CreateDiscountConfig(), CreateDiscountInput(code=code), _OUT, pool=pool
    )
    assert r["status"] == "completed", r
    discount_id = r["discount_id"]

    r2 = await create_discount_rule(
        CreateDiscountRuleConfig(),
        CreateDiscountRuleInput(
            discount_id=discount_id,
            rule_type=rule_type,
            amount_cents=amount_cents,
            percent=percent,
            min_subtotal_cents=min_subtotal_cents,
            max_uses=max_uses,
        ),
        _OUT,
        pool=pool,
    )
    assert r2["status"] == "completed", r2
    return discount_id


class TestDiscountCrudIntegration:
    async def test_create_update_delete_discount_lifecycle(self, commerce_pool):
        r = await create_discount(
            CreateDiscountConfig(), CreateDiscountInput(code="LIFECYCLE"), _OUT, pool=commerce_pool
        )
        assert r["status"] == "completed"
        discount_id = r["discount_id"]

        r2 = await update_discount(
            UpdateDiscountConfig(),
            UpdateDiscountInput(discount_id=discount_id, status="inactive"),
            _OUT,
            pool=commerce_pool,
        )
        assert r2["status"] == "completed"

        r3 = await delete_discount(
            DeleteDiscountConfig(),
            DeleteDiscountInput(discount_id=discount_id),
            _OUT,
            pool=commerce_pool,
        )
        assert r3["status"] == "completed"

    async def test_update_discount_rule_partial_update(self, commerce_pool):
        discount_id = await _make_discount(
            commerce_pool, code="PARTIAL", rule_type="fixed", amount_cents=500
        )
        async with commerce_pool.acquire() as conn:
            rule_id = await conn.fetchval(
                'SELECT id FROM "discount_rule" WHERE discount_id = $1', discount_id
            )
        r = await update_discount_rule(
            UpdateDiscountRuleConfig(),
            UpdateDiscountRuleInput(rule_id=str(rule_id), amount_cents=999),
            _OUT,
            pool=commerce_pool,
        )
        assert r["status"] == "completed"
        async with commerce_pool.acquire() as conn:
            amount = await conn.fetchval(
                'SELECT amount_cents FROM "discount_rule" WHERE id = $1', rule_id
            )
        assert amount == 999

    async def test_discount_condition_create_and_delete(self, commerce_pool):
        discount_id = await _make_discount(
            commerce_pool, code="COND", rule_type="fixed", amount_cents=100
        )
        r = await create_discount_condition(
            CreateDiscountConditionConfig(),
            CreateDiscountConditionInput(
                discount_id=discount_id, condition_type="product", target_id=str(uuid7())
            ),
            _OUT,
            pool=commerce_pool,
        )
        assert r["status"] == "completed"
        condition_id = r["condition_id"]

        r2 = await delete_discount_condition(
            DeleteDiscountConditionConfig(),
            DeleteDiscountConditionInput(condition_id=condition_id),
            _OUT,
            pool=commerce_pool,
        )
        assert r2["status"] == "completed"

    async def test_rule_duplicate_for_same_discount_rejected(self, commerce_pool):
        discount_id = await _make_discount(
            commerce_pool, code="DUPRULE", rule_type="fixed", amount_cents=100
        )
        r = await create_discount_rule(
            CreateDiscountRuleConfig(),
            CreateDiscountRuleInput(discount_id=discount_id, rule_type="percentage", percent=10),
            _OUT,
            pool=commerce_pool,
        )
        assert r["status"] == "failed"


class TestGiftCardCrudIntegration:
    async def test_create_gift_card_persists(self, commerce_pool):
        r = await create_gift_card(
            CreateGiftCardConfig(),
            CreateGiftCardInput(code="GCPERSIST", initial_balance_cents=5000),
            _OUT,
            pool=commerce_pool,
        )
        assert r["status"] == "completed"
        async with commerce_pool.acquire() as conn:
            row = await conn.fetchrow('SELECT * FROM "gift_card" WHERE id = $1', r["gift_card_id"])
        assert row["balance_cents"] == 5000

    async def test_delete_gift_card_with_balance_rejected(self, commerce_pool):
        r = await create_gift_card(
            CreateGiftCardConfig(),
            CreateGiftCardInput(code="GCNODELETE", initial_balance_cents=100),
            _OUT,
            pool=commerce_pool,
        )
        r2 = await delete_gift_card(
            DeleteGiftCardConfig(),
            DeleteGiftCardInput(gift_card_id=r["gift_card_id"]),
            _OUT,
            pool=commerce_pool,
        )
        assert r2["status"] == "failed"
        assert "nonzero balance" in r2["error"]["message"]

    async def test_update_gift_card_status(self, commerce_pool):
        r = await create_gift_card(
            CreateGiftCardConfig(),
            CreateGiftCardInput(code="GCUPDATE", initial_balance_cents=100),
            _OUT,
            pool=commerce_pool,
        )
        r2 = await update_gift_card(
            UpdateGiftCardConfig(),
            UpdateGiftCardInput(gift_card_id=r["gift_card_id"], status="inactive"),
            _OUT,
            pool=commerce_pool,
        )
        assert r2["status"] == "completed"


class TestApplyDiscountToCartIntegration:
    async def test_fixed_discount_reduces_grand_total(self, commerce_pool):
        variant_id, loc_id = await _seed_variant(commerce_pool)
        batch_id = await _make_batch(commerce_pool, variant_id=variant_id, location_id=loc_id)
        cart_id = await _make_cart(commerce_pool)
        await _add_line(commerce_pool, cart_id=cart_id, batch_id=batch_id, quantity=3)  # 6000 cents

        await _make_discount(commerce_pool, code="FIXED500", rule_type="fixed", amount_cents=500)
        r = await apply_discount_to_cart(
            ApplyDiscountToCartConfig(redis_url=TEST_REDIS_URL),
            ApplyDiscountToCartInput(cart_id=cart_id, code="FIXED500"),
            _OUT,
            pool=commerce_pool,
        )
        assert r["status"] == "completed", r
        assert r["applied_amount_cents"] == 500
        assert r["discount_cents"] == 500
        assert r["grand_total_cents"] == 5500

    async def test_percentage_discount_applies_per_line(self, commerce_pool):
        variant_id, loc_id = await _seed_variant(commerce_pool)
        batch_id = await _make_batch(commerce_pool, variant_id=variant_id, location_id=loc_id)
        cart_id = await _make_cart(commerce_pool)
        await _add_line(commerce_pool, cart_id=cart_id, batch_id=batch_id, quantity=1)  # 2000 cents

        await _make_discount(commerce_pool, code="PCT10", rule_type="percentage", percent=10)
        r = await apply_discount_to_cart(
            ApplyDiscountToCartConfig(redis_url=TEST_REDIS_URL),
            ApplyDiscountToCartInput(cart_id=cart_id, code="PCT10"),
            _OUT,
            pool=commerce_pool,
        )
        assert r["status"] == "completed", r
        assert r["applied_amount_cents"] == 200
        assert r["grand_total_cents"] == 1800

    async def test_free_shipping_zeroes_effective_shipping_without_mutating_column(
        self, commerce_pool
    ):
        variant_id, loc_id = await _seed_variant(commerce_pool)
        batch_id = await _make_batch(commerce_pool, variant_id=variant_id, location_id=loc_id)
        cart_id = await _make_cart(commerce_pool)
        await _add_line(commerce_pool, cart_id=cart_id, batch_id=batch_id, quantity=1)  # 2000 cents
        async with commerce_pool.acquire() as conn:
            await conn.execute('UPDATE "cart" SET shipping_cents = 800 WHERE id = $1', cart_id)

        await _make_discount(commerce_pool, code="FREESHIP", rule_type="free_shipping")
        r = await apply_discount_to_cart(
            ApplyDiscountToCartConfig(redis_url=TEST_REDIS_URL),
            ApplyDiscountToCartInput(cart_id=cart_id, code="FREESHIP"),
            _OUT,
            pool=commerce_pool,
        )
        assert r["status"] == "completed", r
        assert r["grand_total_cents"] == 2000  # shipping effectively waived
        async with commerce_pool.acquire() as conn:
            cart_row = await conn.fetchrow(
                'SELECT shipping_cents FROM "cart" WHERE id = $1', cart_id
            )
        assert cart_row["shipping_cents"] == 800  # column itself untouched

    async def test_product_condition_pool_restricts_eligible_lines(self, commerce_pool):
        variant_a, loc_id = await _seed_variant(commerce_pool)
        variant_b, _ = await _seed_variant(commerce_pool)
        batch_a = await _make_batch(
            commerce_pool, variant_id=variant_a, location_id=loc_id, retail_price_cents=1000
        )
        batch_b = await _make_batch(
            commerce_pool, variant_id=variant_b, location_id=loc_id, retail_price_cents=1000
        )
        cart_id = await _make_cart(commerce_pool)
        await _add_line(commerce_pool, cart_id=cart_id, batch_id=batch_a, quantity=1)
        await _add_line(commerce_pool, cart_id=cart_id, batch_id=batch_b, quantity=1)

        async with commerce_pool.acquire() as conn:
            product_a_id = await conn.fetchval(
                'SELECT product_id FROM "product_variant" WHERE id = $1', variant_a
            )

        discount_id = await _make_discount(
            commerce_pool, code="PRODONLY", rule_type="fixed", amount_cents=1000
        )
        await create_discount_condition(
            CreateDiscountConditionConfig(),
            CreateDiscountConditionInput(
                discount_id=discount_id, condition_type="product", target_id=str(product_a_id)
            ),
            _OUT,
            pool=commerce_pool,
        )

        r = await apply_discount_to_cart(
            ApplyDiscountToCartConfig(redis_url=TEST_REDIS_URL),
            ApplyDiscountToCartInput(cart_id=cart_id, code="PRODONLY"),
            _OUT,
            pool=commerce_pool,
        )
        assert r["status"] == "completed", r
        # amount_cents=1000 capped to product A's own line total (1000), not the full cart (2000).
        assert r["applied_amount_cents"] == 1000

    async def test_min_subtotal_not_met_rejected(self, commerce_pool):
        variant_id, loc_id = await _seed_variant(commerce_pool)
        batch_id = await _make_batch(
            commerce_pool, variant_id=variant_id, location_id=loc_id, retail_price_cents=500
        )
        cart_id = await _make_cart(commerce_pool)
        await _add_line(commerce_pool, cart_id=cart_id, batch_id=batch_id, quantity=1)  # 500 cents

        await _make_discount(
            commerce_pool,
            code="MINSPEND",
            rule_type="fixed",
            amount_cents=100,
            min_subtotal_cents=10_000,
        )
        r = await apply_discount_to_cart(
            ApplyDiscountToCartConfig(redis_url=TEST_REDIS_URL),
            ApplyDiscountToCartInput(cart_id=cart_id, code="MINSPEND"),
            _OUT,
            pool=commerce_pool,
        )
        assert r["status"] == "failed"
        assert "not eligible" in r["error"]["message"]

    async def test_duplicate_application_rejected(self, commerce_pool):
        variant_id, loc_id = await _seed_variant(commerce_pool)
        batch_id = await _make_batch(commerce_pool, variant_id=variant_id, location_id=loc_id)
        cart_id = await _make_cart(commerce_pool)
        await _add_line(commerce_pool, cart_id=cart_id, batch_id=batch_id, quantity=1)
        await _make_discount(commerce_pool, code="ONCEONLY", rule_type="fixed", amount_cents=100)

        cfg = ApplyDiscountToCartConfig(redis_url=TEST_REDIS_URL)
        r1 = await apply_discount_to_cart(
            cfg,
            ApplyDiscountToCartInput(cart_id=cart_id, code="ONCEONLY"),
            _OUT,
            pool=commerce_pool,
        )
        assert r1["status"] == "completed"
        r2 = await apply_discount_to_cart(
            cfg,
            ApplyDiscountToCartInput(cart_id=cart_id, code="ONCEONLY"),
            _OUT,
            pool=commerce_pool,
        )
        assert r2["status"] == "failed"
        assert "already applied" in r2["error"]["message"]

    async def test_usage_limit_exhausted_rejects_further_applications(self, commerce_pool):
        variant_id, loc_id = await _seed_variant(commerce_pool)
        batch_id = await _make_batch(commerce_pool, variant_id=variant_id, location_id=loc_id)
        cart_a = await _make_cart(commerce_pool)
        cart_b = await _make_cart(commerce_pool)
        batch_for_a = batch_id
        batch_for_b = await _make_batch(commerce_pool, variant_id=variant_id, location_id=loc_id)
        await _add_line(commerce_pool, cart_id=cart_a, batch_id=batch_for_a, quantity=1)
        await _add_line(commerce_pool, cart_id=cart_b, batch_id=batch_for_b, quantity=1)
        await _make_discount(
            commerce_pool, code="LIMITED1", rule_type="fixed", amount_cents=100, max_uses=1
        )

        cfg = ApplyDiscountToCartConfig(redis_url=TEST_REDIS_URL)
        r1 = await apply_discount_to_cart(
            cfg, ApplyDiscountToCartInput(cart_id=cart_a, code="LIMITED1"), _OUT, pool=commerce_pool
        )
        assert r1["status"] == "completed"
        r2 = await apply_discount_to_cart(
            cfg, ApplyDiscountToCartInput(cart_id=cart_b, code="LIMITED1"), _OUT, pool=commerce_pool
        )
        assert r2["status"] == "failed"
        assert "not eligible" in r2["error"]["message"]

    async def test_remove_discount_restores_totals_and_usage(self, commerce_pool):
        variant_id, loc_id = await _seed_variant(commerce_pool)
        batch_id = await _make_batch(commerce_pool, variant_id=variant_id, location_id=loc_id)
        cart_id = await _make_cart(commerce_pool)
        await _add_line(commerce_pool, cart_id=cart_id, batch_id=batch_id, quantity=1)  # 2000 cents
        discount_id = await _make_discount(
            commerce_pool, code="REMOVAL", rule_type="fixed", amount_cents=500
        )

        await apply_discount_to_cart(
            ApplyDiscountToCartConfig(redis_url=TEST_REDIS_URL),
            ApplyDiscountToCartInput(cart_id=cart_id, code="REMOVAL"),
            _OUT,
            pool=commerce_pool,
        )
        r = await remove_discount_from_cart(
            RemoveDiscountFromCartConfig(redis_url=TEST_REDIS_URL),
            RemoveDiscountFromCartInput(cart_id=cart_id, discount_id=discount_id),
            _OUT,
            pool=commerce_pool,
        )
        assert r["status"] == "completed", r
        assert r["discount_cents"] == 0
        assert r["grand_total_cents"] == 2000

        async with commerce_pool.acquire() as conn:
            uses = await conn.fetchval(
                'SELECT uses_count FROM "discount_rule" WHERE discount_id = $1', discount_id
            )
        assert uses == 0

    async def test_concurrent_apply_respects_usage_limit(self, commerce_pool):
        """Two carts racing to apply the same max_uses=1 discount concurrently must
        be serialized by DistributedLock — exactly one succeeds."""
        variant_id, loc_id = await _seed_variant(commerce_pool)
        batch_a = await _make_batch(commerce_pool, variant_id=variant_id, location_id=loc_id)
        batch_b = await _make_batch(commerce_pool, variant_id=variant_id, location_id=loc_id)
        cart_a = await _make_cart(commerce_pool)
        cart_b = await _make_cart(commerce_pool)
        await _add_line(commerce_pool, cart_id=cart_a, batch_id=batch_a, quantity=1)
        await _add_line(commerce_pool, cart_id=cart_b, batch_id=batch_b, quantity=1)
        await _make_discount(
            commerce_pool, code="RACE1", rule_type="fixed", amount_cents=100, max_uses=1
        )

        cfg = ApplyDiscountToCartConfig(redis_url=TEST_REDIS_URL, lock_timeout_seconds=5.0)
        results = await asyncio.gather(
            apply_discount_to_cart(
                cfg,
                ApplyDiscountToCartInput(cart_id=cart_a, code="RACE1"),
                _OUT,
                pool=commerce_pool,
            ),
            apply_discount_to_cart(
                cfg,
                ApplyDiscountToCartInput(cart_id=cart_b, code="RACE1"),
                _OUT,
                pool=commerce_pool,
            ),
        )
        statuses = [r["status"] for r in results]
        assert statuses.count("completed") == 1
        assert statuses.count("failed") == 1


class TestApplyGiftCardToCartIntegration:
    async def test_gift_card_covers_partial_amount_due(self, commerce_pool):
        variant_id, loc_id = await _seed_variant(commerce_pool)
        batch_id = await _make_batch(
            commerce_pool, variant_id=variant_id, location_id=loc_id, retail_price_cents=2000
        )
        cart_id = await _make_cart(commerce_pool)
        await _add_line(
            commerce_pool, cart_id=cart_id, batch_id=batch_id, quantity=1
        )  # grand_total 2000

        await create_gift_card(
            CreateGiftCardConfig(),
            CreateGiftCardInput(code="GCPART", initial_balance_cents=800),
            _OUT,
            pool=commerce_pool,
        )
        r = await apply_gift_card_to_cart(
            ApplyGiftCardToCartConfig(redis_url=TEST_REDIS_URL),
            ApplyGiftCardToCartInput(cart_id=cart_id, code="GCPART"),
            _OUT,
            pool=commerce_pool,
        )
        assert r["status"] == "completed", r
        assert r["applied_cents"] == 800
        assert r["amount_due_cents"] == 1200
        assert r["remaining_card_balance_cents"] == 0

    async def test_gift_card_balance_exceeds_due_caps_and_leaves_remainder(self, commerce_pool):
        variant_id, loc_id = await _seed_variant(commerce_pool)
        batch_id = await _make_batch(
            commerce_pool, variant_id=variant_id, location_id=loc_id, retail_price_cents=1000
        )
        cart_id = await _make_cart(commerce_pool)
        await _add_line(
            commerce_pool, cart_id=cart_id, batch_id=batch_id, quantity=1
        )  # grand_total 1000

        await create_gift_card(
            CreateGiftCardConfig(),
            CreateGiftCardInput(code="GCBIG", initial_balance_cents=5000),
            _OUT,
            pool=commerce_pool,
        )
        r = await apply_gift_card_to_cart(
            ApplyGiftCardToCartConfig(redis_url=TEST_REDIS_URL),
            ApplyGiftCardToCartInput(cart_id=cart_id, code="GCBIG"),
            _OUT,
            pool=commerce_pool,
        )
        assert r["status"] == "completed", r
        assert r["applied_cents"] == 1000
        assert r["amount_due_cents"] == 0
        assert r["remaining_card_balance_cents"] == 4000

    async def test_duplicate_gift_card_application_rejected(self, commerce_pool):
        variant_id, loc_id = await _seed_variant(commerce_pool)
        batch_id = await _make_batch(commerce_pool, variant_id=variant_id, location_id=loc_id)
        cart_id = await _make_cart(commerce_pool)
        await _add_line(commerce_pool, cart_id=cart_id, batch_id=batch_id, quantity=1)
        await create_gift_card(
            CreateGiftCardConfig(),
            CreateGiftCardInput(code="GCDUP", initial_balance_cents=5000),
            _OUT,
            pool=commerce_pool,
        )

        cfg = ApplyGiftCardToCartConfig(redis_url=TEST_REDIS_URL)
        r1 = await apply_gift_card_to_cart(
            cfg, ApplyGiftCardToCartInput(cart_id=cart_id, code="GCDUP"), _OUT, pool=commerce_pool
        )
        assert r1["status"] == "completed"
        r2 = await apply_gift_card_to_cart(
            cfg, ApplyGiftCardToCartInput(cart_id=cart_id, code="GCDUP"), _OUT, pool=commerce_pool
        )
        assert r2["status"] == "failed"
        assert "already applied" in r2["error"]["message"]

    async def test_remove_gift_card_refunds_balance(self, commerce_pool):
        variant_id, loc_id = await _seed_variant(commerce_pool)
        batch_id = await _make_batch(
            commerce_pool, variant_id=variant_id, location_id=loc_id, retail_price_cents=2000
        )
        cart_id = await _make_cart(commerce_pool)
        await _add_line(commerce_pool, cart_id=cart_id, batch_id=batch_id, quantity=1)

        r0 = await create_gift_card(
            CreateGiftCardConfig(),
            CreateGiftCardInput(code="GCREFUND", initial_balance_cents=800),
            _OUT,
            pool=commerce_pool,
        )
        gift_card_id = r0["gift_card_id"]
        await apply_gift_card_to_cart(
            ApplyGiftCardToCartConfig(redis_url=TEST_REDIS_URL),
            ApplyGiftCardToCartInput(cart_id=cart_id, code="GCREFUND"),
            _OUT,
            pool=commerce_pool,
        )

        r = await remove_gift_card_from_cart(
            RemoveGiftCardFromCartConfig(redis_url=TEST_REDIS_URL),
            RemoveGiftCardFromCartInput(cart_id=cart_id, gift_card_id=gift_card_id),
            _OUT,
            pool=commerce_pool,
        )
        assert r["status"] == "completed", r
        assert r["amount_due_cents"] == 2000

        async with commerce_pool.acquire() as conn:
            balance = await conn.fetchval(
                'SELECT balance_cents FROM "gift_card" WHERE id = $1', gift_card_id
            )
        assert balance == 800

    async def test_expired_gift_card_rejected(self, commerce_pool):
        variant_id, loc_id = await _seed_variant(commerce_pool)
        batch_id = await _make_batch(commerce_pool, variant_id=variant_id, location_id=loc_id)
        cart_id = await _make_cart(commerce_pool)
        await _add_line(commerce_pool, cart_id=cart_id, batch_id=batch_id, quantity=1)

        await create_gift_card(
            CreateGiftCardConfig(),
            CreateGiftCardInput(
                code="GCEXPIRED", initial_balance_cents=500, expires_at="2000-01-01T00:00:00+00:00"
            ),
            _OUT,
            pool=commerce_pool,
        )
        r = await apply_gift_card_to_cart(
            ApplyGiftCardToCartConfig(redis_url=TEST_REDIS_URL),
            ApplyGiftCardToCartInput(cart_id=cart_id, code="GCEXPIRED"),
            _OUT,
            pool=commerce_pool,
        )
        assert r["status"] == "failed"
        assert "expired" in r["error"]["message"]
