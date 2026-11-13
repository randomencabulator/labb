---
name: fact-check
description: Checks whether a claim, quote, statistic, image caption or viral post is true, using primary sources, and gives a clear verdict with the evidence. Use when someone asks "is this true?", "did X really say...", or wants a claim verified.
---
# Fact-check

## Steps

1. **State the claim exactly**, in one sentence. If the message has several claims, list them and check each one.
2. **Find where it came from.** Search for the exact words in quotes, then for the key names and numbers. Try to find the first time it appeared.
3. **Go to the primary source**: the full speech or transcript, the dataset, the study, the law, the original photo. Read it with `fetch_page`. A quote is checked against the full context, not a clip of it.
4. **Look for independent confirmation** from at least two sources that didn't copy each other. Check what dedicated fact-checkers already found.
5. **Check the details** that claims often get wrong: dates, units, percent vs. percentage points, who said it, where a photo was really taken, whether a number is a total or per person.
6. **Give the verdict.**

## Verdicts
- **True**: the evidence supports it as stated.
- **Mostly true**: right in substance, a detail is off.
- **Missing context**: technically accurate but misleading as used.
- **Mostly false**: a grain of truth, wrong in substance.
- **False**: the evidence contradicts it.
- **Unproven**: not enough reliable evidence either way. Say what would settle it.

## Format

```
**Claim:** <the claim>
**Verdict:** <verdict>

<2-4 sentences: what the evidence shows.>

**Evidence**
- <finding> ([source](url), date)

**Where it came from:** <first appearance, if found>
```

Never guess. If you couldn't reach the primary source, say so, and lower your confidence.
