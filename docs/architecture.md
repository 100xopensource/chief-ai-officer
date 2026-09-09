# How it fits together

Four diagrams: what ships, what a person does with it, how data moves, and the
one rule the whole thing exists to enforce.

---

## 1. What ships

Everything below installs from one repository. The pipeline is a Python package
with a `caio` command; the skills are what make it usable from Claude.

```mermaid
graph TD
    REPO["<b>100xopensource/chief-ai-officer</b><br/>Apache-2.0"]

    REPO --> MKT[".claude-plugin/marketplace.json<br/><i>makes the repo installable</i>"]
    REPO --> PLUGIN["plugins/100x-chief-ai-officer/"]
    REPO --> TOOLS["tools/<br/>scrubber · manifest checker<br/><i>publication gates</i>"]
    REPO --> DOCS["docs/mock-reports/<br/><i>3 example reports,<br/>fictional company</i>"]

    PLUGIN --> MANIFEST[".claude-plugin/plugin.json"]
    PLUGIN --> SKILLS["skills/"]
    PLUGIN --> REFS["references/<br/>data contract · pipeline spine<br/>privacy rules"]
    PLUGIN --> PIPE["pipeline/<br/><i>the Python package,<br/>installed as</i> <code>caio</code>"]

    SKILLS --> S1["caio-setup<br/><i>first run, what is this</i>"]
    SKILLS --> S2["caio-weekly-reports<br/><i>produce the reports</i>"]
    SKILLS --> S3["caio-review-candidates<br/><i>judge what was flagged</i>"]
    SKILLS --> S4["caio-deliver-report<br/><i>package one to send</i>"]
    SKILLS --> S5["caio-explain-report<br/><i>explain a number</i>"]

    PIPE --> P1["lake/<br/><i>raw store, partitioned writes</i>"]
    PIPE --> P2["fetch/<br/><i>the only network code</i>"]
    PIPE --> P3["detectors/<br/><i>9 pattern families</i>"]
    PIPE --> P4["judges/<br/><i>demo · worksheet · anthropic</i>"]
    PIPE --> P5["stages/<br/><i>x0 · x2 · x3 · x4 · x5</i>"]
    PIPE --> P6["render/<br/><i>compose · build · validate · deliver</i>"]
    PIPE --> P7["demo/<br/><i>the invented company</i>"]

    classDef gate fill:#fdf6e7,stroke:#c05e12,color:#4a3408
    classDef code fill:#eef6f7,stroke:#0d7f95,color:#083c46
    class TOOLS gate
    class PIPE,P1,P2,P3,P4,P5,P6,P7 code
```

---

## 2. What a person does

Two paths in. Nobody needs credentials to see it work.

```mermaid
flowchart TD
    START(["Someone installs the plugin"]) --> ASK{"Do they have<br/>API keys yet?"}

    ASK -->|"No — most people"| DEMO["<b>caio demo</b><br/>invents a 112-seat company"]
    ASK -->|Yes| PULL["<b>caio fetch</b><br/>analytics · directory · compliance"]

    PULL --> CONSENT{"Conversation content?"}
    CONSENT -->|Yes| RECORD["Consent record required<br/><i>names an accountable person</i>"]
    CONSENT -->|"Cost and usage only"| LAKE
    RECORD --> LAKE

    DEMO --> LAKE[("<b>the lake</b><br/>local folder<br/><i>nothing is uploaded</i>")]

    LAKE --> CHECK["<b>caio check</b><br/>what can this data answer?"]
    CHECK --> RUN["<b>caio all</b><br/>scan → metrics → findings → render"]
    RUN -.->|"add --read"| READ["<b>caio judge</b> + <b>caio verify</b><br/><i>turns matches into findings</i>"]
    READ -.-> RUN

    RUN --> GATES{"12 publication gates"}
    GATES -->|"any fail"| STOP["<b>Not shared.</b><br/>No override flag."]
    GATES -->|"all pass"| HTML["3 HTML files<br/><i>open in any browser</i>"]

    HTML --> SEND["<b>caio deliver</b><br/>+ cover note, manifest, checksums"]

    classDef stop fill:#fdecea,stroke:#b3261e,color:#7a1a14
    classDef good fill:#e9f5ef,stroke:#1c7a4d,color:#0f4a2e
    class STOP stop
    class HTML,SEND good
```

---

## 3. How data moves through the stages

The left column never talks to a network. The right column is the only part
that does.

