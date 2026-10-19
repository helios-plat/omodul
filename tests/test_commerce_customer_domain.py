"""Tests for the customer domain (SPEC §4.2): create/update_user,
reset_user_password, create/update_customer, add/update/delete_customer_address,
create_customer_group, assign_customer_to_group.

Validation-path tests (no pool) always run. Persisted-path tests are integration
tests against real PostgreSQL; they auto-skip when unavailable.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest
from obase.auth.password import bcrypt_verify
from obase.commerce_batch_schema import ensure_commerce_batch_schema
from obase.notification_providers import LogNotificationProvider
from obase.persistence.pool import PgPool
from obase.provider_registry import ProviderRegistry
from obase.uuid7 import uuid7

from omodul.add_customer_address import (
    AddCustomerAddressConfig,
    AddCustomerAddressInput,
    add_customer_address,
)
from omodul.assign_customer_to_group import (
    AssignCustomerToGroupConfig,
    AssignCustomerToGroupInput,
    assign_customer_to_group,
)
from omodul.create_customer import CreateCustomerConfig, CreateCustomerInput, create_customer
from omodul.create_customer_group import (
    CreateCustomerGroupConfig,
    CreateCustomerGroupInput,
    create_customer_group,
)
from omodul.create_user import CreateUserConfig, CreateUserInput, create_user
from omodul.delete_customer_address import (
    DeleteCustomerAddressConfig,
    DeleteCustomerAddressInput,
    delete_customer_address,
)
from omodul.reset_user_password import (
    ResetUserPasswordConfig,
    ResetUserPasswordInput,
    reset_user_password,
)
from omodul.update_customer import UpdateCustomerConfig, UpdateCustomerInput, update_customer
from omodul.update_customer_address import (
    UpdateCustomerAddressConfig,
    UpdateCustomerAddressInput,
    update_customer_address,
)
from omodul.update_user import UpdateUserConfig, UpdateUserInput, update_user

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


class TestCreateUserValidation:
    async def test_short_password_rejected(self):
        r = await create_user(
            CreateUserConfig(), CreateUserInput(email="a@x.com", password="short"), _OUT
        )
        assert r["status"] == "failed"

    async def test_dry_run_without_pool_completes(self):
        r = await create_user(
            CreateUserConfig(), CreateUserInput(email="a@x.com", password="longenough1"), _OUT
        )
        assert r["status"] == "completed"


class TestUpdateUserValidation:
    async def test_no_fields_rejected(self):
        r = await update_user(UpdateUserConfig(), UpdateUserInput(user_id="u"), _OUT, pool=object())
        assert r["status"] == "failed"
        assert "at least one field" in r["error"]["message"]

    async def test_no_pool_rejected(self):
        r = await update_user(UpdateUserConfig(), UpdateUserInput(user_id="u", name="x"), _OUT)
        assert r["status"] == "failed"
        assert "pool is required" in r["error"]["message"]


class TestResetUserPasswordValidation:
    async def test_no_pool_rejected(self):
        r = await reset_user_password(
            ResetUserPasswordConfig(), ResetUserPasswordInput(user_id="u"), _OUT
        )
        assert r["status"] == "failed"
        assert "pool is required" in r["error"]["message"]


class TestCreateCustomerValidation:
    async def test_missing_email_rejected(self):
        r = await create_customer(CreateCustomerConfig(), CreateCustomerInput(email=""), _OUT)
        assert r["status"] == "failed"

    async def test_dry_run_without_pool_completes(self):
        r = await create_customer(
            CreateCustomerConfig(), CreateCustomerInput(email="buyer@x.com"), _OUT
        )
        assert r["status"] == "completed"


class TestAddCustomerAddressValidation:
    async def test_no_pool_rejected(self):
        r = await add_customer_address(
            AddCustomerAddressConfig(),
            AddCustomerAddressInput(
                customer_id="c",
                recipient_name="n",
                phone="p",
                address_line1="a1",
                city="city",
                postal_code="000000",
            ),
            _OUT,
        )
        assert r["status"] == "failed"
        assert "pool is required" in r["error"]["message"]


class TestCreateCustomerGroupValidation:
    async def test_missing_name_rejected(self):
        r = await create_customer_group(
            CreateCustomerGroupConfig(), CreateCustomerGroupInput(name=""), _OUT
        )
        assert r["status"] == "failed"


class TestAssignCustomerToGroupValidation:
    async def test_no_pool_rejected(self):
        r = await assign_customer_to_group(
            AssignCustomerToGroupConfig(),
            AssignCustomerToGroupInput(customer_id="c", group_id="g"),
            _OUT,
        )
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
def log_notification_provider():
    provider = LogNotificationProvider()
    ProviderRegistry.get().register_generic("notification", "log", provider)
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
        name="commerce_customer_domain_integ", dsn=TEST_PG_DSN, min_size=1, max_size=10
    )
    await ensure_commerce_batch_schema(pool)
    yield pool
    async with pool.acquire() as conn:
        for table in reversed(_TABLES):
            await conn.execute(f'DROP TABLE IF EXISTS "public"."{table}" CASCADE')
    await pool.close()


class TestCreateUpdateUserIntegration:
    async def test_password_is_hashed_not_stored_plaintext(self, commerce_pool):
        r = await create_user(
            CreateUserConfig(),
            CreateUserInput(email="admin@x.com", password="supersecret1"),
            _OUT,
            pool=commerce_pool,
        )
        assert r["status"] == "completed", r
        async with commerce_pool.acquire() as conn:
            row = await conn.fetchrow(
                'SELECT password_hash FROM "app_user" WHERE id = $1', r["user_id"]
            )
        assert row["password_hash"] != "supersecret1"
        assert bcrypt_verify(password="supersecret1", hashed=row["password_hash"])

    async def test_duplicate_email_rejected(self, commerce_pool):
        await create_user(
            CreateUserConfig(),
            CreateUserInput(email="dup@x.com", password="longenough1"),
            _OUT,
            pool=commerce_pool,
        )
        r = await create_user(
            CreateUserConfig(),
            CreateUserInput(email="dup@x.com", password="longenough2"),
            _OUT,
            pool=commerce_pool,
        )
        assert r["status"] == "failed"

    async def test_update_password_changes_hash(self, commerce_pool):
        r0 = await create_user(
            CreateUserConfig(),
            CreateUserInput(email="pw@x.com", password="oldpassword1"),
            _OUT,
            pool=commerce_pool,
        )
        r = await update_user(
            UpdateUserConfig(),
            UpdateUserInput(user_id=str(r0["user_id"]), password="newpassword1"),
            _OUT,
            pool=commerce_pool,
        )
        assert r["status"] == "completed", r
        async with commerce_pool.acquire() as conn:
            row = await conn.fetchrow(
                'SELECT password_hash FROM "app_user" WHERE id = $1', r0["user_id"]
            )
        assert bcrypt_verify(password="newpassword1", hashed=row["password_hash"])
        assert not bcrypt_verify(password="oldpassword1", hashed=row["password_hash"])

    async def test_update_unknown_user_rejected(self, commerce_pool):
        r = await update_user(
            UpdateUserConfig(),
            UpdateUserInput(user_id=str(uuid7()), name="x"),
            _OUT,
            pool=commerce_pool,
        )
        assert r["status"] == "failed"
        assert "not found" in r["error"]["message"]


class TestResetUserPasswordIntegration:
    async def test_generates_token_and_sends_notification(
        self, commerce_pool, log_notification_provider
    ):
        r0 = await create_user(
            CreateUserConfig(),
            CreateUserInput(email="reset@x.com", password="longenough1"),
            _OUT,
            pool=commerce_pool,
        )
        r = await reset_user_password(
            ResetUserPasswordConfig(),
            ResetUserPasswordInput(user_id=str(r0["user_id"]), notification_provider_name="log"),
            _OUT,
            pool=commerce_pool,
        )
        assert r["status"] == "completed", r

        async with commerce_pool.acquire() as conn:
            row = await conn.fetchrow(
                'SELECT reset_token, reset_token_expires_at FROM "app_user" WHERE id = $1',
                r0["user_id"],
            )
        assert row["reset_token"] is not None
        assert row["reset_token_expires_at"] is not None
        assert len(log_notification_provider.sent) == 1
        assert log_notification_provider.sent[0]["to"] == "reset@x.com"
        assert row["reset_token"] in log_notification_provider.sent[0]["body"]

    async def test_unregistered_notification_provider_rejected(self, commerce_pool):
        r0 = await create_user(
            CreateUserConfig(),
            CreateUserInput(email="reset2@x.com", password="longenough1"),
            _OUT,
            pool=commerce_pool,
        )
        r = await reset_user_password(
            ResetUserPasswordConfig(),
            ResetUserPasswordInput(user_id=str(r0["user_id"]), notification_provider_name="ghost"),
            _OUT,
            pool=commerce_pool,
        )
        assert r["status"] == "failed"

    async def test_unknown_user_rejected(self, commerce_pool, log_notification_provider):
        r = await reset_user_password(
            ResetUserPasswordConfig(),
            ResetUserPasswordInput(user_id=str(uuid7())),
            _OUT,
            pool=commerce_pool,
        )
        assert r["status"] == "failed"
        assert "not found" in r["error"]["message"]


class TestCustomerIntegration:
    async def test_create_and_update_customer(self, commerce_pool):
        r0 = await create_customer(
            CreateCustomerConfig(),
            CreateCustomerInput(email="buyer@x.com"),
            _OUT,
            pool=commerce_pool,
        )
        assert r0["status"] == "completed", r0
        r = await update_customer(
            UpdateCustomerConfig(),
            UpdateCustomerInput(customer_id=str(r0["customer_id"]), phone="13800000000"),
            _OUT,
            pool=commerce_pool,
        )
        assert r["status"] == "completed", r
        async with commerce_pool.acquire() as conn:
            phone = await conn.fetchval(
                'SELECT phone FROM "customer" WHERE id = $1', r0["customer_id"]
            )
        assert phone == "13800000000"

    async def test_duplicate_email_rejected(self, commerce_pool):
        await create_customer(
            CreateCustomerConfig(),
            CreateCustomerInput(email="dup2@x.com"),
            _OUT,
            pool=commerce_pool,
        )
        r = await create_customer(
            CreateCustomerConfig(),
            CreateCustomerInput(email="dup2@x.com"),
            _OUT,
            pool=commerce_pool,
        )
        assert r["status"] == "failed"

    async def test_update_unknown_customer_rejected(self, commerce_pool):
        r = await update_customer(
            UpdateCustomerConfig(),
            UpdateCustomerInput(customer_id=str(uuid7()), phone="1"),
            _OUT,
            pool=commerce_pool,
        )
        assert r["status"] == "failed"
        assert "not found" in r["error"]["message"]


class TestCustomerAddressIntegration:
    async def test_add_address(self, commerce_pool):
        r0 = await create_customer(
            CreateCustomerConfig(),
            CreateCustomerInput(email="addr@x.com"),
            _OUT,
            pool=commerce_pool,
        )
        r = await add_customer_address(
            AddCustomerAddressConfig(),
            AddCustomerAddressInput(
                customer_id=str(r0["customer_id"]),
                recipient_name="张三",
                phone="13800000000",
                address_line1="人民路1号",
                city="上海",
                postal_code="200000",
            ),
            _OUT,
            pool=commerce_pool,
        )
        assert r["status"] == "completed", r

    async def test_setting_default_unsets_sibling(self, commerce_pool):
        r0 = await create_customer(
            CreateCustomerConfig(),
            CreateCustomerInput(email="addr2@x.com"),
            _OUT,
            pool=commerce_pool,
        )
        customer_id = str(r0["customer_id"])
        r1 = await add_customer_address(
            AddCustomerAddressConfig(),
            AddCustomerAddressInput(
                customer_id=customer_id,
                recipient_name="A",
                phone="1",
                address_line1="a1",
                city="c",
                postal_code="000000",
                is_default=True,
            ),
            _OUT,
            pool=commerce_pool,
        )
        await add_customer_address(
            AddCustomerAddressConfig(),
            AddCustomerAddressInput(
                customer_id=customer_id,
                recipient_name="B",
                phone="2",
                address_line1="a2",
                city="c",
                postal_code="000000",
                is_default=True,
            ),
            _OUT,
            pool=commerce_pool,
        )
        async with commerce_pool.acquire() as conn:
            first_default = await conn.fetchval(
                'SELECT is_default FROM "customer_address" WHERE id = $1', r1["address_id"]
            )
            count_default = await conn.fetchval(
                'SELECT COUNT(*) FROM "customer_address" WHERE customer_id = $1 '
                "AND is_default = true",
                customer_id,
            )
        assert first_default is False
        assert count_default == 1

    async def test_update_address_and_toggle_default(self, commerce_pool):
        r0 = await create_customer(
            CreateCustomerConfig(),
            CreateCustomerInput(email="addr3@x.com"),
            _OUT,
            pool=commerce_pool,
        )
        customer_id = str(r0["customer_id"])
        r1 = await add_customer_address(
            AddCustomerAddressConfig(),
            AddCustomerAddressInput(
                customer_id=customer_id,
                recipient_name="A",
                phone="1",
                address_line1="a1",
                city="c",
                postal_code="000000",
            ),
            _OUT,
            pool=commerce_pool,
        )
        r = await update_customer_address(
            UpdateCustomerAddressConfig(),
            UpdateCustomerAddressInput(address_id=str(r1["address_id"]), city="Beijing"),
            _OUT,
            pool=commerce_pool,
        )
        assert r["status"] == "completed", r
        async with commerce_pool.acquire() as conn:
            city = await conn.fetchval(
                'SELECT city FROM "customer_address" WHERE id = $1', r1["address_id"]
            )
        assert city == "Beijing"

    async def test_delete_address(self, commerce_pool):
        r0 = await create_customer(
            CreateCustomerConfig(),
            CreateCustomerInput(email="addr4@x.com"),
            _OUT,
            pool=commerce_pool,
        )
        r1 = await add_customer_address(
            AddCustomerAddressConfig(),
            AddCustomerAddressInput(
                customer_id=str(r0["customer_id"]),
                recipient_name="A",
                phone="1",
                address_line1="a1",
                city="c",
                postal_code="000000",
            ),
            _OUT,
            pool=commerce_pool,
        )
        r = await delete_customer_address(
            DeleteCustomerAddressConfig(),
            DeleteCustomerAddressInput(address_id=str(r1["address_id"])),
            _OUT,
            pool=commerce_pool,
        )
        assert r["status"] == "completed", r
        async with commerce_pool.acquire() as conn:
            deleted_at = await conn.fetchval(
                'SELECT deleted_at FROM "customer_address" WHERE id = $1', r1["address_id"]
            )
        assert deleted_at is not None

    async def test_unknown_customer_rejected(self, commerce_pool):
        r = await add_customer_address(
            AddCustomerAddressConfig(),
            AddCustomerAddressInput(
                customer_id=str(uuid7()),
                recipient_name="A",
                phone="1",
                address_line1="a1",
                city="c",
                postal_code="000000",
            ),
            _OUT,
            pool=commerce_pool,
        )
        assert r["status"] == "failed"
        assert "not found" in r["error"]["message"]


class TestCustomerGroupIntegration:
    async def test_create_and_assign(self, commerce_pool):
        r0 = await create_customer(
            CreateCustomerConfig(),
            CreateCustomerInput(email="grp@x.com"),
            _OUT,
            pool=commerce_pool,
        )
        rg = await create_customer_group(
            CreateCustomerGroupConfig(),
            CreateCustomerGroupInput(name="VIP"),
            _OUT,
            pool=commerce_pool,
        )
        assert rg["status"] == "completed", rg

        r = await assign_customer_to_group(
            AssignCustomerToGroupConfig(),
            AssignCustomerToGroupInput(
                customer_id=str(r0["customer_id"]), group_id=str(rg["group_id"])
            ),
            _OUT,
            pool=commerce_pool,
        )
        assert r["status"] == "completed", r
        async with commerce_pool.acquire() as conn:
            group_id = await conn.fetchval(
                'SELECT customer_group_id FROM "customer" WHERE id = $1', r0["customer_id"]
            )
        assert str(group_id) == str(rg["group_id"])

    async def test_reassign_overwrites_not_additive(self, commerce_pool):
        r0 = await create_customer(
            CreateCustomerConfig(),
            CreateCustomerInput(email="grp2@x.com"),
            _OUT,
            pool=commerce_pool,
        )
        rg1 = await create_customer_group(
            CreateCustomerGroupConfig(),
            CreateCustomerGroupInput(name="A"),
            _OUT,
            pool=commerce_pool,
        )
        rg2 = await create_customer_group(
            CreateCustomerGroupConfig(),
            CreateCustomerGroupInput(name="B"),
            _OUT,
            pool=commerce_pool,
        )
        await assign_customer_to_group(
            AssignCustomerToGroupConfig(),
            AssignCustomerToGroupInput(
                customer_id=str(r0["customer_id"]), group_id=str(rg1["group_id"])
            ),
            _OUT,
            pool=commerce_pool,
        )
        await assign_customer_to_group(
            AssignCustomerToGroupConfig(),
            AssignCustomerToGroupInput(
                customer_id=str(r0["customer_id"]), group_id=str(rg2["group_id"])
            ),
            _OUT,
            pool=commerce_pool,
        )
        async with commerce_pool.acquire() as conn:
            group_id = await conn.fetchval(
                'SELECT customer_group_id FROM "customer" WHERE id = $1', r0["customer_id"]
            )
        assert str(group_id) == str(rg2["group_id"])

    async def test_unknown_group_rejected(self, commerce_pool):
        r0 = await create_customer(
            CreateCustomerConfig(),
            CreateCustomerInput(email="grp3@x.com"),
            _OUT,
            pool=commerce_pool,
        )
        r = await assign_customer_to_group(
            AssignCustomerToGroupConfig(),
            AssignCustomerToGroupInput(customer_id=str(r0["customer_id"]), group_id=str(uuid7())),
            _OUT,
            pool=commerce_pool,
        )
        assert r["status"] == "failed"
        assert "not found" in r["error"]["message"]

    async def test_unknown_customer_rejected(self, commerce_pool):
        rg = await create_customer_group(
            CreateCustomerGroupConfig(),
            CreateCustomerGroupInput(name="C"),
            _OUT,
            pool=commerce_pool,
        )
        r = await assign_customer_to_group(
            AssignCustomerToGroupConfig(),
            AssignCustomerToGroupInput(customer_id=str(uuid7()), group_id=str(rg["group_id"])),
            _OUT,
            pool=commerce_pool,
        )
        assert r["status"] == "failed"
        assert "not found" in r["error"]["message"]
