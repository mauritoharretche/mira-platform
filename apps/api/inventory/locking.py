"""One PostgreSQL transaction gate for all stock writers in the pilot operation.

Acquire before any Inventory row lock. Nested commands reacquire it in the same
transaction. No external calls, process lock or retry loop belongs here.
"""
from functools import wraps
from django.db import connections, transaction


def lock_inventory():
    connection = connections["default"]
    if not connection.in_atomic_block:
        raise RuntimeError("Inventory lock requires caller transaction")
    with connection.cursor() as cursor:
        cursor.execute("SELECT pg_advisory_xact_lock(%s)", [0x4D495241494E56])


def inventory_transaction(function):
    @wraps(function)
    def guarded(*args, **kwargs):
        with transaction.atomic(using="default"):
            lock_inventory()
            return function(*args, **kwargs)
    return guarded
