# &lt;ClassName&gt; — agent class charter (template)

> `<ClassName>` is the (**&lt;Position&gt;**, **&lt;Lifecycle&gt;**, &lt;domain&gt;) binding from the [agent-role topology](https://github.com/usebessemer/research/blob/main/theory/agent-role-topology.md). Position is `Lead` or `Executor`; lifecycle is `Standing` (durable charter, scheduled, persists) or `Ephemeral` (per-task spec, opens a review artifact, terminates). Keep every per-deployment value in the §3 fields, never in the body.

## 1. What it is

One job, stated tightly. Name the position and lifecycle it binds, and why. State its bounded *execution* autonomy and that every decision stays with the human. State the trust wedge: full traceability, not "never wrong".

## 2. Out of scope

What this class explicitly does **not** do, especially the adjacent ambitions most likely to bloat it. Be concrete; the boundary is load-bearing.

## 3. Context (per-instance fields, the class/instance seam)

The state the class holds. **Every value is set per instance; none live in the class body** — this is what makes the class reusable. A table of `fieldName` | what it is. Instance values and client data live in the private client repo, never in this library.

## 4. Skills (methods)

The operations the class can perform, each marked **[BUILT]** or **[PLANNED]**. Each skill's autonomy line is in §5.

## 5. The autonomy / review boundary (the spine)

For every skill or decision, where the agent acts unattended versus where it escalates to the human. Anchor the line to **where errors carry real consequence** for this domain. Include:
- the **class default** (what it does autonomously, when confident);
- the enumerated **human-review-required** cases;
- a **fail-to-review safety floor** (on any failure, unreadable input, or unset config → escalate, never guess, never silently act; inert until configured);
- conservative **class defaults** an instance can override.

## 6. Contracts (interfaces, files/substrate, not live messages)

The review substrate, shaped by lifecycle:
- an **ephemeral** executor: the PR (code) or the draft file (content), surfaced whole;
- a **standing** executor: the periodic output package (Contract A) + the exceptions-queue / run-log / wake-notification (Contract B, which "replaces the PR");
- a **Lead**: its board + the upward channel.

Information moves as files on the shared substrate, never as live messages.

---

Instances bind the §3 fields per deployment; instance-specific config and client data live in the private client repo, never in this library.
