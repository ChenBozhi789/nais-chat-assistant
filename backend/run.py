#!/usr/bin/env python3
"""Run questions.json beside this file against the chat API and flag failures.

Config (env):  CHAT_URL  default http://localhost:8000/chat
Request body:  {"message": "<question>"}
Response:      {"answer": "...", "sources": ["url", ...] | [{"url": "..."}]}
Adjust ask() if your API differs.

Usage: python backend/run.py [--only ID ...] [--type fact|unknown|refuse] [-v]
"""
import argparse, json, os, sys, urllib.request
from pathlib import Path

URL = os.getenv("CHAT_URL", "http://localhost:8000/chat")
REFUSAL = ["don't know", "do not know", "not sure", "can't help", "cannot help", "can't share",
           "unable to", "isn't listed", "not listed", "don't have", "couldn't find", "could not find",
           "no information", "outside", "only help", "can only", "not able to", "can't provide"]

def ask(q):
    req = urllib.request.Request(URL, json.dumps({"message": q}).encode(),
                                 {"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=60) as r:
        d = json.load(r)
    srcs = [s["url"] if isinstance(s, dict) else s for s in d.get("sources", [])]
    return d.get("answer", ""), srcs

norm = lambda u: u.split("#")[0].rstrip("/").lower()

def check(t, answer, sources):
    a = answer.lower()
    bad = [m for m in t.get("must_not", []) if m.lower() in a]
    if bad:
        return "ANSWER", f"contains forbidden text {bad}"
    if t["type"] in ("unknown", "refuse"):
        if not any(p in a for p in REFUSAL):
            return "PROMPT", "expected refusal / 'I don't know' (review answer)"
        return None, ""
    if not {norm(u) for u in t["expected_urls"]} & {norm(u) for u in sources}:
        return "RETRIEVAL", f"expected source not in {sources}"
    miss = [x for x in t.get("expected_facts", []) if x.lower() not in a]
    if miss:
        return "ANSWER", f"missing facts {miss} (source was retrieved)"
    anyl = t.get("expected_any")
    if anyl and not any(x.lower() in a for x in anyl):
        return "ANSWER", f"none of {anyl} present (source was retrieved)"
    return None, ""

def main():
    p = argparse.ArgumentParser()
    p.add_argument("--only", nargs="*"); p.add_argument("--type"); p.add_argument("-v", action="store_true")
    args = p.parse_args()
    tests = json.loads((Path(__file__).parent / "questions.json").read_text())
    if args.only: tests = [t for t in tests if t["id"] in args.only]
    if args.type: tests = [t for t in tests if t["type"] == args.type]
    results, fails = [], {}
    for t in tests:
        try:
            ans, srcs = ask(t["q"])
        except Exception as e:
            ans, srcs = f"[request failed: {e}]", []
        kind, why = check(t, ans, srcs)
        results.append({**t, "answer": ans, "sources": srcs, "fail": kind, "why": why})
        print(f"\n[{'FAIL:'+kind if kind else 'PASS'}] {t['id']} ({t['type']}) — {t['q']}")
        if kind or args.v:
            print(f"  answer:  {ans[:400]}")
            print(f"  sources: {srcs}")
            if why: print(f"  why:     {why}")
            if t.get("note"): print(f"  note:    {t['note']}")
        if kind: fails[kind] = fails.get(kind, 0) + 1
    Path(__file__).with_name("results.json").write_text(json.dumps(results, indent=2, ensure_ascii=False))
    n = len(results)
    print(f"\n=== {n - sum(fails.values())}/{n} passed ===")
    for k, why in [("RETRIEVAL", "fix first: chunking, junk, embeddings/top-k"),
                   ("ANSWER", "source found but answer wrong: context formatting / prompt"),
                   ("PROMPT", "refusal behaviour: system prompt, then model")]:
        if k in fails: print(f"  {k}: {fails[k]}  -> {why}")
    sys.exit(1 if fails else 0)

if __name__ == "__main__":
    main()
