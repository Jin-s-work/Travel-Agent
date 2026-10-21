"""Trip agent (v6 stage 3): a bounded tool-calling loop over existing services.

The agent can read, build previews and write drafts. It cannot apply, undo,
delete, send or fetch arbitrary URLs — those tools do not exist in the
registry, and a test pins their absence. User and trip scope come from the
server actor, never from tool arguments.
"""
