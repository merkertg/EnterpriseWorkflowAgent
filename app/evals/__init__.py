"""Stage 09 evaluation suite: named cases with explicit expected outcomes.

Each case runs the real services against PostgreSQL and, for delivery, a real
sandbox receiver on loopback. Providers are deterministic substitutes, so the
suite says how the code handles hostile or broken inputs, not how a real
model behaves.
"""
