"""Tests for SPEC §4.8 (履约域), §4.9 (资金与售后域), §4.10 (系统批处理).

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
from omodul.authorize_payment_for_cart import (
    AuthorizePaymentForCartConfig,
    AuthorizePaymentForCartInput,
    authorize_payment_for_cart,
)
from omodul.cancel_batch_job import CancelBatchJobConfig, CancelBatchJobInput, cancel_batch_job
from omodul.cancel_claim import CancelClaimConfig, CancelClaimInput, cancel_claim
from omodul.cancel_fulfillment import (
    CancelFulfillmentConfig,
    CancelFulfillmentInput,
    cancel_fulfillment,
)
from omodul.cancel_return import CancelReturnConfig, CancelReturnInput, cancel_return
from omodul.cancel_swap import CancelSwapConfig, CancelSwapInput, cancel_swap
from omodul.capture_payment import CapturePaymentConfig, CapturePaymentInput, capture_payment
from omodul.complete_checkout import (
    CompleteCheckoutConfig,
    CompleteCheckoutInput,
    complete_checkout,
)
from omodul.create_batch_job import CreateBatchJobConfig, CreateBatchJobInput, create_batch_job
from omodul.create_cart import CreateCartConfig, CreateCartInput, create_cart
from omodul.create_claim import (
    ClaimItem,
    ClaimNewItem,
    CreateClaimConfig,
    CreateClaimInput,
    create_claim,
)
from omodul.create_fulfillment import (
    CreateFulfillmentConfig,
    CreateFulfillmentInput,
    FulfillmentItem,
    create_fulfillment,
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
from omodul.create_return_request import (
    CreateReturnRequestConfig,
    CreateReturnRequestInput,
    ReturnItem,
    create_return_request,
)
from omodul.create_swap import (
    CreateSwapConfig,
    CreateSwapInput,
    SwapNewItem,
    SwapReturnItem,
    create_swap,
)
from omodul.fulfill_claim import FulfillClaimConfig, FulfillClaimInput, fulfill_claim
from omodul.fulfill_swap import FulfillSwapConfig, FulfillSwapInput, fulfill_swap
from omodul.process_swap_payment import (
    ProcessSwapPaymentConfig,
    ProcessSwapPaymentInput,
    process_swap_payment,
)
from omodul.receive_return import ReceiveReturnConfig, ReceiveReturnInput, receive_return
from omodul.refund_payment import RefundPaymentConfig, RefundPaymentInput, refund_payment
from omodul.set_payment_session import (
    SetPaymentSessionConfig,
    SetPaymentSessionInput,
    set_payment_session,
)
from omodul.ship_fulfillment import ShipFulfillmentConfig, ShipFulfillmentInput, ship_fulfillment

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
    "fulfillment",
    "return_request",
    "swap",
    "claim",
    "batch_job",
]


# ---------------------------------------------------------------------------
# Validation-path tests (no DB) — always run
# ---------------------------------------------------------------------------


class TestCapturePaymentValidation:
    async def test_no_pool_rejected(self):
        r = await capture_payment(CapturePaymentConfig(), CapturePaymentInput(order_id="o"), _OUT)
        assert r["status"] == "failed"
        assert "pool is required" in r["error"]["message"]


class TestRefundPaymentValidation:
    async def test_zero_amount_rejected(self):
        r = await refund_payment(
            RefundPaymentConfig(),
            RefundPaymentInput(order_id="o", amount_cents=0),
            _OUT,
            pool=object(),
        )
        assert r["status"] == "failed"

    async def test_no_pool_rejected(self):
        r = await refund_payment(
            RefundPaymentConfig(), RefundPaymentInput(order_id="o", amount_cents=100), _OUT
        )
        assert r["status"] == "failed"
        assert "pool is required" in r["error"]["message"]


class TestCreateFulfillmentValidation:
    async def test_empty_items_rejected(self):
        r = await create_fulfillment(
            CreateFulfillmentConfig(), CreateFulfillmentInput(order_id="o", items=[]), _OUT
        )
        assert r["status"] == "failed"

    async def test_no_pool_rejected(self):
        r = await create_fulfillment(
            CreateFulfillmentConfig(),
            CreateFulfillmentInput(
                order_id="o", items=[FulfillmentItem(order_line_item_id="l", quantity=1)]
            ),
            _OUT,
        )
        assert r["status"] == "failed"
        assert "pool is required" in r["error"]["message"]


class TestCreateReturnRequestValidation:
    async def test_empty_items_rejected(self):
        r = await create_return_request(
            CreateReturnRequestConfig(), CreateReturnRequestInput(order_id="o", items=[]), _OUT
        )
        assert r["status"] == "failed"


class TestCreateSwapValidation:
    async def test_empty_return_items_rejected(self):
        r = await create_swap(
            CreateSwapConfig(),
            CreateSwapInput(
                order_id="o", return_items=[], new_items=[SwapNewItem(batch_id="b", quantity=1)]
            ),
            _OUT,
        )
        assert r["status"] == "failed"

    async def test_empty_new_items_rejected(self):
        r = await create_swap(
            CreateSwapConfig(),
            CreateSwapInput(
                order_id="o",
                return_items=[SwapReturnItem(order_line_item_id="l", quantity=1)],
                new_items=[],
            ),
            _OUT,
        )
        assert r["status"] == "failed"


class TestCreateClaimValidation:
    async def test_empty_items_rejected(self):
        r = await create_claim(CreateClaimConfig(), CreateClaimInput(order_id="o", items=[]), _OUT)
        assert r["status"] == "failed"

    async def test_replace_without_new_items_rejected(self):
        r = await create_claim(
            CreateClaimConfig(),
            CreateClaimInput(
                order_id="o",
                claim_type="replace",
                items=[ClaimItem(order_line_item_id="l", quantity=1)],
            ),
            _OUT,
        )
        assert r["status"] == "failed"
        assert "new_items is required" in r["error"]["message"]

    async def test_refund_with_new_items_rejected(self):
        r = await create_claim(
            CreateClaimConfig(),
            CreateClaimInput(
                order_id="o",
                claim_type="refund",
                items=[ClaimItem(order_line_item_id="l", quantity=1)],
                new_items=[ClaimNewItem(batch_id="b", quantity=1)],
            ),
            _OUT,
        )
        assert r["status"] == "failed"
        assert "must not be set" in r["error"]["message"]

    async def test_unknown_claim_type_rejected(self):
        r = await create_claim(
            CreateClaimConfig(),
            CreateClaimInput(
                order_id="o",
                claim_type="bogus",
                items=[ClaimItem(order_line_item_id="l", quantity=1)],
            ),
            _OUT,
        )
        assert r["status"] == "failed"


class TestCreateBatchJobValidation:
    async def test_empty_job_type_rejected(self):
        r = await create_batch_job(CreateBatchJobConfig(), CreateBatchJobInput(job_type=""), _OUT)
        assert r["status"] == "failed"

    async def test_no_pool_rejected(self):
        r = await create_batch_job(
            CreateBatchJobConfig(), CreateBatchJobInput(job_type="bulk_import"), _OUT
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
def manual_fulfillment_provider():
    from obase.fulfillment_providers import ManualFulfillmentProvider

    provider = ManualFulfillmentProvider()
    ProviderRegistry.get().register_generic("fulfillment", "manual", provider)
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
        name="commerce_aftersales_integ", dsn=TEST_PG_DSN, min_size=1, max_size=10
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
    """Full cart -> checkout flow (real capture via ManualPaymentProvider).
    Returns (order_id, batch_id)."""
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


async def _get_line_item_id(pool: PgPool, order_id: str) -> str:
    async with pool.acquire() as conn:
        return str(
            await conn.fetchval(
                'SELECT id FROM "order_line_item" WHERE order_id = $1 LIMIT 1', order_id
            )
        )


class TestCapturePaymentIntegration:
    async def test_capture_authorized_intent(self, commerce_pool, manual_provider):
        auth = await manual_provider.authorize(amount=1000, currency="CNY")
        order_id = str(uuid7())
        async with commerce_pool.acquire() as conn:
            await conn.execute(
                'INSERT INTO "customer_order" '
                "(id, status, grand_total_cents, payment_provider_name, payment_intent_id) "
                "VALUES ($1, 'pending', 1000, 'manual', $2)",
                order_id,
                auth["intent_id"],
            )
        r = await capture_payment(
            CapturePaymentConfig(),
            CapturePaymentInput(order_id=order_id),
            _OUT,
            pool=commerce_pool,
        )
        assert r["status"] == "completed", r
        assert r["capture_result"]["status"] == "captured"

    async def test_order_without_intent_rejected(self, commerce_pool):
        order_id = str(uuid7())
        async with commerce_pool.acquire() as conn:
            await conn.execute(
                "INSERT INTO \"customer_order\" (id, status) VALUES ($1, 'pending')", order_id
            )
        r = await capture_payment(
            CapturePaymentConfig(),
            CapturePaymentInput(order_id=order_id),
            _OUT,
            pool=commerce_pool,
        )
        assert r["status"] == "failed"


class TestRefundPaymentIntegration:
    async def test_partial_refund_updates_refunded_cents(self, commerce_pool, manual_provider):
        order_id, _ = await _completed_order(commerce_pool)
        r = await refund_payment(
            RefundPaymentConfig(redis_url=TEST_REDIS_URL),
            RefundPaymentInput(order_id=order_id, amount_cents=500),
            _OUT,
            pool=commerce_pool,
        )
        assert r["status"] == "completed", r
        async with commerce_pool.acquire() as conn:
            refunded = await conn.fetchval(
                'SELECT refunded_cents FROM "customer_order" WHERE id = $1', order_id
            )
        assert refunded == 500

    async def test_over_refund_rejected(self, commerce_pool, manual_provider):
        order_id, _ = await _completed_order(commerce_pool)
        r = await refund_payment(
            RefundPaymentConfig(redis_url=TEST_REDIS_URL),
            RefundPaymentInput(order_id=order_id, amount_cents=999999),
            _OUT,
            pool=commerce_pool,
        )
        assert r["status"] == "failed"
        assert "exceed" in r["error"]["message"]


class TestFulfillmentIntegration:
    async def test_create_ship_flow(
        self, commerce_pool, manual_provider, manual_fulfillment_provider
    ):
        order_id, _ = await _completed_order(commerce_pool)
        line_id = await _get_line_item_id(commerce_pool, order_id)

        r = await create_fulfillment(
            CreateFulfillmentConfig(),
            CreateFulfillmentInput(
                order_id=order_id, items=[FulfillmentItem(order_line_item_id=line_id, quantity=1)]
            ),
            _OUT,
            pool=commerce_pool,
        )
        assert r["status"] == "completed", r
        fulfillment_id = r["fulfillment_id"]

        r2 = await ship_fulfillment(
            ShipFulfillmentConfig(),
            ShipFulfillmentInput(
                fulfillment_id=fulfillment_id,
                provider_name="manual",
                shipment_info={"to": "somewhere"},
            ),
            _OUT,
            pool=commerce_pool,
        )
        assert r2["status"] == "completed", r2
        assert r2["tracking_number"]

    async def test_over_fulfillment_rejected(self, commerce_pool, manual_provider):
        order_id, _ = await _completed_order(commerce_pool, quantity=1)
        line_id = await _get_line_item_id(commerce_pool, order_id)

        r = await create_fulfillment(
            CreateFulfillmentConfig(),
            CreateFulfillmentInput(
                order_id=order_id, items=[FulfillmentItem(order_line_item_id=line_id, quantity=2)]
            ),
            _OUT,
            pool=commerce_pool,
        )
        assert r["status"] == "failed"
        assert "remaining unfulfilled" in r["error"]["message"]

    async def test_cancel_fulfillment(self, commerce_pool, manual_provider):
        order_id, _ = await _completed_order(commerce_pool)
        line_id = await _get_line_item_id(commerce_pool, order_id)
        r = await create_fulfillment(
            CreateFulfillmentConfig(),
            CreateFulfillmentInput(
                order_id=order_id, items=[FulfillmentItem(order_line_item_id=line_id, quantity=1)]
            ),
            _OUT,
            pool=commerce_pool,
        )
        r2 = await cancel_fulfillment(
            CancelFulfillmentConfig(),
            CancelFulfillmentInput(fulfillment_id=r["fulfillment_id"]),
            _OUT,
            pool=commerce_pool,
        )
        assert r2["status"] == "completed", r2

        # Canceled fulfillment frees up the line for re-fulfillment.
        r3 = await create_fulfillment(
            CreateFulfillmentConfig(),
            CreateFulfillmentInput(
                order_id=order_id, items=[FulfillmentItem(order_line_item_id=line_id, quantity=1)]
            ),
            _OUT,
            pool=commerce_pool,
        )
        assert r3["status"] == "completed", r3

    async def test_cancel_shipped_fulfillment_rejected(
        self, commerce_pool, manual_provider, manual_fulfillment_provider
    ):
        order_id, _ = await _completed_order(commerce_pool)
        line_id = await _get_line_item_id(commerce_pool, order_id)
        r = await create_fulfillment(
            CreateFulfillmentConfig(),
            CreateFulfillmentInput(
                order_id=order_id, items=[FulfillmentItem(order_line_item_id=line_id, quantity=1)]
            ),
            _OUT,
            pool=commerce_pool,
        )
        await ship_fulfillment(
            ShipFulfillmentConfig(),
            ShipFulfillmentInput(
                fulfillment_id=r["fulfillment_id"], provider_name="manual", shipment_info={}
            ),
            _OUT,
            pool=commerce_pool,
        )
        r2 = await cancel_fulfillment(
            CancelFulfillmentConfig(),
            CancelFulfillmentInput(fulfillment_id=r["fulfillment_id"]),
            _OUT,
            pool=commerce_pool,
        )
        assert r2["status"] == "failed"


class TestReturnRequestIntegration:
    async def test_create_receive_flow(self, commerce_pool, manual_provider):
        order_id, batch_id = await _completed_order(commerce_pool, quantity=2)
        line_id = await _get_line_item_id(commerce_pool, order_id)

        r = await create_return_request(
            CreateReturnRequestConfig(),
            CreateReturnRequestInput(
                order_id=order_id,
                items=[ReturnItem(order_line_item_id=line_id, quantity=1, reason="damaged")],
            ),
            _OUT,
            pool=commerce_pool,
        )
        assert r["status"] == "completed", r

        stock_before = await _stock_qty(commerce_pool, batch_id)
        r2 = await receive_return(
            ReceiveReturnConfig(),
            ReceiveReturnInput(return_request_id=r["return_request_id"]),
            _OUT,
            pool=commerce_pool,
        )
        assert r2["status"] == "completed", r2
        assert r2["refund_amount_cents"] == 2000

        stock_after = await _stock_qty(commerce_pool, batch_id)
        assert stock_after == stock_before + 1

        async with commerce_pool.acquire() as conn:
            refunded = await conn.fetchval(
                'SELECT refunded_cents FROM "customer_order" WHERE id = $1', order_id
            )
        assert refunded == 2000

    async def test_over_return_across_requests_rejected(self, commerce_pool, manual_provider):
        order_id, _ = await _completed_order(commerce_pool, quantity=2)
        line_id = await _get_line_item_id(commerce_pool, order_id)
        await create_return_request(
            CreateReturnRequestConfig(),
            CreateReturnRequestInput(
                order_id=order_id, items=[ReturnItem(order_line_item_id=line_id, quantity=2)]
            ),
            _OUT,
            pool=commerce_pool,
        )
        r = await create_return_request(
            CreateReturnRequestConfig(),
            CreateReturnRequestInput(
                order_id=order_id, items=[ReturnItem(order_line_item_id=line_id, quantity=1)]
            ),
            _OUT,
            pool=commerce_pool,
        )
        assert r["status"] == "failed"
        assert "remaining returnable" in r["error"]["message"]

    async def test_cancel_return_request(self, commerce_pool, manual_provider):
        order_id, _ = await _completed_order(commerce_pool)
        line_id = await _get_line_item_id(commerce_pool, order_id)
        r = await create_return_request(
            CreateReturnRequestConfig(),
            CreateReturnRequestInput(
                order_id=order_id, items=[ReturnItem(order_line_item_id=line_id, quantity=1)]
            ),
            _OUT,
            pool=commerce_pool,
        )
        r2 = await cancel_return(
            CancelReturnConfig(),
            CancelReturnInput(return_request_id=r["return_request_id"]),
            _OUT,
            pool=commerce_pool,
        )
        assert r2["status"] == "completed", r2

    async def test_cancel_received_return_rejected(self, commerce_pool, manual_provider):
        order_id, _ = await _completed_order(commerce_pool)
        line_id = await _get_line_item_id(commerce_pool, order_id)
        r = await create_return_request(
            CreateReturnRequestConfig(),
            CreateReturnRequestInput(
                order_id=order_id, items=[ReturnItem(order_line_item_id=line_id, quantity=1)]
            ),
            _OUT,
            pool=commerce_pool,
        )
        await receive_return(
            ReceiveReturnConfig(),
            ReceiveReturnInput(return_request_id=r["return_request_id"]),
            _OUT,
            pool=commerce_pool,
        )
        r2 = await cancel_return(
            CancelReturnConfig(),
            CancelReturnInput(return_request_id=r["return_request_id"]),
            _OUT,
            pool=commerce_pool,
        )
        assert r2["status"] == "failed"


async def _stock_qty(pool: PgPool, batch_id: str) -> int:
    async with pool.acquire() as conn:
        return await conn.fetchval(
            'SELECT stock_qty FROM "inventory_batch" WHERE id = $1', batch_id
        )


async def _reserved_qty(pool: PgPool, batch_id: str) -> int:
    async with pool.acquire() as conn:
        return await conn.fetchval(
            'SELECT reserved_qty FROM "inventory_batch" WHERE id = $1', batch_id
        )


class TestSwapIntegration:
    async def test_create_swap_positive_difference(self, commerce_pool, manual_provider):
        order_id, _ = await _completed_order(commerce_pool, quantity=1)
        line_id = await _get_line_item_id(commerce_pool, order_id)
        variant_id, loc_id = await _seed_variant(commerce_pool)
        new_batch_id = await _make_batch(
            commerce_pool, variant_id=variant_id, location_id=loc_id, stock_qty=5
        )
        async with commerce_pool.acquire() as conn:
            await conn.execute(
                'UPDATE "inventory_batch" SET retail_price_cents = 3000 WHERE id = $1',
                new_batch_id,
            )

        r = await create_swap(
            CreateSwapConfig(),
            CreateSwapInput(
                order_id=order_id,
                return_items=[SwapReturnItem(order_line_item_id=line_id, quantity=1)],
                new_items=[SwapNewItem(batch_id=new_batch_id, quantity=1)],
            ),
            _OUT,
            pool=commerce_pool,
        )
        assert r["status"] == "completed", r
        assert r["price_difference_cents"] == 1000
        assert r["payment_status"] == "not_paid"
        assert await _reserved_qty(commerce_pool, new_batch_id) == 1

    async def test_create_swap_zero_difference_not_required(self, commerce_pool, manual_provider):
        order_id, _ = await _completed_order(commerce_pool, quantity=1)
        line_id = await _get_line_item_id(commerce_pool, order_id)
        variant_id, loc_id = await _seed_variant(commerce_pool)
        new_batch_id = await _make_batch(
            commerce_pool, variant_id=variant_id, location_id=loc_id, stock_qty=5
        )
        r = await create_swap(
            CreateSwapConfig(),
            CreateSwapInput(
                order_id=order_id,
                return_items=[SwapReturnItem(order_line_item_id=line_id, quantity=1)],
                new_items=[SwapNewItem(batch_id=new_batch_id, quantity=1)],
            ),
            _OUT,
            pool=commerce_pool,
        )
        assert r["price_difference_cents"] == 0
        assert r["payment_status"] == "not_required"

    async def test_cancel_swap_releases_reservation(self, commerce_pool, manual_provider):
        order_id, _ = await _completed_order(commerce_pool)
        line_id = await _get_line_item_id(commerce_pool, order_id)
        variant_id, loc_id = await _seed_variant(commerce_pool)
        new_batch_id = await _make_batch(
            commerce_pool, variant_id=variant_id, location_id=loc_id, stock_qty=5
        )
        r = await create_swap(
            CreateSwapConfig(),
            CreateSwapInput(
                order_id=order_id,
                return_items=[SwapReturnItem(order_line_item_id=line_id, quantity=1)],
                new_items=[SwapNewItem(batch_id=new_batch_id, quantity=1)],
            ),
            _OUT,
            pool=commerce_pool,
        )
        r2 = await cancel_swap(
            CancelSwapConfig(), CancelSwapInput(swap_id=r["swap_id"]), _OUT, pool=commerce_pool
        )
        assert r2["status"] == "completed", r2
        assert await _reserved_qty(commerce_pool, new_batch_id) == 0

    async def test_fulfill_swap_requires_payment_settled(self, commerce_pool, manual_provider):
        order_id, _ = await _completed_order(commerce_pool)
        line_id = await _get_line_item_id(commerce_pool, order_id)
        variant_id, loc_id = await _seed_variant(commerce_pool)
        new_batch_id = await _make_batch(
            commerce_pool, variant_id=variant_id, location_id=loc_id, stock_qty=5
        )
        async with commerce_pool.acquire() as conn:
            await conn.execute(
                'UPDATE "inventory_batch" SET retail_price_cents = 3000 WHERE id = $1',
                new_batch_id,
            )
        r = await create_swap(
            CreateSwapConfig(),
            CreateSwapInput(
                order_id=order_id,
                return_items=[SwapReturnItem(order_line_item_id=line_id, quantity=1)],
                new_items=[SwapNewItem(batch_id=new_batch_id, quantity=1)],
            ),
            _OUT,
            pool=commerce_pool,
        )
        r2 = await fulfill_swap(
            FulfillSwapConfig(), FulfillSwapInput(swap_id=r["swap_id"]), _OUT, pool=commerce_pool
        )
        assert r2["status"] == "failed"
        assert "not settled" in r2["error"]["message"]

    async def test_full_swap_flow_zero_difference(self, commerce_pool, manual_provider):
        order_id, batch_id = await _completed_order(commerce_pool, quantity=1)
        line_id = await _get_line_item_id(commerce_pool, order_id)
        variant_id, loc_id = await _seed_variant(commerce_pool)
        new_batch_id = await _make_batch(
            commerce_pool, variant_id=variant_id, location_id=loc_id, stock_qty=5
        )
        r = await create_swap(
            CreateSwapConfig(),
            CreateSwapInput(
                order_id=order_id,
                return_items=[SwapReturnItem(order_line_item_id=line_id, quantity=1)],
                new_items=[SwapNewItem(batch_id=new_batch_id, quantity=1)],
            ),
            _OUT,
            pool=commerce_pool,
        )
        old_stock_before = await _stock_qty(commerce_pool, batch_id)
        new_stock_before = await _stock_qty(commerce_pool, new_batch_id)

        r2 = await fulfill_swap(
            FulfillSwapConfig(), FulfillSwapInput(swap_id=r["swap_id"]), _OUT, pool=commerce_pool
        )
        assert r2["status"] == "completed", r2
        assert r2["fulfillment_id"]

        assert await _stock_qty(commerce_pool, batch_id) == old_stock_before + 1
        assert await _stock_qty(commerce_pool, new_batch_id) == new_stock_before - 1
        assert await _reserved_qty(commerce_pool, new_batch_id) == 0

    async def test_process_swap_payment_positive_difference(self, commerce_pool, manual_provider):
        order_id, _ = await _completed_order(commerce_pool)
        line_id = await _get_line_item_id(commerce_pool, order_id)
        variant_id, loc_id = await _seed_variant(commerce_pool)
        new_batch_id = await _make_batch(
            commerce_pool, variant_id=variant_id, location_id=loc_id, stock_qty=5
        )
        async with commerce_pool.acquire() as conn:
            await conn.execute(
                'UPDATE "inventory_batch" SET retail_price_cents = 3000 WHERE id = $1',
                new_batch_id,
            )
        r = await create_swap(
            CreateSwapConfig(),
            CreateSwapInput(
                order_id=order_id,
                return_items=[SwapReturnItem(order_line_item_id=line_id, quantity=1)],
                new_items=[SwapNewItem(batch_id=new_batch_id, quantity=1)],
            ),
            _OUT,
            pool=commerce_pool,
        )
        r2 = await process_swap_payment(
            ProcessSwapPaymentConfig(),
            ProcessSwapPaymentInput(swap_id=r["swap_id"]),
            _OUT,
            pool=commerce_pool,
        )
        assert r2["status"] == "completed", r2
        async with commerce_pool.acquire() as conn:
            payment_status = await conn.fetchval(
                'SELECT payment_status FROM "swap" WHERE id = $1', r["swap_id"]
            )
        assert payment_status == "paid"

    async def test_process_swap_payment_negative_difference_refunds(
        self, commerce_pool, manual_provider
    ):
        order_id, _ = await _completed_order(commerce_pool, quantity=1)
        line_id = await _get_line_item_id(commerce_pool, order_id)
        variant_id, loc_id = await _seed_variant(commerce_pool)
        new_batch_id = await _make_batch(
            commerce_pool, variant_id=variant_id, location_id=loc_id, stock_qty=5
        )
        async with commerce_pool.acquire() as conn:
            await conn.execute(
                'UPDATE "inventory_batch" SET retail_price_cents = 500 WHERE id = $1',
                new_batch_id,
            )
        r = await create_swap(
            CreateSwapConfig(),
            CreateSwapInput(
                order_id=order_id,
                return_items=[SwapReturnItem(order_line_item_id=line_id, quantity=1)],
                new_items=[SwapNewItem(batch_id=new_batch_id, quantity=1)],
            ),
            _OUT,
            pool=commerce_pool,
        )
        assert r["price_difference_cents"] == -1500
        r2 = await process_swap_payment(
            ProcessSwapPaymentConfig(),
            ProcessSwapPaymentInput(swap_id=r["swap_id"]),
            _OUT,
            pool=commerce_pool,
        )
        assert r2["status"] == "completed", r2
        async with commerce_pool.acquire() as conn:
            refunded = await conn.fetchval(
                'SELECT refunded_cents FROM "customer_order" WHERE id = $1', order_id
            )
        assert refunded == 1500


class TestClaimIntegration:
    async def test_create_and_fulfill_refund_claim(self, commerce_pool, manual_provider):
        order_id, _ = await _completed_order(commerce_pool, quantity=1)
        line_id = await _get_line_item_id(commerce_pool, order_id)

        r = await create_claim(
            CreateClaimConfig(),
            CreateClaimInput(
                order_id=order_id,
                claim_type="refund",
                items=[ClaimItem(order_line_item_id=line_id, quantity=1, reason="damaged")],
            ),
            _OUT,
            pool=commerce_pool,
        )
        assert r["status"] == "completed", r
        assert r["refund_amount_cents"] == 2000

        r2 = await fulfill_claim(
            FulfillClaimConfig(),
            FulfillClaimInput(claim_id=r["claim_id"]),
            _OUT,
            pool=commerce_pool,
        )
        assert r2["status"] == "completed", r2
        assert r2["refund_result"] is not None

        async with commerce_pool.acquire() as conn:
            refunded = await conn.fetchval(
                'SELECT refunded_cents FROM "customer_order" WHERE id = $1', order_id
            )
        assert refunded == 2000

    async def test_create_and_fulfill_replace_claim(self, commerce_pool, manual_provider):
        order_id, _ = await _completed_order(commerce_pool, quantity=1)
        line_id = await _get_line_item_id(commerce_pool, order_id)
        variant_id, loc_id = await _seed_variant(commerce_pool)
        new_batch_id = await _make_batch(
            commerce_pool, variant_id=variant_id, location_id=loc_id, stock_qty=5
        )

        r = await create_claim(
            CreateClaimConfig(),
            CreateClaimInput(
                order_id=order_id,
                claim_type="replace",
                items=[ClaimItem(order_line_item_id=line_id, quantity=1)],
                new_items=[ClaimNewItem(batch_id=new_batch_id, quantity=1)],
            ),
            _OUT,
            pool=commerce_pool,
        )
        assert r["status"] == "completed", r
        assert await _reserved_qty(commerce_pool, new_batch_id) == 1

        stock_before = await _stock_qty(commerce_pool, new_batch_id)
        r2 = await fulfill_claim(
            FulfillClaimConfig(),
            FulfillClaimInput(claim_id=r["claim_id"]),
            _OUT,
            pool=commerce_pool,
        )
        assert r2["status"] == "completed", r2
        assert r2["fulfillment_id"]
        assert await _stock_qty(commerce_pool, new_batch_id) == stock_before - 1
        assert await _reserved_qty(commerce_pool, new_batch_id) == 0

    async def test_cancel_replace_claim_releases_reservation(self, commerce_pool, manual_provider):
        order_id, _ = await _completed_order(commerce_pool, quantity=1)
        line_id = await _get_line_item_id(commerce_pool, order_id)
        variant_id, loc_id = await _seed_variant(commerce_pool)
        new_batch_id = await _make_batch(
            commerce_pool, variant_id=variant_id, location_id=loc_id, stock_qty=5
        )
        r = await create_claim(
            CreateClaimConfig(),
            CreateClaimInput(
                order_id=order_id,
                claim_type="replace",
                items=[ClaimItem(order_line_item_id=line_id, quantity=1)],
                new_items=[ClaimNewItem(batch_id=new_batch_id, quantity=1)],
            ),
            _OUT,
            pool=commerce_pool,
        )
        r2 = await cancel_claim(
            CancelClaimConfig(),
            CancelClaimInput(claim_id=r["claim_id"]),
            _OUT,
            pool=commerce_pool,
        )
        assert r2["status"] == "completed", r2
        assert await _reserved_qty(commerce_pool, new_batch_id) == 0

    async def test_over_claim_across_requests_rejected(self, commerce_pool, manual_provider):
        order_id, _ = await _completed_order(commerce_pool, quantity=2)
        line_id = await _get_line_item_id(commerce_pool, order_id)
        await create_claim(
            CreateClaimConfig(),
            CreateClaimInput(
                order_id=order_id, items=[ClaimItem(order_line_item_id=line_id, quantity=2)]
            ),
            _OUT,
            pool=commerce_pool,
        )
        r = await create_claim(
            CreateClaimConfig(),
            CreateClaimInput(
                order_id=order_id, items=[ClaimItem(order_line_item_id=line_id, quantity=1)]
            ),
            _OUT,
            pool=commerce_pool,
        )
        assert r["status"] == "failed"
        assert "remaining claimable" in r["error"]["message"]


class TestBatchJobIntegration:
    async def test_create_and_cancel(self, commerce_pool):
        r = await create_batch_job(
            CreateBatchJobConfig(),
            CreateBatchJobInput(job_type="bulk_import", payload={"file": "products.csv"}),
            _OUT,
            pool=commerce_pool,
        )
        assert r["status"] == "completed", r

        r2 = await cancel_batch_job(
            CancelBatchJobConfig(),
            CancelBatchJobInput(batch_job_id=r["batch_job_id"]),
            _OUT,
            pool=commerce_pool,
        )
        assert r2["status"] == "completed", r2

    async def test_double_cancel_rejected(self, commerce_pool):
        r = await create_batch_job(
            CreateBatchJobConfig(),
            CreateBatchJobInput(job_type="bulk_export"),
            _OUT,
            pool=commerce_pool,
        )
        await cancel_batch_job(
            CancelBatchJobConfig(),
            CancelBatchJobInput(batch_job_id=r["batch_job_id"]),
            _OUT,
            pool=commerce_pool,
        )
        r2 = await cancel_batch_job(
            CancelBatchJobConfig(),
            CancelBatchJobInput(batch_job_id=r["batch_job_id"]),
            _OUT,
            pool=commerce_pool,
        )
        assert r2["status"] == "failed"
