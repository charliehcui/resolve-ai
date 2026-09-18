import argparse
import json
import sys

from simulator.lab.scenarios import (
    create_order,
    create_shipment,
    dispatch_order,
    order_facts,
    publish_stock,
    restore_connection,
    seed_scenario,
    set_connection,
    set_shipment_sync,
    set_shop_sync,
    shipment_facts,
)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="resolveai-lab")
    commands = parser.add_subparsers(dest="command", required=True)
    order = commands.add_parser("order")
    order_commands = order.add_subparsers(dest="order_command", required=True)
    create = order_commands.add_parser("create")
    create.add_argument("--shop", required=True)
    create.add_argument("--order", required=True)
    create.add_argument("--sku", required=True)
    create.add_argument("--qty", type=int, required=True)
    create.add_argument("--amount-minor", type=int, required=True)
    create.add_argument("--payment-status", choices=["paid", "unpaid", "cancelled"], default="paid")
    show = order_commands.add_parser("show")
    show.add_argument("--shop", required=True)
    show.add_argument("--order", required=True)
    show.add_argument("--event", required=True)
    dispatch = order_commands.add_parser("dispatch")
    dispatch.add_argument("--shop", required=True)
    dispatch.add_argument("--order", required=True)
    shop = commands.add_parser("shop")
    shop_commands = shop.add_subparsers(dest="shop_command", required=True)
    sync = shop_commands.add_parser("sync")
    sync.add_argument("--shop", required=True)
    sync.add_argument("--enabled", choices=["true", "false"], required=True)
    shipment_sync = shop_commands.add_parser("shipment-sync")
    shipment_sync.add_argument("--shop", required=True)
    shipment_sync.add_argument("--enabled", choices=["true", "false"], required=True)
    connection = commands.add_parser("connection")
    connection_commands = connection.add_subparsers(dest="connection_command", required=True)
    connection_restore = connection_commands.add_parser("restore")
    connection_restore.add_argument("--shop", required=True)
    connection_set = connection_commands.add_parser("set")
    connection_set.add_argument("--shop", required=True)
    connection_set.add_argument("--status", choices=["authorized", "auth_expired", "forbidden", "rate_limited", "internal_error", "unavailable", "timeout"], required=True)
    shipment = commands.add_parser("shipment")
    shipment_commands = shipment.add_subparsers(dest="shipment_command", required=True)
    shipment_create = shipment_commands.add_parser("create")
    shipment_create.add_argument("--shop", required=True)
    shipment_create.add_argument("--order", required=True)
    shipment_create.add_argument("--carrier", required=True)
    shipment_create.add_argument("--tracking", required=True)
    shipment_show = shipment_commands.add_parser("show")
    shipment_show.add_argument("--shop", required=True)
    shipment_show.add_argument("--order", required=True)
    stock = commands.add_parser("stock")
    stock_commands = stock.add_subparsers(dest="stock_command", required=True)
    stock_publish = stock_commands.add_parser("publish")
    stock_publish.add_argument("--shop", required=True)
    stock_publish.add_argument("--sku", required=True)
    stock_publish.add_argument("--warehouse-sku", required=True)
    stock_publish.add_argument("--physical", type=int, required=True)
    stock_publish.add_argument("--reserved", type=int, required=True)
    seed = commands.add_parser("seed")
    seed.add_argument("--scenario", choices=["shipment_response_lost"], required=True)
    return parser


def main() -> None:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    args = build_parser().parse_args()
    if args.command == "seed":
        result = seed_scenario(args.scenario)
    elif args.command == "order" and args.order_command == "create":
        result = create_order(args.shop, args.order, args.sku, args.qty, args.amount_minor, args.payment_status)
    elif args.command == "order" and args.order_command == "show":
        result = order_facts(args.shop, args.order, args.event)
    elif args.command == "order" and args.order_command == "dispatch":
        result = dispatch_order(args.shop, args.order)
    elif args.command == "shop" and args.shop_command == "sync":
        result = set_shop_sync(args.shop, args.enabled == "true")
    elif args.command == "shop" and args.shop_command == "shipment-sync":
        result = set_shipment_sync(args.shop, args.enabled == "true")
    elif args.command == "connection" and args.connection_command == "restore":
        result = restore_connection(args.shop)
    elif args.command == "connection":
        result = set_connection(args.shop, args.status)
    elif args.command == "shipment" and args.shipment_command == "create":
        result = create_shipment(args.shop, args.order, args.carrier, args.tracking)
    elif args.command == "stock":
        result = publish_stock(args.shop, args.sku, args.warehouse_sku, args.physical, args.reserved)
    else:
        result = shipment_facts(args.shop, args.order)
    print(json.dumps(result, ensure_ascii=False, indent=2, default=str))


if __name__ == "__main__":
    main()
