#!/usr/bin/env python3

import argparse
from time import sleep

from InstaxBLE import InstaxBLE


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Query Instax printer status over BLE."
    )
    parser.add_argument("-a", "--device-address", help="Connect to a specific BLE address")
    parser.add_argument("-n", "--device-name", help="Connect to a specific Instax device name")
    parser.add_argument(
        "-w",
        "--wait-seconds",
        type=float,
        default=2.0,
        help="Seconds to wait for status notifications (default: 2.0)",
    )
    parser.add_argument("-q", "--quiet", action="store_true", help="Suppress noisy logging")
    args = parser.parse_args()

    instax = InstaxBLE(
        device_address=args.device_address,
        device_name=args.device_name,
        # Keep library in quiet mode to avoid its internal duplicate status print.
        quiet=True,
    )

    try:
        instax.connect()
        instax.get_printer_info()
        sleep(args.wait_seconds)
    except Exception as exc:
        print(f"Query failed: {exc}")
    finally:
        instax.disconnect()


if __name__ == "__main__":
    main()
