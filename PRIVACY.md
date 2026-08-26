# Privacy

Written for whoever has to answer "what does this thing read, and where does it
go" — not only for engineers.

## Where the data goes

**Nowhere.** It is read from your organisation's own Claude account into a
folder on the machine you run this on, and it stays there. Nothing is sent to
100x. Reports are files on disk.

## What it reads

**Cost and usage** — what was spent, by product, by model, by person, by day.
Counts of activity. No conversation content.

**Directory** — who holds a seat, when their account was created, which groups
they are in. This is what makes it possible to say a silent seat was silent
rather than new.

**Conversation content** — what people typed, what was sent into tools, what
tools handed back, and the names of attached files. This is only read for the
Exposure Report, only from products the compliance export covers, and only
after a consent record exists.

## Consent

Before any conversation content is pulled, a record must exist in the lake
naming an accountable person. It is checked on every run including scheduled
ones.

This is not a formality. Reading a colleague's conversations is a real thing to
do, and in six months somebody will ask who decided to. The record is the
answer.

## What ends up in a report

By default, a report contains **counts, categories and dates**. It does not
contain names, email addresses, filenames, or anything anyone wrote.

A **named edition** exists for a reviewer who has to act on specific cases. It
must be asked for explicitly, it is stamped as named in its own manifest and
cover note so whoever finds the file later can tell what they are holding, and
every other check still applies to it.

## The small-group rule

Any group of fewer than five people is described as "fewer than five", never as
a number.

In a company of a hundred people, "3 people in the commercial team last week"
is close enough to a name that anyone in the company can finish the sentence.
The rule is enforced in code, and a report that gets around it fails its checks
and is not published.

## Matched values are never stored

When the scan finds something that looks like a credential or an identity
number, it records **where** it was and **what kind of thing** matched — and a
hash of the value, never the value.

A scan that stored what it matched would be a second copy of exactly the
material it exists to protect, sitting in a folder with weaker handling than the
original.

## Credentials are never tested

If the scan finds something that looks like a key, it does not try it. Whether
it is live is judged from context — where it appeared, when, what it was
attached to.

## What it deliberately cannot tell you

It reads chat conversations. Other products are not in the export it is allowed
to read, and it does not guess about them. Some tool results are cut off at the
source and nothing past the cut can be examined by anything.

So every count is a floor: at least this many, never a total it cannot support.
Every report says this on its own face, because a reader who discovers a blind
spot on their own stops trusting everything else on the page.

## Deleting it

The lake is a folder. Delete the folder.

```bash
rm -rf data/
```

The raw conversation store can be cleared on its own while keeping the derived
tables:

```python
from pipeline.lake import raw
raw.purge(root)
```

## Version control

`.gitignore` excludes the lake, the raw store, any rendered report built from
real data, and credential files. Lakes are excluded by the files they are made
of, not only by folder name, so a lake put anywhere is still un-committable. A
check in CI looks for those same files and fails the build if any are tracked,
because an ignore rule is a convention and this is a check.
