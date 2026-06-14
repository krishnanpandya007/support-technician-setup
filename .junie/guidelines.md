# Support Technician

This project ships Support Technician: a staged methodology for turning a codebase
into a read-only customer-support agent (knowledge base, read-only tools + access SQL,
diagnostic runbooks, persona, config scaffolding). The full per-stage instructions live in
AGENTS.md at the repo root. When asked to build or set up a support agent - or any of its
stages - open AGENTS.md and follow the matching section. The safety contract is
non-negotiable: the generated support agent only reads data, and the only action it may
take is escalating a proposed change to a human.
