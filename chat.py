#!/usr/bin/env python3
"""Generic chat runner: loads ANY ForgeAI agent definition and chats with it.

    python chat.py runs/hr_policy/agent.json
    python chat.py runs/hr_policy/agent.json --ask "How many days of annual leave do I get?"
    python chat.py runs/hr_policy/agent.json --debug      # show retrieval, tools and citations
"""
import argparse
import sys

from forgeai.runtime import Agent


def show(resp, debug):
    print(f"\n{resp.answer}")
    if resp.citations:
        print(f"  sources: {', '.join(resp.citations)}")
    if debug:
        print(f"  [refused={resp.refused} gate={resp.gate or '-'}]")
        for r in resp.retrieved:
            print(f"  [retrieved] {r['id']} rel={r['relevance']}")
        for t in resp.tool_calls:
            print(f"  [tool] {t['tool']}({t['args']}) -> {t['result'][:200]}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("definition")
    ap.add_argument("--ask", help="ask one question and exit")
    ap.add_argument("--debug", action="store_true")
    a = ap.parse_args()
    agent = Agent.load(a.definition)
    if a.ask:
        show(agent.ask(a.ask), a.debug)
        return
    print(f"{agent.d.name} - {agent.d.description}")
    print(f"tools: {agent.d.tools or 'none'} | type 'quit' to exit\n")
    while True:
        try:
            q = input("you> ").strip()
        except (EOFError, KeyboardInterrupt):
            print()
            return
        if q.lower() in {"quit", "exit", "q"}:
            return
        if q:
            show(agent.ask(q), a.debug)
            print()


if __name__ == "__main__":
    sys.exit(main())
