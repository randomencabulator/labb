---
name: page-summary
description: Reads a web page, article, paper or PDF from a link and summarizes it with the key points, numbers and caveats. Use when someone shares a URL and asks what it says, for a TL;DR, or whether it's worth reading.
---
# Page summary

## Steps

1. **Read the whole page** with `fetch_page`. If it comes in parts, read on until the end (or until the rest is comments and links).
2. **Note what kind of page it is**: news, opinion, research paper, product page, documentation, press release. Who wrote it and when.
3. **Summarize** (format below). Use the page's own facts and numbers; don't add outside facts unless asked, and if you do, mark them.
4. If the page couldn't be read (paywall, login, error), say so and offer to search for other coverage of the same thing.

## Format

```
**<Title>**, <author or site>, <date> · <kind of page>

**TL;DR:** one or two sentences.

**Key points**
- ...
- (3-7 bullets, with the important numbers)

**Worth knowing:** caveats, what it doesn't say, who funds or benefits, if it matters.
```

For a research paper, add the method and sample size in the key points. For a long how-to, list the steps briefly instead of key points.
