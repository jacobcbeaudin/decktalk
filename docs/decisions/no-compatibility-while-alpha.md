# No compatibility while DeckTalk is alpha

## Decision

DeckTalk keeps no backwards or forwards compatibility. A renamed command, key or Python name simply
stops existing. There is no migration command, no alias, no deprecation period and no shim. A
project written against an older release is recreated or hand-edited.

## Why

One person maintains DeckTalk and it has no users who would be stranded. Every alias kept alive is a
second name a reader has to learn, a second path a test has to cover, and a sentence in the
documentation that describes something nobody should write. The cost of carrying them is paid on
every page and in every review, and the cost of not carrying them is paid once, by the author, in an
afternoon.

The rule is stated so that it can be relied on. Because nothing is kept alive, the tree holds exactly
one name for each thing, and a reader who meets a name can trust that it is the current one.

## What it rules out

- No `migrate` command, and no exit code that tells a user to run one.
- No "was formerly" comment, no interim name, and no retired vocabulary anywhere in the tree. The
  one place an old name appears is the breaking-change entry in the changelog.
- A build directory from an older release is deleted rather than upgraded.

## What would change it

The first release that promises stability. From then on a rename is a major version and an alias is
a real obligation, and this note is replaced by a compatibility policy.
