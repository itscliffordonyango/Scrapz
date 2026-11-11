import argparse
import getpass

from app.config import get_settings
from app.db import create_user, init_db


def main() -> None:
    parser = argparse.ArgumentParser(prog="scrapz")
    subparsers = parser.add_subparsers(dest="command", required=True)
    user_parser = subparsers.add_parser("create-user", help="Create a local user account.")
    user_parser.add_argument("--username", required=True)
    user_parser.add_argument("--role", choices=("admin", "analyst", "viewer"), default="admin")
    args = parser.parse_args()

    settings = get_settings()
    init_db(settings.database_path)
    password = getpass.getpass("Password (minimum 12 characters): ")
    confirmation = getpass.getpass("Confirm password: ")
    if password != confirmation:
        parser.error("Passwords do not match.")
    user_id = create_user(settings.database_path, args.username, password, args.role)
    print(f"Created {args.role} account {args.username} (id {user_id}).")


if __name__ == "__main__":
    main()
