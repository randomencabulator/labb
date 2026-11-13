# labb: the full guide

Everything about labb in detail: settings, tools, the terminal, the web chat, the server, documents, memories and the API. For what labb is and how to get it running, start with the [README](../README.md).

```
START → recall → compact → llm_call → END
                    ↑         ↓
                    └─ tool_node
```

`recall` finds the memories the new message is about, `compact` keeps the conversation within the model's context window, `llm_call` decides whether to call tools, and `tool_node` runs them. A tool's error goes back to the model as its result, so a run never crashes on one.

## Running from a checkout

Needs Python 3.14 and [uv](https://docs.astral.sh/uv/); the web chat also needs Node.js 22+. (To run the server with Docker, see the [README](../README.md#run-the-server-with-docker).)

```bash
uv tool install -e .     # the labb command; -e: code changes apply right away
cd ~/some/project && labb
# or without a checkout: uv tool install git+https://github.com/randomencabulator/labb

(cd frontend && npm install && npm run web:build)
uv run python -m labb.server.app  # web chat on http://localhost:8000
```

## Settings

Settings go in `.env` next to the code (in `~/.labb/.env` for a copy installed without `-e`); [`.env.example`](../.env.example) lists them:

| Variable | Default | |
| --- | --- | --- |
| `MODEL` | `ollama:gemma4:e4b` | `<provider>:<model>`, e.g. `openai:gpt-5.5`, `anthropic:claude-sonnet-5-5` |
| `OPENAI_API_KEY`, `ANTHROPIC_API_KEY` | | For those providers. With a key, the web chat's picker and `/model` offer the provider's chat models (asked of its API, kept for an hour). On the server they start turned off, key or not: an admin turns them on in Settings → Models (or `MODEL` is one of theirs) |
| `OLLAMA_API_KEY` | | For Ollama Cloud (the provider `ollama-cloud`, e.g. `ollama-cloud:gpt-oss:120b`): its models run on ollama.com with this key, or without one through the Ollama server once it's signed in (`ollama signin`). On the server it starts turned off; turned on, the pickers offer all of Ollama's cloud models that can use tools |
| `MODELS` | | Comma-separated `<provider>:<model>`s to offer instead of all of them, e.g. `openai:gpt-5.5,anthropic:claude-sonnet-5-5` (like `models` in the settings, which also still work). On the server, Settings → Models starts from it |
| `OLLAMA_HOST` | `http://localhost:11434` | Ollama server (pull the model there first) |
| `OLLAMA_NUM_CTX` | `32768` | Tokens Ollama keeps in memory for a model (its `num_ctx`); its own default is too small for articles. In the terminal `"ollamaNumCtx"`, `--num-ctx` and `/num-ctx` change it; on the server, Settings → Models → Ollama context size |
| `MODEL_STALL_SECONDS` | `600` | How long an Ollama model call may send nothing before it's given up and tried again (OpenAI's and Anthropic's clients have their own 10 minutes). Ollama is quiet while it loads a model and reads a long prompt, which takes minutes on a CPU |
| `OLLAMA_CLOUD_CATALOG` | `https://ollama.com/api/tags` | Where Ollama Cloud's list of models comes from; `off` lists none |
| `CONTEXT_WINDOW` | the model's | Token budget to compact to; lower it to compact earlier (on the server, Settings → Models → Context window overrides it) |
| `CRAWLER_URL` | (none; `http://crawler:3000` in Docker, the server's `/crawler` on PCs) | The [labb-web-search-crawler](../crawler/README.md) the web tools search and read pages through. Without one, labb searches DuckDuckGo itself and reads pages as plain text (no JavaScript) |
| `CRAWLER_TOKEN`, `CRAWLER_TOKEN_FILE` | (made by the crawler in Docker) | The crawler's token, or the file it's kept in (the server sends it; PCs use their API key) |
| `LABB_READ_PRIVATE` | | `1` to let page reads without a crawler reach private and local addresses (an intranet); otherwise they're refused, like the crawler does |
| `BROWSER_URL` | (none; `http://browser:3100` in Docker, the server's `/browser-api` on PCs) | The [labb-browser](../browser/README.md) service the [browser](#the-browser) tools use. On a PC where it's installed (`labb --install-browser`), the browser runs there instead |
| `BROWSER_TOKEN`, `BROWSER_TOKEN_FILE` | | The service's token, or the file it's kept in (the server sends it; PCs use their API key) |
| `LABB_BROWSER_HEADLESS` | | Terminal: `1` for no browser window, even with a display |
| `LABB_DATABASE_URL` | (none; Postgres in Docker) | Server: a Postgres database (`postgresql://user:password@host:5432/db`) for chats, accounts and the rest, instead of the `CHAT_DB` file. See [Database](#database) |
| `POSTGRES_PASSWORD` | `labb` | Docker: the password of the Postgres that `docker compose` runs; set it before the first start (`./setup.sh` puts a random one in `.env`). The server warns at start while it's the default |
| `CHAT_DB` | `chats.sqlite` next to the code | Where conversations are saved without `LABB_DATABASE_URL` (always, for labb on a PC) |
| `EMBED_MODEL` | `embeddinggemma` | Server: the Ollama model that embeds [documents](#documents) and memories (`ollama pull` it there first), until an admin picks one in Settings → Models → Search |
| `PORT` | `8000` | The web server's port |
| `LABB_BIND` | `0.0.0.0` | Docker: the address port 8000 is published on; `127.0.0.1` keeps plain http to the machine itself (with [HTTPS](#https)) |
| `FORWARDED_ALLOW_IPS` | `127.0.0.1,::1` | Server: whose `X-Forwarded-Proto`/`-For` it believes; `*` behind Caddy (see [HTTPS](#https)) |
| `MAX_REQUEST_MB` | `50` | Server: the largest request it accepts, refused before it's read (uploads come as base64, so a 20 MB document is about 27 MB) |
| `SANDBOX_URL` | (none; `http://sandbox:4000` in Docker) | Server: the [sandbox](../sandbox/README.md) that runs Studio's Python steps; without it, Python steps can't run |
| `SANDBOX_TOKEN` | (made by the sandbox in Docker) | A token the server and the sandbox share. Without it, `docker compose` has the sandbox make one on its first start, in a volume the server reads it from (`SANDBOX_TOKEN_FILE`) |
| `SITES_PORT`, `SITES_BIND` | `8300`, `0.0.0.0` | Docker: where the [sites](#sites) are published; `127.0.0.1` keeps plain http to the machine (with [HTTPS](#https)) |
| `SITES_TOKEN` | (made by labb-sites in Docker) | The token uploading [sites](#sites) takes |
| `SITES_URL` | (none; `http://sites-deploy:4200` in Docker) | Server: labb-sites' upload service, where [web pages](#web-pages) are published; without it (or without `SPAWNER_URL`), there are none |
| `SITES_PUBLIC_URL` | (this server's host name on `SITES_PORT`) | Server: where people open [web pages](#web-pages), e.g. `https://pages.example.com` |
| `ARTIFACT_MAX` | `50` | Docker: [web pages](#web-pages) per person |
| `WORKFLOW_WORKERS` | `2` | Server: how many Studio runs go at once |
| `LABB_SECRET_KEY` | (a `secret.key` file next to the data) | Server: the key Studio's secrets, providers' keys and [integrations](#integrations)' tokens are encrypted with; keep it with your backups |
| `LABB_PUBLIC_URL` | (the address the browser used) | Server: its address as people reach it, e.g. `https://labb.example.com`; Google and Microsoft send people back there after they [connect](#integrations) |
| `GOOGLE_CLIENT_ID`, `GOOGLE_CLIENT_SECRET` (and `MICROSOFT_`, `GITHUB_`, `NOTION_`) | | Server: an [integration](#integrations)'s OAuth client, if not set in Settings → Integrations |
| `MICROSOFT_TENANT` | `common` | Server: whose Microsoft accounts can connect: `common` (any), `organizations`, `consumers`, or a tenant id |
| `LABB_SERVER`, `LABB_API_KEY` | | Set by `install.sh` and `labb --login` (see [Server](#server)) |

To reach Ollama from another machine, set `OLLAMA_HOST=0.0.0.0` for its service on its host and open port 11434. This writes it as a drop-in (what `sudo systemctl edit ollama` would, without the editor), restarts Ollama and shows the setting:

```bash
sudo mkdir -p /etc/systemd/system/ollama.service.d && printf '[Service]\nEnvironment="OLLAMA_HOST=0.0.0.0"\n' | sudo tee /etc/systemd/system/ollama.service.d/labb.conf >/dev/null && sudo systemctl daemon-reload && sudo systemctl restart ollama && systemctl show ollama -p Environment
```

To undo it: `sudo rm /etc/systemd/system/ollama.service.d/labb.conf && sudo systemctl daemon-reload && sudo systemctl restart ollama`.

## Tools

| Toolset | Tools | Where |
| --- | --- | --- |
| Web (`src/labb/tools/web.py`) | `web_search` (DuckDuckGo, no key; `read` reads the top results too), `fetch_page` (a page or an RSS/Atom feed as Markdown, in parts with `start`), through labb-web-search-crawler | Both |
| Browser (`src/labb/tools/browser.py`) | `browser_open`, `browser_look`, `browser_click`, `browser_fill`, `browser_press`, `browser_type`, `browser_scroll`, `browser_hover`, `browser_drag`, `browser_tabs`, `browser_wait`, `browser_upload`, `browser_files`, `browser_handover`; see [The browser](#the-browser) | Both, when it's turned on |
| Memory (`src/labb/tools/memory.py`) | `remember`, `forget`: lasting facts and preferences, see [Memories](#memories) | Both |
| Documents (`src/labb/tools/documents.py`) | `search_documents`: your [documents](#documents) on the server, by meaning; `save_document`: saves text (a summary, notes, a report) as a new document when you ask, or replaces the text of one you name | Both, with a server |
| Google Calendar (`src/labb/tools/google_calendar.py`) | `list_calendars`, `list_events`, `create_event`, `update_event`, `delete_event`, in the calendar's time zone; see [Integrations](#integrations) | Web, once you've connected Google |
| Gmail (`src/labb/tools/gmail.py`) | `search_mail` (Gmail's search words), `read_mail`, `draft_mail` (a draft for you to send: it can't send mail) | Web, once you've connected Google |
| Outlook calendar (`src/labb/tools/outlook_calendar.py`) | `list_outlook_events`, `create_outlook_event`, `update_outlook_event`, `delete_outlook_event`, in the mailbox's time zone | Web, once you've connected Microsoft 365 |
| Outlook mail (`src/labb/tools/outlook_mail.py`) | `search_outlook_mail`, `read_outlook_mail`, `draft_outlook_mail` (a draft: it can't send mail) | Web, once you've connected Microsoft 365 |
| GitHub (`src/labb/tools/github.py`) | `search_github` (issues, pull requests, repositories, code), `list_github_repos`, `read_github_issue`, `read_github_file`, `create_github_issue`, `comment_on_github` | Web, once you've connected GitHub |
| Notion (`src/labb/tools/notion.py`) | `search_notion`, `read_notion_page` (a page's text or a database's rows), `create_notion_page`, `append_to_notion_page` | Web, once you've connected Notion |
| Your PCs (`src/labb/tools/pcs.py`) | `list_pcs`, `run_on_pc`, `check_pc_run`: start work on a PC you connected, asking you first, see [Your PCs](#your-pcs) | Web chat |
| Files (`src/labb/tools/files.py`) | `list_files`, `read_file`, `glob`, `grep`: only inside the working directory | Terminal |
| Edits (`src/labb/tools/edits.py`) | `edit_file` (replace exact text), `write_file` (create or replace a file): each shows a diff to approve | Terminal |
| Shell (`src/labb/tools/shell.py`) | `run_command`: bash in the working directory, no stdin, killed after its timeout or on Ctrl-C. With `background`, it keeps running (dev servers, watchers): `read_output` gets what it printed since the last read, `stop_command` stops it, and all of them stop when labb exits (`/jobs` lists them) | Terminal |
| Task (`src/labb/tools/task.py`) | `task`: hands a search or investigation to a helper agent with read-only tools (files, read-only commands, the web, `look_back` over its own work) and a fresh context; only its conclusion comes back, so the conversation stays small. Its old results are cleared as it goes, and its model calls count in the reply's stats and budget | Terminal |
| Todo (`src/labb/tools/todo.py`) | `update_todos`: the steps of a longer job and where it stands, shown under the call and with `/todos`. Steps are numbered; one that won't or can't be done is `skipped` or `blocked` with a reason, and a step left out of a rewrite is put back. Kept per conversation: in `.labb/todos/` (git-ignored) in the terminal, on the server elsewhere | Both |
| Goal (`src/labb/tools/goal.py`) | `get_goal`, `update_goal`: offered while the conversation has an active [goal](#goals) | Both |
| Tool search (`src/labb/tools/tool_search.py`) | `find_tools`: loads tools that wait to be found when all of them would take too much of the window (MCP servers', mail, calendar); see [Context](#how-it-works) | Both |
| Notes (`src/labb/tools/notes.py`) | `read_notes`, `write_notes`: notes the agent keeps for later (decisions, findings, unfinished work): the project's `.labb/notes.md` in the terminal, your own on the server in the web chat, tasks and workflows, the app's own in an app's chat; see [Notes](#notes). No approval needed | Both |
| Questions (`src/labb/tools/questions.py`) | `ask_user`: asks you one question with 2 to 4 choices (its recommendation first); pick a number or type your own answer. With `-p` it goes on with its recommendation | Terminal |
| MCP | Tools of the servers in the [settings files](#settings-files), as `mcp__<server>__<tool>` | Terminal |
| MCP (server) | Tools of the servers an admin adds in Settings → MCP servers, for the default assistant: pick a well-known one or search the [MCP Registry](https://registry.modelcontextprotocol.io), fill in what it needs (a token, a folder) and **Add and connect**; or write them by hand in the same format as `mcpServers`. They're kept encrypted. Packages run on the server with `npx` or `uvx` (both in the Docker image) or `docker` (not in it), and the page says when the server lacks one. Read-only ones run right away; the others ask in the chat (**Allow** / **Don't allow**, with an optional reason), and don't run in scheduled tasks | Web |

Small models can struggle with exact edits: turn the edit tools off with `--disallowed-tools edits` or `"tools": {"deny": ["edits"]}`, and changes go through `run_command` again.

**Turning tools off:** `--tools a,b` keeps only those tools, `--disallowed-tools a,b` turns those off, and `"tools": {"allow": [...], "deny": [...]}` in the [settings](#settings-files) does the same for every session (`--tools` beats `tools.allow`; denied tools are off whatever else says). Names can be a tool (`write_file`), a toolset (`edits`, `shell`, `mcp`) or a wildcard (`mcp__github__*`). `/tools` lists them, crossing out the ones that are off, and `/tools off <name>`, `/tools on <name>` and `/tools reset` change them for the session. The model is told some tools are off, and a call to one is refused.

**Adding a toolset:** create `src/labb/tools/<name>.py` with a `TOOLS` list (and optionally a `PROMPT` for the system prompt, `NEEDS_WORKDIR = True` for terminal-only tools that read `config["configurable"]["workdir"]`, and an `enabled()` function to leave it out when it can't work), then add it to `TOOLSETS` in `src/labb/tools/__init__.py`. The tool's name, docstring and arguments are what the model sees.

## The browser

The agent can use a real browser: open pages, click, fill in and submit forms, download and upload files, and work in web apps like spreadsheets. It's off until you turn it on: the **Browser** switch by the message box in the web chat (each chat keeps its own, like its model), `--browser` or `/browser on` in the terminal (or `"browser": true` in the [settings](#settings-files)). An agent has it if it was given it in its editor.

**Where it runs.** In Docker, in a service of its own ([labb-browser](../browser/README.md), about 1.8 GB, mostly Chromium), which reaches the internet but not your network or labb's other services. On a PC, `labb --install-browser` adds Playwright and Chromium, and the browser then runs on that PC, in a window you can see, and can open addresses on your own machine, which is handy for testing what you're building. Without that, the PC uses the server's.

**What needs your yes:**

| | |
| --- | --- |
| Reading and moving around: opening a page, reading it, scrolling, tabs | Nothing, except an address on your own machine or network (terminal), or one with a long query to a site the chat hasn't been to, since that's a way to carry something off |
| The first click or typing on a site | Once per site, per chat |
| Submitting a form that sends something, showing what it sends and where; anything labelled like it pays, sends, deletes or publishes; giving a page a file | Each time |
| Passwords, card numbers, one-time codes, CAPTCHAs | The model can't fill those in at all. It calls `browser_handover`, and you do them yourself |

Spotting what can't be taken back goes by the form and by English words on the button, so it's a net under the site's yes, not a guarantee: only say yes to sites you'd let someone else click around in. In tasks and workflows nobody can answer, so there the browser only reads. `--allow-all` asks nothing, for any tool, and lets the model fill in sensitive fields too.

**Asking less.** The browser's questions are the main protection against a page telling the model what to do, so both of these trade some of it away. Tasks and workflows have neither: there the browser still only reads.

| | Stops asking | Still asks | Turned on |
| --- | --- | --- | --- |
| **Trusted sites** | The first click or typing on a listed site and its subdomains, in all your chats | Everything else in the table above | **Settings → Trusted sites** in the web chat. In the terminal, `"browserSites": ["example.com"]` in `~/.labb/settings.json` (a project's settings can't add any) |
| **Don't ask** | Everything the table above asks, including sending, paying, deleting and giving a page a file, for this chat | `browser_handover`: passwords, cards, codes and CAPTCHAs stay with you | The **Don't ask** switch by the message box while the Browser is on. It stays with the chat, so turn it off again when you're done with it; new chats start with it off. In the terminal, `--browser-auto` or `/browser auto` |

Shell commands and other tools keep their own approvals with both.

**Watching and taking over.** In the web chat, a panel beside the chat shows the chat's browser live. It's open when the Browser switch is on (if the screen has room), the cursor button in the header opens and closes it even with the switch off, and it stays as you left it. **Take over** sends your clicks and keys to the page whenever you like, without the agent asking, and with nothing open you can type an address to start yourself; before a chat has started its tabs are separate, though what you sign in to carries over. When the agent hands over (to sign in, say), it shows up in the chat with **I'm done**. What you type there goes straight to the page: the model doesn't get it, and it's not kept, and the values of password fields are never in what the model reads. From a PC using the server's browser, the question gives the address (`/browser/<chat>`). With the browser on your PC, you use its window.

**Sign-ins** are kept per person between chats, in a file only the browser's service reads (on the PC, in `~/.labb/browser/`), so signing in once is enough. Chats share them, and each chat has its own tabs, which close after 30 idle minutes. **Sign out of every site** in the live view forgets them. Some sites, Google's among them, can refuse sign-ins from a browser that's being automated.

**Images.** The agent reads a page as text, with an image's description where it has one and `[image, no description]` for a larger one without. To see an image, a chart or anything drawn on a canvas, it scrolls there and takes a screenshot, which needs a model that can see images. It only gets what's on screen, and the screenshot isn't kept in the chat. It doesn't solve CAPTCHAs: it hands those over to you.

**Spreadsheets.** In a web spreadsheet the agent clicks the first cell and pastes tab-separated rows (`browser_type` with `paste`), or types (tabs go to the next column, new lines to the next row), and checks with a screenshot, which needs a model that can see images. It can also upload a CSV it wrote to the app's Import, and read or save what the browser downloaded (`browser_files`).

**What the model sees.** A page's text in order with its controls numbered, like `[3] button "Save" (submits the form)`: open shadow roots and iframes included, on a long page its main part first, with the controls further down always listed. Each action answers with the page as it is after. A screenshot goes only with the next model call and isn't saved in the chat. Text on pages is treated as data, not instructions.

## Terminal

| Command | |
| --- | --- |
| `labb` | New conversation in this folder |
| `labb -c` / `-r [id]` | Continue the latest / pick one of this folder's conversations |
| `labb -p "question"` | One answer, nothing saved; raw Markdown when piped; exits 1 on failure |
| `git diff \| labb -p "review this"` | What's piped in goes with the question (up to 200,000 characters), for scripts and git hooks |
| `labb workflows` | Your [Studio](#studio) workflows on the server |
| `labb run <workflow> [-i name=value …]` | Run one, showing its steps; asks here when it waits for an approval. `--draft` runs the draft, `--output-format json` prints the run. Exits 0 done, 1 failed, 2 stopped, 3 waiting |
| `labb eval tools [-m model …] [-n 3] [-v]` | Score how well models call the tools, see [Tool calls](#tool-calls) |
| `labb eval long [-m model …] [--engine name …] [-k case]` | Run longer jobs for real and measure them, see [Tool calls](#tool-calls) |
| `labb connect [folder …]` | Start labb on this PC from the web page, in these folders, until Ctrl-C: see [Your PCs](#your-pcs). `--allow-all` asks for nothing |
| `labb -p "…" --output-format json` | One JSON object (`result`, `stopped`, `error`, `tool_calls`, `stats`); `stream-json` for events as they happen |
| `-m <model>` | Use that model |
| `--allow-all` | Run every command and edit without asking |
| `--tools a,b`, `--disallowed-tools a,b` | Only these tools / not these, see [Tools](#tools) |
| `--login`, `--logout`, `--update` | Log in to the server / revoke this PC's key / install the server's version |
| `--version`, `--graph` | Print the version / the graph as Mermaid |

**In the chat:** replies stream as Markdown with a stats line (`gemma4:e4b · 12.4s · first token 1.8s · 3,688 in / 412 out · 39 tok/s`). Tool calls are one line each, and the model's reasoning one "✻ Thought for 4.2s" line; **Ctrl-O** shows the last reply's thinking and tool outputs in full. **Ctrl-T** (or `/thinking`) shows the thinking as it streams, and `"showThinking": true` in the settings starts with it shown. Thinking is shown for Ollama's thinking models (qwen3.5 and the like). **Esc** or **Ctrl-C** stops a reply and keeps what was written. **Enter** sends; **Alt-Enter**, **Ctrl-J** or a trailing `\` adds a line. **Esc Esc** edits your last message. `@path` attaches a file or folder listing, `! command` runs a command yourself and shows its output to the agent next time. The status line shows the model, folder, git branch and how full the context is.

| Slash command | |
| --- | --- |
| `/new`, `/clear`, `/resume` | Start over / pick a conversation |
| `/init` | Have the agent write an `AGENTS.md` |
| `/plan`, `/plan show` | Plan mode: read-only, ends with a plan to approve (**y** go ahead, **a** go ahead accepting its edits). An approved plan is saved in `.labb/plans/<conversation>.md` (`/plan show` prints it), and the agent starts by turning it into its todo list. Shift-Tab goes through the modes: default, accept edits (edits in the folder go through without asking; commands still ask) and plan |
| `/goal [--calls N] <objective>`, `/goal`, `/goal pause\|resume\|clear` | Keep the agent working until a [goal](#goals) is done, at most N model calls (100) |
| `/undo`, `/rewind` | Take back the last (or an earlier) message and the file changes since |
| `/compact` | Summarize the conversation now |
| `/context [full]` | What the model gets now: tokens per part (system prompt sections, tools, summary, messages, recalled memories), what was summarized or cleared; `full` pages through the exact input |
| `/model [name]` | Show or switch the model for this session |
| `/loop [interval] <prompt>`, `/loop stop` | Run a prompt again and again in this conversation: see [Loops](#loops) |
| `/num-ctx [tokens\|reset]` | Show or change how many tokens Ollama keeps in memory for the model (`num_ctx`, e.g. `65536` or `64k`) for this session (`--num-ctx` for one run) |
| `/answer [fast\|auto\|thinking]` | Show or switch the [answer mode](#answer-modes) for this session (`--answer` for one run) |
| `/engine [use\|show\|diff <name>]`, `/engine fork <new> [from]` | List, switch to, inspect or start an [engine](#engines) (`--engine` for one run) |
| `/thinking`, `/cost`, `/copy`, `/export [file]` | Show or hide reasoning as it streams (Ctrl-T) / totals, and the cost with `prices` in the settings / copy the last reply / save as Markdown |
| `/memory [add\|pin\|unpin\|forget …]` | What labb [remembers](#memories) about you |
| `/workflows`, `/run <workflow> [name=value …]` | Your [Studio](#studio) workflows, and running one |
| `/tools [off\|on <name> …\|reset]` | The tools, and turning some off for this session |
| `/todos` | The agent's todo list for this conversation |
| `/jobs` | The commands running in the background; `/jobs stop <id>` stops one |
| `/permissions`, `/mcp`, `/help`, `/exit` | |

- **Permissions:** read-only commands (`ls`, `cat`, `grep`, `git status|diff|log`, …) run right away; others ask: **y** once, **a** always in this folder (saved as a prefix like `npm install` in `.labb/permissions.json`), **n** with an optional reason. Edits ask the same way with a colored diff; **a** allows every edit in this folder (the rule `edits`). Command substitution, file redirects, subshells, `sudo`, `find -delete/-exec`, options that write files or run programs (`rg --pre`, `sort -o`, `tree -o`, `git … --output`), variables set in front of a command that make it run other programs (`GIT_*`, `LD_PRELOAD`, `PAGER`, `PATH`, …), and git's reading commands in a repository whose own config runs programs (`core.fsmonitor`, `diff.external`, a `textconv`) always ask. `git branch` and `git remote` run right away only when they list. Reading a file that may hold secrets (`.env`, `*.pem`, `*.key`, SSH keys, `~/.ssh`, `~/.aws`, `~/.labb`, …) asks every time, in commands and `read_file`, and `grep` skips those files; so does a read-only command that reaches outside the working directory (`cat ../x`, `ls /`). Commands aren't sandboxed.
- **Project instructions:** `AGENTS.md` (or `CLAUDE.md`) files from the working folder up to your home folder go into the system prompt, the closest last. So do the git branch, status and last commits, once per session.
- **Longer jobs:** the agent writes a todo list for jobs of 3 steps or more, and saves what took effort to find out in `.labb/notes.md`, under a topic each, instead of counting on its context (old tool results get cleared, older turns summarized). Each model call ends with a short reminder only the model sees: the goal, the open todo list (a long one by what's in progress, next and blocked), where the approved plan is, the notes' topics, and how full the context is once that matters, so it keeps the list up to date and reads its notes before digging again. A reply that ends while steps are still open goes back to work (twice per reply; [engines](#engines) change it), unless it ends with a question for you. Before older turns are summarized, the agent gets one round to save its notes and update its list. The summary keeps the goal, decisions, progress, changed files (listed by labb, not the model) and the next step, and your own messages from that part are kept word for word next to it.

### Goals

`/goal <objective>` gives the conversation a goal: the agent works on it until it has checked that every part of it is done, then says so with `update_goal`. A reply that ends before that goes round again (up to 10 times per reply), and when a reply ends anyway, the next one starts by itself, until the goal is done, blocked or out of budget. Each round reminds the model to keep to the whole objective rather than an easier version of it, to tell real progress from talking about it, and to back "done" with evidence (files read after the change, command output, tests). It can only call the goal blocked once the same thing has stopped it in 3 rounds in a row. `--calls N` is its budget in model calls (100); once that's used, the agent wraps up with what's done and what's left. Esc stops it as usual and the goal stays; the status line shows it, `labb -c` mentions it, `/goal pause` and `/goal resume` stop and restart it, and `/goal resume` after the budget gives it as much again. Goals are kept in `.labb/goals/`. A [task](#tasks) can work the same way with **Keep going until done**.
- **Undo:** before each message, the folder's files are snapshotted in a separate git repo under `~/.labb/snapshots` (following `.gitignore`, never touching the project's `.git`). Off in your home folder and `/`.

### Loops

It sends the same message again and again in the conversation, for watching something or a job done in rounds. The prompt can be a message, one of your commands or a skill (`/loop 10m /check-deploy`).

- `/loop 5m check whether the deploy finished` runs it now and then every 5 minutes after each run ends (`s`, `m`, `h` or `d`, from 10s to 1d).
- `/loop keep fixing the failing tests` is self-paced: after each run the model says when to run it again with `schedule_next` (1 minute to a day), or the loop ends.
- Either way the model can end it with `end_loop` once the job is done ("until the deploy is green"). Esc during a run, Ctrl-C at the prompt or `/loop stop` ends it; `/loop` shows it.
- A run that fails (the model or the connection) is tried again after 30 seconds, then 2 minutes; a fourth failure in a row ends the loop. A self-paced run that says neither when to run again nor to stop is reminded once; if it still doesn't, the next run comes after the last wait the model chose (or 10 minutes), and a second such run in a row ends the loop.

Between runs you chat as usual; the status line counts down to the next one, which waits while you're typing. Only loop runs get the loop's tools and a line in the system prompt about it. Loops last as long as the session.

### Engines

An engine is a saved version of the agent's core: its prompts, how it manages context, its tools and how it repairs tool calls. What labb does as it comes is the built-in `default`; anything else is a file that says only what differs from it, so you can try another way of working and switch back.

```bash
/engine fork terse          # starts ~/.labb/engines/terse.json from the engine in use
/engine use terse           # from the next message on; the conversation carries over
/engine diff terse          # what it changes
labb --engine terse         # for one run; "engine" in the settings to start with it
```

```json
{
  "description": "Short and to the point",
  "prompts": {"coding": "Make the change, run the tests, say what you did."},
  "tool_prompts": {"shell": "Prefer the project's scripts."},
  "sections": {
    "off": ["skills"],
    "order": ["instructions", "base"],
    "add": [{"id": "style", "label": "Style", "text": "Use short names. You're in {{workdir}}.", "when": ["terminal"]}]
  },
  "context": {"compact_at": 0.6, "keep_recent": 0.3, "keep_tool_results": 4},
  "input": {"recalled": true, "work_reminder": false},
  "tools": {"deny": ["web"], "descriptions": {"grep": "Search the code."}},
  "repair": {"fix": true, "constrained": false, "strict": true},
  "continue": {"truncated": 3, "empty": 1, "announced": 0},
  "model": {"model": "ollama:gemma4:e4b", "summary_model": "ollama:gemma4:e4b", "answer_mode": "fast"}
}
```

| Part | What it changes |
|------|-----------------|
| `prompts` | The texts: `base`, `terminal`, `coding`, `plan_mode`, `task`, `mode_fast`, `mode_thinking`, `web_off`, `tool_rules`, `summary`, `work_summary` (the web's, while there's a job in hand), `coding_summary`, `title`, `context_flush` (before a summary), and what the model is told when it [goes on](#how-it-works): `continue_truncated`, `continue_truncated_tool`, `continue_empty`, `continue_announced`, `continue_open_steps` (`{{steps}}`), `continue_loop`, `continue_goal` (`{{objective}}`, `{{usage}}`), `continue_goal_budget`, `continue_audit`. `{{today}}` and `{{workdir}}` are filled in, except in the summaries and the title |
| `tool_prompts` | What a toolset adds to the system prompt, by toolset name |
| `sections` | The system prompt's sections: turn off or reorder them by id (`base`, `agent`, `task`, `loop`, `web_off`, `answer_mode`, `skills`, `memories`, `how_to_work`, `git`, `plan_mode`, `tool_rules`, `instructions`, `tools:<toolset>`), or add your own. `when` is any of `terminal`, `web`, `plan`, `task`; without it, always |
| `context` | `keep_tool_results` (3), `min_cleared_chars`, `clear_at` (0.25), `max_result_share` (0.25), `user_words` (0.08), `summary_share` (0.06), `compact_at`, `keep_recent`, `max_scale`, `recall_query_chars`, `instruction_files` (plain file names), `max_instruction_chars`, `max_instruction_share` (0.12), `tool_share` (0.10); the shares are of the context window |
| `input` | Whether the model gets `recalled` memories and the `work_reminder` |
| `tools` | `allow` and `deny` by tool, toolset or wildcard, and new `descriptions` |
| `repair` | Fixing tool calls (`fix`), rewriting them with constrained decoding (`constrained`), the provider's `strict` mode |
| `continue` | How many times per reply the model is told to go on when its reply ended too early: `truncated` (3), `empty` (1), `announced` (1), `open_steps` (2; 4 in a task), `loop_unscheduled` (1), `goal` (10; 50 in a task), `goal_budget` (1), `hook` (3), `audit` (0: on, it checks the evidence before its final report); 0 turns one off |
| `model` | Its `model`, `summary_model` and `answer_mode`. `-m` and `/model` beat its model, and an answer mode you choose beats its own (`auto` leaves it to the engine) |

An engine can only narrow what a run may do. It can't turn on a tool the session, an agent or the tool rules have off, and commands and edits still ask for approval, also when an engine turns the prompts about them off. Limits and spending caps still apply, and an engine's extra model calls count in them and in the stats. A setting labb doesn't know, or a value out of range, makes the engine unusable and says why: `/engine` lists the problems, `--engine` stops with them, and an engine named in the settings falls back to the default with a warning.

**Its loop.** `loop` replaces the steps of a reply: `{"nodes": [...], "edges": [...], "max_model_calls": 30}`, most easily made on the [web page](#engines-on-the-server). Besides labb's steps (`recall`, `compact`, `llm_call`, `tool_node`) there's `route` (a branch), `model_step` (asks a model on the side, e.g. to plan or review, and keeps the answer in `{{vars.name}}`), `note` (a note for the model's next call, only that call), `check` (runs a command; if it fails, the model gets the output) and `python`. A loop that could leave tool calls without results, never finish, or go round without the model is refused. However it goes round, a reply makes at most `max_model_calls` model calls (50 if not given), or fewer with an admin's limit. In plan mode `check` steps don't run.

**Its own tools.** `tools.custom` is a list of `{"name", "description", "kind", "params": [{"name", "description", "type", "required"}]}`, offered as `custom__name`. The `kind` is `command` (with `"command"`: `{{name}}` is an argument, quoted for the shell; it runs through `run_command`, so it asks when it should and not at all in plan mode), `prompt` (with `"prompt"` and optionally `"model"`), `python` (with `"code"`) or `action` (with `"action"`, a Studio action's id; on the server, as whoever is chatting). Agents don't get them.

**Python** reads `inputs` and sets `result`. For a step, `inputs` has `user_message`, `last_reply`, `messages` and `vars`, and `result` can be `{"vars": {...}, "note": "..."}`; for a tool, `inputs["args"]`. It runs in a process of its own in the terminal, stopped with the reply or after 30 seconds, and only in the [sandbox](#studio) on a server.

**Hooks and servers.** `hooks` works like the settings' (they add up), with more events: `RunStart`, `PreModel`, `PostModel`, `PreToolUse`, `PostToolUse`, `PreCompact`, `Stop`, `RunEnd`. Exit code 2 from `RunStart`, `PreModel` or `PostModel` makes the hook's stderr a note for the model, and from `Stop` sends the model back to work. `mcp` adds MCP servers while the engine is in use.

**What asks first.** Engine files are in `~/.labb/engines/` and a project's `.labb/engines/`; yours wins over a project's of the same name, and both over one from the server (with `LABB_SERVER`, your engines there and the shared ones are listed too). labb shows what an engine changes and asks before using, again whenever its content changes: one with Python, always; one of a project's or the server's that runs commands, hooks or servers; and a project's that the project's settings name, because a repository you cloned shouldn't change how the agent is instructed unnoticed. Without a terminal to ask in (`-p`), it isn't used. Naming a project's plain engine yourself with `--engine` or `/engine use` doesn't ask.

Each reply's stats record the engine that wrote it as `name@fingerprint`, for replies that weren't the default's. `labb eval tools --engine a --engine b` scores tool calling with each.

### Settings files

`~/.labb/settings.json`, then the project's `.labb/settings.json`, which wins (lists are combined, MCP servers merged by name).

**A project's own files ask first.** A folder's `.labb/` came with it (a repository you cloned, say), so what in it runs code or skips asking (`hooks`, `mcpServers`, `permissions.allow`, `"defaultMode": "acceptEdits"`, and the commands in `.labb/permissions.json`) counts only once you agree to it. labb lists it and asks when it starts in a terminal, and asks again whenever it changes; until then it's left out, with a note (`-p` and `labb connect` sessions leave it out). What you agreed to is kept in `~/.labb/trusted-projects.json`. Rules labb saves itself (**a** for always) count without asking; your home folder's `.labb` is yours and never asks.

```json
{
  "model": "ollama:gemma4:e4b",
  "models": ["anthropic:claude-sonnet-5-5", "openai:gpt-5.5"],
  "defaultMode": "plan",
  "answerMode": "auto",
  "ollamaNumCtx": 65536,
  "theme": "monokai",
  "showThinking": true,
  "permissions": {"allow": ["npm test", "mcp__github__*", "edits"]},
  "tools": {"deny": ["web"]},
  "mcpServers": {
    "github": {"command": "npx", "args": ["-y", "@modelcontextprotocol/server-github"], "env": {"GITHUB_TOKEN": "..."}},
    "docs": {"url": "https://example.com/mcp"}
  },
  "hooks": {
    "PreToolUse": [{"matcher": "run_command", "command": "./scripts/check-command.sh"}],
    "PostToolUse": [{"matcher": "run_command", "command": "ruff format --quiet .", "timeout": 30}]
  }
}
```

- `defaultMode` is the mode to start in: `default`, `acceptEdits` or `plan`.
- `parallelTools` is how many tool calls that only read run at the same time (default 5; 1 for one at a time).
- `prices` gives models' prices in dollars per million tokens, `[input, output]` (or `[input, output, cached input, cache write]`), by spec, name or pattern (`"openai:gpt-5.5": [1.25, 10]`, `"anthropic:claude-*": [3, 15]`); labb has none built in, and models without one count as free. `/cost` shows what the session cost, and `maxCostPerSession` (dollars) stops a reply once the session reaches it, and then takes no more messages in that session. The `task` helper's calls are counted.
- `answerMode` is the [answer mode](#answer-modes) to start with; `--answer` beats it.
- `ollamaNumCtx` is how many tokens Ollama keeps in memory for a model (`num_ctx`, from 2,048 to 1,048,576; `OLLAMA_NUM_CTX` without it); `--num-ctx` beats it and `/num-ctx` changes it for the session. More holds longer conversations and files before they're summarized, but takes more memory on the Ollama server. A local Ollama model compacts within it (or `CONTEXT_WINDOW`, if that's set); cloud models use their whole window.
- `engine` is the [engine](#engines) to start with; `--engine` beats it.
- `model` beats `MODEL`, and `-m` beats both. `models` adds choices to `/model` and the web chat's picker. `theme` is any Pygments style.
- **MCP servers** run as a process or are reached at a `url` (streamable HTTP, or `"transport": "sse"`). Their tools ask before running (or are allowed in `permissions.allow`); stderr goes to `~/.labb/logs/mcp-<server>.log`.
- **Hooks** get the call as JSON on stdin (`event`, `tool`, `args`, `workdir`, and `result` after). Exit code 2 blocks a call (its stderr goes to the model as the reason, even with `--allow-all`), or after a call adds its stderr to the result. A `Stop` hook runs when a reply is about to end, with `reply`, `todos`, `goal` and `continued` (how many times a Stop hook already sent it back in this reply); exit code 2 sends the model back to work with the hook's stderr as the reason, at most 3 times per reply. For example, not done while the tests fail: `{"Stop": [{"command": "make test >/dev/null 2>&1 || { echo 'make test fails: fix it before you finish' >&2; exit 2; }"}]}`.
- **Skills:** folders with a `SKILL.md` in `.labb/skills/` (the project's) or `~/.labb/skills/` (yours), and `.claude/skills/` too, plus your server's. `/skills` lists them, and `/name what to do` uses one; see [Skills](#skills).
- **Custom commands:** each `.md` in `~/.labb/commands/` or `.labb/commands/` is a `/name` command; `$ARGUMENTS` is replaced with what you type after it, and a `description:` front matter shows in `/help`.

## Web chat

`uv run python -m labb.server.app` serves the app from `frontend/web/dist` on all interfaces. For development, run `npm run dev` in `frontend/web/` with the server running and open http://localhost:5173; `npm run typecheck` checks the types.

It streams replies with Stop (Esc), keeps answering chats in the background while you switch (and when you leave the page, reload it or lose the connection: the page joins the reply again by itself, or when you open the chat again; only Stop stops it), and has edit (↑) and regenerate, file and image attachments, a model picker (each chat keeps its own model: picking one in a chat changes only that chat, and it stays with the chat, also on another device once a reply used it; picking in a new chat without an agent sets the default for new chats) with an [answer mode](#answer-modes) and a **Web** toggle next to it (each chat keeps those too, along with its browser switch and **Don't ask**: picking one in a chat changes only that chat, and a new chat starts from the last default; with Web off, replies don't search the web or read pages, and the toggle is hidden for an agent without the web tools), expandable tool calls (an agent's todo list shows under its call), a context panel (the header's **NN% context** button, docked beside the chat and updated as a reply goes on: what the next model call gets and what would change it, with tabs for an **Overview** with **Summarize now**, the exact **Input**, the **Tools**, a **Work** tab with what the chat is working on now (its [goal](#goals) and how much of its budget it used, the todo list as it stands, and the plan you approved; for a session on a PC, asked of the PC while it's connected), a **Timeline** with a waterfall of each reply (its model calls, tools and steps on one clock, as they happen, so you can see what ran when and whether anything ran in parallel) and the real tokens of each model call, a **What-if** for trying another model, answer mode, engine or Web without sending anything, and the **Raw** input as JSON), pinned and searchable chats (Ctrl+K), sharing (the header's link button: everyone on the server can read the chat at `/shared/<id>`, and only you can change or continue it), Markdown export, and light and dark themes. The sidebar has the pages (**Agents**, **Tasks**, **Documents**, **Memories**, **Usage**…; **Customize** under them shows, hides, pins to the top and reorders them by dragging, and can hide the Terminal list, kept per browser), then the **Chats** and **Terminal** lists, which fold (Terminal shows labb sessions read-only, with the `labb -r` command to continue them), and your account with Settings at the bottom. Every page, chat, editor and Settings tab has its own address, so links, bookmarks, reloads, Back and Forward work, and Ctrl+click opens a chat or page in a new tab.

## Server

Run the web chat on the machine with Ollama, in Docker; its image also has the wheel the PCs install labb from. A release's `labb-server.tar.gz` pulls the images from GHCR (`LABB_VERSION` in `.env` pins one); in a git checkout, `./setup.sh dev` makes `docker compose` build them from the source instead (`compose.dev.yaml`).

```bash
docker compose up -d           # http://<server>:8000; settings from .env next to compose.yaml
docker compose logs -f
```

Chats, accounts, usage, the search index of documents and memories, and the crawler's recently read pages live in Postgres, in the `postgres-data` volume (`docker compose down -v` deletes it). `labb-data` only keeps an old `chats.sqlite`, if there was one. From the container, Ollama on the same machine is at `http://host.docker.internal:11434`; the crawler runs in its own container next to the server and isn't published. With several users, set `OLLAMA_NUM_PARALLEL` on Ollama so replies don't queue.

**On each PC** (Linux or macOS):

```bash
curl -fsSL http://<server>:8000/install.sh | sh   # installs uv if needed, labb, and ~/.labb/.env
labb --login                                    # approve this PC in the browser; saves its API key
```

- `labb --update` installs the server's version when it's different, and labb says so when it starts. To ship one, raise `version` in `pyproject.toml` and release it (a `v<version>` tag), or rebuild the image from a checkout.
- With `LABB_SERVER` set, labb sends each session to the server in the background after every reply (and after `/undo`, `/rewind`, Esc Esc); unsent sessions go out when the server is back. `-p` answers aren't saved or sent.
- Model calls, searches and page reads go through the server (`/ollama`, `/crawler`) with the PC's key, so keep Ollama off the LAN: bind it to the Docker bridge (`ip -4 addr show docker0`, usually `172.17.0.1`) or firewall port 11434. The crawler isn't published at all. OpenAI and Anthropic models use each PC's own keys.

**Accounts:** the first account is the admin. On a fresh server, creating it takes the setup link the server prints when it starts (`docker compose logs labb-web | grep setup=`; the token is kept in `setup-token` next to the data until it's used), so nobody else reaching the server first can take it; from the machine itself (`http://localhost:8000` in a checkout) it isn't needed. Admins invite people from Settings → Invites (single-use links, 7 days, as member or admin), and manage users and everyone's keys. Everyone sees only their own chats and sessions. Settings also has your password and API keys (`lb_…`, shown once) for scripts. Passwords are hashed with scrypt; tokens and keys are stored as SHA-256; the session cookie is HttpOnly (and `__Host-labb_session` over https, so no other port of the name can set it); cookie requests that change something, and logging in, need an `X-Labb: 1` header. Wrong passwords slow down per address and username, and per address. Every answer has `X-Content-Type-Options`, `X-Frame-Options: SAMEORIGIN`, a `frame-ancestors` CSP and `Referrer-Policy`. Over plain http, they cross your network unencrypted: turn on [HTTPS](#https).

### HTTPS

The server speaks plain http on port 8000. That's fine on the machine itself (`http://localhost:8000`), but from other devices the browser won't let the page use the mic (dictation, voice mode) without https, and passwords and session cookies cross the network unencrypted. The optional `https` profile puts [Caddy](https://caddyserver.com) in front of labb.

**On your network** (no domain needed):

```bash
./setup.sh https                    # names: labb.lan and this machine's LAN address
./setup.sh https labb.lan 192.168.1.5   # or your own names and addresses
docker compose up -d
./setup.sh root                     # saves Caddy's root certificate as caddy-root.crt
```

Caddy runs its own small certificate authority: a root valid for 10 years, which you trust once on each device, and certificates that last 12 hours and renew on their own, so there are no warnings to click through and nothing to renew. Give the server a name: point `labb.lan` at it in your router's DNS (or each device's hosts file), so links keep working if its address changes. The certificate covers the address too, so `https://192.168.1.5` works as well.

Trust `caddy-root.crt` on each device (`./setup.sh root` prints its SHA-256 fingerprint, to check it's this one):

| Device | How |
| --- | --- |
| Linux | `sudo cp caddy-root.crt /usr/local/share/ca-certificates/ && sudo update-ca-certificates` (Fedora: `/etc/pki/ca-trust/source/anchors/`, then `sudo update-ca-trust`). Chrome on Linux keeps its own list: Settings → Privacy and security → Security → Manage certificates → Authorities → Import |
| Windows | Open it → Install Certificate → Local Machine → Place in "Trusted Root Certification Authorities" |
| macOS | Open it in Keychain Access (System keychain), then double-click it → Trust → "Always Trust" |
| iPhone, iPad | Send it to the device (AirDrop, mail) and install the profile in Settings, then turn it on in Settings → General → About → Certificate Trust Settings |
| Android | Settings → Security → More → Install a certificate → CA certificate (Chrome uses it; most other apps don't) |
| Firefox | Settings → Privacy & Security → Certificates → View Certificates → Authorities → Import, "trust to identify websites" (on Windows and macOS, `security.enterprise_roots.enabled` in `about:config` uses the system's instead) |

Trust it only on your own devices: whoever has its key (`caddy-data` volume) can make certificates they'd trust. The [backups](#database) keep it, so a restored server doesn't need trusting again.

**A public domain:** `./setup.sh domain labb.example.com you@example.com`. Point the domain's DNS at the machine and forward ports 80 and 443 to it, and Caddy gets Let's Encrypt certificates, which every device already trusts.

**What it sets in `.env`:** `COMPOSE_PROFILES=https`; `LABB_HOSTNAME` (the names, comma-separated), `LABB_DEFAULT_SNI` (the address, for browsers given one), `LABB_TLS` (`internal`, or the email for Let's Encrypt); `LABB_BIND=127.0.0.1`, so plain http on port 8000 only answers on this machine; `FORWARDED_ALLOW_IPS="*"`, so the server believes Caddy that a request came over https and marks the session cookie Secure (safe because only Caddy and this machine reach port 8000); `SITES_BIND=127.0.0.1` likewise for the [sites](#sites), which Caddy serves on `SITES_HTTPS_PORT` (8301); and `LABB_PUBLIC_URL`. `HTTPS_PORT` and `HTTP_PORT` change Caddy's ports (443 and 80; port 80 only sends people to https). PCs keep working at `http://localhost:8000` on the server, and elsewhere use the https address: `curl -fsSL https://labb.lan/install.sh | sh` once the root is trusted.

Already on Tailscale? `tailscale serve --bg 8000` on the server gives it `https://<machine>.<tailnet>.ts.net` with a certificate every device on your tailnet trusts, with nothing to install.

### Health

**Settings → Health** (admins) checks the server's parts at once and says what to do about each one that isn't working: the Ollama server, the embedding model and the default model on it, the crawler, the sandbox for Studio's Python (and whether it has a token), the database's size, the search index (documents indexing, failed or from another embedding model, memories not embedded yet), which PCs aren't on the server's labb version, and the MCP servers. **Check again** runs them again.

Under the checks, **Resources** shows what everything uses, updated every 5 seconds while it's open: the machine's processor, load, memory, swap and the disk the data is on (as the server's container sees them, which with Docker Desktop is its VM), the server's own process, the database's size, the models Ollama has loaded (their memory, how much is on the GPU, when they unload), and, from the spawner, each of labb's containers (state, CPU, memory, restarts) and people's apps added up, the busiest first. CPU is a share of one processor, so 200% is two busy. A part that can't be read says so, and the rest still shows.

### Limits

**Settings → Limits** (admins) sets how far replies, scheduled tasks and workflows may go, and how many of everything people may have; a change applies to the next reply, run, upload or app start, and each limit shows its default to go back to.

| Group | Limits (default) |
| --- | --- |
| Chat | model calls per reply (no limit), tool calls at once (5), how long a tool that asks waits for Allow (10 minutes), attachments per message (10), image size (5 MB), text file size (200,000 characters) |
| Tasks | model calls per run (25), time per run (15 minutes), tasks per person (20), failures in a row before a task is turned off (3), runs kept per task (50), shortest heartbeat (5 minutes), open to-dos per person (100), prompt length (8,000 characters) |
| Workflows | time per run (30 minutes), steps per run (200), steps at once (4), runs at once per person (3), retries per step (3), model calls per agent step (25), loop items at once (4), webhook calls per minute (30), runs kept per workflow (50), time per Python step (120 seconds, the sandbox's most), HTTP action timeout (20 seconds), workflows per person (100), steps per workflow (60), actions on the server (200) |
| Agents | how deep agents may ask each other (2), agents per person (100), instructions length (8,000 characters), agents it may ask and actions it may use (12 each) |
| Memory | memories per person (500), memory length (500 characters) |
| Documents | documents per person (200), document size (20 MB) |
| Voice | recording size (25 MB, OpenAI's most), read aloud at once (20,000 characters), recordings kept per person (200) |
| Skills | skills per person (50), files per skill (100), file length (200,000 characters), skill size (1,000,000 characters), zip size (5 MB) |
| Engines | engines per person (100), versions kept per engine (200) |
| Server | MCP servers (20), custom providers (20), models in the list (100) |
| Accounts | staying signed in (30 days), invites last (7 days), wrong passwords before each try waits longer (5) |
| Apps | apps per person (10), apps in all (`APP_MAX_TOTAL`, 0: as many as there are ports), memory (256 MB), processor (50% of one) and processes (128) per app, data per app (1,024 MB), database per app (512 MB), database connections per app (10), environment variables per app (50), files per app (500), file size (1 MB), source size (20 MB), stop apps unused for (0 minutes: never), time per build (600 seconds), memory per build (1,024 MB), builds at once (2) |
| Artifacts | artifacts per person (50), files per artifact (300), file size (1 MB), artifact size (10 MB), time per build (180 seconds), memory per build (2,048 MB), published page size (100 MB) and files (10,000) |

The Apps and Artifacts limits are the spawner's and labb-sites' own: their defaults are their environment variables (`APP_MAX_PER_OWNER`, `APP_MEMORY`, `ARTIFACT_MAX`, `SITES_MAX_MB`…), the page shows and saves them through the server, and each service keeps what's saved (`SPAWNER_DATA/limits.json`, and `limits.json` beside labb-sites' token). When one can't be reached, the page says so and shows the rest.

How many tasks and workflow runs the server works on at once is set when it starts, with `TASK_WORKERS` and `WORKFLOW_WORKERS`.

### Services

**Settings → Services** (admins) changes how the server and its services behave, beside Limits' how far they go. Each setting starts as its environment variable set it, and a change applies at once:

| Group | Settings (environment variable) |
| --- | --- |
| Server | read private addresses without the crawler (`LABB_READ_PRIVATE`) |
| Crawler | read private addresses (`ALLOW_PRIVATE`), keep pages read for (`CACHE_HOURS`), wait for a page (`FETCH_TIMEOUT`), wait for a page in the browser (`RENDER_TIMEOUT`), wait for a page to settle (`SETTLE_SECONDS`), largest page (`MAX_BYTES`) |
| Browser | open private addresses (`ALLOW_PRIVATE`), close idle browsers after (`BROWSER_IDLE_MINUTES`), browsers at once (`BROWSER_PROFILES`), page text per look (`BROWSER_SNAPSHOT_CHARS`) |
| Sandbox | memory per Python step (`SANDBOX_MEMORY_MB`) |

Turning on private addresses lets the model read your intranet, NAS or router, and labb's own services, for anyone who can chat: only do it on a network where that's fine. Each service keeps what's saved itself (beside its token, or in the browser's data) and answers `GET|PUT /settings` with its token; when one can't be reached, the page says so and shows the rest. How many renders, Python steps and so on run at once are still set when each starts.

### Models and providers

Admins manage the models in **Settings → Models**, without editing `.env` or restarting:

- **Providers:** the built-in Ollama, OpenAI and Anthropic, plus custom ones: any server that speaks OpenAI's API (LM Studio, vLLM, llama.cpp, OpenRouter, Groq, Together…), whose models are `<name>:<model>` (`lmstudio:qwen2.5-7b`). Each card says whether an address or key comes from `.env`, is the default, or is saved here; what's saved wins, and **Use .env** goes back. Keys are encrypted with Studio's secret key and never shown again. **Test** asks the provider for its models before you save. A provider turned off has no models.
- **The default model** for chats, agents and tasks that don't pick one (`MODEL` until one is set).
- **Context window** (tokens a chat may fill before it's summarized; `CONTEXT_WINDOW` until one is set) and **Ollama context size**, the `num_ctx` the server's Ollama models run with (`OLLAMA_NUM_CTX` until one is set). labb on PCs sends its own `num_ctx` (its settings or `--num-ctx`), also through the server.
- **Which models people can use:** every model the providers have, or only a chosen list (`MODELS` starts it). Chosen models get labels for the pickers and can be **admins only**: members don't see them, and the web chat, agents, tasks and Studio refuse them. Admins can use any model.
- **Strict tool calls** per provider: default (on for OpenAI and Anthropic, off for OpenAI-compatible servers, or `STRICT_TOOLS`), on or off. See [Tool calls](#tool-calls). Ollama models without tool support are marked **no tools** in the picker.
- **Prices:** what paid models cost, in dollars per million tokens (input, output, and optionally cached input and cache writes, which are the input price when left empty), by model, name or pattern (`anthropic:claude-*`), for the usage stats. No provider says what its models cost through its API, so they're set here; **Suggest prices** fills them in from [LiteLLM's public price list](https://github.com/BerriAI/litellm) for you to check against the provider's pricing page before saving. Each reply is costed when it's recorded and keeps that cost, so a new price doesn't change what was spent before; replies from before a model had a price can be costed with **Cost them at these prices**, and **Cost all usage again** costs everything at today's prices. Models without a price, like local ones, count as free. Estimates leave out what the cost of tokens doesn't show (batch discounts, long-prompt surcharges, paid tools like a provider's web search), so the provider's bill can differ a little.
- **Search:** the embedding model that documents and memories are found with. See [Documents](#documents).
- **Speech:** the model that listens and the one that speaks, and its voice. See [Speech](#speech).

### Speech

With a model that **listens** (speech to text), the chat's message box gets a mic: click it, talk, click again, and what you said is written into the box. Recordings (MP3, M4A, WAV, WebM, OGG, FLAC, up to 20 MB) can be added as [documents](#documents): they're written down as they're added (which takes a while for a long one) and searched like any other. With a model that **speaks** (text to speech), every reply has **Read aloud**: it starts after the first sentence is ready (the rest is made while it plays), and a bar over the message box pauses it, speeds it up (up to 2×) or stops it; so does **Esc**. With both, the message box has **voice mode**: it listens until you pause, sends what you said, reads the reply aloud and listens again, until you end it (or say nothing for 30 seconds). The big button does the next thing: send what you said now, stop a reply that's still being written, or cut in while it speaks. In [Studio](#studio), **Transcribe** and **Speak** steps do the same in workflows. The browser only lets the page use the mic on `localhost` or over [HTTPS](#https).

Admins pick the models in **Settings → Models → Speech** (or `SPEECH_TO_TEXT_MODEL`, `TEXT_TO_SPEECH_MODEL` and `TTS_VOICE` in `.env`), and **Test** says a sentence and listens to it. They're models of OpenAI (`openai:gpt-4o-transcribe` or `whisper-1`; `openai:gpt-4o-mini-tts` or `tts-1`, with voices like `alloy`, `coral`, `nova`) or of any server that speaks OpenAI's audio API, added as an OpenAI-compatible provider. Ollama has no speech models.

With docker compose, speech stays on your own machine from the start: compose runs [speaches](https://github.com/speaches-ai/speaches) (faster-whisper to listen, Kokoro and Piper voices to speak), labb has it as the provider `speaches` (`SPEACHES_URL`), and it listens with `speaches:Systran/faster-whisper-small` and speaks with `speaches:speaches-ai/Kokoro-82M-v1.0-ONNX` and the voice `af_heart`. speaches starts with no models, so labb downloads the ones picked of it when the server starts and when an admin picks others (a few hundred MB, which takes a minute or two the first time; they're kept in the `speaches-models` volume). To use a bigger model, pick it in Settings, e.g. `speaches:Systran/faster-distil-whisper-large-v3`; `docker compose exec speaches curl -s http://localhost:8000/v1/registry` lists what speaches can download. The image runs on the CPU; for an NVIDIA GPU, use `ghcr.io/speaches-ai/speaches:latest-cuda` with the GPU given to the service.

### Answer modes

They set how much the model thinks before it answers: **Fast**, **Auto** (the model decides, the default) or **Thinking**. Pick one next to the model picker in the web chat, or with `/answer`, `--answer` or `"answerMode"` in the terminal. Each provider gets its own knob:

| Provider | Fast | Thinking |
|---|---|---|
| Ollama | thinking off | thinking on (as Auto) |
| OpenAI, Anthropic (models with effort levels) | the lowest effort (`none`, `minimal` or `low`) | the highest but `max` (`xhigh` or `high`) |
| Other models and servers | only the prompt | only the prompt |

Fast and Thinking also add a line to the system prompt (answer briefly, or work it through and check), so they change something on every model. An Anthropic model's thinking shows the same way as an Ollama model's.

### Tool calls

Models get tool calls wrong now and then: they sample tokens without checking them against the schema, and they use names they learned elsewhere (`search_web` for `web_search`, `file_path` for `path`). labb makes every reply's calls valid before they run, in the terminal and the web chat alike (`tool_calls.py`):

1. **Repair:** names from other engines and close matches, renamed and misspelled arguments, JSON written as a string, a list of strings where objects belong, and broken JSON (trailing commas, code fences, Python quotes) are fixed. The terminal shows what was fixed after the call, dimmed.
2. **Constrained retry (Ollama):** a call that still doesn't fit is asked for again with Ollama's `format`, which constrains the output to the tool's JSON schema (or the name to the tools offered). One extra request, only for broken calls.
3. **Strict mode (OpenAI, Anthropic, OpenAI-compatible servers):** the provider constrains the arguments itself. Optional arguments become nullable so the schemas qualify; a provider or model that refuses strict mode is asked again without it, and not again for that model.

What still fails goes back to the model as an error, as before. **Usage** shows per model the share of tool calls that were right as written (hover for how many were fixed, retried or still broken).

`labb eval long` runs longer jobs for real in a throwaway project (seven changes in one request; facts given early and asked for after the conversation was summarized) and reports how many of the checks pass (in the files, not by what the agent says), todo steps left open, steps the model tried to drop, how often it was sent back to work, model calls, tokens, the share of input the provider had cached, and summaries. `-m` and `--engine` compare, `-k steps` runs one case, and each run is saved as JSON in `.labb/evals/`, to compare before and after a change.

`labb eval tools` scores models on 35 prompts in a sample project, one model call each and no tool run: whether they called a tool, valid as written, the right tool and arguments, valid after repair and after the retry, and whether, asked for several things, it called them all in one reply, so they run at the same time. `-m` picks models (again for more), `-n 3` repeats the prompts (sampling varies), `-k read` keeps matching prompts, `-v` shows each miss, `--output-format json` for scripts. It calls the models, so hosted ones cost a little.

Measured on 2026-10-31 (labb 0.22.0's tools, one run of the 33 prompts, an Ollama 0.40.4 server on the LAN):

| Model | Called a tool | Valid as written | Right tool | Right arguments | Valid after repair | Valid after retry | Per call |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| `qwen3.5:4b` | 97% | 97% | 70% | 70% | 70% | 70% | 4.9s |
| `gemma4:e4b` | 97% | 97% | 79% | 76% | 76% | 76% | 4.6s |
| `gemma4:12b` | 100% | 100% | 73% | 70% | 70% | 70% | 12.4s |
| `qwen3:8b` | 100% | 100% | 91% | 88% | 88% | 88% | 99.5s |

Every call these models made was valid as written, so repair and the constrained retry had nothing to fix: what they miss is picking the right tool. `qwen3:8b` picks best, but was very slow on that server (it likely didn't fit in its GPU). Hosted models weren't measured.

**vLLM:** start it with `--enable-auto-tool-choice --tool-call-parser <the model's parser>` and, for constrained tool calls, vLLM 0.31's `--tool-strict-level`; then set **Strict tool calls** to on for that provider.

### Database

The server keeps its data in Postgres when `LABB_DATABASE_URL` is set, which `compose.yaml` does for the Postgres it runs next to the server (not published; only the server reaches it). Without it, as in a checkout run with `uv run`, everything is in the SQLite file `CHAT_DB`, which is fine for trying it out and for tests. labb on a PC always keeps its own SQLite file.

- **Backups:** `docker compose --profile backup up -d` starts the `backup` service, which every night at `BACKUP_AT` (default `03:00`, in `TZ`) saves the database as SQL, `secret.key` and (with [HTTPS](#https)) Caddy's root certificate and key in `./backups/labb-<time>/` (`BACKUP_DIR`), keeping the newest `BACKUP_KEEP` (7); it makes one right away the first time, `docker compose run --rm backup --once` makes one now, and `BACKUP_OWNER=1000:1000` gives the files to your user instead of root. A backup holds `secret.key`, which opens every key and token labb keeps: set `BACKUP_AGE_RECIPIENT` to an [age](https://age-encryption.org) public key (`age-keygen -o labb-backup.key` prints it; keep that file off the server) and each file is saved as `<name>.age` (`age -d -i labb-backup.key labb.sql.gz.age > labb.sql.gz` to restore). To restore one: `docker compose up -d postgres`, then `gunzip -c backups/labb-<time>/labb.sql.gz | docker compose exec -T postgres psql -U labb labb` into a fresh volume, and put `secret.key` back (and `caddy-ca/` into the `caddy-data` volume, at `caddy/pki/authorities/local/`). By hand, `docker compose exec postgres pg_dump -U labb labb > labb.sql` has everything, the search index included. To restore into a fresh volume: `docker compose up -d postgres`, then `docker compose exec -T postgres psql -U labb labb < labb.sql`, then `docker compose up -d`.
- **Your own Postgres:** set `LABB_DATABASE_URL` and `CRAWLER_DATABASE_URL` in `.env` (they win over the compose defaults) and drop the `postgres` service. Postgres 14 or newer, with the pgvector extension installed for searches in the database (`CREATE EXTENSION vector` needs a superuser once; without it, the server searches in Python).
- **The Postgres image** is `pgvector/pgvector:pg18-trixie`: Postgres 18 with pgvector, on the same Debian as the official `postgres:18` image (a volume moved between images of different Debians gets a collation warning, and its text indexes need rebuilding).

### Usage stats

**Usage** in the sidebar shows your replies, tokens, what they cost (once an admin has set [prices](#models-and-providers); also per day, model, place and chat, and how much input came from the provider's cache), waiting time, tool calls (and per model, how many were right as written), tokens per day by web chat and PC, models, top tools and your chats that used the most tokens, for 7 days, 30 days or everything, with a CSV download. Admins get **Stats** in Settings (under Server): active users, everyone's usage, per-user numbers (never chat titles), models and busy hours, gateway traffic, and each PC's labb version.

The numbers come from a ledger with one row per reply (`usage.py`) that nothing deletes, so deleting or regenerating doesn't change totals; web replies are added as they end, labb's when a PC sends its session. The gateway's own count of Ollama calls is shown separately so nothing is counted twice. labb identifies itself with an `X-Labb-Client: <version> (<machine>)` header.

### Documents

**Documents** in the sidebar takes PDFs, Word documents (`.docx`) and text files (Markdown, web pages, code, CSV…, up to 20 MB each), and the chat's `search_documents` tool finds passages in them by meaning. Word documents and web pages are read as text with their headings, lists and tables; Markdown keeps its images' descriptions, not the pictures. The answers cite passages as links: clicking one opens the document on the Documents page with the passage marked (click a document's name to read it too). labb on a PC searches the documents of whoever logged in, through the server. Everyone sees only their own.

Ask the chat to save something ("save this summary as a document") and `save_document` adds it to your documents as Markdown (or text, CSV, JSON… when the name ends that way), searchable like an upload; the answer links to it. It won't take a name you have already unless you ask it to update that document, which replaces its text and keeps it in the agents it was given to. labb on a PC saves to the server's documents too. With an agent, it's always your own documents, also with someone else's shared agent. A document an agent saves in a [workflow](#studio) doesn't start the workflows waiting for new documents.

The server splits each file into passages of about 1,000 characters (by page for PDFs, at headings for Markdown), embeds them with the embedding model on Ollama and keeps them in the database: on Postgres with [pgvector](https://github.com/pgvector/pgvector), which Docker's Postgres has; elsewhere (SQLite, or a Postgres without the extension) the server compares the vectors itself, which is fine for a few people's documents. A search finds passages by meaning and by the words in the question at once, so an error code or a name is found even when its meaning isn't close. The text stays in the database too, so another embedding model can index everything again.

**The embedding model** is `embeddinggemma` ([EmbeddingGemma](https://ollama.com/library/embeddinggemma), Google's: 308M parameters, multilingual, made for search). Pull it on the Ollama server first: `ollama pull embeddinggemma`. Admins pick another in **Settings → Models → Search**, which lists suggested ones (`all-minilm` is the smallest and fastest, English only; `nomic-embed-text`, `granite-embedding:278m`, `bge-m3` and `qwen3-embedding:0.6b` are others) and any embedding model pulled on Ollama, each saying whether it's pulled. Saving checks that Ollama can embed with it, then embeds every document and memory again in the background, without a restart; the page shows how far it's got, documents say they're indexing until then, and memories are found by keywords. Models that want words in front of searches and passages get them (EmbeddingGemma, Qwen3, Nomic). Until an admin picks one, it's `EMBED_MODEL`, else the model the documents and memories are embedded with already, else `embeddinggemma`. Scanned PDFs need OCR first; they have no text to read.

### Agents

An agent is an assistant set up for one job, on the **Agents** page: a name and emoji, a description, instructions (added to the system prompt), a model (the default for its chats; the model picker can still change it), which tools it may use, whether it uses memories, which documents it searches, and up to 4 conversation starters. The editor has a **Try it** chat that uses your changes as you make them and isn't kept.

Start a chat with one from its card, or with the agent pill next to the model picker in a new chat. A chat stays with the agent it started with; the chat list and header show its emoji. Its documents are either **whoever chats, their own** (like a chat without an agent), **only some of yours**, or none.

**Working with others:** an agent can be given **agents it can ask** and **actions it can use** (from [Studio](#studio)). It then gets `ask_agent` and `call_action`, and its instructions list them: a "Head chef" can ask the "Baker" about bread, or create a shopping list in another app. An asked agent answers in a conversation of its own that isn't kept, as whoever is chatting, and can ask others in turn, two levels deep at most.

**Ready-made agents and skills:** every account starts with five agents and the skills they use, made for jobs on the web: **News Desk** (recent news from good sources, with dates and links, and fact-checks), **Researcher** (a question across many sources, as a cited report), **Shopping Scout** (independent reviews and current prices, and a pick), **Trip Planner** (itineraries with checked opening hours and costs) and **Writing Editor** (clear, correct text in your voice). The skills are `news-briefing`, `fact-check`, `web-research`, `page-summary`, `product-comparison` and `trip-planner`. They're yours like any other, to change, share or delete. Deleted ones don't come back by themselves: **Settings → Account → Restore defaults** adds back the ones you deleted and leaves the rest as they are.

**Share** an agent and everyone on the server can chat with it and duplicate it; only you can change or delete it. People chatting with it use their own memories and history. A shared agent given some of your documents searches them for everyone who uses it, so they can read passages of those. Deleting an agent keeps its chats, which go on with labb; deleting a document takes it out of your agents.

### Skills

A skill teaches the assistant how to do one kind of task. It's a folder with a `SKILL.md`, in the open [Agent Skills](https://agentskills.io) format, so skills written for other agents work here as they are:

```markdown
---
name: release-notes
description: Writes release notes from a list of commits, grouped into New, Better and Fixed. Use for a changelog.
---
Group the commits, one line each, in words users understand. Follow template.md.
```

Anything else in the folder (references, templates, scripts) is for the instructions to point to. The model sees every skill's name and description, loads one with `use_skill` when a task matches, and reads its other files with `read_skill_file`. Typing `/name` (with what to do after it) uses one on purpose: its instructions go with your message. In the terminal the model can also run a skill's scripts from its folder, with the usual approval; the web chat has no shell, so there skills are instructions and references.

- **In the terminal:** the project's `.labb/skills/` and `.claude/skills/`, then yours in `~/.labb/skills/` and `~/.claude/skills/`, then your server's. The first one with a name wins.
- **In the web chat:** the **Skills** page. Write a `SKILL.md`, or describe the skill in a few sentences under **New skill** and the model writes the `SKILL.md` for you to look over before you create it (on a skill you have, the same box changes it: "also ask who it's for"). Or upload one or a zipped skill folder (text files only), and share it with everyone on the server. Uploading one with a name you have replaces it. **Download** gives you the folder, to put in `~/.labb/skills/`. Typing `/` in the message box suggests your skills.
- **Agents** choose their skills in the editor: whoever chats, their own (the default); only some, picked from the skills you can use, which the agent loads as you, also for people you share it with; or none. With no skills at all, the skill tools aren't offered, so small models aren't distracted.
- **Saving one from a chat:** ask ("save how we write release notes as a skill") and the model writes it with `save_skill`: in the terminal to `~/.labb/skills/` (or the project's `.labb/skills/` if you say so), in the web chat to your Skills page. It won't overwrite a skill unless you ask it to change that one.

### Engines on the server

The Engines page (`/engines`) is where [engines](#engines) are made and tried. **labb default** is there, read-only, to look through and fork. Everyone has their own, and can share one: others can see it, use it and fork it, and only you change it.

Two come with labb, read-only, to use as they are or fork. **labb default** is what labb does as it comes, and stays the default. **labb local** is made for small models on your own machine (4B to 9B, like `gemma4:e4b` or `qwen3.5:4b`): it offers 13 tools instead of 20 (no helper tasks, skill writing, notes, goals, loops, browser or background commands), with one-line descriptions, a short "how to work" and one worked example, and it keeps less of long results and project instructions. In the terminal it starts at about 1,900 tokens of prompt and tools instead of about 5,100, which leaves a 32k window for the work. Pick it in the message box or with `labb --engine labb-local`; plans and measurements are in `local-engine-docs/`.

- **The editor** has a section for each part of the core: Prompt, Loop (a canvas, with ready-made loops: Plan first, Self-review, Test until green, Minimal), Context, Tools (with tools of its own), Input & repair, Model, Hooks & servers, and JSON (to copy, download or import). What differs from the default gets a dot and a way back, and only what differs is stored.
- **The panel** next to it shows what the model would get in a Web, Terminal, Plan mode or Task run, with tokens per section, as you type (**Preview**); a chat that uses your unsaved changes and isn't kept (**Try it**); what you changed, and a box to describe a change in words, which a model turns into a change you can look at before using it (**Changes**); and every saved version, to compare and restore (**Versions**).
- **Compare** runs the same prompts through several engines, one reply at a time, with the time, tokens, model calls and tool calls of each; sets of prompts can be saved. Nothing is kept, and the replies use tokens like any other.
- **Picking one:** in the message box (once there's more than the default), and the chat keeps it; for an agent, in its editor, which its tasks and workflow steps use too; **Use by default** for your new chats; **For the server** (admins, shared engines) for everyone who hasn't chosen. A chat goes by what was picked for it, then its agent's, yours, the server's, then labb default. An engine that's deleted or stops being shared falls back the same way, so a chat always goes on.
- **What can't run on a server:** nothing an engine has runs a command there. One with hooks, `check` steps, command tools or MCP servers works only in the terminal, so the web chat can't pick it, and Python runs only in the sandbox (without `SANDBOX_URL`, one with Python is terminal-only too). Models it names need the same access as any other.

The Usage page breaks replies down by engine.

### Your PCs

`labb connect` lets you start labb on a PC from the web page. The PC dials out to the server, so it works from behind a router, and you start it by hand and leave it running; Ctrl-C ends it:

```bash
labb --login                                # once, if you haven't
labb connect ~/code/project ~/code/other    # these folders; without any, this one
```

Then the **PCs** page lists it. In a new chat, the **Runs on** picker above the message box has this server and each connected PC's folders, grouped by PC; one that isn't connected stays listed, greyed, with when it was last seen, and **See your PCs** opens the PCs page. Pick a PC and folder, say what to do, and follow the run there. It keeps going if you close the page, and any of your tabs can follow it; when it's finished it's a session like the terminal's, in the sidebar's list. A run is always a new session.

A session on a connected PC has the same context panel as any chat (the header's **NN% context**): the PC answers it, from the prompt, tools, project instructions and settings its replies run with, so what you see is what its next model call gets. **Summarize now** summarizes there, the Timeline shows its replies' waterfalls, and What-if tries its other models and answer modes. While the PC isn't connected the panel isn't offered.

From a chat, labb can do the same: with a PC connected it gets `list_pcs`, `run_on_pc` and `check_pc_run`, and an agent can be given them (**Your PCs**). It asks you in the chat before starting anything, and passes on what the run wants approved. If you stop the reply, the run stops. Questions aren't passed on: the run goes on with what it recommends, and says so.

What keeps this safe:

- **Only yours:** a PC belongs to the account whose key connected it. Other people, admins included, can't see it, its runs or its folders.
- **Only those folders:** the PC itself refuses any folder you didn't name, whatever the server says. Inside one, the run has the [same limits](#terminal) as in the terminal: reading outside it or reading a secret asks first.
- **Commands and edits ask you, in the page:** your yes is for that one action; "always" is only offered in the terminal. Rules already in that folder's `.labb/permissions.json` apply.
- **Only you, logged in:** starting a run and answering it take your session in the web page. An API key can't, so a key that leaks from one PC can't run anything on the others. Tasks and workflows can't start runs either.
- **The settings are the PC's:** a job says which folder and what to do, nothing about models, tools or permissions.

`labb connect --allow-all` runs everything without asking, and the web page says so. Anyone who gets into your account can then run anything on that PC, so keep it for machines you could lose. Three runs go at once per PC, and a job a PC doesn't pick up within 5 minutes fails instead of starting later. The newest 100 finished runs per person are kept.

### Inbox

The **Inbox** page (the bell, first in the sidebar, with the unread count) is where labb tells you what happened while you weren't looking:

- **Tasks:** a task's result, a run that failed and why, and a task that turned itself off after failing; the same for the heartbeat's to-dos.
- **Studio:** an approval waiting for you (in your runs, and in runs of your workflows), a run that failed (not a test run), and the result of a run that started on its own (a schedule, a webhook or an event).
- **Apps:** an app that crashed, keeps crashing, ran out of memory or was stopped for its data (not one stopped for being idle, or by you), and a page that stopped building after a change to its files. The server asks the spawner every minute.
- **Documents:** one that couldn't be indexed, and why.
- **For admins:** someone who used your invite, and a service that stopped answering (Ollama, the crawler, the sandbox, an MCP server), then that it's working again. Settings → Health's checks run every 5 minutes for this.

Opening one goes to what it's about and marks it read. A task's result and its chat go together: opening the chat reads what the inbox said about it, and marking the result read in the inbox reads the chat (its dot, and the Tasks count); answering an approval reads it for everyone it was sent to. While one is unread, a newer one about the same thing takes its place (a task every 5 minutes leaves one result, not a pile). New ones arrive as they happen, and with desktop notifications turned on (the button on the page), each one also shows on your desktop while labb is open in a tab. Each person keeps the newest 200 (Settings → Limits), and nobody else sees theirs.

### Tasks

A task runs a prompt on its own, with labb or one of your agents, on the **Tasks** page: every N minutes or hours, or at a time of day every day, on weekdays, on chosen days of the week, every N days or once a month (the 29th to 31st mean the last day in shorter months), or just once. Times are in your browser's time zone (8:00 stays 8:00 when the clocks change). A task that's still running when its next time comes skips that run, so an every-minute task never piles up; with a local model, though, frequent tasks keep it busy, and chats wait less when they're spaced out. Each run writes to a new chat ("News digest · Mon 26 Oct, 08:00"), or always to the same one, so it can see its earlier results. With **Keep going until done**, each run is a [goal](#goals): the agent works until it has checked every part of the prompt, up to the run's model calls, instead of stopping at its first answer. Each run then says how its goal ended (done, blocked, out of model calls or unfinished) and how many calls it used, with the agent's note (how it checked, or what blocked it) under the run. Tasks also get more rounds when they end with todo steps open (4 rather than 2), since nobody is there to say "go on". Chats a task wrote to have a dot until you open them, the count is next to Tasks in the sidebar, and each result or failure is in your [inbox](#inbox).

**Run now** runs it whatever the schedule, and Stop (or the chat's own Stop) ends a run. Make one from scratch, with **Schedule** on an agent's card, or with the clock button in a chat's header ("Run this every…"), which takes the chat's agent and first message.

Tasks run inside the server, one at a time (`TASK_WORKERS`), and wait while someone's web reply is being written. A run stops after 15 minutes or 25 model calls. After 3 failures in a row a task turns itself off and says why; turning it on again starts over. After a restart, a task missed in the last hour runs once, and older misses wait for the next time. Each user has at most 20 tasks, and nobody else sees them.

#### The heartbeat

At the top of the Tasks page, the **heartbeat** lets labb work through a to-do list on its own: you leave to-dos (things to do or find out), and every so often it takes the oldest open ones and does each in a chat of its own, marked unread like a task's. It's off until you turn it on; a new chat mentions it once, until you turn it on or put the tip away.

- **Every** how many minutes it looks (30 at first; Settings → Limits' shortest heartbeat is the least, 5), **with** labb or one of your agents, and how many to-dos **each time** (1 to 10).
- A look with nothing open does nothing, so leaving it on costs nothing until there's something to do. **Do them now** doesn't wait for the next look.
- Each to-do then shows done, with its chat, or failed and why, with **Try again**. Removing one that's being worked on stops it.
- Its runs are task runs: the same limits (time, model calls), they wait while someone's chatting, and after 3 failures in a row the heartbeat turns itself off and says why. After a restart, a to-do that was cut off goes back on the list.
- Each person has at most 100 open to-dos (Settings → Limits), and nobody else sees them.

### Studio

**Studio** in the sidebar is where agents, skills, tools and actions work together as **workflows**: steps on a canvas, connected left to right, that run when you say, on a schedule, from a webhook, on an event, or from the terminal. Because the workflow holds the plan, each model only does one small job at a time, which suits small local models.

**Steps:**

| Kind | Steps |
| --- | --- |
| Start | The run's inputs (asked when you run it; a webhook's JSON fills them by name), and optionally a schedule, a webhook and events |
| AI | **Agent** (one of yours, or labb, with its tools, documents and skills), **Prompt** (one model call; can answer in fields for the next steps), **Skill** (labb follows one of your skills), **Transcribe** (what's said in a recording: a link to it, a Speak step's `{{key.url}}`, or a `data:` URL) and **Speak** (text read aloud; its `url` plays in a **Write to chat** message), with the [speech](#speech) models |
| Tools | **Tool** (web search, read a page, search or save documents, remember), **Action** (an HTTP request or a saved prompt, below), **Python** (in the sandbox) |
| Logic | **If / else** (by rules, or by asking a model to choose), **For each** (a step for every item of a list, one after another or several at once), **Merge**, **Approval** (waits for you), **Wait**, **Transform** (text or JSON without a model) |
| Output | **Write to chat** (a new chat, unread), **Save document**, **Web page** (labb builds and publishes a [web page](#web-pages) from a prompt; its output has the `url` for a later step to send, and **Keep one page** updates the same page each run so its link stays), **Result** (what the terminal prints and a webhook caller gets) |

A step's output is `{{key}}` for the steps after it (`{{research}}`, `{{review.notes}}`), the inputs are `{{trigger.name}}`, and filters help: `{{list | json}}`, `{{text | lines}}`, `| length`, `| upper`, `| lower`, `| trim`. Each text setting has an **Insert** menu of what that step can use.

**How runs go:** a step starts when the steps before it have ended; steps that don't depend on each other run at the same time. A branch not taken is skipped, and so is everything only it leads to, so an if/else can meet again in one step. A step can try again (up to 3 times) or let the run go on without it. Approvals and waits pause the run, also across restarts; answer approvals in the run panel, in **Waiting for you** on Studio's page (the count is next to Studio in the sidebar), or in the terminal. A run stops after 30 minutes or 200 steps, and each person has at most 3 runs going at once.

**Building:** **Add step** finds a step (type to search); click it to add it after the selected one, or drag it onto the canvas; drag from a step's right side to another to connect them. Backspace deletes, Ctrl+Z / Ctrl+Shift+Z undo and redo, Ctrl+C / Ctrl+V copy a step, **Arrange** lays it all out. Changes save on their own; what still needs fixing shows on the steps and in a list. **Test run** runs the draft and lights the steps up as they go; **Runs** lists the workflow's runs (filter to the failed or waiting ones), and in a run, click a step to see its input and output, show it on the canvas, or run again from it. **List** shows the steps top to bottom instead of on a canvas: the default on a phone, where the panel is a sheet at the bottom that opens when you tap a step. **Publish** makes the draft the version that schedules, webhooks, events and the terminal run; earlier versions are kept, and each run says which it used. Start from a blank workflow, a template, a file (**Export** / import), or describe it in the **drafting room**:

1. **Describe it**: say when it runs, what it looks at and where the result goes.
2. **A few questions**: if the description leaves something open that changes the design (a time, where the result goes, whether to ask you first), the model asks up to three, with answers to pick. **Skip** lets it decide.
3. **The blueprint**: the steps in plain words beside a preview, and **What it needs** (anything missing or not set up). Ask for changes ("also email it to me, but ask me first"); each answer is a new version, with the new and changed steps marked, and you can go back to any version.
4. **Try it** (optional): one real run of the unsaved blueprint. Its steps really happen, and approvals wait for you there. These runs aren't in the Runs list.
5. **Create**: saves it as a draft and opens the builder.

Nothing is saved before **Create**. Bigger models do this much better. The model is told your agents, skills, actions and models; settings it gets wrong ("default" for a model, an agent by name, a cron line for the schedule) are fixed or left at their defaults, and a schedule is in your browser's time zone.

**Ask labb**, under the canvas, changes a workflow in words: "run at 7 instead", "add an approval before it emails", "write the summary in Slovenian" (<kbd>/</kbd> goes there). Shift+click steps first to change only those. The model answers with edits to the steps it means, so the others stay exactly as they are, and you see them on the canvas before anything changes: new steps striped, changed ones outlined, removed ones faded, and each change in words in the **Changes** tab. **Keep** makes them one step you can undo, **Discard** drops them, and asking again refines them. On a failed run, **Fix it** asks for a change that makes it work, and **Explain** says what the workflow does in plain words.

**Actions** (Studio → Actions) are steps of your own. **HTTP actions** call another service: a method, URL, headers and body with `{{inputs}}` and `{{secrets.NAME}}`. Only admins make them, for everyone; they can only reach the internet unless an admin lets one reach the local network (e.g. Home Assistant), and they check every redirect. **Prompt actions** are a saved prompt with its model, made by anyone. Each can be tried once from its editor. **Secrets** (admins) are encrypted on the server, never shown again, never given to models or Python, and masked in what runs record.

**Python** steps run in the [sandbox](../sandbox/README.md), a container of its own: no internet, a read-only system, a user of its own per run, and limits on time (`timeout`, up to 2 minutes), memory and processes. `inputs` has the run's inputs and every step's output so far; set `result`. Only the standard library is there.

**Webhooks:** turn it on in the Start step, publish, then make an address in the workflow's settings (shown once). `POST` JSON to it; `?wait=30` answers with the result if the run ends by then. At most 30 calls a minute per workflow.

**Sharing:** a shared workflow can be run and duplicated by everyone on the server, as themselves; only you change it. Runs are seen by whoever started them and the workflow's owner.

### Memories

The agent keeps short facts and preferences you share across conversations ("prefers metric units", "works on labb in Python") with its `remember` and `forget` tools; you can add, edit, pin and delete them on the **Memories** page of the sidebar or with `/memory` in the terminal. **Pinned** memories (your name, language, how you like answers) are in every system prompt, at most 30. The others are recalled when a message is about them: before each turn the 3 closest are attached to your message for the model, and the saved chat stays as you typed it.

What the agent saves or changes on its own waits for your review: it's listed under **To review** on the Memories page (**👍 Keep**, edit or forget it) and marked in `/memory` (`/memory keep <n>`). Until you keep it, it's only recalled when a message is about it, even if the agent pinned it, so nothing wrong stays in every conversation. What you add yourself needs no review.

Memories are per account on the server, shared by the web chat and labb on your PCs; labb without a server keeps its own. The server finds them by meaning with the [embedding model](#documents) (in the database, next to the documents' passages) and by keywords until that model is pulled. Nobody else sees them, admins included.

### Notes

The agent writes down what took effort to find out, decisions and where a longer job stands, under a topic each, so it can pick the work up after its context was cleared or in a later conversation. It's told the topics with each message and reads the notes when one matters. In the terminal they're the project's `.labb/notes.md`; in the web chat, tasks and workflows they're **your notes**, kept on the server (only you see them; an agent you chat with uses yours). An [app's chat](#apps) keeps that **app's own notes** instead: how it's built, what was decided, what's left, for its later sessions. It doesn't read your notes, and what it writes stays out of them; removing the app removes its notes.

The **Notes** page shows them a topic each, newest first: add one, edit or delete a topic, or **Edit all** as Markdown. Its menu also has your apps (an app's notes, at `/notes/app/<id>`) and the folders of your [PCs](#your-pcs): there the page shows and changes that project's `.labb/notes.md`, while the PC is connected (asked through `labb connect`, which only touches the folders it was started with). A change made after someone else's (the agent's, or another tab's) is refused with a note to load them again, so nothing is overwritten unseen.

### Apps

Early, and growing: the **Apps** page has a tile for each of your apps and **New app** to start one. Each app is a container of its own, started by labb-spawner (its own service in Docker), built from its own files, and opening its tile shows what it serves, in the page. A new app starts from a **starter**, and every one has a React page in `frontend/`, built with Vite apart from everything else: a change to its files is built and goes live by itself a few seconds later (one that doesn't build leaves the last good page up, and the **Build** tab says why). **React app** is just the page; **React and Python** (FastAPI) or **React and Node** (Express) add a server in `backend/` for the app's JSON API at `/api/…`, with an example to start from (a list of items, kept in SQLite in `/data`, or in the app's own Postgres once it has one, its table made by `db/migrations/0001_items.sql`). The server doesn't serve the page: **Build** builds the server and runs the new one. The page is built with [shadcn/ui](https://ui.shadcn.com) on Tailwind CSS, and **New app** asks three things about its look: its **colours** (Neutral, Blue, Emerald, Violet or Amber), its **spacing and shadows** (Compact and flat, Balanced and soft, or Roomy and raised) and its **font** (Geist, Inter, Plus Jakarta Sans, Lora or JetBrains Mono). They're attributes on `<html>` in `frontend/index.html` (`data-color`, `data-style`, `data-font`), so the agent or you can change them later; dark mode follows your system. Each app is two containers: its front (the same for every app) serves the page and passes `/api/…` to its server, which runs as an unprivileged user, with a read-only filesystem except `/tmp` and `/data`, on port 8080.

On an app's page, **Files** has its files to read and change in a code editor (highlighting, line numbers, a find bar with Ctrl/Cmd-F that counts the matches and can replace them, Ctrl/Cmd-S to save). The field over the files (Ctrl/Cmd-Shift-F) searches all of them at once, by name and inside, with case, whole word and regular expression; picking a line opens the file there, and **Rebuild** builds them and runs the new build in its place. If a build fails, or the new build crashes in its first seconds (`APP_SETTLE_SECONDS`, 5 by default), the last one that worked keeps running, and **Build** shows why (for a crash, the start of what the app wrote). Every build saves the files as a **version**: **History** lists them with what each changed (and the changes not built yet), and **Restore this version** puts the files back as they were (saved as a new version, so it can be undone too). **Download as zip (with git)**, under the files (or in the app's ⋯ menu), gives the files as they are now with that history as a `.git` folder: unzip it and `git log`, `git status` (changes not built yet show as changes) and pushing it somewhere work as usual.

Beside the app is **its chat**: say what it should do or change, and labb reads and changes its files, builds it, and checks it (its pages and logs) until it works. Writing a Python, JSON or JavaScript file tells it at once when the file won't parse, and an edit whose text only differs from the file in spaces at the ends of lines still applies; the app reloads by itself on each new build. The chat is kept like any other, and only it has these tools, only for that app. Its **New session** button (+) starts again with an empty conversation, so labb comes to the app fresh, with nothing from the earlier talk in its context (it reads the files again as it needs them); the menu beside it goes back to an earlier session, and the page remembers which one was open. If labb goes round in circles (builds or changes failing again and again), the chat says so and offers **Start fresh**, also a button beside New session: it stops the reply and opens a new session that starts with what you asked for, the steps left and where the app is now (its last error), but none of what the stuck session concluded. The message is yours in the new session, so you see what labb was told. Its message box is the main chat's: attach files and images, dictate or use voice mode, pick the model (each session its own, like any chat; a new session or Start fresh begins with the one you were using), how it thinks and Web (the same choices as your chats), edit a message (or press ↑) and **Regenerate** the last reply. Editing or regenerating changes the conversation, not the app's files: what the replaced replies built stays, and the app's **History** tab can restore an earlier version.

An app keeps what it must remember in `/data`, the one place it can write that stays after a restart or a rebuild (up to `APP_DATA_MB`, 1024 by default: one that grows past it is stopped). Its **Settings** tab shows how much it keeps, downloads it as a tar, and sets its **environment variables**: a **secret** among them is kept encrypted and never shown again. Saving starts the app again with them. With nightly backups on, every app's files, settings and data are in each backup (`apps.tar.gz`).

An app can have a **database** of its own (Postgres, on the apps' own server, not labb's): **Add a database** on its Database tab, and it starts again with `DATABASE_URL`. Its tables come from **migrations**, `db/migrations/0001_….sql` files applied in order at each Rebuild (one that fails fails the build, and the last good build runs on; one that ran can't be changed, only followed by a new one). The tab shows its tables and rows, runs SQL (read-only unless you tick Allow changes), downloads it as SQL, and removes it. Each is limited to `APP_DB_MB` (512 by default), and the nightly backup has all of them (`apps-db.sql.gz`).

With a database, an app can also turn on **its API** (on the Database tab): its tables over HTTP at its port + 100 (`http://labb.lan:9200/rest/` for an app on 9100), so its pages can read and write data with no server code. It has only what the app's migrations grant to the `anon` role (`GRANT SELECT ON items TO anon;`), and nothing by default; for https, Caddy serves 9200–9299 too, and other devices need those ports through the firewall.

Its API also has **sign-in for the app's own users** (not labb's): its pages load `/client.js` from it (`api.signUp`, `api.signIn`, `api.from('notes').select()`…), and row-level security in its migrations decides which rows each signed-in user sees, with `auth.uid()`. The Database tab lists them under **Users**, where you can disable or remove one (they're signed out at once) and close sign-ups. There's no email confirmation or password reset by email yet.

An app can also **use labb** as you: under **Use labb** on its Settings tab, tick what it may do (ask labb's models, run your agents, search your documents, read or save your memories) and save. The app gets a key of its own (`LABB_URL`, `LABB_API_KEY`) and starts again with it; `backend/labb.py` and `backend/labb.js` in the Python and Node starters call it, from the app's server only. The key opens nothing but that (`/app-api`, below), goes when you untick everything or remove the app, and **New key** replaces it.

Each app's container is on a port of its own, from 9100 to 9199 (`APP_PORT_FIRST`, `APP_PORT_LAST`): with labb at `http://labb.lan:8000`, an app is at `http://labb.lan:9100`. With [HTTPS](#https), Caddy has those ports and serves each app with labb's certificate (`https://labb.lan:9100`). For other devices, let those ports through the server's firewall as well as labb's; `APP_BIND=127.0.0.1` keeps them to this machine. For now anyone who can reach an app's port can open it.

An app's page has **Stop**, **Start** and **Restart**, its state and memory use, and **Logs** (the end of what it wrote). When it isn't running, it says why: stopped, idle, out of memory, or crashed with an exit code. Opening a stopped app starts it again. `APP_IDLE_MINUTES` stops apps nobody used for that long (their container sent and got nothing), and `APP_MAX_TOTAL` limits the apps on the server (10 per person either way).

### Web pages

Ask in any chat for a dashboard, a chart, a diagram, a report or a small tool, and labb builds it as a web page and gives you a link; the chat shows the page under the link. Each page (an *artifact*) starts from a template that's ready to use: Next.js as a static site, React, TypeScript and Tailwind CSS, components on [Base UI](https://base-ui.com) (buttons, cards, tables, tabs, forms, dialogs, menus, tooltips), [Phosphor](https://phosphoricons.com) icons, ten chart components on [Recharts](https://recharts.org) (bar, line, area, bar and line, pie or donut, scatter or bubbles, radar, radial, funnel, treemap) and diagrams laid out by [ELK](https://eclipse.dev/elk/) and drawn with [React Flow](https://reactflow.dev). An `AGENTS.md` in it tells the model how to use all of it.

Everything is typed: a chart's fields must be the data's, and a diagram's arrows must join boxes that are there. A mistake fails the build with the file, the line and what was meant ("Did you mean 'month'?"), which the model reads and fixes; the link keeps showing the last page that built. Ask for a change later in the same chat and labb changes the same page, at the same link.

The spawner keeps each page's files (`SPAWNER_DATA/artifacts/<name>/`, in the nightly backup with the apps) and builds them in a container of their own, with the template's packages already installed and no network, in about 12 seconds (the first build on a server makes that container's image first, which takes a minute or two). labb then publishes the site to [labb-sites](#sites) at `http://<server>:8300/artifacts/<name>/` (`https://<server>:8301/…` with [HTTPS](#https)), where anyone with the link can open it; names end in 6 random characters, so they can't be guessed. Deleting the chat deletes its pages. Each person can have `ARTIFACT_MAX` (50; Settings → Limits).

The **Artifacts** page in the sidebar lists your pages from every chat: whether each is published (a failed build leaves the last good one up), the chat it came from, and its link. Open one to see the page and the last build's error if it failed, to go to its chat, or to delete it, which removes its files and its published page. Its files open in the same editor as an app's (a tree, a search of all of them with Ctrl/Cmd-Shift-F, highlighting, find and replace, Ctrl/Cmd-S to save): change, add or delete them, then **Publish** builds the page and puts it live, or shows the build's error. The template's example files are left out.

They're on in the web chat whenever the spawner and labb-sites are there (`SPAWNER_URL`, `SITES_URL`), and an agent has them if it's given **Web pages** in its tools; tasks and workflows don't build them. Pages can't add npm packages yet, and have no server of their own: their data is in the page, or fetched from public APIs in the browser. For something with a server, a database or its own users, make an [app](#apps).

### Sites

labb-sites hosts static websites next to labb, the [web pages](#web-pages) labb builds among them: each is a folder of files at `http://<server>:8300/artifacts/<name>/`, uploaded as one zip or tar that replaces the whole site at once, so visitors never see half of an upload. It's two containers: Caddy serving the sites (the only one published) and a small service taking uploads behind a token, which it makes on its first start.

```bash
TOKEN=$(docker compose exec sites-deploy cat /run/labb-sites/token)
cd dist && zip -r ../site.zip . && cd ..
curl -T site.zip -H "Authorization: Bearer $TOKEN" http://labb.lan:8300/_api/artifacts/blog   # → http://labb.lan:8300/artifacts/blog/
curl -H "Authorization: Bearer $TOKEN" http://labb.lan:8300/_api/artifacts                     # every site
curl -X DELETE -H "Authorization: Bearer $TOKEN" http://labb.lan:8300/_api/artifacts/blog
```

A name is lowercase letters, digits and hyphens. A zip of the folder itself (`zip -r site.zip dist`) works too: a single folder without an `index.html` beside it is served from inside. Every site is a path on the same address, so links in it must be relative (Vite: `base: "./"` or `base: "/artifacts/<name>/"`), and sites share an origin: one site's scripts can read another's `localStorage`, so host only sites you trust. Everything uploaded is public. `SITES_MAX_MB` (100) and `SITES_MAX_FILES` (10000) limit a site; with [HTTPS](#https), Caddy serves them at `https://labb.lan:8301/artifacts/<name>/`, and the nightly backup keeps them (`sites.tar.gz`). The API and the rest are in [sites/README.md](../sites/README.md).

### Integrations

People connect their own accounts in **Settings → Integrations**, and the assistant can then use them in their chats, with agents and in their tasks and workflows: always the account of whoever is chatting (or the task's owner), never the agent owner's. Each integration's tools are only offered to people who connected it, and an agent with chosen tools needs them ticked.

| Integration | What the assistant can do | Tools |
| --- | --- | --- |
| Google | See and change calendar events; search and read Gmail, write drafts (never send) | Google Calendar, Gmail |
| Microsoft 365 | See and change Outlook calendar events; search and read Outlook mail, write drafts (never send) | Outlook calendar, Outlook mail |
| GitHub | Search and read issues, pull requests, repositories and files; open issues and comment when asked (never merges, closes or pushes) | GitHub |
| Notion | Search and read the pages the person shares with labb when connecting; make pages and add to them when asked (never deletes) | Notion |

An admin sets each one up first, once, in the server's **Settings → Integrations**, which shows the steps and the redirect URI to register, or with `<NAME>_CLIENT_ID` and `<NAME>_CLIENT_SECRET` (`GOOGLE_`, `MICROSOFT_`, `GITHUB_`, `NOTION_`):

- **Google:** in [Google Cloud](https://console.cloud.google.com/apis/credentials), make a project and turn on the **Google Calendar API** and the **Gmail API**. On the OAuth consent screen, choose **External** and add the people who'll connect as test users, or publish the app (Google warns that it isn't verified; that's fine for your own household or team). While it's in testing, Google ends connections after 7 days. Make an OAuth client ID of type **Web application** with the redirect URI.
- **Microsoft 365:** in [Microsoft Entra](https://entra.microsoft.com/), under **App registrations**, make a **New registration** for accounts in any organization and personal Microsoft accounts, add the **Web** platform with the redirect URI under **Authentication**, and make a client secret under **Certificates & secrets**. For only your organization's accounts, register it for that and set `MICROSOFT_TENANT` to its tenant id. It asks for `Mail.ReadWrite` (drafts) and `Calendars.ReadWrite`, never `Mail.Send`.
- **GitHub:** under **Settings → Developer settings → OAuth Apps** (yours or an organization's), make a **New OAuth App** with the redirect URI as its callback URL. It asks for `repo`, which reaches private repositories too.
- **Notion:** in [Notion's integrations](https://www.notion.so/profile/integrations), make a **Public** integration with read, update and insert content, and the redirect URI. When people connect, they pick the pages labb may use; more can be added later from a page's ••• menu, under **Connections**.

Google and Microsoft only send people back to `localhost` or to an `https` address on a public domain (GitHub to any address). When the server is one (set `LABB_PUBLIC_URL` behind a reverse proxy, Tailscale Funnel or a tunnel), connecting comes straight back. On a LAN address like `http://192.168.1.5:8000`, they send people to `http://localhost:8000/…` instead, which doesn't load: they copy that address from the address bar and paste it into Settings → Integrations. Either way, register the redirect URI the settings show.

Tokens are encrypted with `LABB_SECRET_KEY` and refreshed on their own (GitHub's and Notion's don't run out); disconnecting revokes them where the app can (Google, GitHub). If an app turns a connection down (a changed password, removed access, 7 days in Google's testing), it shows as needing connecting again. What the assistant reads goes to the model it's chatting with, so with OpenAI or Anthropic it leaves your network.

### API

Everything except `/health`, `/version`, `/wheels/…`, `/install.sh` and the page needs the session cookie (plus `X-Labb: 1` for changes) or `Authorization: Bearer lb_…`.

| Endpoints | |
| --- | --- |
| `POST /chat` | `{"message", "thread_id"?, "model"?, "attachments"?, "replace_from"?, "keep_attachments"?, "browser"? (true offers the browser tools), "browser_auto"? (true: the browser doesn't ask), "web"? (false: no web search or page reading)}` → NDJSON events: `thread`, `thinking`, `token`, `tool_call`, `tool_result`, `compact`, `continue` (`reason`: the reply ended too early and goes on), `retry` (`wait`, `message`: a model call is tried again), `ping` (every 15 seconds of quiet), `title`, `error`, then `done` or `stopped` with `stats`. Closing the stream doesn't stop the reply |
| `GET /chat/live?thread_id=` | Join the reply being written in a chat, from its start: a `user` event with its `turn`, then its events as `/chat` sends them; 204 if there's none |
| `POST /stop` | `{"thread_id"}`: stop a running reply |
| `GET /notes`, `POST /notes` | Your [notes](#notes) (`text`, `digest`, `topics`); change them with `{"action": "add", "text", "topic"?}` or `{"action": "replace", "text", "base"?}` (the digest you read: 409 if they changed since) |
| `GET /pcs/{id}/notes?folder=`, `POST /pcs/{id}/notes` | The same for a connected PC's folder, from the web page only (not with a key) |
| `GET /apps/{id}/notes`, `POST /apps/{id}/notes` | The same for one of your apps: the notes its chat keeps |
| `GET /history`, `/export`, `/tool_result` | A conversation as events, as Markdown, or one tool call in full |
| `GET /context?thread_id=…&model=…` | What a chat's next model call gets, with estimated tokens per part (this server's own chats only) |
| `GET /context/work?thread_id=…` | What a chat works on now: `todos`, `open` (steps still to do), `goal`, `plan` and `plan_file`; a session on a PC is asked of the PC |
| `GET /threads`, `PATCH`/`DELETE /threads/{id}` | List, rename or pin, delete chats |
| `GET /models`, `GET /cli_sessions` | Models to pick from, and whether there's a browser; labb sessions |
| `GET /browser-api/{thread}/state`, `…/frame`, `POST …/input`, `POST /browser-api/act`, `POST /browser-api/view/done`, `DELETE /browser-api/profile`, `GET`/`POST /browser-api/trusted`, `DELETE /browser-api/trusted/{site}` | [The browser](#the-browser): its tabs, what it shows as a JPEG, your clicks and keys; acting (labb on the PCs); keeping sign-ins; signing out of everything; your trusted sites. Always your own |
| `/admin/providers[/{name}]`, `POST /admin/providers/{name}/test`, `GET`/`PUT /admin/models` | Providers (`{"base_url"?, "api_key"?, "clear_key"?, "enabled"?}`; `DELETE` goes back to `.env`) and the model settings (`{"default"?, "context_window"? (tokens, null for `.env`'s), "ollama_num_ctx"? (tokens, null for `OLLAMA_NUM_CTX`), "offer"?: "all"\|"chosen", "chosen"?: [{"spec", "label"?, "admins_only"?}]}`), admins only |
| `POST`/`DELETE /sync/{thread_id}` | Save or delete a session a PC sends |
| `/auth/…`, `/keys`, `/admin/…` | Accounts, labb's device login, API keys, users, invites |
| `/ollama/api/{chat,show,tags,version,ps}`, `GET /crawler/{read,search,health}` | The gateway |
| `GET`/`POST /documents`, `DELETE /documents/{id}`, `POST /documents/search` | Your documents: `{"name", "data"}` (base64) to add, `{"query", "k"?}` to search |
| `GET /speech`, `POST /speech/transcribe`, `POST /speech/speak`, `GET /speech/clips/{id}`, `/admin/speech`, `POST /admin/speech/test` | [Speech](#speech): what the server can do; `{"data", "name"?, "language"?}` (base64) → `{"text"}`; `{"text", "voice"?, "markdown"?}` → MP3; a Speak step's clip; the settings (`{"listen"?, "speak"?, "voice"?}`), admins only |
| `GET /connections`, `POST /connections/{app}/start`, `…/finish`, `DELETE /connections/{app}`, `/admin/apps[/{app}]` | Your [integrations](#integrations): start connecting (→ `{"url", "mode"}`), finish with the pasted address (`{"url"}`), disconnect; the apps' OAuth clients (`{"client_id", "client_secret"?}`), admins only |
| `GET`/`POST`/`DELETE /memories`, `PATCH`/`DELETE /memories/{id}`, `POST /memories/recall` | Your memories: `{"text", "pinned"?}`; recall takes `{"query", "k"?, "min_score"?, "pinned"?}` |
| `/studio/workflows[/{id}]`, `…/publish`, `…/runs`, `…/webhook`, `…/export`, `POST /studio/import` | Studio's workflows: list, make, change, publish, run (`{"inputs", "draft"?}`), webhook on or off |
| `GET /studio/runs[/{id}]`, `POST /studio/runs/{id}/stop`, `…/approve`, `…/rerun` | Runs with their steps; stop, answer an approval (`{"node_id", "approved", "comment"?}`), run again from a step |
| `/studio/actions[/{id}]`, `…/test`, `/studio/secrets/{name}`, `GET /studio/catalog`, `/studio/templates`, `POST /studio/describe`, `POST /studio/drafts`, `POST /studio/try`, `POST /studio/explain`, `POST /studio/workflows/{id}/suggest` | Actions, secrets (admins), the kinds of steps, templates, drafting from a description (the drafting room's turns, and a run of an unsaved draft), what a workflow does, and changes in words or fixes for a failed run (previews, not saved) |
| `POST /hooks/{token}` | Start a workflow from outside (no login: the address is the key); `?wait=N` up to 30 seconds |
| `/app-api/me`, `POST /app-api/ask`, `GET /app-api/agents`, `POST /app-api/agents/{id}/ask`, `POST /app-api/documents/search`, `GET /app-api/memories`, `POST /app-api/memories/recall`, `POST /app-api/memories` | For [apps](#apps) only, with an app's key (`lba_…`) and for what it was given: `ask` takes `{"message", "model"?, "conversation"?}` → `{"text", "conversation", …}`. An app's key opens nothing else |
| `GET /usage`, `/admin/usage[/users\|/gateway\|/machines]` | Usage stats; `since`, `until`, `tz` (minutes from UTC); CSV with `Accept: text/csv` |

## How it works

- **Saved conversations:** LangGraph's checkpointer (Postgres on the server, SQLite in `chats.sqlite` without `LABB_DATABASE_URL` and on PCs), plus tables for the chat list, labb sessions per folder, reply stats, synced sessions, accounts, usage, documents and memories. Terminal threads are `cli-<id>`. The model titles a chat after its first reply.
- **Several tools at once:** tool calls in one reply that only read (fetching pages, searching, reading files) run at the same time, at most 5 (Settings → Limits on the server, `parallelTools` in the terminal; 1 for one at a time). Anything else runs on its own, in order, and the model gets the results in the order it asked. Stop gives up on what's still running, and keeps what finished.
- **Replies that end too early:** a reply without a tool call ends the turn, but not when it was cut off at the model's length limit, came back empty, ended with todo steps still open (unless it asks you something, or only answered a question in between), only said what the model would do next after doing some work, left a [goal](#goals) unfinished, or a `Stop` hook said no. Then the model is told why, for its next call only, and goes on: at most 3, 1, 2, 1, 10 and 3 times per reply, which an [engine](#engines) can change; an engine can also have it check the evidence once before its final report (`continue.audit`). A cut-off tool call is dropped and asked again in smaller parts. The terminal says when a reply still looks unfinished, and each reply's stats keep why it ended (`end_reason`).
- **Model calls that fail for a moment** (busy, rate-limited, a broken or silent connection) are tried again after 2, 8 and 20 seconds (or what the provider asks, up to a minute); Stop ends the wait. Running out of credits, a bad key or a missing model isn't tried again.
- **Context:** large tool results stay in full until those the model has read take a quarter of the window; then the older ones are cleared in one go (each becomes a note), leaving the last 3. Clearing in jumps rather than one result at a time keeps the start of the input the same between jumps, so the provider's prompt cache (and Ollama's) holds. The model always reads what it just asked for; if several results don't fit together they share the room, and no one result takes more than a quarter of the window, each cut with a note saying how to read the rest. A file read again while the earlier read is still in full comes back as "unchanged since #N". When the tools would take more than a tenth of the window, MCP servers' tools and mail and calendar wait to be found: the system prompt lists them and `find_tools` loads the ones needed (an MCP server with `"alwaysLoad": true` is always loaded). A long `AGENTS.md` is cut to its share of the window, with a note saying where the rest is and what its headings are. Over 75% of the context window, old results are cleared first, then older turns are summarized (in parts if they're more than the summary model takes at once, at up to 6% of the window) and the recent ones that fit in 40% are kept; the saved conversation stays complete, and the agent can get any of it back with `look_back`: a cleared result by the number in its note (`#12`), or a search of the whole conversation (an error, a path, a name), a page of 8,000 characters at a time. It works in the web chat and for every agent too. Token counts are estimated (4 characters per token) and scaled by what the provider reported. `/context` and the web chat's context button show all of this for the current chat.

## Development

```bash
uv run pytest                       # no model or Ollama needed: a fake model replies
uv run pytest tests/test_usage.py   # one file

# The same tests on Postgres (the database is emptied first)
docker run -d --name labb-pg -e POSTGRES_PASSWORD=test -p 55432:5432 pgvector/pgvector:pg18-trixie
TEST_DATABASE_URL=postgresql://postgres:test@localhost:55432/postgres uv run pytest
```

[`AGENTS.md`](../AGENTS.md) describes the architecture for coding agents. The code is in `src/labb` (core at the top, `cli/` and `server/` below it). Changes to what labb runs need a version bump so `--update` picks them up.
