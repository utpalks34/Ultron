"""Episodic memory: one row per run (route, task, outcome). Retrieval by meaning ("what did
you do yesterday") arrives later; the embedding column stays NULL for now."""
OUTCOMES = ("ok", "failed", "cancelled")


async def log_episode(pool, run_id: str, agent: str, summary: str, outcome: str) -> None:
    if outcome not in OUTCOMES:
        outcome = "failed"
    await pool.execute(
        "INSERT INTO episodes (run_id, agent, summary, outcome) VALUES ($1,$2,$3,$4)",
        run_id, agent, summary[:1000], outcome)


async def recent(pool, n: int = 20) -> list[dict]:
    rows = await pool.fetch(
        "SELECT created_at, run_id, agent, outcome, summary FROM episodes "
        "ORDER BY created_at DESC LIMIT $1", n)
    return [dict(r) for r in rows]
