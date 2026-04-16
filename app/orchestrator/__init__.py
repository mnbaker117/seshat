"""
Orchestration layer — the glue that ties IRC, the filter, the rate
limiter, the grab path, and the qBit submission together into one
coherent pipeline.

`dispatch.handle_announce` is the function the IRC listener's
`on_announce` callback should point at. `dispatch.inject_grab` is
the function the manual-inject HTTP endpoint calls.

Both funnel through the same set of dependency-injected primitives
so the tests can swap in fakes for the cookie, the grab fetcher,
and the qBit client without monkey-patching production modules.
"""
