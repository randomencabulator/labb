"""The prompts of `labb eval tools`: each should make the model call one tool.

`tool` is the tool to call, `args` the arguments that must be there (a value is a piece of
text the argument must contain, None just that it's there), `together` how many calls of it one
reply should make, and `web` marks prompts that run as in the web chat, without the terminal's tools. They run in a small sample project
(`PROJECT`), so the paths are real.
"""

PROJECT = {
    "README.md": "# Shop\n\nA tiny shop API. Run the tests with `pytest`.\n",
    "app.py": (
        "def total(prices, discount=0):\n"
        "    \"\"\"The sum of the prices, less the discount (0 to 1)\"\"\"\n"
        "    return sum(prices) * (1 - discount)\n\n\n"
        "def add_item(cart, item):\n"
        "    cart.append(item)\n"
        "    return cart\n"
    ),
    "tests/test_app.py": "from app import total\n\n\ndef test_total():\n    assert total([1, 2]) == 3\n",
    "config/settings.toml": "[server]\nport = 8080\ndebug = true\n",
    "notes/todo.txt": "- add a checkout\n- fix the discount rounding\n",
}

CASES = [
    # Files
    {"prompt": "Show me what's in app.py", "tool": "read_file", "args": {"path": "app.py"}},
    {"prompt": "Read the README", "tool": "read_file", "args": {"path": "README"}},
    {"prompt": "Open config/settings.toml and tell me the port", "tool": "read_file", "args": {"path": "settings.toml"}},
    {"prompt": "What does the test file in tests/ check? Read it.", "tool": "read_file", "args": {"path": "test_app.py"}},
    {"prompt": "Read just the first 3 lines of app.py", "tool": "read_file", "args": {"path": "app.py", "limit": None}},
    {"prompt": "List the files in this folder", "tool": "list_files", "args": {}},
    {"prompt": "What's in the notes folder?", "tool": "list_files", "args": {"path": "notes"}},
    {"prompt": "Find every Python file in the project", "tool": "glob", "args": {"pattern": "py"}},
    {"prompt": "Are there any .toml files here? Find them by name.", "tool": "glob", "args": {"pattern": "toml"}},
    {"prompt": "Search the code for where add_item is defined", "tool": "grep", "args": {"pattern": "add_item"}},
    {"prompt": "Which files mention 'discount'? Search their contents.", "tool": "grep", "args": {"pattern": "discount"}},
    {"prompt": "Search for TODO in the Python files only", "tool": "grep", "args": {"pattern": "TODO", "glob": "py"}},
    # Edits
    {"prompt": "In app.py, rename the function add_item to add_to_cart. Change only the def line.",
     "tool": "edit_file", "args": {"path": "app.py", "old_string": "add_item", "new_string": "add_to_cart"}},
    {"prompt": "In config/settings.toml, change debug = true to debug = false",
     "tool": "edit_file", "args": {"path": "settings.toml", "old_string": "debug = true", "new_string": "debug = false"}},
    {"prompt": "Create a file named .gitignore containing __pycache__/", "tool": "write_file",
     "args": {"path": ".gitignore", "content": "__pycache__"}},
    {"prompt": "Write a new file docs/usage.md with a one-line description of the shop", "tool": "write_file",
     "args": {"path": "usage.md", "content": None}},
    # Shell
    {"prompt": "Run the tests with pytest", "tool": "run_command", "args": {"command": "pytest"}},
    {"prompt": "Show me the git log", "tool": "run_command", "args": {"command": "git log"}},
    {"prompt": "Install the requests package with pip", "tool": "run_command", "args": {"command": "pip install"}},
    {"prompt": "Make a folder named build", "tool": "run_command", "args": {"command": "mkdir"}},
    {"prompt": "Run python app.py and give it 30 seconds at most", "tool": "run_command",
     "args": {"command": "python", "timeout": None}},
    # Todo list, notes, questions
    {"prompt": "Plan these steps with your todo list: add a checkout function, test it, update the README. "
               "Start with the first one.", "tool": "update_todos", "args": {"todos": None}},
    {"prompt": "Write down in your todo list that the discount rounding is done and the checkout is next",
     "tool": "update_todos", "args": {"todos": "discount"}},
    {"prompt": "What did earlier sessions note about this project? Check your notes.", "tool": "read_notes", "args": {}},
    {"prompt": "Save in your project notes that tests run with pytest from the project folder",
     "tool": "write_notes", "args": {"text": "pytest"}},
    {"prompt": "Add a checkout. Before you do, ask me whether it should take a coupon code: give me the choices.",
     "tool": "ask_user", "args": {"question": None, "options": None}},
    # Web, in the terminal
    {"prompt": "Look up the latest Python release on the web", "tool": "web_search", "args": {"query": "python"}},
    {"prompt": "Read the page https://docs.python.org/3/library/json.html", "tool": "fetch_page",
     "args": {"url": "docs.python.org"}},
    {"prompt": "Read both README.md and app.py", "tool": "read_file", "args": {"path": None}, "together": 2},
    # As in the web chat: no files or shell
    {"prompt": "Search the web for today's weather in Oslo", "tool": "web_search", "args": {"query": "Oslo"}, "web": True},
    {"prompt": "What's the news about the Rust programming language this week? Search for it.", "tool": "web_search",
     "args": {"query": "Rust"}, "web": True},
    {"prompt": "Open https://example.com and tell me what it says", "tool": "fetch_page",
     "args": {"url": "example.com"}, "web": True},
    {"prompt": "Read these three pages: https://example.com/a, https://example.com/b and https://example.com/c",
     "tool": "fetch_page", "args": {"url": "example.com"}, "together": 3, "web": True},
    {"prompt": "Remember that I prefer answers in metric units", "tool": "remember", "args": {"text": "metric"}, "web": True},
    {"prompt": "Please keep in mind for later that my dog is called Pixel", "tool": "remember",
     "args": {"text": "Pixel"}, "web": True},
]
