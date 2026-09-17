import argparse
import json
import sys

from lab.scenarios import create_order, order_facts, set_shop_sync


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
    shop = commands.add_parser("shop")
    shop_commands = shop.add_subparsers(dest="shop_command", required=True)
    sync = shop_commands.add_parser("sync")
    sync.add_argument("--shop", required=True)
    sync.add_argument("--enabled", choices=["true", "false"], required=True)
    return parser


def main() -> None:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    args = build_parser().parse_args()
    if args.command == "order" and args.order_command == "create":
        result = create_order(args.shop, args.order, args.sku, args.qty, args.amount_minor, args.payment_status)
    elif args.command == "order" and args.order_command == "show":
        result = order_facts(args.shop, args.order, args.event)
    else:
        result = set_shop_sync(args.shop, args.enabled == "true")
    print(json.dumps(result, ensure_ascii=False, indent=2, default=str))


if __name__ == "__main__":
    main()
