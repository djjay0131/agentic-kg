#!/usr/bin/env python3
"""
GitHub CLI wrapper using ghapi.

This provides a gh-like interface when the gh CLI cannot be installed
due to network restrictions.

Usage:
    ./github-cli.py pr list
    ./github-cli.py pr view <number>
    ./github-cli.py pr checks <number>
    ./github-cli.py issue list
    ./github-cli.py repo info
"""

import argparse
import os
import sys
from datetime import datetime


def get_api():
    """Get configured GhApi instance."""
    try:
        from ghapi.all import GhApi
    except ImportError:
        print("Error: ghapi not installed. Run: pip install ghapi")
        sys.exit(1)

    # Get repo info from git
    remote_url = os.popen("git remote get-url origin 2>/dev/null").read().strip()

    owner_name = None
    repo_name = None

    if "github.com" in remote_url:
        # Parse github.com/owner/repo or git@github.com:owner/repo
        if ":" in remote_url and "github.com:" in remote_url:
            parts = remote_url.split(":")[-1]
        else:
            parts = remote_url.split("github.com/")[-1]
        parts = parts.replace(".git", "").split("/")
        owner_name, repo_name = parts[0], parts[1]
    elif "/git/" in remote_url:
        # Handle local proxy format: http://proxy@host:port/git/owner/repo
        parts = remote_url.split("/git/")[-1].split("/")
        if len(parts) >= 2:
            owner_name, repo_name = parts[0], parts[1]

    if owner_name and repo_name:
        return GhApi(owner=owner_name, repo=repo_name), owner_name, repo_name
    else:
        print(f"Error: Could not parse GitHub repository from: {remote_url}")
        sys.exit(1)


def pr_list(args):
    """List pull requests."""
    api, owner, repo = get_api()
    prs = api.pulls.list(state=args.state)

    if not prs:
        print(f"No {args.state} pull requests")
        return

    print(f"{'#':<6} {'Title':<50} {'Branch':<30} {'State':<10}")
    print("-" * 100)
    for pr in prs:
        title = pr['title'][:47] + "..." if len(pr['title']) > 50 else pr['title']
        branch = pr['head']['ref'][:27] + "..." if len(pr['head']['ref']) > 30 else pr['head']['ref']
        print(f"#{pr['number']:<5} {title:<50} {branch:<30} {pr['state']:<10}")


def pr_view(args):
    """View a pull request."""
    api, owner, repo = get_api()
    pr = api.pulls.get(args.number)

    print(f"Title: {pr['title']}")
    print(f"Number: #{pr['number']}")
    print(f"State: {pr['state']}")
    print(f"Author: {pr['user']['login']}")
    print(f"Branch: {pr['head']['ref']} -> {pr['base']['ref']}")
    print(f"Created: {pr['created_at']}")
    print(f"URL: {pr['html_url']}")
    print()
    if pr.get('body'):
        print("Description:")
        print("-" * 40)
        print(pr['body'])


def pr_checks(args):
    """View PR check status."""
    api, owner, repo = get_api()
    pr = api.pulls.get(args.number)

    # Get check runs for the head SHA
    head_sha = pr['head']['sha']

    try:
        checks = api.checks.list_for_ref(head_sha)

        if not checks.get('check_runs'):
            print("No checks found")
            return

        print(f"Checks for PR #{args.number} ({head_sha[:7]}):")
        print("-" * 60)
        for check in checks['check_runs']:
            status = check.get('conclusion') or check.get('status')
            symbol = "✓" if status == "success" else "✗" if status == "failure" else "○"
            print(f"  {symbol} {check['name']}: {status}")
    except Exception as e:
        print(f"Could not fetch checks: {e}")


def issue_list(args):
    """List issues."""
    api, owner, repo = get_api()
    issues = api.issues.list_for_repo(state=args.state)

    # Filter out PRs (they show up as issues)
    issues = [i for i in issues if 'pull_request' not in i]

    if not issues:
        print(f"No {args.state} issues")
        return

    print(f"{'#':<6} {'Title':<60} {'State':<10}")
    print("-" * 80)
    for issue in issues:
        title = issue['title'][:57] + "..." if len(issue['title']) > 60 else issue['title']
        print(f"#{issue['number']:<5} {title:<60} {issue['state']:<10}")


def repo_info(args):
    """Show repository info."""
    api, owner, repo = get_api()
    info = api.repos.get()

    print(f"Repository: {info['full_name']}")
    print(f"Description: {info.get('description') or 'N/A'}")
    print(f"Default branch: {info['default_branch']}")
    print(f"Language: {info.get('language') or 'N/A'}")
    print(f"Stars: {info['stargazers_count']}")
    print(f"Forks: {info['forks_count']}")
    print(f"Open issues: {info['open_issues_count']}")
    print(f"URL: {info['html_url']}")


def main():
    parser = argparse.ArgumentParser(
        description="GitHub CLI wrapper using ghapi",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__
    )
    subparsers = parser.add_subparsers(dest="command", help="Command to run")

    # PR commands
    pr_parser = subparsers.add_parser("pr", help="Pull request commands")
    pr_subparsers = pr_parser.add_subparsers(dest="pr_command")

    pr_list_parser = pr_subparsers.add_parser("list", help="List PRs")
    pr_list_parser.add_argument("--state", default="open", choices=["open", "closed", "all"])
    pr_list_parser.set_defaults(func=pr_list)

    pr_view_parser = pr_subparsers.add_parser("view", help="View a PR")
    pr_view_parser.add_argument("number", type=int, help="PR number")
    pr_view_parser.set_defaults(func=pr_view)

    pr_checks_parser = pr_subparsers.add_parser("checks", help="View PR checks")
    pr_checks_parser.add_argument("number", type=int, help="PR number")
    pr_checks_parser.set_defaults(func=pr_checks)

    # Issue commands
    issue_parser = subparsers.add_parser("issue", help="Issue commands")
    issue_subparsers = issue_parser.add_subparsers(dest="issue_command")

    issue_list_parser = issue_subparsers.add_parser("list", help="List issues")
    issue_list_parser.add_argument("--state", default="open", choices=["open", "closed", "all"])
    issue_list_parser.set_defaults(func=issue_list)

    # Repo commands
    repo_parser = subparsers.add_parser("repo", help="Repository commands")
    repo_subparsers = repo_parser.add_subparsers(dest="repo_command")

    repo_info_parser = repo_subparsers.add_parser("info", help="Show repo info")
    repo_info_parser.set_defaults(func=repo_info)

    args = parser.parse_args()

    if not args.command:
        parser.print_help()
        sys.exit(1)

    if hasattr(args, 'func'):
        args.func(args)
    else:
        parser.print_help()
        sys.exit(1)


if __name__ == "__main__":
    main()
