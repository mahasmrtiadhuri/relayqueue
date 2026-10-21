# Contributing

Keep changes focused on a concrete behavior. Include a regression test for changes to queue ownership, retry timing, or idempotency. Run `python -m unittest discover -s tests -v` before opening a pull request.

Describe the problem, the resulting behavior, and how it was verified. Document changes to API responses or persisted state. Do not add dependencies without explaining the tradeoff. Database changes must consider existing queue files and include a migration strategy.
