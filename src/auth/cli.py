"""Admin CLI for MANUAL account provisioning — there is no public signup.

    python -m auth.cli create <handle> [--role admin]   # prompts for a password
    python -m auth.cli passwd <handle>                   # reset a password
    python -m auth.cli grant  <handle> <n>               # add credits (manual top-up)
    python -m auth.cli refund <handle> <n>               # return credits (manual refund)
    python -m auth.cli list

The store lives at <working_directory>/private/auth.db — a `private/` subtree that no
file-serving route is rooted in, kept out of the run dirs.
"""

import argparse
import getpass
import sys

from auth import store


def _prompt_password() -> str:
    pw = getpass.getpass("password: ")
    if pw != getpass.getpass("confirm : "):
        sys.exit("passwords do not match")
    if not pw:
        sys.exit("password is required")
    return pw


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(prog="auth.cli", description=__doc__)
    sub = parser.add_subparsers(dest="cmd", required=True)

    p_create = sub.add_parser("create", help="create a new account")
    p_create.add_argument("handle")
    p_create.add_argument("--role", default="user", choices=["user", "admin"])

    p_passwd = sub.add_parser("passwd", help="reset an account's password")
    p_passwd.add_argument("handle")

    p_grant = sub.add_parser("grant", help="add credits to an account (manual top-up)")
    p_grant.add_argument("handle")
    p_grant.add_argument("n", type=int)

    p_refund = sub.add_parser("refund", help="return credits to an account (manual refund)")
    p_refund.add_argument("handle")
    p_refund.add_argument("n", type=int)

    sub.add_parser("list", help="list accounts")

    args = parser.parse_args(argv)

    if args.cmd == "create":
        user = store.create_user(args.handle, _prompt_password(), role=args.role)
        print(f"created {user.handle!r} (id={user.id}, role={user.role})")
    elif args.cmd == "passwd":
        store.set_password(args.handle, _prompt_password())
        print(f"password updated for {args.handle!r}")
    elif args.cmd == "grant":
        user = store.get_user_by_handle(args.handle)
        if user is None:
            sys.exit(f"no user {args.handle!r}")
        new_balance = store.grant(user.id, args.n, "admin_grant")
        print(f"granted {args.n} to {user.handle!r} (balance={new_balance})")
    elif args.cmd == "refund":
        user = store.get_user_by_handle(args.handle)
        if user is None:
            sys.exit(f"no user {args.handle!r}")
        new_balance = store.refund(user.id, args.n, "admin_refund")
        print(f"refunded {args.n} to {user.handle!r} (balance={new_balance})")
    elif args.cmd == "list":
        for u in store.list_users():
            print(f"{u.id}  {u.handle:<20} {u.role}")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except ValueError as e:
        sys.exit(str(e))
