# watsonx Orchestrate evaluation exports

Files from the wxO agent evaluation, named by run date:

- `results_YYYY-MM-DD_<test>.csv`: per-test metrics exported from wxO
  Evaluations (tool call precision and recall, routing accuracy, journey
  success, response time). `_rerun`, `_rerun2` and so on mark later runs of the
  same test that day.
- `screenshots/eval_YYYY-MM-DD_<test>.png`: the agent's answer (and, where
  noted, its trace) for that run, checked by hand against that day's report.
- `screenshots/defect_YYYY-MM-DD_<name>.png`: an answer that showed a defect.

See `../README.md` for the method, results, validation and defects found.
