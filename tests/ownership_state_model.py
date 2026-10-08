"""Independent bounded ownership state explorer.

A state is a finite ownership ledger, not a copy of EOAI's resolver.
Enumerate every reachable state and transition to a configurable depth.
The real-HA replay tests use its generated transitions as witnesses.
"""
from collections import deque
from dataclasses import dataclass

USERS = ("alice", "bob")
DEVICES = ("kitchen", "hall")
MODES = ("ha", "user", "device")
OPERATIONS = ("start", "continue", "switch", "end", "reset")


@dataclass(frozen=True)
class State:
    current: str = "alice"
    # tuples of (conversation token, authenticated owner, device, guest)
    sessions: tuple[tuple[str, str, str, bool], ...] = ()
    active: str | None = None
    sequence: int = 0


def transitions(state: State, mode: str, guest: bool):
    """Yield legal actions and expected states without touching implementation."""
    if mode not in MODES:
        raise ValueError(mode)
    token = f"c{state.sequence}"
    for device in DEVICES:
        created = State(
            state.current,
            state.sessions + ((token, state.current, device, guest),),
            token,
            state.sequence + 1,
        )
        yield ("start", device, None), created
    yield ("switch", USERS[1] if state.current == USERS[0] else USERS[0], None), State(
        USERS[1] if state.current == USERS[0] else USERS[0],
        state.sessions, state.active, state.sequence,
    )
    if state.active is not None:
        yield ("end", None, state.active), State(
            state.current, state.sessions, None, state.sequence,
        )
        yield ("reset", None, state.active), State(
            state.current, state.sessions, None, state.sequence,
        )
    for token_id, owner, device, is_guest in state.sessions:
        # Caller-supplied IDs may only recover history for the same owner.
        resumed = owner == state.current and is_guest == guest
        next_active = token_id if resumed else None
        yield ("continue", device, token_id), State(
            state.current, state.sessions, next_active, state.sequence
        )


def explore(mode: str, guest: bool, depth: int = 3):
    """All reachable operation traces, with bounded depth and finite tokens."""
    if depth < 0 or depth > 5:
        raise ValueError("unsupported depth")
    queue = deque([(State(), ())])
    states = set()
    traces = []
    while queue:
        state, trace = queue.popleft()
        states.add(state)
        traces.append((trace, state))
        if len(trace) >= depth:
            continue
        for action, successor in transitions(state, mode, guest):
            queue.append((successor, trace + (action,)))
    return traces, states


def replay_witnesses(depth: int = 3):
    """Small exhaustive traces where one user's ID is attempted by the other."""
    for mode in MODES:
        for guest in (False, True):
            traces, _ = explore(mode, guest, depth)
            for trace, state in traces:
                if (
                    len(trace) == 3
                    and trace[0][0] == "start"
                    and trace[1][0] == "switch"
                    and trace[2][0] == "continue"
                    and trace[2][2] == "c0"
                ):
                    yield mode, guest, trace, state