```mermaid
flowchart LR
    subgraph NET ["touches a network"]
        API["Claude Enterprise APIs<br/><i>analytics · directory · compliance</i>"]
    end

    subgraph X1 ["x1 · pull"]
        FETCH["fetch/"]
        RAW[("raw store<br/><i>full responses,<br/>written before parsing</i>")]
    end

    subgraph LOCAL ["everything else runs locally"]
        PARSE["parse_raw"]
        TABLES[("dim_chat · fact_message<br/>fact_block · fact_attachment<br/>analytics_*")]
        X0["<b>x0</b> gap check<br/><i>what can be answered,<br/>which week</i>"]
        X2S["<b>x2</b> scan<br/><i>9 detector families</i>"]
        X2M["<b>x2</b> metrics<br/><i>waste · value · exposure</i>"]
        X3["<b>x3</b> classify<br/><i>read and judge</i>"]
        X4["<b>x4</b> verify<br/><i>independent second read</i>"]
        X5["<b>x5</b> lock<br/><i>findings ledger</i>"]
        COMPOSE["compose → build → validate"]
        OUT["3 self-rendering<br/>HTML reports"]
    end

    API --> FETCH --> RAW --> PARSE --> TABLES
    TABLES --> X0 --> X2M
    TABLES --> X2S
    X2S -->|candidates| X3
    RAW -.->|"passage text,<br/>fetched at read time,<br/>never persisted"| X3
    RAW -.-> X4
    X3 -->|verdicts| X4
    X4 -->|confirmed only| X5
    X2M --> X5 --> COMPOSE --> OUT

    classDef net fill:#fdf6e7,stroke:#c05e12,color:#4a3408
    classDef store fill:#f2efe6,stroke:#5c6b70,color:#0e1b20
    class API net
    class RAW,TABLES store
```

---

## 4. The one rule

A pattern match is not a finding. This is what the two reading passes are for,
and it is the difference between a report worth sending and a number that
overstates reality by more than double.

```mermaid
flowchart TD
    TEXT["A passage in a conversation"] --> SCAN{"x2 · does it match<br/>a pattern?"}
    SCAN -->|No| IGNORED["Never looked at again"]
    SCAN -->|Yes| CAND["<b>candidate</b><br/><i>nothing has read it</i>"]

    CAND --> READ{"x3 · read it.<br/>Is it real?"}
    READ -->|"false alarm"| CLEARED["<b>cleared</b><br/><i>~56% end here</i>"]
    READ -->|unsure| STILL["<b>still a candidate</b>"]
    READ -->|real| CLAIM["claimed real<br/><i>by one reader</i>"]

    CLAIM --> V{"x4 · read it again,<br/>never shown the<br/>first answer.<br/>Still real?"}
    V -->|"agrees"| CONF["<b>confirmed</b><br/><i>published as a finding</i>"]
    V -->|"disagrees"| DISP["<b>disputed</b><br/><i>published as neither —<br/>a person settles it</i>"]
    V -->|"not read yet"| UNV["<b>unverified</b><br/><i>stays a candidate</i>"]

    CONF --> REPORT["The Exposure Report"]
    DISP --> REPORT
    STILL -.->|"counted as a floor,<br/>never as a finding"| REPORT
    UNV -.-> REPORT

    classDef good fill:#e9f5ef,stroke:#1c7a4d,color:#0f4a2e
    classDef warn fill:#fdf6e7,stroke:#c05e12,color:#4a3408
    classDef dim fill:#f2efe6,stroke:#5c6b70,color:#0e1b20
    class CONF good
    class DISP warn
    class CLEARED,IGNORED,STILL,UNV dim
```

**Who did the reading matters, and the report says so.** Three judges are
available: an offline stand-in for the demo and CI, a worksheet a person fills
in, and a model. A report whose verdicts came from the stand-in opens by saying
what share of them nobody actually read.

---

## Against OST-74

The shipping framework asked for three things:

| Asked for | State |
|---|---|
| Plugin with skills to create reports | **Done** — 5 skills, plugin and marketplace manifests, gated in CI |
| HTML reports | **Done** — 3 reports, self-rendering, 12 publication gates each |
| Claude artifact that refreshes with new data | **Not built** — reports are static HTML files today |

The third one is a real gap. A report is one file that renders itself from a
data record embedded inside it, which is what makes it safe to email and
impossible to disagree with itself — but it also means the numbers are frozen at
build time. A refreshing artifact would need somewhere live to read from, which
is a different shape from "a file you can send", and worth deciding on
deliberately rather than by default.
