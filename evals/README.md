# Evals

`tasks.json` is a list of natural-language requests and the script name(s)/flag(s) a correct
agent response should invoke. Paste each `request` (via `python3 evals/run.py --list`) to the
agent you're testing SKILL.md's triggering against, save its response as `results/<id>.txt`, then
score them:

```bash
python3 evals/run.py --list                  # print the prompts
python3 evals/run.py results/                # folder of <id>.txt transcripts -> pass rate
python3 evals/run.py results/5.txt --task 5  # score one transcript
```

A transcript "passes" a task when every string in its `expect` list appears somewhere in it
(case-sensitive substring match) -- it's checking that the right tool got invoked, not grading
the response's prose.
