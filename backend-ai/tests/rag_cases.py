# Live RAG eval set, written against samples/. Append your own questions after ingesting real data.
RAG_CASES = [
    {"q": "what is my gym locker number", "kind": "answer", "contains": ["212"], "source": "home_and_life"},
    {"q": "where is the spare house key", "kind": "answer", "contains": ["blue drawer"], "source": "home_and_life"},
    {"q": "when does my laptop warranty end", "kind": "answer", "contains": ["november 2027"], "source": "home_and_life"},
    {"q": "who is allergic to peanuts", "kind": "answer", "contains": ["priya"], "source": "home_and_life"},
    {"q": "what did I decide about the wake word", "kind": "answer", "contains": ["push-to-talk"], "source": "ultron_notes"},
    {"q": "what is the peak VRAM limit", "kind": "answer", "contains": ["2900"], "source": "ultron_notes"},
    {"q": "what is the warranty on my toaster", "kind": "gap", "rewrites": True},   # hard negative: the laptop warranty is close
    {"q": "what is my passport number", "kind": "gap"},
    {"q": "what does Gita 2.47 say", "kind": "verse", "contains": ["2.47"], "collection": "scripture"},
    {"q": "what does chapter 2 verse 48 say", "kind": "verse", "contains": ["2.48"], "collection": "scripture"},
    {"q": "what does chapter 9 verse 22 say", "kind": "gap", "collection": "scripture"},
    {"q": "what does the Gita say about acting without attachment to results", "kind": "answer", "collection": "scripture", "source": "Gita"},
]
