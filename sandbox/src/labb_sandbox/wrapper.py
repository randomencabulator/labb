"""Runs in the child process: the step's code with `inputs`, then `result` written as JSON.

Kept apart from the service, and started with `python -I`, so the code sees nothing of it."""

import json
import sys
import traceback


def main():
    folder = sys.argv[1]
    with open(f"{folder}/inputs.json", encoding="utf-8") as f:
        inputs = json.load(f)
    with open(f"{folder}/code.py", encoding="utf-8") as f:
        code = f.read()
    scope = {"__name__": "__main__", "inputs": inputs, "result": None}
    try:
        exec(compile(code, "step.py", "exec"), scope)
    except BaseException:
        lines = traceback.format_exc().splitlines()
        # Leave out this file's own frame
        kept = [lines[0]] + [l for l in lines[1:] if "wrapper.py" not in l and "exec(compile(" not in l]
        with open(f"{folder}/error.txt", "w", encoding="utf-8") as f:
            f.write("\n".join(kept)[-4000:])
        sys.exit(1)
    try:
        text = json.dumps(scope.get("result"), default=str, ensure_ascii=False)
    except (TypeError, ValueError) as e:
        with open(f"{folder}/error.txt", "w", encoding="utf-8") as f:
            f.write(f"result can't be turned into JSON: {e}")
        sys.exit(1)
    with open(f"{folder}/result.json", "w", encoding="utf-8") as f:
        f.write(text)


if __name__ == "__main__":
    main()
