import json
import secrets
from pathlib import Path

from app.auth import hash_token
from app.config import PROJECT_ROOT
from app.db import get_connection, initialize_database

TOKEN_FILE = PROJECT_ROOT / ".local" / "test_tokens.json"
SERVICE_TOKEN_FILE = PROJECT_ROOT / ".local" / "service_tokens.json"


def bootstrap() -> Path:
    initialize_database()
    tokens = {"admin-a": secrets.token_urlsafe(32), "staff-a": secrets.token_urlsafe(32), "staff-b": secrets.token_urlsafe(32)}
    with get_connection() as connection:
        connection.execute("INSERT INTO support.companies (company_id, name) VALUES ('company-a', '演示商家 A'), ('company-b', '演示商家 B') ON CONFLICT (company_id) DO UPDATE SET name = EXCLUDED.name")
        connection.execute("INSERT INTO support.users (user_id, company_id, name, role) VALUES ('admin-a', 'company-a', '管理员 A', 'admin'), ('staff-a', 'company-a', '员工 A', 'staff'), ('staff-b', 'company-b', '员工 B', 'staff') ON CONFLICT (user_id) DO UPDATE SET company_id = EXCLUDED.company_id, name = EXCLUDED.name, role = EXCLUDED.role")
        connection.execute("DELETE FROM support.access_tokens WHERE user_id IN ('admin-a', 'staff-a', 'staff-b')")
        with connection.cursor() as cursor:
            cursor.executemany("INSERT INTO support.access_tokens (token_hash, user_id) VALUES (%s, %s)", [(hash_token(token), user_id) for user_id, token in tokens.items()])
        connection.execute("""INSERT INTO merchant.shops (company_id, shop_id, channel, sync_enabled, connection_status)
            VALUES ('company-a', 'shop-a', 'A', TRUE, 'authorized'), ('company-a', 'shop-b', 'B', TRUE, 'authorized'), ('company-b', 'shop-b-company', 'B', TRUE, 'authorized')
            ON CONFLICT (company_id, shop_id) DO UPDATE SET channel = EXCLUDED.channel, sync_enabled = EXCLUDED.sync_enabled, connection_status = EXCLUDED.connection_status""")
        connection.execute("""INSERT INTO merchant.sku_mappings (company_id, shop_id, platform_sku, merchant_sku)
            VALUES ('company-a', 'shop-a', 'SKU-1', 'MERCHANT-SKU-1'), ('company-a', 'shop-b', 'SKU-1', 'MERCHANT-SKU-1'), ('company-b', 'shop-b-company', 'SKU-1', 'MERCHANT-SKU-1')
            ON CONFLICT (company_id, shop_id, platform_sku) DO UPDATE SET merchant_sku = EXCLUDED.merchant_sku, active = TRUE""")
    TOKEN_FILE.parent.mkdir(parents=True, exist_ok=True)
    TOKEN_FILE.write_text(json.dumps(tokens, ensure_ascii=False, indent=2), encoding="utf-8")
    service_tokens = json.loads(SERVICE_TOKEN_FILE.read_text(encoding="utf-8")) if SERVICE_TOKEN_FILE.exists() else {}
    changed = False
    for name in ("merchant-ingest", "lab-control", "support-read", "support-write"):
        if name not in service_tokens:
            service_tokens[name] = secrets.token_urlsafe(32)
            changed = True
    if changed:
        SERVICE_TOKEN_FILE.write_text(json.dumps(service_tokens, ensure_ascii=False, indent=2), encoding="utf-8")
    return TOKEN_FILE


def main() -> None:
    token_file = bootstrap()
    print(f"Bootstrap complete. Local test tokens: {token_file}")


if __name__ == "__main__":
    main()
