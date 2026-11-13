---
name: web-research
description: Researches a question across many web sources and writes a structured, cited report. Use for open questions that need more than one search, like comparing options, understanding a topic, history, trends, or "find out everything about...".
---
# Web research

## Steps

1. **Plan.** Break the question into 3-6 sub-questions. If the scope isn't clear (how deep, which region, what time frame), make a sensible choice and say what you chose.
2. **Search each sub-question.** 1-3 `web_search` queries each, with `read` on the most promising. Vary the words. Search for opposing views too ("criticism", "problems", "vs").
3. **Read the best pages fully** with `fetch_page`: official docs, papers, reputable reporting, data. Skip SEO listicles and pages with no author or date.
4. **Keep track** of each fact with its source and date as you go. With the notes tool, save what took effort to find.
5. **Weigh it.** Where sources agree, say so. Where they don't, give each view and which evidence is stronger. Mark anything that only one source supports.
6. **Write the report** (format below).

## Format

```
# <Question>

## Summary
The answer in 3-5 sentences, with the confidence level.

## Findings
### <Sub-question 1>
Paragraphs or bullets, every fact cited inline ([Source](url)).
...

## Open questions
What the sources don't settle.

## Sources
1. [Title](url), publisher, date
```

Cite inline, never in a block at the end only. Don't pad: if a sub-question turned out not to matter, drop it.
