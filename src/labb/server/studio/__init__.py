"""Studio: workflows that coordinate agents, skills, tools and actions, built on a canvas in the
web chat and run on the server.

- store.py keeps workflows (a draft and published versions), runs and their steps, actions and secrets.
- nodes.py is the catalog of step types, and checks a workflow before it's published or run.
- template.py fills in `{{step.output}}` references between steps.
- engine.py runs a workflow: steps start when the ones before them are done, branches not taken
  are skipped, independent steps run in parallel, and a run can pause (an approval, a wait) and
  go on later, also after a restart, since every step is saved as it ends.
- steps.py runs each kind of step; actions.py the custom actions (HTTP requests, saved prompts);
  sandbox.py sends Python code to the sandbox service (sandbox/).
- triggers.py starts runs on a schedule, from webhooks and on events; routes.py is the API.
"""
