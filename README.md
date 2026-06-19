# agent-classes

The Bessemer vertical agent-class library: reliable, bounded-autonomy AI agents that fill real roles in small organizations.

Each class is a **charter**, a durable specification a deployment runs against, not a piece of code. A class is a (position, lifecycle, domain) binding from the [agent-role topology](https://github.com/usebessemer/research/blob/main/theory/agent-role-topology.md): typed by the topology, scaffolded by [icm-kit](https://github.com/usebessemer/icm-kit). The classes are the third layer of the Bessemer stack: **research** (the methodology) → **tooling** (the factory) → **classes** (the product).

## The classes

| Class | Position × Lifecycle | Vertical | Status |
|---|---|---|---|
| [Bookkeeper](bookkeeper.md) | Executor × Standing | vertical-agnostic (any small business) | charter v0.1 |
| Legal Admin | Executor × Standing | small law firms | planned |

The library carries both kinds: **vertical-agnostic** classes (the Bookkeeper's books-and-tax shape fits any small business) and **vertical-specific** ones (a Legal Admin's obligations are particular to legal practice).

## The class/instance seam

A class charter is **public and reusable**: it names per-instance *fields* but holds no values. A deployment binds those fields to a specific organization, its chart of accounts, its jurisdiction, its systems of record, and that instance config plus any client data lives in the **private client repo, never here**. The charter is the reusable IP; the instance is bespoke.

## Adding a class

Start from [`class-template.md`](class-template.md), the shape every class follows, derived from the topology's concrete-binding form. The spine of every executor class is its **autonomy / review boundary**: where the agent acts unattended versus where it escalates to the human, anchored to where errors carry real consequence, with a fail-safe-never-silent floor.

## License

MIT.
