"""Run locally to issue private access codes; never commit the output."""
import argparse
import hashlib
import json
import re
import secrets


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('testers', nargs='+', help='Unique stable tester names')
    args = parser.parse_args()
    if len(set(args.testers)) != len(args.testers) or any(not re.fullmatch(r'[A-Za-z0-9_-]{1,64}', n) for n in args.testers):
        parser.error('Use unique names containing only letters, numbers, underscores and hyphens')
    records = {}
    for name in args.testers:
        code = secrets.token_urlsafe(32)
        records[name] = hashlib.sha256(code.encode()).hexdigest()
        print(f'{name}: {code}')
    print('\nPILOT_USERS_JSON (store in Railway variables):')
    print(json.dumps(records))


if __name__ == '__main__':
    main()
